"""
institutional_engine.py — Institutional-Grade Market Intelligence Platform

15 advanced analytical engines for the Rockkstaar Trade Assistant:

 1. Market Internals Engine        — breadth, ADD proxy, sector participation
 2. Institutional Liquidity Map    — equal highs/lows, stop hunt zones, traps
 3. Volatility Compression Engine  — squeeze, ATR compression, Bollinger bands
 4. Earnings Drift Engine          — post-earnings continuation detection
 5. Failed Breakout / Breakdown    — bear/bull traps, liquidity sweeps
 6. Volume Profile Engine          — HVN, LVN, POC, volume shelves
 7. Advanced Pattern Engine        — scored bull flags, tight flags, cups
 8. Risk Management Intelligence   — position sizing, ATR stops, R:R
 9. Emotional Discipline AI        — chase, FOMO, poor R:R, oversize warnings
10. Replay + Learning System       — setup stats, daily review generation
11. Weighted Probability Engine    — multi-factor probability score 1-100
12. Institutional Continuation     — shallow pullbacks, dip buying structure
13. Smart Watchlist Builder        — AI-ranked setup tiers
14. Macro Correlation Engine       — VIX/DXY/yield/sector condition alerts
15. Adaptive AI Learning          — win-rate tracking by setup + regime

All public functions are non-blocking. Per-stock OHLCV data is fetched in
background threads and cached. Falls back gracefully with empty dicts when
data is unavailable so the request thread is never blocked.
"""

from __future__ import annotations

import logging
import math
import statistics
import threading
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Per-stock OHLCV cache (background-fetched) ───────────────────────────────

_ohlcv_cache: dict[str, dict] = {}   # ticker → {ohlcv, fetched_at}
_ohlcv_lock = threading.Lock()
_OHLCV_TTL_MIN = 60   # refresh every hour

_fetch_active: set[str] = set()
_fetch_lock = threading.Lock()


def _fetch_ohlcv_bg(ticker: str) -> None:
    """Background thread: fetch 60d daily OHLCV for *ticker* and cache it."""
    try:
        from data_fetcher import _fetch_ohlcv_via_chart_api
        data = _fetch_ohlcv_via_chart_api(ticker, interval="1d", range_str="3mo")
        if data and data.get("closes"):
            with _ohlcv_lock:
                _ohlcv_cache[ticker] = {"ohlcv": data, "fetched_at": datetime.now()}
    except Exception as exc:
        logger.debug("institutional_engine ohlcv fetch %s: %s", ticker, exc)
    finally:
        with _fetch_lock:
            _fetch_active.discard(ticker)


def _get_ohlcv(ticker: str) -> dict | None:
    """
    Return cached OHLCV for *ticker*. Triggers a background fetch if stale/missing.
    Never blocks the caller.
    """
    t = (ticker or "").upper().strip()
    now = datetime.now()
    with _ohlcv_lock:
        cached = _ohlcv_cache.get(t)
        if cached:
            age = (now - cached["fetched_at"]).total_seconds()
            if age < _OHLCV_TTL_MIN * 60:
                return cached["ohlcv"]

    with _fetch_lock:
        if t not in _fetch_active:
            _fetch_active.add(t)
            threading.Thread(
                target=_fetch_ohlcv_bg, args=(t,), daemon=True,
                name=f"ohlcv-{t}"
            ).start()
    return cached["ohlcv"] if cached else None


# ─── Math helpers ─────────────────────────────────────────────────────────────

def _safe(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def _ema(prices: list[float], period: int) -> float | None:
    if not prices or len(prices) < period:
        return None
    mult = 2 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * mult + val * (1 - mult)
    return val


def _sma(prices: list[float], period: int) -> float | None:
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    if len(highs) < period + 1:
        return None
    trs = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def _std(prices: list[float], period: int) -> float | None:
    if len(prices) < period:
        return None
    window = prices[-period:]
    mean = sum(window) / period
    variance = sum((p - mean) ** 2 for p in window) / period
    return math.sqrt(variance) if variance > 0 else 0.0


# =============================================================================
# 1. MARKET INTERNALS ENGINE
# =============================================================================

def get_market_internals(mkt_ctx: dict) -> dict:
    """
    Derive market breadth / internals from sector ETF data we already have.

    Returns:
        advancing_sectors  — count of sectors with positive 1d change
        declining_sectors  — count of sectors with negative 1d change
        sectors_above_ema  — count above their 20 EMA
        add_proxy          — advance/decline ratio (-100 to +100)
        breadth_strength   — "Strong" | "Moderate" | "Mixed" | "Weak" | "Bearish"
        participation      — "Broad" | "Narrow" | "Very Narrow"
        breakouts_likely   — True if internals support continuation
        internals_summary  — human-readable summary string
        new_highs_proxy    — sectors making 20d highs (proxy for NH/NL)
    """
    sectors = mkt_ctx.get("sectors", [])
    if not sectors:
        return _empty_internals()

    advancing = sum(1 for s in sectors if s.get("change_1d", 0) > 0)
    declining  = sum(1 for s in sectors if s.get("change_1d", 0) < 0)
    neutral    = len(sectors) - advancing - declining
    above_ema  = sum(1 for s in sectors if s.get("above_ema20", False))
    leading    = sum(1 for s in sectors if s.get("strength") in ("leading", "strong"))
    weak       = sum(1 for s in sectors if s.get("strength") in ("weak", "lagging"))
    total      = len(sectors)

    # ADD proxy: ratio scaled to -100/+100
    add_proxy = round((advancing - declining) / max(total, 1) * 100, 1)

    # New-highs proxy: count sectors within 1% of their 20d high (approximated
    # by above_ema20 + positive 5d change)
    new_highs_proxy = sum(
        1 for s in sectors
        if s.get("above_ema20") and (s.get("change_5d") or 0) > 0
    )

    # Breadth strength classification
    if advancing >= total * 0.75 and above_ema >= total * 0.7:
        breadth = "Strong"
    elif advancing >= total * 0.55 and above_ema >= total * 0.5:
        breadth = "Moderate"
    elif advancing >= total * 0.4:
        breadth = "Mixed"
    elif declining >= total * 0.6:
        breadth = "Bearish"
    else:
        breadth = "Weak"

    # Participation width
    if leading >= 5:
        participation = "Broad"
    elif leading >= 3:
        participation = "Moderate"
    elif leading >= 1:
        participation = "Narrow"
    else:
        participation = "Very Narrow"

    breakouts_likely = (breadth in ("Strong", "Moderate") and
                        participation in ("Broad", "Moderate") and
                        add_proxy > 20)

    # Summary sentence
    regime = mkt_ctx.get("regime", "NEUTRAL")
    qqq_1d = mkt_ctx.get("qqq_1d_pct") or 0
    if breadth == "Strong":
        if abs(qqq_1d) < 0.3:
            summary = "Strong breadth with quiet QQQ — healthy accumulation."
        else:
            summary = f"Strong breadth supports continuation. {leading}/{total} sectors leading."
    elif breadth == "Bearish":
        summary = f"Broad selling pressure — {declining}/{total} sectors declining."
    elif breadth == "Weak" and qqq_1d > 0:
        summary = f"QQQ green but weak breadth — narrow rally. Only {leading} sectors leading."
    elif breadth == "Mixed":
        summary = f"Mixed internals — {advancing} advancing, {declining} declining. Be selective."
    else:
        summary = f"Moderate breadth. {above_ema}/{total} sectors above 20 EMA."

    return {
        "advancing_sectors":  advancing,
        "declining_sectors":  declining,
        "neutral_sectors":    neutral,
        "sectors_above_ema":  above_ema,
        "leading_sectors":    leading,
        "weak_sectors":       weak,
        "total_sectors":      total,
        "add_proxy":          add_proxy,
        "new_highs_proxy":    new_highs_proxy,
        "breadth_strength":   breadth,
        "participation":      participation,
        "breakouts_likely":   breakouts_likely,
        "internals_summary":  summary,
    }


def _empty_internals() -> dict:
    return {
        "advancing_sectors": 0, "declining_sectors": 0, "neutral_sectors": 0,
        "sectors_above_ema": 0, "leading_sectors": 0, "weak_sectors": 0,
        "total_sectors": 0, "add_proxy": 0, "new_highs_proxy": 0,
        "breadth_strength": "Mixed", "participation": "Narrow",
        "breakouts_likely": False,
        "internals_summary": "Market internals data unavailable.",
    }


# =============================================================================
# 2. INSTITUTIONAL LIQUIDITY MAP
# =============================================================================

def detect_liquidity_zones(ohlcv: dict | None, current_price: float) -> dict:
    """
    Identify liquidity clusters, equal highs/lows, stop hunt zones, and trapped traders.
    Operates on daily OHLCV bars.
    """
    if not ohlcv or not current_price:
        return _empty_liquidity()

    highs  = ohlcv.get("highs", [])
    lows   = ohlcv.get("lows", [])
    closes = ohlcv.get("closes", [])
    n = min(len(highs), len(lows), len(closes))
    if n < 10:
        return _empty_liquidity()

    highs  = highs[-60:]
    lows   = lows[-60:]
    closes = closes[-60:]
    cp = current_price

    # Equal highs: two or more highs within 0.25% of each other
    def _cluster_levels(levels: list[float], tol_pct: float = 0.25) -> list[float]:
        clusters = []
        sorted_l = sorted(levels)
        i = 0
        while i < len(sorted_l):
            group = [sorted_l[i]]
            while i + 1 < len(sorted_l) and abs(sorted_l[i+1] - sorted_l[i]) / max(sorted_l[i], 0.01) < tol_pct / 100:
                i += 1
                group.append(sorted_l[i])
            if len(group) >= 2:
                clusters.append(round(sum(group) / len(group), 2))
            i += 1
        return clusters

    equal_highs = _cluster_levels(highs)
    equal_lows  = _cluster_levels(lows)

    # Resistance above: equal highs above current price (where shorts are trapped, longs target)
    resistance_above = sorted([h for h in equal_highs if h > cp * 1.002])[:3]
    # Support below: equal lows below current price
    support_below = sorted([l for l in equal_lows if l < cp * 0.998], reverse=True)[:3]

    # Stop hunt zones: price spiked below a level then closed above (bear trap)
    bear_traps = []
    bull_traps = []
    for i in range(2, n):
        prev_low = min(lows[max(0,i-5):i])
        prev_high = max(highs[max(0,i-5):i])
        # Bear trap: spike below prior swing low, then close back above
        if lows[i] < prev_low * 0.995 and closes[i] > prev_low:
            bear_traps.append(round(lows[i], 2))
        # Bull trap: spike above prior swing high, then close back below
        if highs[i] > prev_high * 1.005 and closes[i] < prev_high:
            bull_traps.append(round(highs[i], 2))

    # Most recent traps
    recent_bear_trap = bear_traps[-1] if bear_traps else None
    recent_bull_trap = bull_traps[-1] if bull_traps else None

    # Liquidity pool: price ranges with high frequency of touch → institutions accumulate
    price_range = max(highs) - min(lows)
    if price_range > 0:
        bins = 20
        bin_size = price_range / bins
        bin_counts = [0] * bins
        for i in range(n):
            for price in [highs[i], lows[i], closes[i]]:
                idx = int((price - min(lows)) / bin_size)
                idx = max(0, min(bins - 1, idx))
                bin_counts[idx] += 1
        max_count = max(bin_counts)
        liquidity_pools = []
        for idx, count in enumerate(bin_counts):
            if count >= max_count * 0.7:
                level = round(min(lows) + (idx + 0.5) * bin_size, 2)
                liquidity_pools.append(level)
    else:
        liquidity_pools = []

    # Nearest sweep target above/below
    sweep_above = resistance_above[0] if resistance_above else None
    sweep_below = support_below[0] if support_below else None

    # Where might a reversal happen?
    reversal_zone_up   = sweep_above  # institutions may sell at equal highs
    reversal_zone_down = sweep_below  # institutions may buy at equal lows

    alerts = []
    if sweep_above and cp > sweep_above * 0.99:
        alerts.append(f"Price near equal-high resistance at ${sweep_above:.2f} — potential reversal zone.")
    if sweep_below and cp < sweep_below * 1.01:
        alerts.append(f"Price near equal-low support at ${sweep_below:.2f} — potential squeeze zone.")
    if recent_bear_trap and cp > recent_bear_trap:
        alerts.append(f"Failed breakdown reclaim detected near ${recent_bear_trap:.2f} — bullish.")
    if recent_bull_trap and cp < recent_bull_trap:
        alerts.append(f"Bull trap above ${recent_bull_trap:.2f} — bearish reversal risk.")

    return {
        "equal_highs":       equal_highs[:5],
        "equal_lows":        equal_lows[:5],
        "resistance_above":  resistance_above,
        "support_below":     support_below,
        "liquidity_pools":   liquidity_pools[:5],
        "bear_traps":        bear_traps[-3:],
        "bull_traps":        bull_traps[-3:],
        "recent_bear_trap":  recent_bear_trap,
        "recent_bull_trap":  recent_bull_trap,
        "sweep_above":       sweep_above,
        "sweep_below":       sweep_below,
        "reversal_zone_up":  reversal_zone_up,
        "reversal_zone_down":reversal_zone_down,
        "liquidity_alerts":  alerts,
    }


def _empty_liquidity() -> dict:
    return {
        "equal_highs": [], "equal_lows": [], "resistance_above": [],
        "support_below": [], "liquidity_pools": [], "bear_traps": [],
        "bull_traps": [], "recent_bear_trap": None, "recent_bull_trap": None,
        "sweep_above": None, "sweep_below": None,
        "reversal_zone_up": None, "reversal_zone_down": None,
        "liquidity_alerts": [],
    }


# =============================================================================
# 3. VOLATILITY COMPRESSION ENGINE
# =============================================================================

def detect_volatility_compression(ohlcv: dict | None, current_price: float) -> dict:
    """
    Detect tight ranges, ATR compression, Bollinger squeeze setups.
    Returns squeeze probability and alert message.
    """
    if not ohlcv or not current_price:
        return _empty_compression()

    closes  = ohlcv.get("closes", [])
    highs   = ohlcv.get("highs", [])
    lows    = ohlcv.get("lows", [])

    if len(closes) < 20:
        return _empty_compression()

    cp = current_price

    # ATR compression: compare recent 5-day ATR to 20-day ATR
    atr_20 = _atr(highs, lows, closes, 20)
    atr_5  = _atr(highs[-7:], lows[-7:], closes[-7:], 5)

    atr_pct_20 = (atr_20 / cp * 100) if atr_20 and cp else None
    atr_pct_5  = (atr_5  / cp * 100) if atr_5  and cp else None

    atr_compressed = (
        atr_pct_5 is not None and atr_pct_20 is not None and
        atr_pct_5 < atr_pct_20 * 0.65
    )

    # Bollinger band squeeze: current BB width < 50% of 20-period mean BB width
    std_20 = _std(closes, 20)
    mid_20 = _sma(closes, 20)
    bb_width_pct = (4 * std_20 / mid_20 * 100) if (std_20 and mid_20) else None

    # Historical BB widths for context
    bb_widths = []
    for i in range(20, len(closes)):
        s = _std(closes[i-20:i], 20)
        m = _sma(closes[i-20:i], 20)
        if s and m and m > 0:
            bb_widths.append(4 * s / m * 100)

    avg_bb_width = sum(bb_widths) / len(bb_widths) if bb_widths else None
    bb_squeeze   = (bb_width_pct is not None and avg_bb_width is not None and
                    bb_width_pct < avg_bb_width * 0.55)

    # Range compression: last 5 daily ranges vs 20-day avg range
    ranges_20 = [highs[i] - lows[i] for i in range(-20, 0) if highs[i] > 0]
    ranges_5  = [highs[i] - lows[i] for i in range(-5, 0)  if highs[i] > 0]
    avg_range_20 = sum(ranges_20) / len(ranges_20) if ranges_20 else None
    avg_range_5  = sum(ranges_5)  / len(ranges_5)  if ranges_5  else None
    range_compressed = (
        avg_range_5 is not None and avg_range_20 is not None and
        avg_range_5 < avg_range_20 * 0.6
    )

    # Squeeze score 0-3 (one point per compression signal)
    score = sum([atr_compressed, bb_squeeze, range_compressed])

    squeeze_probability = {0: "Low", 1: "Low", 2: "Moderate", 3: "High"}[score]
    expansion_likely = score >= 2

    if score == 3:
        alert = f"Volatility compression detected — expansion likely soon. ATR at {atr_pct_5:.2f}% (normal: {atr_pct_20:.2f}%)."
    elif score == 2:
        alert = "Moderate squeeze setup — watch for breakout direction."
    elif bb_squeeze:
        alert = "Bollinger squeeze — range tightening."
    elif atr_compressed:
        alert = "ATR compression — reduced volatility period."
    else:
        alert = ""

    return {
        "atr_14":             round(atr_20, 3) if atr_20 else None,
        "atr_pct":            round(atr_pct_20, 2) if atr_pct_20 else None,
        "atr_5_pct":          round(atr_pct_5, 2) if atr_pct_5 else None,
        "atr_compressed":     atr_compressed,
        "bb_width_pct":       round(bb_width_pct, 2) if bb_width_pct else None,
        "bb_squeeze":         bb_squeeze,
        "range_compressed":   range_compressed,
        "squeeze_score":      score,
        "squeeze_probability":squeeze_probability,
        "expansion_likely":   expansion_likely,
        "compression_alert":  alert,
        "bb_upper":           round(mid_20 + 2*std_20, 2) if (mid_20 and std_20) else None,
        "bb_lower":           round(mid_20 - 2*std_20, 2) if (mid_20 and std_20) else None,
        "bb_mid":             round(mid_20, 2) if mid_20 else None,
    }


def _empty_compression() -> dict:
    return {
        "atr_14": None, "atr_pct": None, "atr_5_pct": None,
        "atr_compressed": False, "bb_width_pct": None, "bb_squeeze": False,
        "range_compressed": False, "squeeze_score": 0,
        "squeeze_probability": "Low", "expansion_likely": False,
        "compression_alert": "", "bb_upper": None, "bb_lower": None, "bb_mid": None,
    }


# =============================================================================
# 4. EARNINGS DRIFT ENGINE
# =============================================================================

def detect_earnings_drift(stock: dict, ohlcv: dict | None) -> dict:
    """
    Detect post-earnings continuation and multi-day drift momentum.
    """
    earnings_date_str = stock.get("earnings_date") or ""
    current_price = _safe(stock.get("current_price"))
    gap_pct = _safe(stock.get("gap_pct"))

    result = {
        "in_earnings_drift":       False,
        "days_since_earnings":     None,
        "earnings_gap_pct":        None,
        "post_earnings_trend":     "Unknown",
        "drift_continuation":      False,
        "accumulation_signal":     False,
        "earnings_drift_alert":    "",
    }

    if not earnings_date_str or not current_price:
        return result

    try:
        ed = datetime.strptime(str(earnings_date_str)[:10], "%Y-%m-%d")
        days_since = (datetime.now() - ed).days
    except Exception:
        return result

    result["days_since_earnings"] = days_since

    # Earnings drift window: 1-15 trading days after report
    if not (0 <= days_since <= 21):
        return result

    result["in_earnings_drift"] = True

    # Use gap_pct as proxy for earnings day reaction
    if abs(gap_pct) > 3:
        result["earnings_gap_pct"] = round(gap_pct, 2)

    # Post-earnings trend from OHLCV
    if ohlcv and len(ohlcv.get("closes", [])) >= days_since + 5:
        closes = ohlcv["closes"]
        # Compare price now vs price days_since ago
        if days_since < len(closes):
            price_at_earnings = closes[-(days_since + 1)]
            if price_at_earnings > 0:
                drift_pct = (current_price - price_at_earnings) / price_at_earnings * 100

                if drift_pct > 5 and gap_pct > 3:
                    result["post_earnings_trend"] = "Strong Continuation"
                    result["drift_continuation"]  = True
                    result["earnings_drift_alert"] = (
                        f"Post-earnings continuation +{drift_pct:.1f}% since report. "
                        f"Institutional accumulation pattern."
                    )
                elif drift_pct > 2:
                    result["post_earnings_trend"] = "Mild Continuation"
                    result["drift_continuation"]  = True
                    result["earnings_drift_alert"] = f"Earnings drift +{drift_pct:.1f}% — watch for breakout."
                elif drift_pct < -3 and gap_pct > 0:
                    result["post_earnings_trend"] = "Failed Earnings Gap"
                    result["earnings_drift_alert"] = "Failed earnings gap — sellers absorbed the move."
                else:
                    result["post_earnings_trend"] = "Consolidating"

    # Accumulation signal: price stable/rising on days 3-10 after big gap
    if gap_pct > 5 and days_since > 2 and result["post_earnings_trend"] in ("Strong Continuation", "Mild Continuation", "Consolidating"):
        result["accumulation_signal"] = True

    return result


# =============================================================================
# 5. FAILED BREAKOUT / FAILED BREAKDOWN DETECTOR
# =============================================================================

def detect_failed_moves(ohlcv: dict | None, current_price: float) -> dict:
    """
    Detect bear traps, bull traps, failed breakdowns (reclaim) and failed breakouts.
    """
    if not ohlcv or not current_price:
        return _empty_failed_moves()

    closes = ohlcv.get("closes", [])
    highs  = ohlcv.get("highs", [])
    lows   = ohlcv.get("lows", [])
    n = min(len(closes), len(highs), len(lows))

    if n < 10:
        return _empty_failed_moves()

    closes = closes[-30:]
    highs  = highs[-30:]
    lows   = lows[-30:]
    cp = current_price

    recent_high = max(highs[-10:])
    recent_low  = min(lows[-10:])
    n = len(closes)

    # Failed breakdown: stock fell below 20-day low then reclaimed it
    low_20 = min(lows[:-3]) if len(lows) > 3 else recent_low
    failed_breakdown = (
        recent_low < low_20 * 0.99 and      # spiked below
        closes[-1] > low_20 * 0.995          # closed back above
    )

    # Failed breakout: stock broke above 20-day high then closed back below
    high_20 = max(highs[:-3]) if len(highs) > 3 else recent_high
    failed_breakout = (
        recent_high > high_20 * 1.005 and    # spiked above
        closes[-1] < high_20 * 1.002         # closed back below
    )

    # Squeeze probability: failed breakdown + below-avg range + building volume
    squeeze_prob = "High" if (failed_breakdown and not failed_breakout) else "Low"

    alerts = []
    if failed_breakdown:
        alerts.append("Failed breakdown reclaim detected — high squeeze probability.")
    if failed_breakout:
        alerts.append("Failed breakout above resistance — reversal risk elevated.")

    return {
        "failed_breakdown":      failed_breakdown,
        "failed_breakout":       failed_breakout,
        "failed_breakdown_level":round(low_20, 2)  if failed_breakdown else None,
        "failed_breakout_level": round(high_20, 2) if failed_breakout  else None,
        "squeeze_probability":   squeeze_prob,
        "failed_move_alerts":    alerts,
    }


def _empty_failed_moves() -> dict:
    return {
        "failed_breakdown": False, "failed_breakout": False,
        "failed_breakdown_level": None, "failed_breakout_level": None,
        "squeeze_probability": "Low", "failed_move_alerts": [],
    }


# =============================================================================
# 6. VOLUME PROFILE ENGINE
# =============================================================================

def compute_volume_profile(ohlcv: dict | None, current_price: float, bins: int = 15) -> dict:
    """
    Approximate volume profile from daily OHLCV.
    Identifies HVN (High Volume Nodes), LVN (Low Volume Nodes), and POC.
    """
    if not ohlcv or not current_price:
        return _empty_volume_profile()

    closes  = ohlcv.get("closes", [])[-60:]
    highs   = ohlcv.get("highs",  [])[-60:]
    lows    = ohlcv.get("lows",   [])[-60:]
    volumes = ohlcv.get("volumes",[])[-60:]
    n = min(len(closes), len(highs), len(lows), len(volumes))

    if n < 10:
        return _empty_volume_profile()

    price_min = min(lows[:n])
    price_max = max(highs[:n])
    price_range = price_max - price_min
    if price_range <= 0:
        return _empty_volume_profile()

    bin_size = price_range / bins
    bin_volumes = [0.0] * bins
    bin_centers = [round(price_min + (i + 0.5) * bin_size, 2) for i in range(bins)]

    for i in range(n):
        h, l, v = highs[i], lows[i], volumes[i]
        bar_range = h - l
        if bar_range <= 0 or not v:
            continue
        # Distribute volume across price bins touched by this bar
        for b in range(bins):
            bin_lo = price_min + b * bin_size
            bin_hi = bin_lo + bin_size
            overlap = min(h, bin_hi) - max(l, bin_lo)
            if overlap > 0:
                fraction = overlap / bar_range
                bin_volumes[b] += v * fraction

    total_vol = sum(bin_volumes)
    if total_vol == 0:
        return _empty_volume_profile()

    bin_pcts = [round(v / total_vol * 100, 1) for v in bin_volumes]

    # POC: bin with highest volume
    poc_idx = bin_volumes.index(max(bin_volumes))
    poc     = bin_centers[poc_idx]

    # HVN: bins with volume > 70% of max
    max_vol = max(bin_volumes)
    hvn = [bin_centers[i] for i, v in enumerate(bin_volumes) if v >= max_vol * 0.7]

    # LVN: bins with volume < 20% of max (price moves quickly through here)
    lvn = [bin_centers[i] for i, v in enumerate(bin_volumes) if v <= max_vol * 0.2 and v > 0]

    # Value area: 70% of volume
    sorted_by_vol = sorted(range(bins), key=lambda i: bin_volumes[i], reverse=True)
    va_vol = 0.0
    va_bins = []
    for idx in sorted_by_vol:
        va_vol += bin_volumes[idx]
        va_bins.append(idx)
        if va_vol >= total_vol * 0.70:
            break
    va_high = bin_centers[max(va_bins)]
    va_low  = bin_centers[min(va_bins)]

    cp = current_price
    cp_bin = min(bins - 1, int((cp - price_min) / bin_size))
    cp_vol_pct = bin_pcts[cp_bin] if 0 <= cp_bin < bins else 0

    # Acceptance vs rejection
    if cp_vol_pct >= 8:
        zone_type = "High Volume Node — price accepted here"
    elif cp_vol_pct <= 2:
        zone_type = "Low Volume Node — price likely to move quickly"
    elif va_low <= cp <= va_high:
        zone_type = "Value Area — fair price range"
    else:
        zone_type = "Outside Value Area — extension or reversion likely"

    return {
        "poc":          round(poc, 2),
        "va_high":      round(va_high, 2),
        "va_low":       round(va_low, 2),
        "hvn":          [round(p, 2) for p in hvn[:4]],
        "lvn":          [round(p, 2) for p in lvn[:4]],
        "bin_centers":  bin_centers,
        "bin_pcts":     bin_pcts,
        "current_zone": zone_type,
        "above_poc":    cp > poc,
    }


def _empty_volume_profile() -> dict:
    return {
        "poc": None, "va_high": None, "va_low": None,
        "hvn": [], "lvn": [], "bin_centers": [], "bin_pcts": [],
        "current_zone": "No data", "above_poc": None,
    }


# =============================================================================
# 7. ADVANCED PATTERN ENGINE
# =============================================================================

def detect_patterns(ohlcv: dict | None, stock: dict) -> dict:
    """
    Detect and score bull flags, tight flags, high tight flags, compression wedges,
    cup & handle, and stair-step trends. Returns scored pattern dict.
    """
    if not ohlcv:
        return _empty_pattern()

    closes  = ohlcv.get("closes", [])
    highs   = ohlcv.get("highs",  [])
    lows    = ohlcv.get("lows",   [])
    volumes = ohlcv.get("volumes",[])

    if len(closes) < 20:
        return _empty_pattern()

    closes  = closes[-60:]
    highs   = highs[-60:]
    lows    = lows[-60:]
    volumes = volumes[-60:]
    n = len(closes)
    cp = closes[-1]

    patterns_found = []
    best_pattern   = None
    best_score     = 0

    # ── Bull Flag ─────────────────────────────────────────────────────────────
    # Pole: strong move up (>10%) in last 5-10 bars
    # Flag: tight consolidation in last 5 bars (< 4% range)
    if n >= 15:
        pole_start_idx = max(0, n - 15)
        pole_high = max(highs[-15:-5])
        flag_range_pct = (max(highs[-5:]) - min(lows[-5:])) / max(cp, 0.01) * 100

        pole_low = min(lows[-15:-5])
        pole_move = (pole_high - pole_low) / max(pole_low, 0.01) * 100

        avg_vol_pole = sum(volumes[-15:-5]) / 10 if volumes else 0
        avg_vol_flag = sum(volumes[-5:]) / 5     if volumes else 0
        vol_contraction = avg_vol_flag < avg_vol_pole * 0.7

        if pole_move > 8 and flag_range_pct < 5 and vol_contraction:
            struct_quality = min(100, int(pole_move * 3 + (5 - flag_range_pct) * 5))
            vol_quality    = 70 if vol_contraction else 40
            momentum_q     = min(100, int(pole_move * 4))
            breakout_q     = 60 if cp >= max(highs[-5:]) * 0.98 else 40
            cont_prob      = min(95, (struct_quality + vol_quality + momentum_q + breakout_q) // 4 + 10)
            score = (struct_quality + vol_quality) // 2
            patterns_found.append({
                "pattern": "Bull Flag",
                "score": score,
                "structure_quality": struct_quality,
                "breakout_quality": breakout_q,
                "volume_quality": vol_quality,
                "momentum_quality": momentum_q,
                "continuation_probability": cont_prob,
                "description": f"Pole +{pole_move:.1f}%, flag range {flag_range_pct:.1f}%, volume contracting."
            })

        # High Tight Flag: pole > 25% in 8 bars, very tight flag
        if pole_move > 25 and flag_range_pct < 3:
            score = 88
            patterns_found.append({
                "pattern": "High Tight Flag",
                "score": score,
                "structure_quality": 90,
                "breakout_quality": 85,
                "volume_quality": 80,
                "momentum_quality": 95,
                "continuation_probability": 82,
                "description": f"HTF: pole +{pole_move:.1f}%, tight {flag_range_pct:.1f}% flag — rare explosive setup."
            })

    # ── Tight Flag / Volatility Contraction Pattern ───────────────────────────
    if n >= 10:
        recent_range_pct = (max(highs[-5:]) - min(lows[-5:])) / max(cp, 0.01) * 100
        prior_range_pct  = (max(highs[-15:-5]) - min(lows[-15:-5])) / max(cp, 0.01) * 100
        ema20 = _ema(closes, min(20, n))
        above_ema = cp > ema20 if ema20 else False

        if recent_range_pct < prior_range_pct * 0.5 and above_ema and recent_range_pct < 4:
            score = 70
            patterns_found.append({
                "pattern": "VCP / Tight Flag",
                "score": score,
                "structure_quality": 75,
                "breakout_quality": 65,
                "volume_quality": 60,
                "momentum_quality": 70,
                "continuation_probability": 68,
                "description": f"Volatility contraction {recent_range_pct:.1f}% vs prior {prior_range_pct:.1f}%. Above EMA20."
            })

    # ── Stair-Step Trend / Institutional Continuation ─────────────────────────
    if n >= 20:
        # Count higher highs and higher lows over last 20 days
        hh_count = sum(1 for i in range(1, 10) if highs[-(i)] > highs[-(i+1)])
        hl_count = sum(1 for i in range(1, 10) if lows[-(i)]  > lows[-(i+1)])
        if hh_count >= 6 and hl_count >= 5:
            score = 75
            patterns_found.append({
                "pattern": "Stair-Step Trend",
                "score": score,
                "structure_quality": 80,
                "breakout_quality": 70,
                "volume_quality": 65,
                "momentum_quality": 75,
                "continuation_probability": 72,
                "description": f"Consistent HH/HL pattern — {hh_count}/9 higher highs, {hl_count}/9 higher lows."
            })

    # ── Cup & Handle ──────────────────────────────────────────────────────────
    if n >= 40:
        left_high = max(highs[-40:-20])
        cup_low   = min(lows[-30:-10])
        right_high = max(highs[-10:])
        handle_low = min(lows[-5:])
        cup_depth  = (left_high - cup_low) / max(left_high, 0.01) * 100
        right_recovery = (right_high - cup_low) / max(left_high - cup_low, 0.01) * 100

        if (15 < cup_depth < 50 and right_recovery > 0.85 and
                handle_low > cup_low and right_high >= left_high * 0.97):
            score = 80
            patterns_found.append({
                "pattern": "Cup & Handle",
                "score": score,
                "structure_quality": 82,
                "breakout_quality": 80,
                "volume_quality": 70,
                "momentum_quality": 78,
                "continuation_probability": 76,
                "description": f"Cup depth {cup_depth:.1f}%, {right_recovery:.0%} recovery. Handle forming."
            })

    # Select best pattern by score
    if patterns_found:
        best = max(patterns_found, key=lambda p: p["score"])
        best_pattern = best["pattern"]
        best_score   = best["score"]

    return {
        "patterns_detected":         patterns_found,
        "best_pattern":              best_pattern,
        "pattern_score":             best_score,
        "pattern_count":             len(patterns_found),
        "best_continuation_prob":    patterns_found[0]["continuation_probability"] if patterns_found else 0,
    }


def _empty_pattern() -> dict:
    return {
        "patterns_detected": [], "best_pattern": None,
        "pattern_score": 0, "pattern_count": 0, "best_continuation_prob": 0,
    }


# =============================================================================
# 8. RISK MANAGEMENT INTELLIGENCE
# =============================================================================

def compute_risk_levels(stock: dict, mkt_ctx: dict, account_size: float = 50000.0) -> dict:
    """
    Calculate position sizing, ATR-based stops, and dynamic R:R.
    Risk per trade: 1% of account in RISK_ON, 0.5% in CAUTION, 0% in NO_TRADE.
    """
    cp = _safe(stock.get("current_price"))
    if not cp:
        return _empty_risk()

    regime = mkt_ctx.get("regime", "NEUTRAL")
    atr_data = stock.get("_compression", {})
    atr = atr_data.get("atr_14") if atr_data else None

    # Risk percent by regime
    risk_pct_map = {"RISK_ON": 0.01, "NEUTRAL": 0.0075, "CAUTION": 0.005, "RISK_OFF": 0.0, "NO_TRADE": 0.0}
    risk_pct = risk_pct_map.get(regime, 0.0075)
    risk_dollars = account_size * risk_pct

    # ATR-based stop: 1.5x ATR below entry for longs
    atr_val = atr if atr else cp * 0.025   # fallback: 2.5% of price
    stop_loss = round(cp - atr_val * 1.5, 2)
    stop_pct  = round((cp - stop_loss) / cp * 100, 2)

    # Position size = risk_dollars / stop_distance
    stop_distance = cp - stop_loss
    shares = int(risk_dollars / stop_distance) if stop_distance > 0 and risk_dollars > 0 else 0
    position_value = round(shares * cp, 2)

    # Profit targets: 2R and 3R
    target_1 = round(cp + atr_val * 3, 2)    # 2R
    target_2 = round(cp + atr_val * 4.5, 2)  # 3R
    rr_1 = round((target_1 - cp) / max(stop_distance, 0.01), 2)
    rr_2 = round((target_2 - cp) / max(stop_distance, 0.01), 2)

    # Reduce size if CAUTION regime or low probability
    size_modifier = {"RISK_ON": 1.0, "NEUTRAL": 0.75, "CAUTION": 0.5, "RISK_OFF": 0.0, "NO_TRADE": 0.0}
    size_mult = size_modifier.get(regime, 0.75)
    adjusted_shares = int(shares * size_mult)

    return {
        "entry_price":     round(cp, 2),
        "stop_loss":       stop_loss,
        "stop_pct":        stop_pct,
        "target_1":        target_1,
        "target_2":        target_2,
        "risk_reward_1":   rr_1,
        "risk_reward_2":   rr_2,
        "shares_full":     shares,
        "shares_adjusted": adjusted_shares,
        "position_value":  position_value,
        "risk_dollars":    round(risk_dollars, 2),
        "atr_used":        round(atr_val, 3),
        "size_modifier":   size_mult,
        "regime_size_note":f"Sizing at {int(size_mult*100)}% for {regime} regime.",
    }


def _empty_risk() -> dict:
    return {
        "entry_price": None, "stop_loss": None, "stop_pct": None,
        "target_1": None, "target_2": None, "risk_reward_1": None,
        "risk_reward_2": None, "shares_full": 0, "shares_adjusted": 0,
        "position_value": 0, "risk_dollars": 0, "atr_used": None,
        "size_modifier": 0, "regime_size_note": "No trade.",
    }


# =============================================================================
# 9. EMOTIONAL DISCIPLINE AI
# =============================================================================

def check_emotional_discipline(stock: dict, mkt_ctx: dict) -> dict:
    """
    Detect chase risk, FOMO entries, poor R:R, oversize risk, momentum exhaustion.
    """
    warnings = []
    alerts   = []
    cp = _safe(stock.get("current_price"))
    gap_pct  = _safe(stock.get("gap_pct"))
    vwap     = _safe(stock.get("vwap"))
    ema20    = _safe(stock.get("ema_20_daily"))
    rs_score = _safe(stock.get("rs_score"), 50)
    rvol     = _safe(stock.get("rel_volume"), 1.0)
    regime   = mkt_ctx.get("regime", "NEUTRAL")

    discipline_score = 100   # start at 100, deduct for each flag

    # 1. Extended above VWAP
    if vwap and cp and cp > vwap * 1.04:
        ext_pct = (cp - vwap) / vwap * 100
        warnings.append(f"Trade extended {ext_pct:.1f}% above VWAP.")
        alerts.append("High chase risk — extended above VWAP.")
        discipline_score -= 25

    # 2. Chasing a huge gap
    if gap_pct > 15:
        warnings.append(f"Gap of +{gap_pct:.1f}% — momentum exhaustion risk.")
        alerts.append("Large gap — wait for pullback entry.")
        discipline_score -= 20
    elif gap_pct > 8:
        warnings.append(f"Gap +{gap_pct:.1f}% — high chase risk.")
        discipline_score -= 12

    # 3. Extended above EMA20
    if ema20 and cp and cp > ema20 * 1.12:
        ext = (cp - ema20) / ema20 * 100
        warnings.append(f"Price {ext:.1f}% above 20 EMA — overextended.")
        discipline_score -= 15

    # 4. Weak R:R
    rr = _safe(stock.get("risk_reward"))
    if rr and rr < 1.5:
        warnings.append(f"Poor R:R ({rr:.1f}x) — minimum 2:1 required.")
        alerts.append("Poor RR setup.")
        discipline_score -= 20

    # 5. Low RVOL entry
    if rvol < 0.7:
        warnings.append(f"Low RVOL ({rvol:.2f}x) — no institutional urgency.")
        discipline_score -= 10

    # 6. No-trade regime
    if regime == "NO_TRADE":
        warnings.append("Market in NO_TRADE regime — VIX extreme.")
        alerts.append("NO TRADE — VIX extreme.")
        discipline_score -= 40
    elif regime == "RISK_OFF":
        warnings.append("Risk-off market — avoid new longs.")
        discipline_score -= 20

    # 7. RS laggard chasing
    if rs_score < 40 and gap_pct > 3:
        warnings.append(f"RS laggard (score {int(rs_score)}) — chasing a weak stock.")
        discipline_score -= 15

    discipline_score = max(0, discipline_score)

    grade = "A" if discipline_score >= 85 else "B" if discipline_score >= 70 else "C" if discipline_score >= 55 else "D"

    return {
        "discipline_score":   discipline_score,
        "discipline_grade":   grade,
        "discipline_warnings":warnings,
        "discipline_alerts":  alerts,
        "safe_to_trade":      discipline_score >= 60 and regime not in ("NO_TRADE",),
    }


# =============================================================================
# 10. REPLAY + LEARNING SYSTEM
# =============================================================================

# In-memory session stats (persisted across requests via module-level dict)
_daily_review_lock = threading.Lock()
_session_trades: list[dict] = []


def record_trade_event(ticker: str, action: str, price: float, setup_type: str = "", outcome: str = "") -> None:
    """Record a trade event for end-of-day review."""
    with _daily_review_lock:
        _session_trades.append({
            "ticker":     ticker,
            "action":     action,
            "price":      price,
            "setup_type": setup_type,
            "outcome":    outcome,
            "timestamp":  datetime.now().isoformat(),
        })


def generate_daily_review(setups: list[dict]) -> dict:
    """
    Generate an end-of-day performance review from tracked setup data.
    `setups` is a list of stock analysis dicts from the current session.
    """
    if not setups:
        return {"summary": "No trade data available for review.", "stats": {}}

    a_plus  = [s for s in setups if s.get("prob_score", 0) >= 80]
    strong  = [s for s in setups if 65 <= s.get("prob_score", 0) < 80]
    weak    = [s for s in setups if s.get("prob_score", 0) < 50]

    best_setup   = max(setups, key=lambda s: s.get("prob_score", 0), default=None)
    worst_setup  = min(setups, key=lambda s: s.get("prob_score", 0), default=None)

    # Setup type distribution
    setup_counts: dict[str, int] = {}
    for s in setups:
        stype = s.get("best_pattern") or "Unknown"
        setup_counts[stype] = setup_counts.get(stype, 0) + 1

    top_setup_type = max(setup_counts, key=setup_counts.get) if setup_counts else "None"

    summary_lines = [
        f"Daily Review — {datetime.now().strftime('%Y-%m-%d')}",
        f"Total setups scanned: {len(setups)}",
        f"A+ setups (score ≥80): {len(a_plus)}",
        f"Strong setups (65-79): {len(strong)}",
        f"Weak setups (<50): {len(weak)}",
        f"Dominant pattern: {top_setup_type}",
    ]
    if best_setup:
        summary_lines.append(
            f"Best setup: {best_setup.get('ticker','')} "
            f"(score {best_setup.get('prob_score',0)}, {best_setup.get('best_pattern','')})"
        )

    return {
        "summary":       "\n".join(summary_lines),
        "a_plus_setups": [s.get("ticker") for s in a_plus],
        "strong_setups": [s.get("ticker") for s in strong],
        "weak_setups":   [s.get("ticker") for s in weak],
        "best_setup":    best_setup.get("ticker") if best_setup else None,
        "setup_counts":  setup_counts,
        "top_setup_type":top_setup_type,
        "total":         len(setups),
        "stats": {
            "a_plus": len(a_plus),
            "strong": len(strong),
            "weak":   len(weak),
        },
    }


# =============================================================================
# 11. WEIGHTED PROBABILITY ENGINE
# =============================================================================

def compute_probability_score(stock: dict, mkt_ctx: dict) -> dict:
    """
    Multi-factor weighted probability score (0-100) for trade quality.

    Component weights:
      RS Score          15 pts  — relative strength vs QQQ
      RVOL              20 pts  — volume confirmation
      Market Regime     15 pts  — index environment
      Sector Aligned    10 pts  — sector leadership
      Zone Location      15 pts  — demand zone / above VWAP
      Pattern Quality   10 pts  — bull flag, VCP, etc.
      Catalyst          10 pts  — news / earnings quality
      Penalties          —      — overextended, poor RR, weak market
    """
    breakdown = {}
    total = 0

    # ── RS Score (0-15) ────────────────────────────────────────────────────────
    rs = _safe(stock.get("rs_score"), 50)
    if rs >= 85:    rs_pts = 15
    elif rs >= 70:  rs_pts = 12
    elif rs >= 55:  rs_pts = 8
    elif rs >= 40:  rs_pts = 4
    else:           rs_pts = 0
    breakdown["Relative Strength"] = rs_pts
    total += rs_pts

    # ── RVOL (0-20) ───────────────────────────────────────────────────────────
    rvol = _safe(stock.get("rel_volume"), 1.0)
    if rvol >= 3.0:   rv_pts = 20
    elif rvol >= 2.0: rv_pts = 16
    elif rvol >= 1.5: rv_pts = 12
    elif rvol >= 1.0: rv_pts = 7
    else:             rv_pts = 0
    breakdown["RVOL"] = rv_pts
    total += rv_pts

    # ── Market Regime (0-15) ──────────────────────────────────────────────────
    regime = mkt_ctx.get("regime", "NEUTRAL")
    regime_pts = {"RISK_ON": 15, "NEUTRAL": 8, "CAUTION": 3, "RISK_OFF": 0, "NO_TRADE": 0}.get(regime, 8)
    breakdown["Market Regime"] = regime_pts
    total += regime_pts

    # ── Sector Aligned (0-10) ─────────────────────────────────────────────────
    sector_etf = stock.get("sector_etf", "")
    leading_sectors = mkt_ctx.get("leading_sectors", [])
    sector_pts = 10 if sector_etf and sector_etf in leading_sectors else (5 if sector_etf else 0)
    breakdown["Sector Aligned"] = sector_pts
    total += sector_pts

    # ── Zone Location (0-15) ──────────────────────────────────────────────────
    in_demand = stock.get("in_demand_zone", False)
    above_vwap = stock.get("price_above_vwap", False)
    zone_loc  = stock.get("zone_location", "")
    vwap = _safe(stock.get("vwap"))
    cp   = _safe(stock.get("current_price"))
    if in_demand:
        zone_pts = 15
    elif above_vwap and "DEMAND" in str(zone_loc).upper():
        zone_pts = 12
    elif above_vwap or in_demand:
        zone_pts = 8
    else:
        zone_pts = 3
    breakdown["Zone / VWAP"] = zone_pts
    total += zone_pts

    # ── Pattern Quality (0-10) ────────────────────────────────────────────────
    pattern_score = _safe(stock.get("pattern_score"), 0)
    pat_pts = min(10, int(pattern_score / 10))
    breakdown["Pattern Quality"] = pat_pts
    total += pat_pts

    # ── Catalyst (0-10) ───────────────────────────────────────────────────────
    cat_score = _safe(stock.get("catalyst_score"), 5)
    cat_pts = min(10, int(cat_score))
    breakdown["Catalyst"] = cat_pts
    total += cat_pts

    # ── Penalties ─────────────────────────────────────────────────────────────
    penalties = {}
    # Overextended above VWAP
    if vwap and cp and cp > vwap * 1.05:
        penalties["Overextended VWAP"] = -8
    # Very low RVOL
    if rvol < 0.7:
        penalties["Low RVOL"] = -8
    # No-trade conditions
    if regime == "NO_TRADE":
        penalties["VIX Extreme"] = -20
    elif regime == "RISK_OFF":
        penalties["Risk-Off Market"] = -10
    # RS laggard
    if rs < 35:
        penalties["RS Laggard"] = -8

    penalty_total = sum(penalties.values())
    breakdown.update(penalties)
    total = max(0, min(100, total + penalty_total))

    # Grade
    if total >= 85:   grade = "A+"
    elif total >= 75: grade = "A"
    elif total >= 65: grade = "B+"
    elif total >= 55: grade = "B"
    elif total >= 45: grade = "C"
    else:             grade = "D"

    return {
        "prob_score":     total,
        "prob_grade":     grade,
        "prob_breakdown": breakdown,
        "penalty_total":  penalty_total,
    }


# =============================================================================
# 12. INSTITUTIONAL CONTINUATION ENGINE
# =============================================================================

def detect_continuation(stock: dict, ohlcv: dict | None, mkt_ctx: dict) -> dict:
    """
    Detect institutional continuation setups: shallow pullbacks, dip buying,
    higher lows, flag consolidations, aggressive recovery bars.
    """
    if not ohlcv:
        return _empty_continuation()

    closes  = ohlcv.get("closes", [])[-30:]
    highs   = ohlcv.get("highs",  [])[-30:]
    lows    = ohlcv.get("lows",   [])[-30:]
    volumes = ohlcv.get("volumes",[])[-30:]
    n = len(closes)

    if n < 10:
        return _empty_continuation()

    cp = closes[-1]
    signals = []
    score = 0

    # ── Shallow Pullback (< 38.2% Fibonacci of prior move) ────────────────────
    if n >= 15:
        prior_high = max(highs[-15:-3])
        prior_low  = min(lows[-15:-3])
        move = prior_high - prior_low
        fib_382 = prior_high - move * 0.382
        fib_50  = prior_high - move * 0.50
        pullback_depth = (prior_high - min(lows[-5:])) / max(move, 0.01)

        if pullback_depth <= 0.382 and cp > fib_50:
            signals.append("Shallow pullback (< 38.2% fib) — institutional holding.")
            score += 25
        elif pullback_depth <= 0.50:
            signals.append("Moderate pullback to 50% fib — watch for reclaim.")
            score += 12

    # ── Higher Lows Structure ─────────────────────────────────────────────────
    if n >= 10:
        hl_count = sum(1 for i in range(1, 8) if lows[-(i)] > lows[-(i+1)])
        if hl_count >= 5:
            signals.append(f"Higher lows structure ({hl_count}/7) — trend persistence.")
            score += 20
        elif hl_count >= 3:
            signals.append(f"Building higher lows ({hl_count}/7).")
            score += 10

    # ── Aggressive Dip Buying (long lower wicks on pullback bars) ─────────────
    if n >= 5:
        recent_wicks = []
        for i in range(-5, -1):
            bar_range = highs[i] - lows[i]
            if bar_range > 0:
                lower_wick = closes[i] - lows[i]
                recent_wicks.append(lower_wick / bar_range)
        avg_wick = sum(recent_wicks) / len(recent_wicks) if recent_wicks else 0
        if avg_wick > 0.45:
            signals.append("Aggressive dip buying — long lower wicks on pullback bars.")
            score += 15

    # ── Strong Closes (close in upper half of bar) ────────────────────────────
    if n >= 5:
        strong_closes = 0
        for i in range(-5, 0):
            bar_range = highs[i] - lows[i]
            if bar_range > 0 and (closes[i] - lows[i]) / bar_range > 0.6:
                strong_closes += 1
        if strong_closes >= 3:
            signals.append(f"Strong closes in upper half ({strong_closes}/5 bars).")
            score += 15

    # ── Volume Drying Up on Pullback ──────────────────────────────────────────
    if len(volumes) >= 10 and volumes:
        avg_vol_prior = sum(volumes[-15:-5]) / 10 if len(volumes) >= 15 else sum(volumes[:5]) / max(len(volumes[:5]), 1)
        avg_vol_recent = sum(volumes[-5:]) / 5
        if avg_vol_recent < avg_vol_prior * 0.65:
            signals.append("Volume drying up on pullback — healthy consolidation.")
            score += 10

    score = min(100, score)
    strength = ("Strong" if score >= 65 else "Moderate" if score >= 40 else "Weak")

    summary = ""
    if score >= 65:
        summary = "Strong continuation setup — institutional structure intact. Catch before expansion."
    elif score >= 40:
        summary = "Moderate continuation signals — monitor for entry trigger."
    else:
        summary = "Weak continuation signals — wait for cleaner structure."

    return {
        "continuation_score":    score,
        "continuation_strength": strength,
        "continuation_signals":  signals,
        "continuation_summary":  summary,
    }


def _empty_continuation() -> dict:
    return {
        "continuation_score": 0, "continuation_strength": "Weak",
        "continuation_signals": [], "continuation_summary": "Insufficient data.",
    }


# =============================================================================
# 13. SMART WATCHLIST BUILDER
# =============================================================================

def build_smart_watchlist(stocks: list[dict], mkt_ctx: dict) -> dict:
    """
    Rank stocks by probability score into A+/A/B/C tiers.
    Returns watchlist categories for display.
    """
    if not stocks:
        return {"a_plus": [], "a_grade": [], "b_grade": [], "watchlists": {}}

    scored = sorted(stocks, key=lambda s: s.get("prob_score", 0), reverse=True)

    a_plus = [s for s in scored if s.get("prob_score", 0) >= 80]
    a_grade = [s for s in scored if 70 <= s.get("prob_score", 0) < 80]
    b_grade = [s for s in scored if 55 <= s.get("prob_score", 0) < 70]

    # Specialized watchlists
    earnings_plays = [s for s in stocks if s.get("in_earnings_drift") or s.get("earnings_date")]
    orb_candidates = [s for s in stocks if s.get("rvol", 0) >= 1.5 and s.get("gap_pct", 0) > 2]
    continuation   = [s for s in stocks if s.get("continuation_score", 0) >= 50]
    squeeze_plays  = [s for s in stocks if s.get("squeeze_probability") in ("High", "Moderate")]

    def _ticker_list(lst, max_n=8):
        return [s.get("ticker", "") for s in lst[:max_n] if s.get("ticker")]

    return {
        "a_plus":     _ticker_list(a_plus),
        "a_grade":    _ticker_list(a_grade),
        "b_grade":    _ticker_list(b_grade),
        "watchlists": {
            "AI Momentum":    _ticker_list(a_plus),
            "Earnings Plays": _ticker_list(sorted(earnings_plays, key=lambda s: s.get("prob_score", 0), reverse=True)),
            "ORB Candidates": _ticker_list(sorted(orb_candidates, key=lambda s: s.get("rvol", 0), reverse=True)),
            "Continuation":   _ticker_list(sorted(continuation, key=lambda s: s.get("continuation_score", 0), reverse=True)),
            "Squeeze Plays":  _ticker_list(squeeze_plays),
            "Swing Setups":   _ticker_list([s for s in scored if s.get("swing_score", 0) >= 6]),
        },
    }


# =============================================================================
# 14. MACRO CORRELATION ENGINE
# =============================================================================

def get_macro_context(mkt_ctx: dict) -> dict:
    """
    Build macro correlation summary from cached market context.
    Flags macro pressure conditions for tech/momentum stocks.
    """
    regime   = mkt_ctx.get("regime", "NEUTRAL")
    vix      = mkt_ctx.get("vix_level")
    qqq_1d   = mkt_ctx.get("qqq_1d_pct") or 0
    qqq_5d   = mkt_ctx.get("qqq_5d_pct") or 0
    sectors  = mkt_ctx.get("sectors", [])

    # Fetch macro data from extended market context (added by market_engine extension)
    yield_10y  = mkt_ctx.get("yield_10y")
    yield_chg  = mkt_ctx.get("yield_1d_chg")
    dxy        = mkt_ctx.get("dxy")
    dxy_chg    = mkt_ctx.get("dxy_1d_chg")
    oil        = mkt_ctx.get("oil_price")
    oil_chg    = mkt_ctx.get("oil_1d_chg")

    # Sector correlation signals
    xlk = next((s for s in sectors if s["etf"] == "XLK"), {})
    smh = next((s for s in sectors if s["etf"] == "SMH"), {})
    xlf = next((s for s in sectors if s["etf"] == "XLF"), {})

    alerts = []
    conditions = []

    # VIX conditions
    if vix:
        if vix > 30:
            alerts.append(f"VIX {vix:.1f} — extreme fear. No new positions.")
        elif vix > 22:
            alerts.append(f"VIX {vix:.1f} elevated — reduce size, favor quality.")
        elif vix < 15:
            conditions.append(f"VIX {vix:.1f} — low fear, risk appetite healthy.")

    # Yield conditions (if available)
    if yield_10y and yield_chg:
        if yield_chg > 0.05:
            alerts.append(f"10Y yield rising +{yield_chg:.2f}% — headwind for growth/tech.")
        elif yield_chg < -0.05:
            conditions.append(f"10Y yield falling — tailwind for tech and growth stocks.")

    # DXY conditions
    if dxy_chg:
        if dxy_chg > 0.5:
            alerts.append(f"DXY rising +{dxy_chg:.1f}% — pressure on commodities and EM.")
        elif dxy_chg < -0.5:
            conditions.append(f"DXY falling — positive for risk assets and commodities.")

    # Semiconductor leadership (key for NVDA/AMD/MRVL momentum)
    if smh:
        smh_1d = smh.get("change_1d", 0)
        xlk_1d = xlk.get("change_1d", 0)
        if smh_1d >= 1.5:
            conditions.append(f"Semiconductors leading (+{smh_1d:.1f}%) — strong AI/tech environment.")
        elif smh_1d <= -1.5:
            alerts.append(f"Semiconductor weakness ({smh_1d:.1f}%) — headwind for AI momentum stocks.")
        if xlk_1d > 0 and smh_1d < 0:
            alerts.append("Tech green but semis lagging — narrow rally, be selective.")

    # Financial conditions (credit environment)
    if xlf:
        xlf_1d = xlf.get("change_1d", 0)
        if xlf_1d >= 1.5 and qqq_1d > 0:
            conditions.append(f"Financials leading (+{xlf_1d:.1f}%) with QQQ up — broad risk-on signal.")

    # Overall risk appetite
    if regime == "RISK_ON" and vix and vix < 18:
        risk_appetite = "Strong"
    elif regime == "RISK_ON":
        risk_appetite = "Moderate"
    elif regime == "CAUTION":
        risk_appetite = "Cautious"
    else:
        risk_appetite = "Weak"

    macro_summary = " | ".join(conditions[:3]) if conditions else mkt_ctx.get("signal", "")

    return {
        "risk_appetite":    risk_appetite,
        "vix":              vix,
        "yield_10y":        yield_10y,
        "yield_1d_chg":     yield_chg,
        "dxy":              dxy,
        "dxy_1d_chg":       dxy_chg,
        "oil_price":        oil,
        "oil_1d_chg":       oil_chg,
        "smh_1d":           smh.get("change_1d") if smh else None,
        "xlk_1d":           xlk.get("change_1d") if xlk else None,
        "xlf_1d":           xlf.get("change_1d") if xlf else None,
        "macro_alerts":     alerts,
        "macro_conditions": conditions,
        "macro_summary":    macro_summary,
    }


# =============================================================================
# 15. ADAPTIVE AI LEARNING
# =============================================================================

_adaptive_lock = threading.Lock()
_setup_outcomes: dict[str, dict] = {}   # setup_type → stats


def record_setup_outcome(setup_type: str, outcome: str, regime: str, pattern: str = "") -> None:
    """
    Record trade outcome for adaptive learning.
    outcome: "win" | "loss" | "breakeven"
    """
    key = setup_type or pattern or "Unknown"
    with _adaptive_lock:
        if key not in _setup_outcomes:
            _setup_outcomes[key] = {"wins": 0, "losses": 0, "breakevens": 0, "total": 0, "regimes": {}}
        s = _setup_outcomes[key]
        s["total"] += 1
        if outcome == "win":
            s["wins"] += 1
        elif outcome == "loss":
            s["losses"] += 1
        else:
            s["breakevens"] += 1
        s["regimes"][regime] = s["regimes"].get(regime, 0) + 1


def get_adaptive_insights() -> dict:
    """Return adaptive learning stats: best setups, best regimes, recommendations."""
    with _adaptive_lock:
        if not _setup_outcomes:
            return {
                "best_setup": None, "worst_setup": None,
                "setup_stats": [],
                "recommendations": ["No trade history yet — record outcomes to enable adaptive learning."],
                "best_regime": None,
            }

        stats = []
        for stype, s in _setup_outcomes.items():
            if s["total"] < 3:
                continue
            wr = round(s["wins"] / s["total"] * 100, 1)
            best_regime = max(s["regimes"], key=s["regimes"].get) if s["regimes"] else "Unknown"
            stats.append({
                "setup_type": stype, "total": s["total"],
                "wins": s["wins"], "losses": s["losses"],
                "win_rate": wr, "best_regime": best_regime,
            })

        stats.sort(key=lambda x: x["win_rate"], reverse=True)
        best   = stats[0] if stats else None
        worst  = stats[-1] if len(stats) > 1 else None

        recs = []
        if best:
            recs.append(f"Best setup: {best['setup_type']} ({best['win_rate']}% win rate in {best['best_regime']} regime).")
        if worst and worst["win_rate"] < 40:
            recs.append(f"Avoid {worst['setup_type']} setups — only {worst['win_rate']}% win rate.")
        if not recs:
            recs.append("Keep recording outcomes to build adaptive recommendations.")

        all_regimes: dict[str, int] = {}
        for s in _setup_outcomes.values():
            for r, c in s["regimes"].items():
                all_regimes[r] = all_regimes.get(r, 0) + c
        best_regime = max(all_regimes, key=all_regimes.get) if all_regimes else None

        return {
            "best_setup":      best,
            "worst_setup":     worst,
            "setup_stats":     stats,
            "recommendations": recs,
            "best_regime":     best_regime,
        }


# =============================================================================
# MASTER ANALYSIS FUNCTION
# =============================================================================

def analyze_institutional(stock: dict, mkt_ctx: dict) -> dict:
    """
    Run all 15 institutional engines on a stock dict + market context.
    Returns a dict of all engine outputs merged into the stock.
    All operations are non-blocking (uses cached OHLCV, triggers bg fetch if needed).
    """
    ticker = (stock.get("ticker") or "").upper().strip()
    cp     = _safe(stock.get("current_price"))

    # Get cached OHLCV (triggers background fetch if not cached yet)
    ohlcv = _get_ohlcv(ticker) if ticker else None

    # Run all engines
    compression   = detect_volatility_compression(ohlcv, cp)
    liquidity     = detect_liquidity_zones(ohlcv, cp)
    failed_moves  = detect_failed_moves(ohlcv, cp)
    vol_profile   = compute_volume_profile(ohlcv, cp)
    patterns      = detect_patterns(ohlcv, stock)
    earnings_drift= detect_earnings_drift(stock, ohlcv)
    continuation  = detect_continuation(stock, ohlcv, mkt_ctx)
    macro_ctx     = get_macro_context(mkt_ctx)

    # Inject compression into stock for risk calc (needs ATR)
    stock_with_compression = {**stock, "_compression": compression, **patterns}

    risk          = compute_risk_levels(stock_with_compression, mkt_ctx)
    discipline    = check_emotional_discipline(stock_with_compression, mkt_ctx)
    prob          = compute_probability_score({**stock_with_compression, **continuation}, mkt_ctx)

    return {
        # Feature 3
        **compression,
        # Feature 2
        **liquidity,
        # Feature 5
        **failed_moves,
        # Feature 6
        "vol_profile":       vol_profile,
        # Feature 7
        **patterns,
        # Feature 4
        **earnings_drift,
        # Feature 12
        **continuation,
        # Feature 14 (subset per-stock)
        "macro_alerts":      macro_ctx.get("macro_alerts", []),
        # Feature 8
        **risk,
        # Feature 9
        **discipline,
        # Feature 11
        **prob,
    }
