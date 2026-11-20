"""Portfolio Intelligence 2.0: conservative exposure and split-capital modeling.

A cached broad sector is NOT a verified industry. ETF look-through requires
constituent weights from a trusted dated source; absent data is unclassified.
"""
from __future__ import annotations
from datetime import date, datetime, timezone
from math import isfinite

ETF_TICKERS = {"VOO", "VTI", "SCHD", "GLD", "GLDM", "SGOV", "SPCK"}
KNOWN_INDUSTRY = {"KLAC": ("Semiconductor equipment", "Semiconductors"),
                  "NVDA": ("Semiconductors", "AI infrastructure"),
                  "GOOG": ("Internet services", "Digital platforms"),
                  "GOOGL": ("Internet services", "Digital platforms"),
                  "V": ("Payment networks", "Financial services"),
                  "TSM": ("Semiconductor foundry", "Semiconductors")}
THEMES = {"KLAC": ["Semiconductors", "AI infrastructure"],
          "NVDA": ["Semiconductors", "AI infrastructure"],
          "TSM": ["Semiconductors", "AI infrastructure"],
          "GOOG": ["AI infrastructure", "Digital platforms"],
          "GOOGL": ["AI infrastructure", "Digital platforms"],
          "V": ["Financial services"],
          "SKHY": ["Semiconductors", "AI infrastructure"]}


def age_days(raw):
    try:
        stamp = date.fromisoformat(str(raw or "")[:10])
        age = (datetime.now(timezone.utc).date() - stamp).days
        return age if age >= 0 else None
    except (ValueError, TypeError):
        return None


def classify(ticker, cached_sector=None):
    ticker = str(ticker or "").upper()
    if ticker == "SPCX":
        return {"sector": None, "industry": None, "themes": [], "verified": False,
                "reason": "Ambiguous symbol: historical SPCX ETF changed to SPCK; verify broker security identity",
                "asset_type": "unresolved"}
    if ticker in ETF_TICKERS:
        return {"sector": None, "industry": None, "themes": [],
                "verified": False, "reason": "ETF holdings look-through unavailable",
                "asset_type": "fund"}
    if ticker in KNOWN_INDUSTRY:
        industry, theme = KNOWN_INDUSTRY[ticker]
        return {"sector": cached_sector if cached_sector and cached_sector != "Unclassified" else None,
                "industry": industry, "themes": THEMES.get(ticker, [theme]),
                "verified": False, "reason": "Reference classification requires provider verification",
                "asset_type": "equity"}
    return {"sector": cached_sector if cached_sector and cached_sector != "Unclassified" else None,
            "industry": None, "themes": [], "verified": False,
            "reason": "No verified industry classification", "asset_type": "unknown"}


def exposure(state, classifications):
    holdings = state.get("holdings") or {}
    total = float(state.get("total_value") or 0)
    sectors, industries, themes = {}, {}, {}
    unknown = []
    rows = []
    for ticker, raw in sorted(holdings.items()):
        value = float(raw)
        c = classifications.get(ticker) or classify(ticker)
        pct = round(100 * value / total, 2) if total else None
        rows.append({"ticker": ticker, "value": value, "weight_pct": pct, **c})
        if not c.get("verified"):
            unknown.append(ticker)
            continue
        for mapping, key in ((sectors, c.get("sector")), (industries, c.get("industry"))):
            if key:
                mapping[key] = mapping.get(key, 0) + value
        for theme in c.get("themes") or []:
            themes[theme] = themes.get(theme, 0) + value
    def percentages(d):
        return {k: round(v * 100 / total, 2) for k, v in sorted(d.items())} if total else {}
    return {"holdings": rows, "sectors_pct": percentages(sectors),
            "industries_pct": percentages(industries), "themes_pct": percentages(themes),
            "unclassified": unknown, "coverage_pct": round(100 * (sum(v for t,v in holdings.items()
                   if t not in unknown)) / total, 1) if total else 0,
            "notes": ["Themes overlap; thematic percentages must not be summed.",
                      "Unverified ETFs are excluded from sector and thematic totals, not assigned to Technology."]}


def split_options(amount, rows):
    """Illustrative, no-order plans. Only fully eligible candidates can be deployed."""
    amount = float(amount)
    eligible = [r for r in rows if r.get("eligible") and r.get("max_allocation") is not None
                and r["max_allocation"] > 0]
    eligible.sort(key=lambda r: (-(r.get("score") or 0), r["ticker"]))
    def plan(name, allocations):
        deployed = round(sum(allocations.values()), 2)
        return {"name": name, "allocations": allocations, "deployed": deployed,
                "cash_remaining": round(amount - deployed, 2)}
    plans = [plan("Hold cash", {})]
    if eligible:
        first = eligible[0]
        plans.append(plan("Single candidate", {first["ticker"]: round(min(amount, first["max_allocation"]), 2)}))
        remaining = amount
        split = {}
        for r in eligible:
            use = round(min(remaining, r["max_allocation"]), 2)
            if use > 0:
                split[r["ticker"]] = use
                remaining = round(remaining - use, 2)
            if remaining <= 0:
                break
        plans.append(plan("Split across eligible candidates", split))
    return plans


def quality_monitor(candidates, state):
    checks = []
    if state.get("unknown_market_values"):
        checks.append({"status": "blocked", "category": "Brokerage",
                       "detail": "Missing market values for " + ", ".join(state["unknown_market_values"])})
    for c in candidates:
        t = c["ticker"]
        age = age_days(c.get("valuation_as_of"))
        checks.append({"status": "current" if age is not None and age <= 3 else "stale_or_missing",
                       "category": "Market quote", "ticker": t,
                       "detail": "Quote age " + str(age) + " days" if age is not None else "Missing quote date"})
        if not c.get("valuation_verified"):
            checks.append({"status": "unverified", "category": "Valuation", "ticker": t,
                           "detail": "Fair-value evidence has not been verified"})
        if not c.get("sector_verified"):
            checks.append({"status": "unverified", "category": "Classification", "ticker": t,
                           "detail": "Sector/ETF holdings not verified"})
    return checks
