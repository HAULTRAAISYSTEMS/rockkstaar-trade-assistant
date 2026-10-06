"""Tradestaar Thesis view: bear/base/bull targets, est. upside, factor flags.

The one screen that answers "should I own this, and what's the upside" —
built from the same EDGAR fundamentals and 0-40 scorecard as Company
Research, repackaged for an investing decision instead of a trading one.

The valuation is a deliberately simple scenario model: forward earnings
(or free cash flow, or sales when there are no profits yet) capitalized at
scenario multiples. Every assumption is shown on screen. It is a starting
point for judgment, not a prediction — verify the fundamentals yourself.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Scenario growth adjustments. Multiples scale with the company's own
# historical growth: a 10% grower earns an 18x base multiple, a 30% grower
# 30x, a flat business 12x. Bear assumes the growth story breaks (slower
# growth, compressed multiple); bull assumes it accelerates.
_SCENARIO_G_ADJ = {"bear": -0.05, "base": 0.00, "bull": 0.05}
_SCENARIO_MULT = {"bear": 0.60, "base": 1.00, "bull": 1.35}

_GROWTH_MIN, _GROWTH_MAX = -0.10, 0.30


def _base_multiple(growth: float) -> float:
    """Fair multiple for a business growing at `growth` (PEG-style)."""
    return max(10.0, min(32.0, 12.0 + max(growth, _GROWTH_MIN) * 60.0))


def _base_ps(growth: float) -> float:
    """Base price/sales for the no-profits fallback, growth-scaled."""
    return max(1.0, min(6.0, 2.0 + max(growth, _GROWTH_MIN) * 10.0))


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    v = abs(value)
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if v >= div:
            return f"{sign}${v / div:,.1f}{unit}"
    return f"{sign}${v:,.0f}"


def _fmt_price(value: float | None) -> str:
    return f"${value:,.2f}" if value is not None else "—"


def _median_positive(values: list) -> float | None:
    vals = sorted(v for v in values if isinstance(v, (int, float)) and v > 0)
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def _history_series(data: dict) -> list[dict]:
    """Annual revenue / net income / FCF, newest first, as raw numbers."""
    out = []
    for row in data.get("history") or []:
        if not isinstance(row, dict):
            continue
        out.append({
            "revenue": row.get("revenue_num"),
            "net_income": row.get("net_income_num"),
            "fcf": row.get("fcf_num"),
        })
    return out


def _revenue_cagr(history: list[dict]) -> float:
    revs = [h["revenue"] for h in reversed(history)
            if isinstance(h["revenue"], (int, float)) and h["revenue"] > 0]
    if len(revs) < 2:
        return 0.0
    years = len(revs) - 1
    try:
        g = (revs[-1] / revs[0]) ** (1 / years) - 1
    except (ZeroDivisionError, ValueError):
        return 0.0
    return max(_GROWTH_MIN, min(_GROWTH_MAX, g))


def _earnings_power(history: list[dict], growth: float) -> tuple[float | None, str]:
    """Normalized earnings power from annual history.

    Growing businesses are valued on their current run-rate (most recent
    profitable year); declining ones on the cycle median, so a peak year
    doesn't flatter a fading business. Falls back to free cash flow, then
    to sales for companies with no profits yet.
    """
    positives = [h["net_income"] for h in history
                 if isinstance(h["net_income"], (int, float)) and h["net_income"] > 0]
    if positives:
        power = positives[0] if growth >= 0 else _median_positive(positives)
        return power, "net income"
    fcf_pos = [h["fcf"] for h in history
               if isinstance(h["fcf"], (int, float)) and h["fcf"] > 0]
    if fcf_pos:
        power = fcf_pos[0] if growth >= 0 else _median_positive(fcf_pos)
        return power, "free cash flow"
    revs = [h["revenue"] for h in history
            if isinstance(h["revenue"], (int, float)) and h["revenue"] > 0]
    if revs:
        return revs[0], "sales (no profits yet)"
    return None, ""


def _shares_outstanding(ticker: str, price: float | None) -> float | None:
    """Share count for per-share targets. Never fatal — the model degrades."""
    try:
        from finnhub_ttm import fetch_finnhub_metrics
        metrics = fetch_finnhub_metrics(ticker) or {}
        raw = metrics.get("_raw_metric") or {}
        shares = raw.get("shareOutstanding")
        if isinstance(shares, (int, float)) and shares > 0:
            return float(shares)
        cap_m = raw.get("marketCapitalization")  # Finnhub: USD millions
        if (isinstance(cap_m, (int, float)) and cap_m > 0
                and isinstance(price, (int, float)) and price > 0):
            return float(cap_m) * 1e6 / price
    except Exception as exc:
        logger.debug("thesis share count unavailable for %s: %s", ticker, exc)
    return None


def build_targets(data: dict, ticker: str) -> dict:
    """Bear/base/bull price targets from the fundamentals history.

    Returns {"available": bool, ...} — always safe to render.
    """
    history = _history_series(data)
    valuation = data.get("valuation") or {}
    price = valuation.get("price")
    price = float(price) if isinstance(price, (int, float)) and price > 0 else None

    power, basis = _earnings_power(history, _revenue_cagr(history))
    shares = _shares_outstanding(ticker, price) if power else None

    if power is None or not shares:
        note = ("Not enough history to model targets." if power is None
                else "Share count unavailable — targets need per-share math.")
        return {"available": False, "note": note, "price": price}

    growth = _revenue_cagr(history)
    sales_based = basis.startswith("sales")
    base_mult = _base_ps(growth) if sales_based else _base_multiple(growth)
    targets, scenario_rows = {}, []
    for key, label in (("bear", "Bear"), ("base", "Base"), ("bull", "Bull")):
        adj = _SCENARIO_G_ADJ[key]
        g = min(growth, 0.0) + adj if key == "bear" else growth + adj
        multiple = base_mult * _SCENARIO_MULT[key]
        target = power * (1 + g) * multiple / shares
        multiple_label = f"{multiple:.1f}x" + (" sales" if sales_based else "")
        targets[key] = target
        scenario_rows.append({
            "key": key, "label": label,
            "growth": g, "growth_pct": f"{g * 100:+.1f}%",
            "multiple": multiple_label, "target": target,
            "target_fmt": _fmt_price(target),
            "upside": (target / price - 1) if price else None,
            "upside_pct": (f"{(target / price - 1) * 100:+.1f}%" if price else "—"),
        })

    base = targets["base"]
    # Range-bar geometry: pad the bear..bull span so markers never sit on the edge.
    lo = min(targets["bear"], price or targets["bear"]) * 0.94
    hi = max(targets["bull"], price or targets["bull"]) * 1.06
    span = hi - lo if hi > lo else 1.0
    bar = {
        "bear_pct": round((targets["bear"] - lo) / span * 100, 1),
        "base_pct": round((base - lo) / span * 100, 1),
        "bull_pct": round((targets["bull"] - lo) / span * 100, 1),
        "price_pct": round((price - lo) / span * 100, 1) if price else None,
    }
    return {
        "available": True,
        "price": price,
        "price_fmt": _fmt_price(price),
        "bear": targets["bear"], "base": base, "bull": targets["bull"],
        "bear_fmt": _fmt_price(targets["bear"]),
        "base_fmt": _fmt_price(base),
        "bull_fmt": _fmt_price(targets["bull"]),
        "bar": bar,
        "upside": (base / price - 1) if price else None,
        "upside_pct": (f"{(base / price - 1) * 100:+.1f}%" if price else "—"),
        "upside_tone": ("pos" if price and base >= price else "neg") if price else "neutral",
        "basis": basis,
        "power": power,
        "power_fmt": _fmt_money(power),
        "growth": growth,
        "growth_pct": f"{growth * 100:+.1f}%",
        "sales_based": sales_based,
        "base_multiple": base_mult,
        "scenarios": scenario_rows,
        "note": (f"Forward {basis} capitalized at growth-scaled multiples "
                 f"(base {base_mult:.1f}x). Simple by design — check the "
                 "assumptions, not just the numbers."),
    }


def build_flags(data: dict) -> list[dict]:
    """Scorecard metrics as green/red/gray factor flags, grouped by section."""
    groups = []
    for section in data.get("sections") or []:
        rows = []
        for m in section.get("rows") or []:
            passed = m.get("passed")
            status = "pass" if passed is True else ("fail" if passed is False else "na")
            rows.append({
                "key": m.get("key"), "label": m.get("label"),
                "value": m.get("value") or "—", "status": status,
            })
        if rows:
            groups.append({"name": section.get("name") or "Factors", "rows": rows})
    return groups


def build_thesis(data: dict, ticker: str) -> dict:
    """Assemble everything the Thesis view needs. Never raises."""
    ticker = (ticker or "").upper().strip()
    try:
        valuation = data.get("valuation") or {}
        price = valuation.get("price")
        price = float(price) if isinstance(price, (int, float)) and price > 0 else None
        return {
            "ticker": ticker,
            "company_name": data.get("company_name") or ticker,
            "sector": data.get("sector") or "",
            "industry": data.get("industry") or "",
            "price": price,
            "price_fmt": _fmt_price(price),
            "change_pct": valuation.get("change_pct"),
            "score": data.get("normalized_score"),
            "verdict": data.get("verdict") or "",
            "verdict_class": data.get("verdict_class") or "",
            "targets": build_targets(data, ticker),
            "flags": build_flags(data),
            "red_flags": data.get("red_flags") or [],
            "error": data.get("error"),
        }
    except Exception as exc:
        logger.exception("thesis build failed for %s", ticker)
        return {"ticker": ticker, "company_name": ticker, "price": None,
                "price_fmt": "—", "score": None, "verdict": "", "verdict_class": "",
                "targets": {"available": False, "note": str(exc)},
                "flags": [], "red_flags": [], "error": str(exc)}
