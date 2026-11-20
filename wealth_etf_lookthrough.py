"""Dated, source-attributed ETF look-through.

Data is supplied by a configured provider export in TRADESTAAR_ETF_HOLDINGS_JSON.
No live ETF feed is implied. Reject stale, malformed and over-allocated data.
Example payload: {"SPCX":{"as_of":"2026-11-18","source":"issuer export",
"constituents":[{"ticker":"ABC","weight_pct":12.5,"sector":"Industrials"}]}}
"""
import json
import os
from datetime import date
from wealth_portfolio_intelligence import age_days


def validated_snapshot(ticker, raw, max_age_days=35):
    if not isinstance(raw, dict):
        return None
    source = str(raw.get("source") or "").strip()[:180]
    as_of = str(raw.get("as_of") or "")[:10]
    age = age_days(as_of)
    if not source or age is None or age > max_age_days:
        return None
    holdings = raw.get("constituents")
    if not isinstance(holdings, list) or not holdings or len(holdings) > 1000:
        return None
    result = []
    seen = set()
    total = 0.0
    for item in holdings:
        if not isinstance(item, dict):
            return None
        symbol = str(item.get("ticker") or "").strip().upper()
        sector = str(item.get("sector") or "").strip()
        try:
            weight = float(item.get("weight_pct"))
        except (TypeError, ValueError):
            return None
        import math
        if not symbol or len(symbol) > 18 or symbol in seen or not math.isfinite(weight) or weight <= 0 or weight > 100:
            return None
        seen.add(symbol)
        total += weight
        if total > 100.01:
            return None
        result.append({"ticker": symbol, "sector": sector or None, "weight_pct": weight})
    return {"ticker": ticker, "as_of": as_of, "source": source,
            "age_days": age, "constituents": result,
            "coverage_pct": round(total, 2), "complete": total >= 99.0}


def load_snapshots(tickers):
    raw = os.environ.get("TRADESTAAR_ETF_HOLDINGS_JSON", "")
    if not raw or len(raw) > 2000000:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    result = {}
    for ticker in tickers:
        snapshot = validated_snapshot(ticker, data.get(ticker))
        if snapshot:
            result[ticker] = snapshot
    return result


def lookthrough(state, snapshots):
    """Percentages use the WHOLE portfolio denominator, not the ETF alone."""
    total = float(state.get("total_value") or 0)
    sectors = {}
    details = []
    covered_value = 0.0
    for ticker, market_value in (state.get("holdings") or {}).items():
        snap = snapshots.get(ticker)
        if not snap:
            continue
        covered_value += market_value * snap["coverage_pct"] / 100
        for h in snap["constituents"]:
            if h["sector"]:
                sectors[h["sector"]] = sectors.get(h["sector"], 0) + market_value * h["weight_pct"] / 100
        details.append({"ticker": ticker, "source": snap["source"], "as_of": snap["as_of"],
                        "age_days": snap["age_days"], "coverage_pct": snap["coverage_pct"],
                        "constituents": snap["constituents"][:25]})
    return {"sector_exposure_pct": {k: round(v / total * 100, 2) for k,v in sorted(sectors.items())} if total else {},
            "portfolio_lookthrough_coverage_pct": round(covered_value / total * 100, 2) if total else 0,
            "funds": details, "status": "source-attributed snapshot, not a live issuer feed"}
