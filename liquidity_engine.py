"""
liquidity_engine.py — Fed Liquidity Monitor & Macro Research Engine

Tracks Fed activity and liquidity conditions using official FRED data:

  get_liquidity_status()   → cached liquidity score + Risk-On/Neutral/Risk-Off
  get_yield_curve()        → 2Y/10Y spread, inversion status, trend
  get_liquidity_alerts()   → specific event alerts (reverse repo drop, etc.)
  get_money_flow()         → sector ETF flow rankings
  get_full_liquidity_ctx() → complete context dict for templates

FRED series tracked:
  WALCL     — Fed balance sheet total assets (weekly)
  M2SL      — M2 money supply (monthly)
  WRESBAL   — Bank reserves (weekly)
  RRPONTSYD — Overnight reverse repo (daily)
  WTREGEN   — Treasury General Account (weekly)
  DFF       — Fed Funds Rate (daily)
  SOFR      — SOFR (daily)
  DGS10     — 10Y Treasury yield (daily)
  DGS2      — 2Y Treasury yield (daily)
  T10Y2Y    — 10Y-2Y spread (daily, yield curve)

API key: optional env var FRED_API_KEY (higher rate limits, still works without).
Data cached 6 hours — FRED series update weekly/monthly; daily series fine with 6h cache.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache_lock  = threading.Lock()
_cache: dict = {}                  # series_id → {values, dates, fetched_at}
_CACHE_TTL_H = 6                   # hours

_ctx_lock       = threading.Lock()
_liq_ctx:  dict = {}
_liq_ctx_at: Optional[datetime] = None
_CTX_TTL_H = 6

_bg_lock   = threading.Lock()
_bg_active = False

# ── FRED series manifest ───────────────────────────────────────────────────────
_FRED_SERIES = {
    "WALCL":     {"name": "Fed Balance Sheet",      "unit": "$B",   "freq": "weekly"},
    "M2SL":      {"name": "M2 Money Supply",         "unit": "$B",   "freq": "monthly"},
    "WRESBAL":   {"name": "Bank Reserves",           "unit": "$B",   "freq": "weekly"},
    "RRPONTSYD": {"name": "Reverse Repo",            "unit": "$B",   "freq": "daily"},
    "WTREGEN":   {"name": "Treasury Gen Account",    "unit": "$B",   "freq": "weekly"},
    "DFF":       {"name": "Fed Funds Rate",          "unit": "%",    "freq": "daily"},
    "SOFR":      {"name": "SOFR",                    "unit": "%",    "freq": "daily"},
    "DGS10":     {"name": "10Y Treasury Yield",      "unit": "%",    "freq": "daily"},
    "DGS2":      {"name": "2Y Treasury Yield",       "unit": "%",    "freq": "daily"},
    "T10Y2Y":    {"name": "10Y-2Y Yield Spread",     "unit": "%",    "freq": "daily"},
}

# Sector ETF universe for money flow
_SECTOR_ETFS = {
    "SPY":  {"name": "S&P 500",          "theme": "broad"},
    "QQQ":  {"name": "Nasdaq 100",       "theme": "tech"},
    "IWM":  {"name": "Small Caps",       "theme": "small_cap"},
    "SMH":  {"name": "Semiconductors",   "theme": "ai_infra"},
    "XLK":  {"name": "Technology",       "theme": "tech"},
    "XLE":  {"name": "Energy",           "theme": "energy"},
    "XLF":  {"name": "Financials",       "theme": "financials"},
    "XLI":  {"name": "Industrials/Def",  "theme": "defense"},
    "XLP":  {"name": "Staples",          "theme": "defensive"},
    "XLU":  {"name": "Utilities",        "theme": "defensive"},
    "XBI":  {"name": "Biotech",          "theme": "biotech"},
    "ARKK": {"name": "ARK Innovation",   "theme": "ai_infra"},
    "GLD":  {"name": "Gold",             "theme": "commodities"},
    "TLT":  {"name": "Long Bonds",       "theme": "bonds"},
}


# ── FRED data fetcher ──────────────────────────────────────────────────────────

def _fred_url(series_id: str, limit: int = 20) -> str:
    api_key = os.environ.get("FRED_API_KEY", "")
    base    = "https://api.stlouisfed.org/fred/series/observations"
    params  = (
        f"series_id={series_id}"
        f"&sort_order=desc"
        f"&limit={limit}"
        f"&file_type=json"
        f"&observation_start=2023-01-01"
    )
    if api_key:
        params += f"&api_key={api_key}"
    return f"{base}?{params}"


def _fetch_fred(series_id: str, limit: int = 20) -> dict | None:
    """
    Fetch a FRED series and return {values: [float], dates: [str]}.
    Returns None on any failure. Gracefully handles missing API key.
    """
    with _cache_lock:
        cached = _cache.get(series_id)
        if cached:
            age = (datetime.now() - cached["fetched_at"]).total_seconds() / 3600
            if age < _CACHE_TTL_H:
                return cached

    try:
        import requests as _req
        url = _fred_url(series_id, limit)
        r = _req.get(url, timeout=10, headers={"Accept": "application/json"})
        if r.status_code == 400:
            logger.debug("FRED %s: 400 — likely needs API key or bad series", series_id)
            return None
        if r.status_code != 200:
            logger.debug("FRED %s: HTTP %s", series_id, r.status_code)
            return None

        data = r.json()
        obs  = data.get("observations", [])
        # Filter out "." (missing values) and sort oldest→newest
        valid = [(o["date"], o["value"]) for o in obs if o.get("value", ".") != "."]
        valid.sort(key=lambda x: x[0])

        if not valid:
            return None

        result = {
            "series_id":  series_id,
            "dates":      [v[0] for v in valid],
            "values":     [float(v[1]) for v in valid],
            "fetched_at": datetime.now(),
            "meta":       _FRED_SERIES.get(series_id, {}),
        }
        with _cache_lock:
            _cache[series_id] = result
        return result

    except Exception as exc:
        logger.debug("_fetch_fred %s: %s", series_id, exc)
        return None


def _last(data: dict | None) -> float | None:
    if data and data.get("values"):
        return data["values"][-1]
    return None


def _prev(data: dict | None, n: int = 1) -> float | None:
    if data and len(data.get("values", [])) > n:
        return data["values"][-(n + 1)]
    return None


def _pct_chg(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or prev == 0:
        return None
    return round((cur - prev) / abs(prev) * 100, 3)


def _abs_chg(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None:
        return None
    return round(cur - prev, 4)


# ── Individual series analysis ─────────────────────────────────────────────────

def _analyze_balance_sheet() -> dict:
    d = _fetch_fred("WALCL")
    cur  = _last(d)
    prev = _prev(d, 1)
    chg  = _abs_chg(cur, prev)
    trend_4w = _pct_chg(_last(d), _prev(d, 4))

    expanding = chg and chg > 0
    result = {
        "value":      round(cur / 1000, 1) if cur else None,   # convert M→B, show in T
        "unit":       "$T",
        "chg_wow":    round(chg / 1000, 1) if chg else None,
        "trend_4w":   trend_4w,
        "expanding":  expanding,
        "signal":     "Expanding — liquidity bullish" if expanding else "Contracting — QT in progress",
        "dates":      (d or {}).get("dates", [])[-8:],
        "values":     [round(v / 1000, 2) for v in (d or {}).get("values", [])[-8:]],
    }
    return result


def _analyze_reverse_repo() -> dict:
    d    = _fetch_fred("RRPONTSYD")
    cur  = _last(d)
    prev = _prev(d, 1)
    prev5= _prev(d, 5)
    chg  = _abs_chg(cur, prev)
    trend= _pct_chg(cur, prev5)

    # Falling reverse repo = liquidity LEAVING the Fed's facility = bullish (more $ in system)
    falling_fast = trend and trend < -5
    result = {
        "value":      round(cur / 1000, 2) if cur else None,   # M→T
        "unit":       "$T",
        "chg_dod":    round(chg / 1000, 3) if chg else None,
        "trend_5d":   trend,
        "falling_fast": falling_fast,
        "signal":     (
            "Draining sharply — liquidity entering markets (bullish)" if falling_fast else
            "Rising — cash parked at Fed (less liquid)" if (chg and chg > 0) else
            "Falling — cash moving to markets"
        ),
        "dates":      (d or {}).get("dates", [])[-10:],
        "values":     [round(v / 1000, 3) for v in (d or {}).get("values", [])[-10:]],
    }
    return result


def _analyze_m2() -> dict:
    d    = _fetch_fred("M2SL")
    cur  = _last(d)
    prev = _prev(d, 1)
    prev12 = _prev(d, 12)
    chg_mom = _pct_chg(cur, prev)
    chg_yoy = _pct_chg(cur, prev12)

    result = {
        "value":    round(cur / 1000, 1) if cur else None,
        "unit":     "$T",
        "chg_mom":  chg_mom,
        "chg_yoy":  chg_yoy,
        "expanding": chg_yoy and chg_yoy > 0,
        "signal":   (
            f"M2 growing +{chg_yoy:.1f}% YoY — money supply expanding" if (chg_yoy and chg_yoy > 2) else
            f"M2 contracting {chg_yoy:.1f}% YoY — tighter monetary conditions" if (chg_yoy and chg_yoy < 0) else
            "M2 stable — neutral monetary backdrop"
        ),
        "dates":    (d or {}).get("dates", [])[-12:],
        "values":   [round(v / 1000, 2) for v in (d or {}).get("values", [])[-12:]],
    }
    return result


def _analyze_yield_curve() -> dict:
    spread_d = _fetch_fred("T10Y2Y")
    y10_d    = _fetch_fred("DGS10")
    y2_d     = _fetch_fred("DGS2")

    spread  = _last(spread_d)
    y10     = _last(y10_d)
    y2      = _last(y2_d)
    computed = (y10 - y2) if (y10 and y2) else spread

    prev_spread = _prev(spread_d, 5) if spread_d else None
    trend = _abs_chg(spread, prev_spread)

    inverted    = computed is not None and computed < 0
    steepening  = trend is not None and trend > 0.05
    flattening  = trend is not None and trend < -0.05

    if computed is None:
        status = "Unknown"
        signal = "Yield curve data unavailable."
    elif computed < -0.5:
        status = "Deeply Inverted"
        signal = f"Yield curve deeply inverted ({computed:.2f}%) — recession risk elevated, watch growth stocks."
    elif computed < 0:
        status = "Inverted"
        signal = f"Yield curve inverted ({computed:.2f}%) — growth headwind for tech/high-multiple stocks."
    elif computed < 0.3:
        status = "Flat"
        signal = f"Yield curve flat ({computed:.2f}%) — neutral conditions."
    elif steepening:
        status = "Steepening"
        signal = f"Yield curve steepening ({computed:.2f}%) — risk appetite improving."
    else:
        status = "Normal"
        signal = f"Yield curve normal ({computed:.2f}%) — healthy credit environment."

    return {
        "spread":      round(computed, 3) if computed else None,
        "y10":         y10,
        "y2":          y2,
        "inverted":    inverted,
        "steepening":  steepening,
        "flattening":  flattening,
        "status":      status,
        "signal":      signal,
        "dates":       (spread_d or {}).get("dates", [])[-20:],
        "values":      (spread_d or {}).get("values", [])[-20:],
    }


def _analyze_fed_rate() -> dict:
    dff  = _fetch_fred("DFF")
    sofr = _fetch_fred("SOFR")
    rate = _last(dff)
    sofr_val = _last(sofr)

    prev = _prev(dff, 30)
    cutting = rate is not None and prev is not None and rate < prev
    hiking  = rate is not None and prev is not None and rate > prev

    return {
        "fed_funds":  rate,
        "sofr":       sofr_val,
        "cutting":    cutting,
        "hiking":     hiking,
        "stable":     not cutting and not hiking,
        "signal":     (
            f"Fed cutting rates ({rate:.2f}%) — risk-on catalyst for growth stocks." if cutting else
            f"Fed hiking ({rate:.2f}%) — headwind for valuations." if hiking else
            f"Fed on hold ({rate:.2f}%) — market waiting for next move."
        ),
    }


def _analyze_tga() -> dict:
    d   = _fetch_fred("WTREGEN")
    cur = _last(d)
    prev= _prev(d, 4)
    chg = _abs_chg(cur, prev)
    # TGA falling = Treasury spending into economy = bullish liquidity
    draining = chg and chg < -50
    filling  = chg and chg > 50

    return {
        "value":   round(cur / 1000, 2) if cur else None,
        "unit":    "$T",
        "chg_mow": round(chg / 1000, 2) if chg else None,
        "draining": draining,
        "filling":  filling,
        "signal":  (
            "TGA draining — Treasury spending into economy (bullish liquidity)" if draining else
            "TGA filling — Treasury absorbing liquidity (bearish)" if filling else
            "TGA stable"
        ),
    }


# ── Master liquidity score ─────────────────────────────────────────────────────

def _build_liquidity_context() -> dict:
    """Full liquidity context build. Runs in background thread."""
    bs    = _analyze_balance_sheet()
    rrp   = _analyze_reverse_repo()
    m2    = _analyze_m2()
    yc    = _analyze_yield_curve()
    rate  = _analyze_fed_rate()
    tga   = _analyze_tga()
    mflow = _fetch_money_flow()

    # ── Liquidity score 0-100 ─────────────────────────────────────────────────
    score = 50   # start neutral
    alerts = []

    # 1. Fed balance sheet: expanding = +15, contracting = -15
    if bs.get("expanding"):
        score += 15
        alerts.append({"type": "bullish", "msg": f"Fed balance sheet expanding +${bs.get('chg_wow','?')}B WoW."})
    elif bs.get("chg_wow") and bs["chg_wow"] < -10:
        score -= 15
        alerts.append({"type": "bearish", "msg": "Fed balance sheet contracting — QT in progress."})

    # 2. Reverse repo: falling fast = +15 (liquidity leaving Fed, entering markets)
    if rrp.get("falling_fast"):
        score += 15
        alerts.append({"type": "bullish", "msg": "Reverse repo draining rapidly — excess liquidity entering markets."})
    elif rrp.get("trend_5d") and rrp["trend_5d"] > 10:
        score -= 10
        alerts.append({"type": "caution", "msg": "Reverse repo rising — cash being parked at Fed."})

    # 3. M2 growth
    m2_yoy = m2.get("chg_yoy")
    if m2_yoy and m2_yoy > 3:
        score += 10
        alerts.append({"type": "bullish", "msg": f"M2 growing +{m2_yoy:.1f}% YoY — monetary expansion."})
    elif m2_yoy and m2_yoy < -2:
        score -= 10
        alerts.append({"type": "bearish", "msg": f"M2 contracting {m2_yoy:.1f}% YoY — tighter money supply."})

    # 4. Yield curve
    spread = yc.get("spread")
    if spread is not None:
        if spread > 0.5:
            score += 10
        elif spread > 0:
            score += 5
        elif spread < -0.5:
            score -= 15
            alerts.append({"type": "caution", "msg": f"Yield curve deeply inverted ({spread:.2f}%) — recession signal."})
        elif spread < 0:
            score -= 8
            alerts.append({"type": "caution", "msg": f"Yield curve inverted ({spread:.2f}%) — growth headwind."})
        if yc.get("steepening"):
            score += 5
            alerts.append({"type": "bullish", "msg": "Yield curve steepening — risk appetite improving."})

    # 5. Fed rate policy
    if rate.get("cutting"):
        score += 12
        alerts.append({"type": "bullish", "msg": f"Fed cutting rates — tailwind for growth and risk assets."})
    elif rate.get("hiking"):
        score -= 12
        alerts.append({"type": "bearish", "msg": "Fed hiking — headwind for high-multiple stocks."})

    # 6. TGA
    if tga.get("draining"):
        score += 8
        alerts.append({"type": "bullish", "msg": "Treasury draining TGA — fiscal injection into economy."})
    elif tga.get("filling"):
        score -= 8

    score = max(0, min(100, score))

    if score >= 65:
        status = "RISK_ON"
        status_label = "Risk-On — Liquidity Expanding"
        status_color = "green"
        summary = "Multiple liquidity indicators are bullish. Conditions historically favor risk assets."
    elif score >= 35:
        status = "NEUTRAL"
        status_label = "Neutral — Mixed Liquidity"
        status_color = "yellow"
        summary = "Mixed liquidity signals. Market sensitive to Fed communication and data releases."
    else:
        status = "RISK_OFF"
        status_label = "Risk-Off — Liquidity Contracting"
        status_color = "red"
        summary = "Multiple liquidity headwinds. High-multiple and growth stocks most at risk."

    # 10Y yield level alerts
    y10 = yc.get("y10")
    if y10:
        if y10 > 4.8:
            alerts.append({"type": "bearish", "msg": f"10Y yield elevated at {y10:.2f}% — compression risk for tech valuations."})
        elif y10 < 4.0:
            alerts.append({"type": "bullish", "msg": f"10Y yield falling ({y10:.2f}%) — multiple expansion tailwind."})

    return {
        "score":        score,
        "status":       status,
        "status_label": status_label,
        "status_color": status_color,
        "summary":      summary,
        "alerts":       alerts,
        "balance_sheet":bs,
        "reverse_repo": rrp,
        "m2":           m2,
        "yield_curve":  yc,
        "fed_rate":     rate,
        "tga":          tga,
        "money_flow":   mflow,
        "fetched_at":   datetime.now().isoformat(),
        "has_fred_key": bool(os.environ.get("FRED_API_KEY")),
    }


def _empty_liquidity_context() -> dict:
    return {
        "score":        50,
        "status":       "NEUTRAL",
        "status_label": "Neutral — Fetching data",
        "status_color": "yellow",
        "summary":      "Liquidity data loading in background. Check back in 30 seconds.",
        "alerts":       [],
        "balance_sheet":{}, "reverse_repo": {}, "m2": {},
        "yield_curve":  {}, "fed_rate":     {}, "tga": {},
        "money_flow":   [],
        "fetched_at":   None,
        "has_fred_key": bool(os.environ.get("FRED_API_KEY")),
    }


# ── Background refresh ─────────────────────────────────────────────────────────

def _do_refresh() -> None:
    global _liq_ctx, _liq_ctx_at, _bg_active
    try:
        ctx = _build_liquidity_context()
        with _ctx_lock:
            _liq_ctx    = ctx
            _liq_ctx_at = datetime.now()
        logger.info("liquidity_engine: refreshed  score=%s  status=%s", ctx["score"], ctx["status"])
    except Exception as exc:
        logger.warning("liquidity_engine._build_liquidity_context: %s", exc)
    finally:
        with _bg_lock:
            _bg_active = False


def get_liquidity_status() -> dict:
    """
    Return cached liquidity context. Never blocks the caller.
    Triggers a background refresh if stale/missing.
    """
    global _bg_active
    now = datetime.now()
    with _ctx_lock:
        has_data = bool(_liq_ctx)
        is_fresh = (
            has_data and _liq_ctx_at is not None and
            (now - _liq_ctx_at).total_seconds() < _CTX_TTL_H * 3600
        )
        if is_fresh:
            return dict(_liq_ctx)
        snapshot = dict(_liq_ctx) if has_data else {}

    with _bg_lock:
        if not _bg_active:
            _bg_active = True
            threading.Thread(target=_do_refresh, daemon=True, name="liq-refresh").start()

    return snapshot if snapshot else _empty_liquidity_context()


def refresh_liquidity_bg() -> None:
    """Kick off a background liquidity refresh without blocking the caller."""
    global _bg_active
    with _bg_lock:
        if not _bg_active:
            _bg_active = True
            threading.Thread(target=_do_refresh, daemon=True, name="liq-refresh").start()


# ── Money flow (sector ETF momentum) ─────────────────────────────────────────

def _fetch_money_flow() -> list[dict]:
    """
    Compute money flow scores for sector ETFs using Yahoo Finance chart API.
    Returns list sorted by momentum score (best first).
    """
    try:
        from market_engine import _fetch_prices_chart, _pct_change, _ema
        tickers = list(_SECTOR_ETFS.keys())
        prices  = _fetch_prices_chart(tickers, period="60d")

        results = []
        for sym, meta in _SECTOR_ETFS.items():
            px = prices.get(sym, [])
            if not px or len(px) < 20:
                results.append({
                    "ticker": sym, "name": meta["name"], "theme": meta["theme"],
                    "price": None, "chg_1d": None, "chg_5d": None, "chg_20d": None,
                    "above_ema20": None, "above_ema50": None,
                    "flow_score": 0, "flow_label": "No data", "flow_css": "flow-neutral",
                })
                continue

            chg_1d  = _pct_change(px, 1)
            chg_5d  = _pct_change(px, 5)
            chg_20d = _pct_change(px, 20)
            ema20   = _ema(px, min(20, len(px)))
            ema50   = _ema(px, min(50, len(px)))
            last    = px[-1]
            above20 = bool(ema20 and last > ema20)
            above50 = bool(ema50 and last > ema50)

            # Flow score 0-100 based on momentum indicators
            fs = 50
            if chg_1d:
                fs += min(15, max(-15, chg_1d * 5))
            if chg_5d:
                fs += min(15, max(-15, chg_5d * 2))
            if chg_20d:
                fs += min(10, max(-10, chg_20d * 0.8))
            if above20:
                fs += 5
            if above50:
                fs += 5
            fs = max(0, min(100, round(fs)))

            if fs >= 72:
                label, css = "Strong Inflow",  "flow-strong"
            elif fs >= 58:
                label, css = "Inflow",         "flow-in"
            elif fs >= 42:
                label, css = "Neutral",        "flow-neutral"
            elif fs >= 28:
                label, css = "Outflow",        "flow-out"
            else:
                label, css = "Strong Outflow", "flow-weak"

            results.append({
                "ticker":     sym,
                "name":       meta["name"],
                "theme":      meta["theme"],
                "price":      round(last, 2),
                "chg_1d":     round(chg_1d, 2)  if chg_1d  else None,
                "chg_5d":     round(chg_5d, 2)  if chg_5d  else None,
                "chg_20d":    round(chg_20d, 2) if chg_20d else None,
                "above_ema20":above20,
                "above_ema50":above50,
                "flow_score": fs,
                "flow_label": label,
                "flow_css":   css,
            })

        results.sort(key=lambda x: x["flow_score"], reverse=True)
        return results

    except Exception as exc:
        logger.debug("_fetch_money_flow: %s", exc)
        return []


def get_money_flow() -> list[dict]:
    """Return cached money flow. Uses cached liq context if available."""
    ctx = get_liquidity_status()
    mf  = ctx.get("money_flow", [])
    if not mf:
        mf = _fetch_money_flow()
    return mf


def get_liquidity_alerts() -> list[dict]:
    """Return current liquidity alerts from cached context."""
    ctx = get_liquidity_status()
    return ctx.get("alerts", [])
