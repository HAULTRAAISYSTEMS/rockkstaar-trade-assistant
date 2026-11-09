"""Research Memory as an edge: thesis scorecard, "what changed", recall pings.

* thesis_scorecard()  — every saved card for one ticker as a timeline, with a
  running bull/bear tally (strengthened vs weakened) and the AI's bull and
  bear case built only from your own notes (ai_case(), cached until a card
  changes).
* whats_changed()     — a 20-second brief when you open a ticker: price move,
  new headlines, new notes and earnings since your previous look.
* recall_ping()       — once a week (or the days you choose), pushes a few of
  your own due recall questions to your phone and Telegram.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

LOOK_GAP = timedelta(minutes=30)     # a reload within this is the same visit
MODEL = os.environ.get("ANTHROPIC_NEWS_MODEL", "claude-sonnet-4-6")
_ai_slots = threading.BoundedSemaphore(2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _get(key):
    from database import get_setting
    try:
        raw = get_setting(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _set(key, value):
    from database import set_setting
    set_setting(key, json.dumps(value))


# ── Thesis scorecard ─────────────────────────────────────────────────────────

def thesis_scorecard(user_id: int, ticker: str, cards: list[dict] | None = None) -> dict:
    import research_memory as memory
    ticker = ticker.upper()
    if cards is None:
        cards = memory.list_cards(user_id, ticker=ticker, limit=200)
    cards = sorted(cards, key=lambda c: c.get("created_at") or "")
    counts = {k: sum(1 for c in cards if c.get("thesis_impact") == k) for k in memory.IMPACTS}
    net = counts["strengthened"] - counts["weakened"]
    if not cards:
        lean = "none"
    elif net >= 2:
        lean = "bull"
    elif net <= -2:
        lean = "bear"
    else:
        lean = "mixed"
    timeline = [{"id": c["id"], "date": (c.get("created_at") or "")[:10], "impact": c.get("thesis_impact"),
                 "headline": c.get("headline"), "key_change": c.get("key_change") or "",
                 "why": c.get("why_it_matters") or ""} for c in cards]
    cached = _get(f"thesis_case:{int(user_id)}:{ticker}") or {}
    sig = _cards_sig(cards)
    return {"ticker": ticker, "total": len(cards), "counts": counts, "net": net, "lean": lean,
            "timeline": timeline[::-1], "case": cached.get("case") if cached.get("sig") == sig else None,
            "case_stale": bool(cached) and cached.get("sig") != sig}


def _cards_sig(cards: list[dict]) -> str:
    basis = "|".join(f"{c.get('id')}:{c.get('updated_at')}" for c in cards)
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def _case_prompt(ticker: str, cards: list[dict]) -> str:
    lines = []
    for c in cards[-40:]:
        lines.append(f"- {(c.get('created_at') or '')[:10]} [{c.get('thesis_impact')}] {c.get('headline')}\n"
                     f"  why: {c.get('why_it_matters') or ''}\n  change: {c.get('key_change') or ''}\n"
                     f"  could be wrong if: {c.get('disconfirming_evidence') or ''}")
    return f"""These are a trader's own research notes on {ticker}, oldest first.

{chr(10).join(lines)}

Using ONLY these notes (no outside facts), return JSON:
  "bull": 2-4 short bullets — the strongest points for owning {ticker}.
  "bear": 2-4 short bullets — the strongest points against.
  "verdict": one sentence on where the evidence leans now and how firmly.
  "watch": 1-3 short bullets — the next facts that would change the verdict.
If the notes are thin, say so in the verdict."""


def ai_case(user_id: int, ticker: str, *, ask=None) -> dict:
    """Bull/bear case from the user's notes. Cached until a card changes."""
    import research_memory as memory
    ticker = ticker.upper()
    cards = sorted(memory.list_cards(user_id, ticker=ticker, limit=200), key=lambda c: c.get("created_at") or "")
    if not cards:
        return {"ok": False, "error": f"Save at least one {ticker} note first."}
    sig = _cards_sig(cards)
    key = f"thesis_case:{int(user_id)}:{ticker}"
    cached = _get(key) or {}
    if cached.get("sig") == sig and cached.get("case"):
        return {"ok": True, "case": cached["case"], "cached": True}
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not (api_key or ask):
        return {"ok": False, "error": "No AI key is configured on the server."}
    if not _ai_slots.acquire(blocking=False):
        return {"ok": False, "error": "The AI is busy. Try again in a moment."}
    try:
        raw = (ask or _ask)(_case_prompt(ticker, cards), api_key)
    except Exception as exc:
        logger.warning("thesis case %s failed: %s", ticker, exc)
        return {"ok": False, "error": "The AI case could not be built right now."}
    finally:
        _ai_slots.release()

    def bullets(v):
        if isinstance(v, str):
            v = [x for x in re.split(r"\n+|•", v) if x.strip()]
        return [" ".join(str(x).split()).lstrip("-• ")[:220] for x in (v or []) if str(x).strip()][:4]

    case = {"bull": bullets(raw.get("bull")), "bear": bullets(raw.get("bear")),
            "verdict": " ".join(str(raw.get("verdict") or "").split())[:400],
            "watch": bullets(raw.get("watch"))[:3], "built_at": _now().isoformat(), "notes": len(cards)}
    _set(key, {"sig": sig, "case": case})
    return {"ok": True, "case": case, "cached": False}


def _ask(prompt: str, api_key: str) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key, timeout=25, max_retries=0)
    resp = client.messages.create(
        model=MODEL, max_tokens=900, temperature=0.2,
        system="You summarize a trader's own notes into a bull and bear case. Use only the notes. JSON only.",
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


# ── What changed since you last looked ──────────────────────────────────────

def _price(ticker: str) -> float | None:
    try:
        from market_engine import live_quotes
        q = (live_quotes([ticker]) or {}).get(ticker) or {}
        if q.get("price"):
            return float(q["price"])
    except Exception:
        pass
    try:
        from database import get_stock_data
        snap = get_stock_data(ticker) or {}
        return float(snap["current_price"]) if snap.get("current_price") else None
    except Exception:
        return None


def record_look(user_id: int, ticker: str, price: float | None, now: datetime | None = None) -> dict:
    """Roll the visit: a new look after LOOK_GAP becomes 'current', the old one 'previous'."""
    now = now or _now()
    key = f"last_look:{int(user_id)}:{ticker.upper()}"
    state = _get(key) or {}
    at = _parse(state.get("at"))
    if at is None:
        state = {"at": now.isoformat(), "price": price, "prev_at": None, "prev_price": None}
    elif now - at >= LOOK_GAP:
        state = {"at": now.isoformat(), "price": price, "prev_at": state.get("at"), "prev_price": state.get("price")}
    _set(key, state)
    return state


def _ago(delta: timedelta) -> str:
    mins = int(delta.total_seconds() // 60)
    if mins < 60:
        return f"{mins} min"
    hours = mins // 60
    if hours < 48:
        return f"{hours} h"
    return f"{hours // 24} days"


def whats_changed(user_id: int, ticker: str, *, now: datetime | None = None, price=None,
                  news=None, cards=None, earnings_days=None, earnings_date=None) -> dict:
    """Brief since the previous visit. Inputs are fetched when not supplied."""
    import research_memory as memory
    ticker = ticker.upper()
    now = now or _now()
    price = _price(ticker) if price is None else price
    state = record_look(user_id, ticker, price, now)
    prev_at = _parse(state.get("prev_at"))
    if prev_at is None:
        return {"ok": True, "first_look": True, "ticker": ticker,
                "lines": [f"First look at {ticker} here — next time you'll see what changed since now."]}
    lines, items = [], []
    prev_price = state.get("prev_price")
    move = None
    if price and prev_price:
        move = (price / float(prev_price) - 1) * 100
        arrow = "▲" if move > 0 else ("▼" if move < 0 else "•")
        lines.append(f"{arrow} {move:+.2f}% — {float(prev_price):.2f} → {price:.2f}")
    if news is None:
        try:
            import intel_engine
            news = intel_engine.fetch_ticker_news(ticker, limit=8)
        except Exception:
            news = []
    fresh = []
    for n in news or []:
        ts = _parse(n.get("published_at"))
        if ts and ts > prev_at:
            fresh.append({"headline": n.get("headline"), "source": n.get("source"), "url": n.get("url") or "",
                          "time": n.get("time") or ""})
    if fresh:
        lines.append(f"{len(fresh)} new headline{'s' if len(fresh) != 1 else ''}")
        items = fresh[:3]
    if cards is None:
        try:
            cards = memory.list_cards(user_id, ticker=ticker, limit=200)
        except Exception:
            cards = []
    new_cards = [c for c in cards if (_parse(c.get("created_at")) or now) > prev_at]
    if new_cards:
        lines.append(f"{len(new_cards)} new note{'s' if len(new_cards) != 1 else ''} in your Memory")
    if earnings_days is None:
        try:
            import trade_gate
            ind = trade_gate.gather(ticker)
            earnings_days, earnings_date = ind.get("earnings_days"), ind.get("earnings_date")
        except Exception:
            pass
    if earnings_days is not None and 0 <= earnings_days <= 7:
        lines.append(f"Earnings in {earnings_days} day{'s' if earnings_days != 1 else ''} ({earnings_date})")
    if not lines:
        lines.append("Nothing material — no price data, news or notes since then.")
    return {"ok": True, "first_look": False, "ticker": ticker, "since": state["prev_at"],
            "since_label": _ago(now - prev_at) + " ago", "move_pct": round(move, 2) if move is not None else None,
            "lines": lines, "headlines": items}


# ── Weekly recall ping ──────────────────────────────────────────────────────

def _et(now: datetime) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return now


def recall_ping(user_id: int = 1, *, now: datetime | None = None, push=None, telegram=None,
                due_cards=None, force: bool = False) -> dict:
    """Send up to 3 due recall questions once on each ping day (default Sunday), from 6 PM ET."""
    now = now or _now()
    et = _et(now)
    days = {int(d) for d in re.findall(r"\d", os.environ.get("RECALL_PING_WEEKDAYS", "6"))}
    hour = int(os.environ.get("RECALL_PING_HOUR_ET", "18"))
    key = f"recall_ping:{int(user_id)}"
    state = _get(key) or {}
    today = et.strftime("%Y-%m-%d")
    if not force:
        if et.weekday() not in days or et.hour < hour or et.hour >= 22:
            return {"sent": False, "reason": "not ping time"}
        if state.get("last") == today:
            return {"sent": False, "reason": "already sent today"}
    if due_cards is None:
        import research_memory as memory
        nowiso = now.isoformat()
        due_cards = [c for c in memory.list_cards(user_id, limit=500) if (c.get("due_at") or "") <= nowiso]
    if not due_cards:
        _set(key, {"last": today, "count": 0})
        return {"sent": False, "reason": "nothing due"}
    picks = sorted(due_cards, key=lambda c: c.get("due_at") or "")[:3]
    questions = [f"{c['ticker']}: {c.get('review_question') or 'What happened, and why did it matter?'}" for c in picks]
    body = questions[0] + (f" (+{len(due_cards) - 1} more due)" if len(due_cards) > 1 else "")
    payload = {"title": f"🧠 Recall: {len(due_cards)} note{'s' if len(due_cards) != 1 else ''} due",
               "body": body[:240], "url": "/research-memory/review", "tag": "recall-weekly"}
    pushed = None
    try:
        if push:
            pushed = push(user_id, payload)
        else:
            import browser_push
            pushed = browser_push.send_to_user(user_id, payload)
    except Exception as exc:
        logger.warning("recall push failed: %s", exc)
    sent_tg = False
    try:
        text = "🧠 Research Memory recall — answer before you peek:\n\n" + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions)) \
               + f"\n\n{len(due_cards)} due · /research-memory/review"
        if telegram:
            sent_tg = bool(telegram(text))
        else:
            import uptime_watchdog
            sent_tg = uptime_watchdog.send_telegram(text)
    except Exception as exc:
        logger.warning("recall telegram failed: %s", exc)
    _set(key, {"last": today, "count": len(due_cards)})
    return {"sent": True, "due": len(due_cards), "questions": questions, "push": pushed, "telegram": sent_tg}
