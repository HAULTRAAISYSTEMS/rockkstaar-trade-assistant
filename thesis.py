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


def _fcf_power(history: list[dict], growth: float) -> float | None:
    fcf_pos = [h["fcf"] for h in history
               if isinstance(h["fcf"], (int, float)) and h["fcf"] > 0]
    if not fcf_pos:
        return None
    return fcf_pos[0] if growth >= 0 else _median_positive(fcf_pos)


# Free cash flow this far above reported profit means non-cash charges
# (acquisition amortization, stock pay, write-downs) are holding profit down;
# cash earned is the fairer yardstick then.
_FCF_OVER_NI_SWITCH = 1.3


def _choose_power(history: list[dict], growth: float, basis: str,
                  ttm: dict | None = None) -> tuple[float | None, str, str]:
    """(power, basis label, basis key) for the requested yardstick.

    basis: "earnings" (reported net income), "fcf" (free cash flow) or
    "auto" — earnings unless cash earned runs well above reported profit.
    For a growing business the trailing twelve months replace the last
    fiscal year (fresher by up to three quarters); a shrinking one keeps
    the cycle median, so one good stretch doesn't flatter it.
    """
    ni_power, ni_basis = _earnings_power(history, growth)
    fcf_power = _fcf_power(history, growth)
    ttm = ttm or {}
    if growth >= 0:
        if ni_basis == "net income" and ttm.get("net_income"):
            ni_power = ttm["net_income"]
        elif not ni_basis.startswith("net") and ttm.get("net_income"):
            ni_power, ni_basis = ttm["net_income"], "net income"
        if ttm.get("fcf"):
            fcf_power = ttm["fcf"]
    if basis == "fcf" and fcf_power:
        return fcf_power, "free cash flow", "fcf"
    if basis == "earnings" and ni_basis == "net income":
        return ni_power, ni_basis, "earnings"
    if basis == "auto" and ni_basis == "net income" and fcf_power and fcf_power > ni_power * _FCF_OVER_NI_SWITCH:
        return fcf_power, "free cash flow", "fcf"
    key = "fcf" if ni_basis == "free cash flow" else ("sales" if ni_basis.startswith("sales") else "earnings")
    return ni_power, ni_basis, key


def _ttm_values(ticker: str) -> dict:
    """Trailing-12-month net income and free cash flow (USD) from Finnhub.

    Annual filings can be up to a year old; the last four quarters are not.
    Net income comes from market cap / trailing P/E (both Finnhub, so they
    describe the same moment); free cash flow from freeCashFlowTTM, or market
    cap / trailing P/FCF. {} when unavailable — the model then uses the
    latest fiscal year.
    """
    try:
        from finnhub_ttm import fetch_finnhub_metrics
        raw = (fetch_finnhub_metrics(ticker) or {}).get("_raw_metric") or {}
    except Exception as exc:
        logger.debug("thesis TTM unavailable for %s: %s", ticker, exc)
        return {}

    def pos(v):
        return float(v) if isinstance(v, (int, float)) and v > 0 else None
    cap_m = pos(raw.get("marketCapitalization"))
    pe = pos(raw.get("peTTM")) or pos(raw.get("peExclExtraTTM"))
    pfcf = pos(raw.get("pfcfShareTTM"))
    out = {}
    if cap_m and pe:
        out["net_income"] = cap_m * 1e6 / pe
    fcf_m = pos(raw.get("freeCashFlowTTM"))
    if fcf_m:
        out["fcf"] = fcf_m * 1e6
    elif cap_m and pfcf:
        out["fcf"] = cap_m * 1e6 / pfcf
    return out


def _shares_outstanding(ticker: str, price: float | None) -> float | None:
    """Share count for per-share targets. Never fatal — the model degrades."""
    try:
        from finnhub_ttm import fetch_finnhub_metrics
        metrics = fetch_finnhub_metrics(ticker) or {}
        raw = metrics.get("_raw_metric") or {}
        shares = raw.get("shareOutstanding")
        if isinstance(shares, (int, float)) and shares > 0:
            return float(shares) * 1e6  # Finnhub reports in millions
        cap_m = raw.get("marketCapitalization")  # Finnhub: USD millions
        if (isinstance(cap_m, (int, float)) and cap_m > 0
                and isinstance(price, (int, float)) and price > 0):
            return float(cap_m) * 1e6 / price
    except Exception as exc:
        logger.debug("thesis share count unavailable for %s: %s", ticker, exc)
    return None


def _price_read(price: float | None, targets: dict) -> dict | None:
    """One plain-English line: where today's price sits among the scenarios."""
    if not price:
        return None
    bear, base, bull = targets["bear"], targets["base"], targets["bull"]
    if price > bull:
        return {"tone": "neg", "text": (
            f"Today's price is {price / bull - 1:.0%} above even the bull case. The market "
            "is paying for more growth than this model assumes — expensive on these numbers.")}
    if price > base:
        return {"tone": "warn", "text": (
            "Today's price is between the base and bull cases: fully valued. "
            "You are paying for things going better than the base case.")}
    if price >= bear:
        return {"tone": "pos", "text": (
            f"Today's price is {1 - price / base:.0%} below the base case: a discount "
            "if the base-case growth holds.")}
    return {"tone": "pos", "text": (
        "Today's price is below even the bear case. Either a deep discount or "
        "the business is breaking — check the red flags below.")}


def build_targets(data: dict, ticker: str, basis: str = "auto") -> dict:
    """Bear/base/bull price targets from the fundamentals history.

    Returns {"available": bool, ...} — always safe to render.
    """
    history = _history_series(data)
    valuation = data.get("valuation") or {}
    price = valuation.get("price")
    price = float(price) if isinstance(price, (int, float)) and price > 0 else None

    ttm = _ttm_values(ticker)
    g0 = _revenue_cagr(history)
    power, basis_label, basis_key = _choose_power(history, g0, basis, ttm)
    uses_ttm = g0 >= 0 and ((basis_key == "earnings" and bool(ttm.get("net_income")))
                            or (basis_key == "fcf" and bool(ttm.get("fcf"))))
    basis = basis_label + (", last 12 months" if uses_ttm else "")
    ni_check = _earnings_power(history, g0)
    fcf_check = _fcf_power(history, g0) or (ttm.get("fcf") if g0 >= 0 else None)
    basis_options = [k for k, ok in (("earnings", ni_check[1] == "net income" or bool(g0 >= 0 and ttm.get("net_income"))),
                                     ("fcf", bool(fcf_check))) if ok]
    shares = _shares_outstanding(ticker, price) if power else None

    if power is None or not shares:
        note = ("Not enough history to model targets." if power is None
                else "Share count unavailable — targets need per-share math.")
        return {"available": False, "note": note, "price": price}

    growth = _revenue_cagr(history)
    sales_based = basis_key == "sales"
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
    # Labels sit under their own marks; marks closer than ~18% of the bar
    # take turns on a second row so the text never overlaps.
    labels, last_pct, row = [], None, 0
    for key, label in (("bear", "Bear"), ("base", "Base"), ("bull", "Bull")):
        pct = bar[key + "_pct"]
        row = (1 - row) if last_pct is not None and pct - last_pct < 18 else 0
        labels.append({"key": key, "text": f"{label} {_fmt_price(targets[key])}",
                       "pct": pct, "row": row})
        last_pct = pct
    bar["labels"] = labels
    return {
        "available": True,
        "price": price,
        "price_fmt": _fmt_price(price),
        "read": _price_read(price, targets),
        "bear": targets["bear"], "base": base, "bull": targets["bull"],
        "bear_fmt": _fmt_price(targets["bear"]),
        "base_fmt": _fmt_price(base),
        "bull_fmt": _fmt_price(targets["bull"]),
        "bar": bar,
        "upside": (base / price - 1) if price else None,
        "upside_pct": (f"{(base / price - 1) * 100:+.1f}%" if price else "—"),
        "upside_tone": ("pos" if price and base >= price else "neg") if price else "neutral",
        "basis": basis,
        "basis_key": basis_key,
        "uses_ttm": uses_ttm,
        "basis_options": basis_options,
        "shares": shares,
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


# --- Value zones: "what price do I buy at?" ---------------------------------

# Load the boat starts 25% under the base value: a margin of safety, so the
# position still works if the model is a quarter too optimistic.
MARGIN_OF_SAFETY = 0.25

ZONE_META = {
    "load":       {"label": "Load the boat", "short": "LOAD", "tone": "load",
                   "action": "25%+ under base value. Buy with size."},
    "accumulate": {"label": "Accumulate", "short": "ACCUM", "tone": "accum",
                   "action": "Under base value. Start or add to a position."},
    "fair":       {"label": "Fair", "short": "FAIR", "tone": "fair",
                   "action": "Between base and bull. Hold; add only on strong setups."},
    "pricey":     {"label": "Pricey", "short": "PRICEY", "tone": "pricey",
                   "action": "Above the bull case. Wait for a pullback."},
}


def zone_for(price: float | None, base: float, bull: float) -> str | None:
    if not price or not base or base <= 0:
        return None
    if price <= base * (1 - MARGIN_OF_SAFETY):
        return "load"
    if price <= base:
        return "accumulate"
    if price <= bull:
        return "fair"
    return "pricey"


def build_zones(price: float | None, base: float, bull: float) -> dict:
    """The four zones in dollars, today's zone, and how far to the next buy zone."""
    load_top = base * (1 - MARGIN_OF_SAFETY)
    current = zone_for(price, base, bull)
    rows = [
        dict(ZONE_META["pricey"], key="pricey", lo=bull, hi=None,
             range_fmt=f"Above {_fmt_price(bull)}"),
        dict(ZONE_META["fair"], key="fair", lo=base, hi=bull,
             range_fmt=f"{_fmt_price(base)} – {_fmt_price(bull)}"),
        dict(ZONE_META["accumulate"], key="accumulate", lo=load_top, hi=base,
             range_fmt=f"{_fmt_price(load_top)} – {_fmt_price(base)}"),
        dict(ZONE_META["load"], key="load", lo=None, hi=load_top,
             range_fmt=f"Under {_fmt_price(load_top)}"),
    ]
    for r in rows:
        r["current"] = r["key"] == current
    to_buy = None
    if price and current in ("fair", "pricey"):
        to_buy = {"price": base, "price_fmt": _fmt_price(base),
                  "pct": base / price - 1, "pct_fmt": f"{(base / price - 1) * 100:.0f}%"}
    return {"current": current, "meta": ZONE_META.get(current) if current else None,
            "rows": rows, "load_top": load_top, "accum_top": base, "bull": bull,
            "to_buy_zone": to_buy}


def implied_growth(price: float | None, power: float | None, shares: float | None,
                   sales_based: bool = False) -> float | None:
    """Growth today's price needs under the model's own pricing rule.

    Solves power * (1 + g) * fair_multiple(g) / shares = price. The fair
    multiple rises with growth (capped), so a richer price needs faster
    growth — this turns "it's expensive" into a number you can judge.
    """
    if not price or not power or not shares or power <= 0:
        return None
    mult = _base_ps if sales_based else _base_multiple
    f = lambda g: power * (1 + g) * mult(g) / shares
    lo, hi = -0.95, 20.0
    if f(lo) >= price:
        return lo
    if f(hi) <= price:
        return hi
    for _ in range(80):
        mid = (lo + hi) / 2
        if f(mid) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def buy_plan(base: float) -> list[dict]:
    """A three-step ladder through the buy zones: 25% / 25% / 50%."""
    load_top = base * (1 - MARGIN_OF_SAFETY)
    steps = [("Starter", 0.25, base), ("Add", 0.25, (base + load_top) / 2),
             ("Load the boat", 0.50, load_top)]
    return [{"label": label, "weight": w, "weight_pct": f"{w * 100:.0f}%",
             "price": px, "price_fmt": _fmt_price(px)} for label, w, px in steps]


# --- Valuation against its own past ------------------------------------------

def _ema(values: list[float], n: int) -> list[float]:
    k, out = 2 / (n + 1), []
    for v in values:
        out.append(v if not out else v * k + out[-1] * (1 - k))
    return out


def _rsi(closes: list[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, len(closes)):
        if i > n:
            ag = (ag * (n - 1) + gains[i - 1]) / n
            al = (al * (n - 1) + losses[i - 1]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def multiple_history(data: dict, bars: dict | None, basis_key: str = "earnings") -> dict:
    """Daily P/E (or P/FCF) over the last five years and where today ranks.

    Per-share earnings come from the fiscal year ends in the valuation
    history (price / multiple at each year end); every trading day is then
    priced against the most recent fiscal year in effect.
    """
    out = {"available": False}
    vh = data.get("valuation_history") or {}
    valuation = data.get("valuation") or {}
    key = "pfcf" if basis_key == "fcf" else "pe"
    today_mult = valuation.get(key)
    if not isinstance(today_mult, (int, float)) or today_mult <= 0 or not bars:
        return out
    per_share = []
    for r in vh.get("rows") or []:
        if r.get("is_today") or not r.get("period_end"):
            continue
        m, px = r.get(key), r.get("price")
        if isinstance(m, (int, float)) and m > 0 and isinstance(px, (int, float)) and px > 0:
            per_share.append((str(r["period_end"])[:10], px / m))
    if not per_share:
        return out
    per_share.sort()
    from datetime import datetime, timezone
    ts, closes = bars.get("timestamps") or [], bars.get("closes") or []
    series = []
    for t, c in zip(ts, closes):
        if not c:
            continue
        day = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
        eps = None
        for end, v in per_share:
            if end <= day:
                eps = v
        if eps and eps > 0:
            series.append((t, c / eps))
    if len(series) < 60:
        return out
    vals = sorted(v for _, v in series)
    below = sum(1 for v in vals if v < today_mult)
    pct = below / len(vals)
    median = vals[len(vals) // 2]
    lo, hi = vals[int(len(vals) * 0.1)], vals[int(len(vals) * 0.9)]
    price = valuation.get("price")
    at_median = price * median / today_mult if isinstance(price, (int, float)) and price > 0 else None
    step = max(1, len(series) // 120)
    spark = [round(v, 2) for _, v in series[::step]]
    label = "P/FCF" if key == "pfcf" else "P/E"
    if pct >= 0.8:
        read = f"Pricier than {pct:.0%} of the last {len(series) // 252 or 1} years' trading days."
    elif pct <= 0.2:
        read = f"Cheaper than {1 - pct:.0%} of the last {len(series) // 252 or 1} years' trading days."
    else:
        read = f"Middle of its own range: pricier than {pct:.0%} of past days."
    return {"available": True, "label": label, "today": today_mult,
            "today_fmt": f"{today_mult:.1f}x", "percentile": pct,
            "percentile_fmt": f"{pct * 100:.0f}", "median": median,
            "median_fmt": f"{median:.1f}x", "low_fmt": f"{lo:.1f}x", "high_fmt": f"{hi:.1f}x",
            "price_at_median": at_median, "price_at_median_fmt": _fmt_price(at_median),
            "spark": spark, "spark_min": min(spark), "spark_max": max(spark),
            "years": round(len(series) / 252, 1), "read": read}


# --- Timing: is the daily chart turning up? ---------------------------------

def trend_check(bars: dict | None) -> dict:
    """Four daily-chart checks a buy in a value zone should wait for."""
    closes = [c for c in (bars or {}).get("closes") or [] if c]
    lows = [x for x in (bars or {}).get("lows") or [] if x]
    if len(closes) < 40:
        return {"available": False, "score": 0, "checks": []}
    e9, e20 = _ema(closes, 9), _ema(closes, 20)
    rsi = _rsi(closes)
    last, r_now, r_prev = closes[-1], rsi[-1], rsi[-4]
    recent_low = min(lows[-3:]) if len(lows) >= 23 else None
    prior_low = min(lows[-23:-3]) if len(lows) >= 23 else None
    checks = [
        {"label": "Above the 9-day EMA", "ok": last > e9[-1],
         "detail": f"{_fmt_price(last)} vs {_fmt_price(e9[-1])}"},
        {"label": "9 EMA over 20 EMA", "ok": e9[-1] > e20[-1],
         "detail": f"{_fmt_price(e9[-1])} vs {_fmt_price(e20[-1])}"},
        {"label": "RSI recovering", "ok": bool(r_now and r_prev and r_now > 40 and r_now > r_prev),
         "detail": f"RSI {r_now:.0f}" if r_now else "—"},
        {"label": "No new 20-day low", "ok": bool(recent_low and prior_low and recent_low > prior_low),
         "detail": "Higher low holding" if (recent_low and prior_low and recent_low > prior_low) else "Still making lows"},
    ]
    score = sum(1 for c in checks if c["ok"])
    return {"available": True, "score": score, "checks": checks,
            "confirmed": score >= 3}


def load_signal(zone: str | None, trend: dict) -> dict:
    """Value (zone) and timing (trend) together."""
    confirmed = bool(trend.get("confirmed"))
    if zone == "load":
        return ({"key": "load", "title": "Load the boat", "tone": "load",
                 "text": "Deep value and the daily chart is turning up. Both say buy."} if confirmed else
                {"key": "load-wait", "title": "Load zone — wait for the turn", "tone": "accum",
                 "text": "The price is deep value, but the daily chart is still falling. Scale in or wait for the turn."})
    if zone == "accumulate":
        return ({"key": "buy", "title": "Buy signal", "tone": "accum",
                 "text": "Under base value and the daily trend is confirming. A good spot to start or add."} if confirmed else
                {"key": "accum-wait", "title": "In the buy zone — timing not there yet", "tone": "fair",
                 "text": "The price is right; the chart isn't. Set the alert, wait for the daily trend to turn."})
    if zone == "fair":
        return {"key": "hold", "title": "Fair value — hold", "tone": "fair",
                "text": "Not cheap. Own it if you have it; add only on a strong setup."}
    if zone == "pricey":
        return {"key": "wait", "title": "Too pricey — wait", "tone": "pricey",
                "text": "You'd be paying more than the bull case. Let the price come to you."}
    return {"key": "none", "title": "No signal", "tone": "fair", "text": "Targets unavailable."}


def zone_summary(data: dict, ticker: str, bars: dict | None = None) -> dict:
    """Compact zones for the chart, watchlist and daily scan."""
    t = build_targets(data, ticker)
    if not t.get("available") or not t.get("price"):
        return {"available": False, "ticker": ticker}
    z = build_zones(t["price"], t["base"], t["bull"])
    trend = trend_check(bars) if bars else {"available": False}
    sig = load_signal(z["current"], trend) if bars else None
    return {"available": True, "ticker": ticker, "price": t["price"],
            "zone": z["current"], "zone_label": ZONE_META[z["current"]]["label"],
            "load_top": round(z["load_top"], 2), "accum_top": round(t["base"], 2),
            "bull": round(t["bull"], 2), "bear": round(t["bear"], 2),
            "basis": t["basis"], "signal": sig}


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


def build_thesis(data: dict, ticker: str, basis: str = "auto", bars: dict | None = None) -> dict:
    """Assemble everything the Thesis view needs. Never raises."""
    ticker = (ticker or "").upper().strip()
    try:
        valuation = data.get("valuation") or {}
        price = valuation.get("price")
        price = float(price) if isinstance(price, (int, float)) and price > 0 else None
        targets = build_targets(data, ticker, basis=basis)
        value = {"available": False}
        if targets.get("available") and price:
            zones = build_zones(price, targets["base"], targets["bull"])
            ig = implied_growth(price, targets["power"], targets.get("shares"), targets["sales_based"])
            trend = trend_check(bars)
            value = {
                "available": True,
                "zones": zones,
                "implied_growth": ig,
                "implied_growth_fmt": (f"{ig * 100:+.0f}%" if ig is not None else "—"),
                "history_growth_fmt": targets["growth_pct"],
                "growth_gap": (ig - targets["growth"]) if ig is not None else None,
                "plan": buy_plan(targets["base"]),
                "history": multiple_history(data, bars, targets["basis_key"]),
                "trend": trend,
                "signal": load_signal(zones["current"], trend),
                "model": {"power": targets["power"], "shares": targets.get("shares"),
                          "zone": zones["current"], "accum_top": targets["base"],
                          "load_top": zones["load_top"], "bull": targets["bull"],
                          "growth": targets["growth"], "sales_based": targets["sales_based"],
                          "price": price, "mos": MARGIN_OF_SAFETY},
            }
        ends = [str(r.get("period_end"))[:10] for r in (data.get("history") or [])
                if isinstance(r, dict) and r.get("period_end")]
        last_bar = None
        if bars and bars.get("timestamps"):
            from datetime import datetime as _dt, timezone as _tz
            last_bar = _dt.fromtimestamp(bars["timestamps"][-1], _tz.utc).strftime("%b %-d, %Y")
        sources = {
            "price": valuation.get("price_source") or {"live": False, "source": "Finnhub",
                                                       "note": "From the last data load"},
            "fiscal_year_end": max(ends) if ends else None,
            "uses_ttm": bool(targets.get("uses_ttm")),
            "basis": targets.get("basis") or "",
            "bars_through": last_bar,
            "shares": "Finnhub shares outstanding",
        }
        return {
            "ticker": ticker,
            "company_name": data.get("company_name") or ticker,
            "sources": sources,
            "sector": data.get("sector") or "",
            "industry": data.get("industry") or "",
            "price": price,
            "price_fmt": _fmt_price(price),
            "change_pct": valuation.get("change_pct"),
            "score": data.get("normalized_score"),
            "verdict": data.get("verdict") or "",
            "verdict_class": data.get("verdict_class") or "",
            "targets": targets,
            "value": value,
            "flags": build_flags(data),
            "red_flags": data.get("red_flags") or [],
            "error": data.get("error"),
        }
    except Exception as exc:
        logger.exception("thesis build failed for %s", ticker)
        return {"ticker": ticker, "company_name": ticker, "price": None,
                "price_fmt": "—", "score": None, "verdict": "", "verdict_class": "",
                "targets": {"available": False, "note": str(exc)},
                "value": {"available": False},
                "flags": [], "red_flags": [], "error": str(exc)}
