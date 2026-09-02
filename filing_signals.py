"""Governance and filing red flags, straight from a company's 8-K item codes.

The scorecard reads what a company reports. This reads what it had to disclose
and would rather you skipped: that it restated earnings, changed auditors,
lost its CFO, filed late, or got a delisting notice. None of that shows up in
a margin trend, and all of it changes what the margins are worth.

Every signal here is a filed fact with a date and a document behind it, not an
inference. An 8-K's item codes say exactly which disclosure obligation the
filing satisfied — 4.02 IS "non-reliance on previously issued financial
statements", nothing softer — so there is no keyword guessing and no judgment
call. When the submissions feed does not carry item codes the layer reports
itself unavailable rather than reading the absence as a clean record, because
"nothing filed" and "we could not look" are very different answers.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

# 8-K item code -> (label, why it matters, severity)
# https://www.sec.gov/files/form8-k.pdf
ITEM_SIGNALS: dict[str, tuple[str, str, str]] = {
    "4.02": ("Financial statements restated",
             "The company told investors its own previously issued numbers "
             "cannot be relied on. Every ratio built from that period is "
             "suspect until the restated figures land.", "critical"),
    "1.03": ("Bankruptcy or receivership",
             "Filed under Chapter 11 or 7, or entered receivership.", "critical"),
    "3.01": ("Delisting or listing-standard notice",
             "The exchange has told the company it no longer meets listing "
             "standards, or it has moved to delist.", "critical"),
    "4.01": ("Auditor changed",
             "A new accounting firm signs the numbers. Sometimes routine, "
             "sometimes a disagreement the outgoing auditor had to disclose.",
             "high"),
    "5.02": ("Officer or director departure",
             "A named executive or board member left, was appointed, or had "
             "their compensation changed. A finance chief leaving shortly "
             "before a restatement is a pattern worth knowing.", "medium"),
    "2.06": ("Material asset impairment",
             "The company wrote down assets it had been carrying at a higher "
             "value.", "high"),
    "1.02": ("Material agreement terminated",
             "A contract the company had called material has ended.", "medium"),
    "2.04": ("Debt acceleration or covenant trigger",
             "An obligation came due early, usually because a covenant was "
             "breached.", "high"),
}

# Forms that are themselves the signal.
FORM_SIGNALS: dict[str, tuple[str, str, str]] = {
    "NT 10-K": ("Annual report filed late",
                "The company told the SEC it could not file its 10-K on time.",
                "high"),
    "NT 10-Q": ("Quarterly report filed late",
                "The company told the SEC it could not file its 10-Q on time.",
                "medium"),
}

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2}
DEFAULT_LOOKBACK_DAYS = 1095          # three years


def _as_date(value) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _rows(recent: dict) -> list[dict]:
    """Turn SEC's parallel arrays into rows.

    filings.recent is column-oriented — every field is its own array and the
    index ties them together. Zipping by the shortest array means a feed that
    omits a column produces no rows at all rather than rows whose form and date
    belong to different filings.
    """
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    items = recent.get("items") or []
    accns = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    if not forms or not dates:
        return []
    n = min(len(forms), len(dates))
    out = []
    for i in range(n):
        out.append({
            "form": str(forms[i] or "").strip(),
            "filed": str(dates[i] or "")[:10],
            "items": str(items[i] or "") if i < len(items) else "",
            "accn": str(accns[i] or "") if i < len(accns) else "",
            "doc": str(docs[i] or "") if i < len(docs) else "",
        })
    return out


def _filing_url(cik: str, accn: str, doc: str) -> str:
    if not (cik and accn):
        return ""
    bare = accn.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{str(cik).lstrip('0')}/{bare}"
    return f"{base}/{doc}" if doc else f"{base}/{accn}-index.htm"


def extract_signals(submissions: dict, *, cik: str = "",
                    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                    today: date | None = None) -> dict:
    """Signals from an SEC submissions payload.

    Returns ``available`` False when the feed carries no item codes at all, so
    the page can say it could not look rather than implying a clean record.
    """
    today = today or date.today()
    floor = today - timedelta(days=lookback_days)
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    rows = _rows(recent)
    if not rows:
        return {"available": False, "reason": "no filing index returned",
                "signals": [], "lookback_days": lookback_days}

    saw_items_column = bool(recent.get("items"))
    signals = []
    for row in rows:
        filed = _as_date(row["filed"])
        if not filed or filed < floor or filed > today:
            continue
        found: list[tuple[str, str, str, str]] = []
        if row["form"] in FORM_SIGNALS:
            label, why, sev = FORM_SIGNALS[row["form"]]
            found.append(("", label, why, sev))
        for code in [c.strip() for c in row["items"].split(",") if c.strip()]:
            if code in ITEM_SIGNALS:
                label, why, sev = ITEM_SIGNALS[code]
                found.append((code, label, why, sev))
        for code, label, why, sev in found:
            signals.append({
                "date": row["filed"], "form": row["form"], "item": code,
                "label": label, "why": why, "severity": sev,
                "url": _filing_url(cik, row["accn"], row["doc"]),
            })

    signals.sort(key=lambda s: (SEVERITY_RANK.get(s["severity"], 9),
                                s["date"]), reverse=False)
    signals.sort(key=lambda s: s["date"], reverse=True)
    signals.sort(key=lambda s: SEVERITY_RANK.get(s["severity"], 9))
    return {
        "available": True if saw_items_column else False,
        "reason": "" if saw_items_column else "filing index carried no item codes",
        "signals": signals,
        "lookback_days": lookback_days,
        "filings_scanned": len(rows),
        "worst": signals[0]["severity"] if signals else None,
    }


# ─── Fetch ────────────────────────────────────────────────────────────────────

_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_CACHE_TTL = 3600
_CACHE: dict[str, tuple[float, dict]] = {}


def clear_cache() -> None:
    _CACHE.clear()


def fetch_filing_signals(ticker: str, *, today: date | None = None) -> dict:
    """Signals for a ticker. Never raises — a failure here must not take down
    a page whose main content is fine without it."""
    import copy
    import time as _time

    import fundamentals_engine as fe

    ticker = (ticker or "").upper().strip()
    hit = _CACHE.get(ticker)
    if hit and (_time.time() - hit[0]) < _CACHE_TTL:
        return copy.deepcopy(hit[1])

    try:
        cik, _name = fe._edgar_cik(ticker)
        if not cik:
            return {"available": False, "reason": "ticker not found at SEC",
                    "signals": []}
        resp = fe._req_module.get(_SUBMISSIONS_URL.format(cik=cik), timeout=12,
                                  headers=fe._EDGAR_HEADERS)
        if resp.status_code != 200:
            return {"available": False,
                    "reason": f"SEC returned {resp.status_code}", "signals": []}
        result = extract_signals(resp.json(), cik=cik, today=today)
    except Exception as exc:
        return {"available": False,
                "reason": f"lookup failed ({type(exc).__name__})", "signals": []}

    if len(_CACHE) > 32:
        _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k][0]), None)
    _CACHE[ticker] = (_time.time(), copy.deepcopy(result))
    return result
