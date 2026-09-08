"""quarter_brief.py — why a check flagged, in the filing's own words.

The seven checks say what happened. "Costs outran sales, +28% revenue against
+65% operating expenses" is correct and, to someone learning, not yet an
answer: it does not say what the money went on. The reader's next question is
always the same one — is this a company losing control of its costs, or a
company building something.

For Meta's June 2026 quarter the answer is written down. Its own management
discussion says cost of revenue rose "primarily due to higher infrastructure
expenses related to our data centers, technical infrastructure, and
third-party cloud services", and research and development rose 67%. That is
the data-centre story, told by the company, in the same document the figures
came from.

So this module does not ask a model what it thinks happened. It assembles
evidence and then asks the model to write over it:

  1. The decomposition. Which component line actually moved, from the same
     XBRL the drill already reads. Operating expenses grew 65% — but R&D grew
     67% and cost of revenue 33%, and capital expenditure went from x to y.
     No model is involved and this alone is frequently the whole answer.

  2. The company's own words. The management discussion from that exact
     filing, narrowed to the paragraphs about the line that flagged. Filers
     explain their own variances: "primarily due to", "driven by".

  3. Only then, prose. The model is given the evidence and the quotes and may
     use nothing else, following the rule this codebase already sets in
     tradestaar_take.py: AI is an analysis assistant, never a source of
     financial facts. If the evidence is thin it has to say so rather than
     reach for what it remembers about the company.

Without an API key the first two still work, and they are the part that
cannot be wrong.
"""

from __future__ import annotations

import logging
import re

import quarter_facts as qf

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1000

# The filing document is fetched whole and can be tens of megabytes of inline
# XBRL. Past this it is not worth the wait on a small dyno.
MAX_DOCUMENT_BYTES = 24 * 1024 * 1024
MAX_DISCUSSION_CHARS = 7000
MAX_QUOTES = 6

_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/"


# ── 1. What actually moved ────────────────────────────────────────────────────
#
# Each check names the component lines underneath it. "q" is the three-month
# column, "ytd" the cumulative one, "instant" a balance-sheet date. Rows the
# filer never tagged are dropped, so a lean filer gets a short table rather
# than a wall of blanks.

_RND = ["ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"]
_SM = ["SellingAndMarketingExpense", "MarketingExpense",
       "SellingGeneralAndAdministrativeExpense"]
_GA = ["GeneralAndAdministrativeExpense"]
_DA = ["DepreciationDepletionAndAmortization",
       "DepreciationAmortizationAndAccretionNet", "DepreciationNonproduction"]
_SBC = ["ShareBasedCompensation"]
_BUYBACK = ["PaymentsForRepurchaseOfCommonStock"]
_CASH = ["CashAndCashEquivalentsAtCarryingValue"]
_SECS = ["MarketableSecuritiesCurrent", "ShortTermInvestments",
         "AvailableForSaleSecuritiesDebtSecuritiesCurrent"]
_INV = ["InventoryNet"]
_AP = ["AccountsPayableCurrent"]
_STD = ["LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper"]

EVIDENCE = {
    "operating_leverage": [
        ("Revenue", qf.QUARTER_TAGS["rev"][1], "q"),
        ("Cost of revenue", qf.QUARTER_TAGS["cogs"][1], "q"),
        ("Research and development", _RND, "q"),
        ("Sales and marketing", _SM, "q"),
        ("General and administrative", _GA, "q"),
        ("Total operating expenses", qf.QUARTER_TAGS["opex"][1], "q"),
        ("Operating income", qf.QUARTER_TAGS["opinc"][1], "q"),
        ("Depreciation and amortization, year to date", _DA, "ytd"),
        ("Capital expenditure, year to date", qf.YTD_TAGS["capex"][1], "ytd"),
    ],
    "gross_margin": [
        ("Revenue", qf.QUARTER_TAGS["rev"][1], "q"),
        ("Cost of revenue", qf.QUARTER_TAGS["cogs"][1], "q"),
        ("Gross profit", ["GrossProfit"], "q"),
        ("Depreciation and amortization, year to date", _DA, "ytd"),
        ("Capital expenditure, year to date", qf.YTD_TAGS["capex"][1], "ytd"),
    ],
    "cash_conversion": [
        ("Net income, year to date", qf.YTD_TAGS["niy"][1], "ytd"),
        ("Cash from operations, year to date", qf.YTD_TAGS["cfo"][1], "ytd"),
        ("Depreciation and amortization, year to date", _DA, "ytd"),
        ("Stock-based compensation, year to date", _SBC, "ytd"),
        ("Accounts receivable", qf.INSTANT_TAGS["ar"][1], "instant"),
        ("Inventories", _INV, "instant"),
    ],
    "free_cash_flow": [
        ("Cash from operations, year to date", qf.YTD_TAGS["cfo"][1], "ytd"),
        ("Capital expenditure, year to date", qf.YTD_TAGS["capex"][1], "ytd"),
        ("Depreciation and amortization, year to date", _DA, "ytd"),
        ("Revenue, year to date", qf.YTD_TAGS["revYtd"][1], "ytd"),
    ],
    "current_ratio": [
        ("Cash and cash equivalents", _CASH, "instant"),
        ("Marketable securities", _SECS, "instant"),
        ("Accounts receivable", qf.INSTANT_TAGS["ar"][1], "instant"),
        ("Inventories", _INV, "instant"),
        ("Total current assets", qf.INSTANT_TAGS["ca"][1], "instant"),
        ("Accounts payable", _AP, "instant"),
        ("Debt due within a year", _STD, "instant"),
        ("Total current liabilities", qf.INSTANT_TAGS["cl"][1], "instant"),
    ],
    "dso": [
        ("Accounts receivable", qf.INSTANT_TAGS["ar"][1], "instant"),
        ("Revenue", qf.QUARTER_TAGS["rev"][1], "q"),
    ],
    "share_count": [
        ("Diluted share count", qf.QUARTER_TAGS["sh"][1], "q"),
        ("Net income", qf.QUARTER_TAGS["ni"][1], "q"),
        ("Diluted EPS", qf.QUARTER_TAGS["eps"][1], "q"),
        ("Share repurchases, year to date", _BUYBACK, "ytd"),
        ("Stock-based compensation, year to date", _SBC, "ytd"),
    ],
}


def _growth(now, prior):
    try:
        if now is None or not prior:
            return None
        return (float(now) - float(prior)) / abs(float(prior))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def decompose(facts: dict, check_key: str, period_end: str,
              prior_end: str | None, ytd_prior_end: str | None = None) -> list[dict]:
    """The component lines under one check, this period against the last.

    Operating expenses grew 65% is a fact about a subtotal. Which of research
    and development, cost of revenue and marketing did the growing is the
    question the reader is actually asking, and it is sitting in the same
    XBRL document.
    """
    rows = []
    for label, tags, window in EVIDENCE.get(check_key, []):
        if window == "instant":
            now = qf._pick(facts, tags, window=None, end=period_end)
            prior = qf._prior_instant(facts, tags, now, prior_end)
        else:
            span = qf.QUARTER_DAYS if window == "q" else qf.YTD_DAYS
            back = prior_end if window == "q" else (ytd_prior_end or prior_end)
            now = qf._pick(facts, tags, window=span, end=period_end)
            prior = (qf._pick(facts, tags, window=span, near=back,
                              slack=qf.PERIOD_SLACK_DAYS) if back else None)
        if not now:
            continue
        rows.append({
            "label": label,
            "now": now.get("value"),
            "prior": (prior or {}).get("value"),
            "change": _growth(now.get("value"), (prior or {}).get("value")),
            "tag": now.get("tag"),
        })
    return rows


# ── 2. What the company said about it ─────────────────────────────────────────

DISCUSSION_TERMS = {
    "operating_leverage": ["costs and expenses", "cost of revenue",
                           "research and development", "marketing and sales",
                           "sales and marketing", "general and administrative",
                           "headcount", "income from operations",
                           "operating margin", "operating expenses"],
    "gross_margin": ["cost of revenue", "cost of sales", "gross margin",
                     "gross profit", "infrastructure"],
    "cash_conversion": ["operating activities", "cash provided by operating",
                        "working capital", "accounts receivable"],
    "free_cash_flow": ["capital expenditures", "purchases of property",
                       "investing activities", "data center", "infrastructure"],
    # Filers write "Cash, cash equivalents, and marketable securities were
    # $90.26 billion" — the phrase with "and" in it never appears, so a term
    # list carrying only the textbook wording matches nothing.
    "current_ratio": ["sources of liquidity", "liquidity", "capital resources",
                      "cash, cash equivalents", "credit facility",
                      "commercial paper", "marketable securities"],
    "dso": ["accounts receivable", "cash collections", "collections",
            "receivable"],
    "share_count": ["share repurchase", "repurchases of common stock",
                    "repurchase", "net share settlement",
                    "restricted stock unit", "dilutive",
                    "share-based compensation", "stock-based compensation"],
}

# A paragraph that merely mentions a line is a table caption. One that
# explains it says why.
_EXPLAINS = re.compile(
    r"primarily due to|primarily driven|driven by|\bdue to\b|"
    r"attributable to|reflect(?:s|ing)? |"
    r"consist(?:ed|ing) of|as a result of|resulting from|to support|"
    r"we anticipate|we expect",
    re.I)

_MDA_START = re.compile(
    r"Management'?s\s+Discussion\s+and\s+Analysis", re.I)
_MDA_END = re.compile(
    r"Item\s*3[.\s]+Quantitative\s+and\s+Qualitative", re.I)


def _strip_html(html: str) -> str:
    """Filing HTML to readable paragraphs.

    Block tags become newlines before anything else is removed, otherwise the
    whole filing collapses into one line and paragraph selection has nothing
    to select.
    """
    from html import unescape
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br[^>]*>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|td|th|li|h[1-6]|table)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text)


def _paragraphs(text: str) -> list[str]:
    out = []
    for chunk in re.split(r"\n\s*\n|\n(?=\s*[A-Z])", text):
        cleaned = " ".join(chunk.split())
        if len(cleaned) >= 80:
            out.append(cleaned)
    return out


def discussion(text: str, check_key: str) -> list[str]:
    """The management-discussion paragraphs that explain this check's line.

    Narrowed to the discussion section first: the same phrases appear in the
    risk factors and the notes, where they are boilerplate rather than an
    explanation of this quarter.
    """
    body = text
    starts = [m.end() for m in _MDA_START.finditer(text)]
    if starts:
        # The first hit is the table of contents; the section itself is the
        # last one that has real length after it.
        begin = starts[-1] if len(starts) > 1 else starts[0]
        end = _MDA_END.search(text, begin)
        body = text[begin:end.start() if end else len(text)]

    terms = DISCUSSION_TERMS.get(check_key) or []
    if not terms:
        return []

    # Ranked, not taken in document order. Every check's terms match SOMETHING
    # in the income-statement discussion, which comes first, so document order
    # handed the free-cash-flow question three paragraphs about operating
    # income and never reached the one about capital expenditure. A term
    # earlier in the list is more specific to the check, and a term in the
    # paragraph's opening words means the paragraph is ABOUT that line rather
    # than mentioning it in passing.
    scored, seen = [], set()
    for order, para in enumerate(_paragraphs(body)):
        low = para.lower()
        if not _EXPLAINS.search(para):
            continue
        score = 0
        for rank, term in enumerate(terms):
            where = low.find(term)
            if where < 0:
                continue
            weight = len(terms) - rank
            score += weight * (3 if where < 150 else 1)
        if not score:
            continue
        key = low[:90]
        if key in seen:
            continue
        seen.add(key)
        scored.append((score, order, para[:1200]))

    if not scored:
        return []
    # A weak match is worse than no match: it fills the prompt with the
    # income-statement discussion when the question was about capital
    # spending, and the model then writes about the wrong thing. Anything
    # scoring well below the best match is dropped rather than padded in.
    scored.sort(key=lambda row: (-row[0], row[1]))
    floor = scored[0][0] * 0.45
    best = [row for row in scored[:MAX_QUOTES] if row[0] >= floor]
    best.sort(key=lambda row: row[1])          # back into reading order
    return [para for _score, _order, para in best]


def _primary_document(cik: str, accn: str) -> str | None:
    """The filing's main document, from the submission's own file index."""
    import fundamentals_engine as fe
    base = _ARCHIVE.format(cik=str(cik).lstrip("0"), accn=accn.replace("-", ""))
    try:
        resp = fe._req_module.get(base + "index.json", timeout=15,
                                  headers=fe._EDGAR_HEADERS)
        if resp.status_code != 200:
            return None
        items = ((resp.json().get("directory") or {}).get("item") or [])
    except Exception as exc:
        logger.warning("quarter brief: filing index failed for %s: %s", accn, exc)
        return None

    best, best_size = None, 0
    for item in items:
        name = str(item.get("name") or "")
        if not name.lower().endswith((".htm", ".html")):
            continue
        low = name.lower()
        # Exhibits, certifications and the XBRL viewer shell are not the 10-Q.
        if low.startswith(("ex", "r")) or "cert" in low or low == "index.htm":
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size > best_size:
            best, best_size = name, size
    return base + best if best else None


def filing_text(cik: str, accn: str) -> tuple[str, str | None]:
    """(plain text, url) for the filing that reported these figures."""
    if not cik or not accn:
        return "", None
    url = _primary_document(cik, accn)
    if not url:
        return "", None
    import fundamentals_engine as fe
    try:
        resp = fe._req_module.get(url, timeout=30, headers=fe._EDGAR_HEADERS)
        if resp.status_code != 200:
            return "", url
        raw = resp.content or b""
        if len(raw) > MAX_DOCUMENT_BYTES:
            logger.info("quarter brief: %s is %d bytes, skipping", url, len(raw))
            return "", url
        return _strip_html(raw.decode("utf-8", "ignore")), url
    except Exception as exc:
        logger.warning("quarter brief: document fetch failed for %s: %s", url, exc)
        return "", url


# ── 3. The prose, over the evidence and nothing else ──────────────────────────

SYSTEM = (
    "You explain one financial check on one quarter to someone who is "
    "learning to read filings. You are an analysis assistant and never a "
    "source of financial facts. Use only the evidence and the quoted filing "
    "text supplied to you. Never state a figure that is not in the evidence, "
    "never bring in anything you recall about the company, its stock price, "
    "its competitors or later events, and never give investment advice. If "
    "the evidence does not explain the result, say plainly that the filing "
    "does not say and name what a reader would have to look at next."
)


def _fmt(value):
    if value is None:
        return "not filed"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:,.2f}" if abs(number) < 1000 else f"{number:,.0f}"


def _evidence_block(rows):
    lines = []
    for row in rows:
        change = (f"  ({row['change'] * 100:+.1f}%)"
                  if row.get("change") is not None else "")
        lines.append(f"- {row['label']}: {_fmt(row['now'])} "
                     f"vs {_fmt(row['prior'])} prior{change}")
    return "\n".join(lines) or "No component lines were tagged in this filing."


def compose(check: dict, rows: list[dict], quotes: list[str], *,
            company: str, period_end: str, api_key: str | None) -> dict | None:
    """Three or four sentences over the evidence. None when no key is set."""
    if not api_key:
        return None
    import json as _json

    prompt = f"""Company: {company}
Quarter ended: {period_end}
Check: {check.get('name')} — graded {check.get('verdict')}
What the check computed: {check.get('value')} ({check.get('basis') or 'no basis given'})

COMPONENT LINES FROM THE SAME FILING
{_evidence_block(rows)}

WHAT MANAGEMENT SAID, quoted from this filing's discussion section
{chr(10).join('"' + q + '"' for q in quotes) or 'No relevant passage was found in the filing text.'}

Write JSON with these keys and nothing else:
  "headline": one sentence, under 20 words, naming the actual cause.
  "paragraphs": 2 to 3 short paragraphs. The first says which component line
     moved and by how much. The second says why, in management's own terms,
     and may quote a short phrase from the filing. The third, if the evidence
     supports it, says whether this looks like spending that builds something
     or spending that is getting away from them — and if the evidence does
     not support saying, say that instead.
  "watch": 2 or 3 short items to check next quarter, each a specific line or
     ratio, not general advice.
  "grounded": true only if the filing's own words explained it; false if you
     are working from the numbers alone."""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in response.content).strip()
    except Exception as exc:
        logger.warning("quarter brief: model call failed: %s", exc)
        return None

    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        out = _json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(out, dict):
        return None
    return {
        "headline": str(out.get("headline") or "").strip(),
        "paragraphs": [str(p).strip() for p in (out.get("paragraphs") or []) if str(p).strip()],
        "watch": [str(w).strip() for w in (out.get("watch") or []) if str(w).strip()],
        "grounded": bool(out.get("grounded")),
    }


# ── Putting it together ───────────────────────────────────────────────────────

def explain(ticker: str, check: dict, *, facts: dict, cik: str | None,
            period_end: str, prior_end: str | None, accn: str | None = None,
            ytd_prior_end: str | None = None, api_key: str | None = None) -> dict:
    """Evidence, quotes and prose for one flagged check."""
    check_key = check.get("key") or ""
    rows = decompose(facts, check_key, period_end, prior_end, ytd_prior_end)

    text, url = filing_text(cik, accn) if accn else ("", None)
    quotes = discussion(text, check_key) if text else []
    if quotes:
        # Keep the prompt small; the first passages are the closest matches.
        total, kept = 0, []
        for quote in quotes:
            if total + len(quote) > MAX_DISCUSSION_CHARS:
                break
            kept.append(quote)
            total += len(quote)
        quotes = kept

    brief = compose(check, rows, quotes,
                    company=(facts or {}).get("entityName") or ticker.upper(),
                    period_end=period_end, api_key=api_key)

    return {
        "available": True,
        "ticker": ticker.upper(),
        "check": check_key,
        "name": check.get("name"),
        "verdict": check.get("verdict"),
        "period_end": period_end,
        "evidence": rows,
        "quotes": quotes,
        "filing_url": url,
        "brief": brief,
        "note": None if brief else (
            "The AI summary is not configured on this server, so this is the "
            "evidence on its own: which line moved, and what the filing says "
            "about it."),
    }
