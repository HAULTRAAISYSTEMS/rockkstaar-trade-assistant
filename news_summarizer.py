"""Summarize one news story into Research Memory fields.

When a story is saved from the news scanner, the save sheet is pre-filled
with what matters in it: a short summary, the key numbers, why it matters,
what changed, the thesis impact, what would prove the read wrong and a
recall question. The reader edits it and saves; nothing is saved unasked.

Sources, best first:
  1. The article page itself (fetched server-side, text only, capped).
  2. The provider's summary that came with the headline.
The model (ANTHROPIC_API_KEY) writes the fields from that text only. With no
key, or if the call fails, a rule-based fill from the same text is returned
and marked ai=False so the sheet says so.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import logging
import os
import re
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

MODEL = os.environ.get("ANTHROPIC_NEWS_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 1100
ARTICLE_CHARS = 12000
PAGE_CHARS = 600_000      # raw HTML read from the article page
MODEL_TIMEOUT = 25        # seconds for the whole model call
DEADLINE = 30             # seconds for one summary end to end
MAX_CONCURRENT = 2        # summaries running at once per worker
FETCH_TIMEOUT = 5
CACHE_TTL = 6 * 3600
IMPACTS = ("strengthened", "weakened", "neutral", "uncertain")

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
# Summaries are slow (page fetch + model call). Never let them occupy more
# than MAX_CONCURRENT of a worker's request threads, and never longer than
# DEADLINE: the rest of the site, and Render's health check, must keep answering.
_slots = threading.BoundedSemaphore(MAX_CONCURRENT)
_pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT, thread_name_prefix="news-summary")

SYSTEM = (
    "You are a buy-side research assistant writing a trader's notebook entry "
    "about one news story. Use ONLY facts stated in the text you are given. "
    "Never invent numbers, names, dates or quotes. If the text does not give a "
    "number, say so rather than guessing. Write in your own words; do not copy "
    "sentences from the article. Be concise and specific. Answer with JSON only."
)


# ── Article text ─────────────────────────────────────────────────────────────

def _public_host(host: str) -> bool:
    """True only if every address the host resolves to is public.

    The URL comes from a news feed via the browser, so it is untrusted: never
    let it reach the server's own network (metadata endpoints, localhost).
    """
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return bool(infos)


def _safe_url(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and _public_host(parts.hostname or "")


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _safe_url(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _ParaText(HTMLParser):
    """Collect <p> text in one linear pass, skipping scripts, menus and forms.

    This replaced a regex that, on pages with unclosed <form>/<nav>/<header>
    tags, rescanned the rest of the page for every one of them: 40+ seconds of
    CPU holding the GIL, which froze the whole worker — health checks
    included — until Render restarted it (the 502 of Nov 9, 6:14 PM).
    """

    _SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.in_p = 0
        self.buf: list[str] = []
        self.paras: list[str] = []
        self.size = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self.skip += 1
        elif tag == "p":
            self._flush()
            self.in_p = 1

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag == "p":
            self._flush()
            self.in_p = 0

    def handle_data(self, data):
        if self.in_p and not self.skip:
            self.buf.append(data)

    def _flush(self):
        if self.buf:
            text = " ".join(" ".join(self.buf).split())
            self.buf = []
            if len(text) >= 40:  # skip bylines, captions, "Share this" stubs
                self.paras.append(text)
                self.size += len(text)


def html_to_text(page: str) -> str:
    """The article's paragraph text, without markup, menus or scripts. Linear time."""
    parser = _ParaText()
    try:
        parser.feed((page or "")[:PAGE_CHARS])
        parser.close()
    except Exception:
        pass
    parser._flush()
    return "\n".join(parser.paras)[:ARTICLE_CHARS]


def fetch_article_text(url: str) -> str:
    """Best-effort article text; "" whenever it cannot be read."""
    if not url or not _safe_url(url):
        return ""
    try:
        opener = urllib.request.build_opener(_CheckedRedirects())
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; TradestaarElite/1.0; research notebook)",
            "Accept": "text/html,application/xhtml+xml",
        })
        with opener.open(req, timeout=FETCH_TIMEOUT) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "html" not in ctype.lower():
                return ""
            raw = resp.read(PAGE_CHARS)
            charset = resp.headers.get_content_charset() or "utf-8"
        return html_to_text(raw.decode(charset, errors="replace"))
    except Exception as exc:
        logger.debug("news summary: article fetch failed for %s: %s", url, exc)
        return ""


# ── Fallback without the model ──────────────────────────────────────────────

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9$])")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(" ".join((text or "").split())) if len(s.strip()) > 20]


def rule_based(item: dict, text: str = "") -> dict:
    """Fill the fields from the story's own text when no model is available."""
    from sentiment_engine import explain_headline, score_headline

    headline = str(item.get("headline") or "").strip()
    ticker = str(item.get("ticker") or "").upper()
    body = text or str(item.get("summary") or "")
    sents = _sentences(body)
    scored = score_headline(headline, item.get("summary"))
    tone = scored["label"]
    numbered = [s for s in sents if re.search(r"\d", s)]
    key_points = (numbered or sents)[:4]
    impact = {"BULLISH": "strengthened", "BEARISH": "weakened"}.get(tone, "uncertain")
    return {
        "summary": " ".join(sents[:2]) or headline,
        "key_points": key_points,
        "why_it_matters": explain_headline(headline, item.get("reason")),
        "key_change": numbered[0] if numbered else "",
        "thesis_impact": impact,
        "disconfirming_evidence": "",
        "review_question": (f"Without looking at your notes, what happened with {ticker} "
                            f"in this story, and why did it matter to your thesis?") if ticker else "",
        "tone": tone,
    }


# ── Model call ───────────────────────────────────────────────────────────────

def _prompt(item: dict, text: str) -> str:
    return f"""Ticker: {item.get('ticker') or 'unknown'}
Headline: {item.get('headline') or ''}
Source: {item.get('source') or 'unknown'} · Published: {item.get('published_at') or 'unknown'}

STORY TEXT
{text}

Return JSON with exactly these keys:
  "summary": 2-3 sentences: what happened and the market's reaction if stated.
  "key_points": 3-5 short bullets, each a specific fact — prefer numbers
     (EPS vs estimate, revenue, guidance, % move, deal size, dates).
  "why_it_matters": 1-2 sentences on the investment implication for {item.get('ticker') or 'the company'}.
  "key_change": one line naming the single most important number or change, or "" if none is stated.
  "thesis_impact": one of "strengthened", "weakened", "neutral", "uncertain" — for a long thesis in this stock.
  "disconfirming_evidence": one sentence: what future fact would prove this read wrong.
  "review_question": one recall question to test memory of this story next week.
  "tone": one of "BULLISH", "BEARISH", "NEUTRAL"."""


def _ask_model(item: dict, text: str, api_key: str) -> dict:
    import anthropic  # imported lazily; the app already depends on it

    client = anthropic.Anthropic(api_key=api_key, timeout=MODEL_TIMEOUT, max_retries=0)
    response = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS, temperature=0.2, system=SYSTEM,
        messages=[{"role": "user", "content": _prompt(item, text)}],
    )
    out_text = "".join(getattr(b, "text", "") for b in response.content).strip()
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise ValueError("answer cut off by the token budget")
    match = re.search(r"\{.*\}", out_text, re.S)
    if not match:
        raise ValueError("no JSON in the answer")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("answer was not an object")
    return data


def _clean_result(data: dict, fallback: dict) -> dict:
    def s(key, limit):
        val = data.get(key)
        return " ".join(str(val).split())[:limit] if isinstance(val, (str, int, float)) and str(val).strip() else ""

    points = data.get("key_points")
    if isinstance(points, str):
        points = [p for p in re.split(r"\n+|•", points) if p.strip()]
    points = [" ".join(str(p).split()).lstrip("-• ")[:240] for p in (points or []) if str(p).strip()][:6]
    impact = s("thesis_impact", 20).lower()
    tone = s("tone", 10).upper()
    return {
        "summary": s("summary", 900) or fallback["summary"],
        "key_points": points or fallback["key_points"],
        "why_it_matters": s("why_it_matters", 600) or fallback["why_it_matters"],
        "key_change": s("key_change", 300),
        "thesis_impact": impact if impact in IMPACTS else fallback["thesis_impact"],
        "disconfirming_evidence": s("disconfirming_evidence", 500),
        "review_question": s("review_question", 300) or fallback["review_question"],
        "tone": tone if tone in ("BULLISH", "BEARISH", "NEUTRAL") else fallback["tone"],
    }


def _key(item: dict) -> str:
    basis = (item.get("url") or "") + "|" + (item.get("headline") or "") + "|" + (item.get("ticker") or "")
    return hashlib.sha256(basis.encode("utf-8", "ignore")).hexdigest()


def summarize(item: dict, *, fetch=fetch_article_text, ask=None, fresh: bool = False) -> dict:
    """Notebook fields for one story. Never raises; always says how it was made."""
    item = {k: (str(v) if v is not None else "") for k, v in (item or {}).items()}
    if not item.get("headline", "").strip():
        return {"ok": False, "error": "A headline is required."}
    key = _key(item)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and not fresh and time.time() - hit[0] < CACHE_TTL:
            return dict(hit[1], cached=True)

    if not _slots.acquire(blocking=False):
        out = dict(rule_based(item, item.get("summary", "") or item["headline"]), ai=False, provider="Story text")
        out.update({"ok": True, "basis": "provider summary" if item.get("summary") else "headline only",
                    "note": "The summarizer is busy, so this was filled from the story text. Tap Re-summarize in a moment."})
        return out
    try:
        future = _pool.submit(_summarize_now, item, key, fetch, ask)
        try:
            return future.result(timeout=DEADLINE)
        except _FutTimeout:
            logger.warning("news summary: over %ss for %s", DEADLINE, item.get("url") or item.get("headline"))
            out = dict(rule_based(item, item.get("summary", "") or item["headline"]), ai=False, provider="Story text")
            out.update({"ok": True, "basis": "provider summary" if item.get("summary") else "headline only",
                        "note": "The AI summary took too long, so this was filled from the story text."})
            return out
    finally:
        _slots.release()


def _summarize_now(item: dict, key: str, fetch, ask) -> dict:
    article = fetch(item.get("url", "")) if fetch else ""
    provided = item.get("summary", "").strip()
    text = article if len(article) >= 200 else ""
    if provided and provided not in text:
        text = (provided + "\n\n" + text).strip()
    text = (text or item["headline"])[:ARTICLE_CHARS]
    basis = "full article" if len(article) >= 200 else ("provider summary" if provided else "headline only")

    fallback = rule_based(item, text)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    result = None
    note = ""
    if api_key or ask:
        try:
            raw = (ask or _ask_model)(item, text, api_key)
            result = dict(_clean_result(raw, fallback), ai=True, provider=f"Claude · {MODEL}")
        except Exception as exc:
            logger.warning("news summary: model call failed: %s", exc)
            note = "The AI summary was unavailable, so this was filled from the story text."
    else:
        note = "No AI key is configured, so this was filled from the story text."
    if result is None:
        result = dict(fallback, ai=False, provider="Story text")
    result.update({"ok": True, "basis": basis, "note": note})
    if result["ai"]:
        with _cache_lock:
            if len(_cache) > 500:
                _cache.clear()
            _cache[key] = (time.time(), result)
    return result


def notes_block(summary: str, key_points: list[str]) -> str:
    """The summary and key points as they are stored in a card's notes."""
    lines = []
    if summary:
        lines += ["SUMMARY", summary.strip()]
    pts = [p.strip() for p in key_points or [] if p and p.strip()]
    if pts:
        if lines:
            lines.append("")
        lines.append("KEY POINTS")
        lines += [f"• {p}" for p in pts]
    return "\n".join(lines)
