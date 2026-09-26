"""Disclosure categories from the SEC submissions index, with source links.

An item code establishes a disclosure category, not the underlying cause or
whether it remains unresolved. Broad categories must not be turned into a
claim of default, executive departure, or misconduct without reading the filing.
Coverage is limited to the returned index and is reported explicitly.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

# 8-K item code -> (label, why it matters, severity)
# https://www.sec.gov/files/form8-k.pdf
ITEM_SIGNALS: dict[str, tuple[str, str, str]] = {
    "4.02": ("Non-reliance on financial statements or audit report",
             "This item covers non-reliance on previously issued financial statements or an audit report. Read the filing for the affected periods and any subsequent correction.", "critical"),
    "1.03": ("Bankruptcy or receivership",
             "Filed under Chapter 11 or 7, or entered receivership.", "critical"),
    "3.01": ("Delisting or listing-standard notice",
             "The exchange has told the company it no longer meets listing "
             "standards, or it has moved to delist.", "critical"),
    "4.01": ("Auditor changed",
             "A new accounting firm signs the numbers. Sometimes routine, "
             "sometimes a disagreement the outgoing auditor had to disclose.",
             "high"),
    "5.02": ("Leadership or compensation disclosure",
             "This item covers departures, appointments, elections, and compensation arrangements. The item code does not identify which occurred or establish a governance problem.", "info"),
    "2.06": ("Material impairment disclosure",
             "This item reports a conclusion that a material impairment charge is required. Read the filing for the assets, amount, and timing.", "high"),
    "1.02": ("Material agreement terminated",
             "A contract the company had called material has ended.", "medium"),
    "2.04": ("Financial obligation trigger disclosure",
             "This item covers events that accelerate or increase a financial obligation. It can include a repayment or refinancing; the code alone does not establish a default or covenant breach.", "review"),
}

# Forms that are themselves the signal.
FORM_SIGNALS: dict[str, tuple[str, str, str]] = {
    "NT 10-K": ("Annual report delay notification",
                "The company filed a notice of inability to file on time. Check the notice and subsequent filing; this does not establish that an extension deadline was missed.",
                "high"),
    "NT 10-Q": ("Quarterly report delay notification",
                "The company filed a notice of inability to file on time. Check the notice and subsequent filing for resolution.",
                "medium"),
}

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "review": 3, "info": 4}
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
            "report_date": str((recent.get("reportDate") or [])[i] or "")[:10]
                           if i < len(recent.get("reportDate") or []) else "",
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

    window_rows = [r for r in rows if _as_date(r["filed"]) and floor <= _as_date(r["filed"]) <= today]
    saw_items_column = any(r["items"] for r in window_rows if r["form"] in ("8-K", "8-K/A"))
    # A full parallel items column is meaningful when there are no 8-Ks.
    if not any(r["form"] in ("8-K", "8-K/A") for r in window_rows):
        saw_items_column = len(recent.get("items") or []) >= len(recent.get("form") or [])
    signals = []
    for row in rows:
        filed = _as_date(row["filed"])
        if not filed or filed < floor or filed > today:
            continue
        found: list[tuple[str, str, str, str]] = []
        if row["form"] in FORM_SIGNALS:
            label, why, sev = FORM_SIGNALS[row["form"]]
            found.append(("", label, why, sev))
        codes = row["items"].split(",") if row["form"] in ("8-K", "8-K/A") else []
        for code in [c.strip() for c in codes if c.strip()]:
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
    financials = sorted([r for r in window_rows if r["form"] in ("10-K", "10-Q", "20-F", "40-F")],
                        key=lambda r: (r["report_date"], r["filed"]), reverse=True)
    latest = financials[0] if financials else None
    oldest = min((_as_date(r["filed"]) for r in rows if _as_date(r["filed"])), default=today)
    recent_filings = sorted([r for r in window_rows if r["form"] in ("10-K", "10-Q", "20-F", "40-F", "8-K", "6-K")],
                            key=lambda r: r["filed"], reverse=True)[:8]
    return {
        "available": True if saw_items_column else False,
        "reason": "" if saw_items_column else "filing index carried no item codes",
        "signals": signals,
        "lookback_days": lookback_days,
        "filings_scanned": len(window_rows),
        "coverage_start": max(floor, oldest).isoformat(),
        "coverage_end": today.isoformat(),
        "coverage_complete": oldest <= floor,
        "latest_financial_report": ({"form": latest["form"], "period_end": latest["report_date"],
                                     "filed": latest["filed"], "url": _filing_url(cik, latest["accn"], latest["doc"])} if latest else None),
        "recent_filings": [{"form": r["form"], "filed": r["filed"], "period_end": r["report_date"],
                            "url": _filing_url(cik, r["accn"], r["doc"])} for r in recent_filings],
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
