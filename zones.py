"""
zones.py — Institutional Supply & Demand Zone Engine v2

Detects professional-grade supply and demand zones from OHLCV data.
Replaces V1 simple consolidation-before-impulse approach with real
smart money / institutional concepts:

  Supply Zone  — Strong bearish rejection after bullish expansion.
                 Zone drawn from wick high down to candle body base.

  Demand Zone  — Strong bullish displacement from consolidation/base.
                 Zone drawn from wick low up to candle body base.

Smart Money Features:
  Fair Value Gaps (FVG)       — 3-candle imbalance / liquidity void
  Order Blocks                — Last opposing candle before displacement
  Liquidity Sweeps            — Wick through swing level then reversal
  Break of Structure (BOS)    — Close through prior swing high/low
  Market Structure Shift (MSS)— Trend reversal via BOS

Zone Scoring: A+ / A / B+ / B based on:
  Displacement magnitude, volume, FVG present, zone freshness,
  body strength, recency

Confluence System:
  EMA alignment, VWAP, HTF trend, structure, RVOL, catalyst, fib levels

AI Setup Classification:
  Continuation / Reversal / Breakout / Pullback / VWAP Reclaim /
  Trend Continuation / Failed Breakout / Exhaustion Move

Public API (backward-compatible with V1):
  detect_zones(ticker, current_price, stock_data=None) -> flat dict
  zones_need_refresh(zones_fetched_at) -> bool
"""

from __future__ import annotations

import json
import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

_YF_AVAILABLE = False
try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    pass


# ── Tuning constants ───────────────────────────────────────────────────────────

ZONE_T = {
    # Minimum body-to-range ratio for an impulse/displacement candle.
    "impulse_body_pct":     0.40,
    # Minimum % price move for an impulse candle to count.
    "impulse_move_pct":     0.007,    # 0.7%
    # Minimum ATR multiple for displacement to count as institutional.
    "impulse_atr_mult":     0.70,
    # Minimum volume ratio vs rolling avg to count as institutional.
    "impulse_vol_min":      0.60,
    # Maximum zone width as % of price (zones > this are noise).
    "max_zone_width_pct":   0.12,     # 12%
    # Approach distance: "approaching zone" when within this % of zone edge.
    "approach_pct":         0.03,     # 3%
    # Order block lookback bars.
    "ob_lookback":          4,
    # Minimum bars needed before zone detection begins.
    "min_bars":             15,
    # FVG minimum size as % of price (tiny gaps are noise).
    "fvg_min_pct":          0.001,    # 0.1%
    # Cache TTL in minutes — zones don't change intraday.
    "cache_minutes":        60,
    # Swing high/low detection window (bars each side).
    "swing_window":         3,
}


# ── Array helpers ──────────────────────────────────────────────────────────────

def _compute_atr(hi, lo, cl, period: int = 14) -> float:
    """Simple Average True Range."""
    n = len(hi)
    if n < 2:
        return max(hi[0] - lo[0], 0.01) if n == 1 else 0.01
    trs = []
    for i in range(1, n):
        trs.append(max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1])))
    window = trs[-period:] if len(trs) >= period else trs
    return max(sum(window) / len(window), 0.01)


def _swing_highs(hi, window: int = 3) -> list:
    """Return indices of swing highs (local maxima ± window bars)."""
    n = len(hi)
    out = []
    for i in range(window, n - window):
        if hi[i] == max(hi[i - window: i + window + 1]):
            out.append(i)
    return out


def _swing_lows(lo, window: int = 3) -> list:
    """Return indices of swing lows (local minima ± window bars)."""
    n = len(lo)
    out = []
    for i in range(window, n - window):
        if lo[i] == min(lo[i - window: i + window + 1]):
            out.append(i)
    return out


# ── Smart money detection ──────────────────────────────────────────────────────

def _detect_fvg(hi, lo) -> tuple[list, list]:
    """
    Fair Value Gaps — 3-candle imbalance zones.
    Bullish FVG:  hi[i-2] < lo[i]  →  gap between candle i-2 and i (price ran up)
    Bearish FVG:  lo[i-2] > hi[i]  →  gap between candle i-2 and i (price fell down)
    Returns (bullish_fvgs, bearish_fvgs) each as list of {"bottom", "top", "bar"}.
    """
    bullish, bearish = [], []
    min_pct = ZONE_T["fvg_min_pct"]
    for i in range(2, len(hi)):
        if hi[i - 2] < lo[i]:
            gap_pct = (lo[i] - hi[i - 2]) / hi[i - 2] if hi[i - 2] > 0 else 0
            if gap_pct >= min_pct:
                bullish.append({"bottom": round(hi[i - 2], 4), "top": round(lo[i], 4), "bar": i})
        elif lo[i - 2] > hi[i]:
            gap_pct = (lo[i - 2] - hi[i]) / lo[i - 2] if lo[i - 2] > 0 else 0
            if gap_pct >= min_pct:
                bearish.append({"bottom": round(hi[i], 4), "top": round(lo[i - 2], 4), "bar": i})
    return bullish, bearish


def _detect_bos_mss(hi, lo, cl, swing_w: int = 3) -> dict:
    """
    Break of Structure (BOS) and Market Structure Shift (MSS).
    BOS: price closes through the most recent swing high (bullish) or low (bearish).
    MSS: same but represents a trend change (bearish trend breaks swing high = bullish MSS).
    Returns dict of flags.
    """
    n = len(cl)
    if n < 10:
        return {"bos_bullish": False, "bos_bearish": False, "mss_bullish": False, "mss_bearish": False}

    sh_idx = _swing_highs(hi, swing_w)
    sl_idx = _swing_lows(lo, swing_w)

    # Use last 3 confirmed swing highs/lows (exclude the last few bars — not confirmed yet)
    confirmed_sh = [i for i in sh_idx if i < n - swing_w][-3:]
    confirmed_sl = [i for i in sl_idx if i < n - swing_w][-3:]

    last_close = cl[-1]

    bos_bull = False   # price closed above prior swing high
    bos_bear = False   # price closed below prior swing low
    mss_bull = False   # bearish market broke above swing high → trend shift
    mss_bear = False   # bullish market broke below swing low → trend shift

    if confirmed_sh:
        prior_sh_price = hi[confirmed_sh[-1]]
        if last_close > prior_sh_price:
            bos_bull = True
            # Is it an MSS? Check if preceding structure was lower-highs
            if len(confirmed_sh) >= 2:
                prev_sh = hi[confirmed_sh[-2]]
                if prior_sh_price < prev_sh:   # prior high was lower → downtrend
                    mss_bull = True

    if confirmed_sl:
        prior_sl_price = lo[confirmed_sl[-1]]
        if last_close < prior_sl_price:
            bos_bear = True
            if len(confirmed_sl) >= 2:
                prev_sl = lo[confirmed_sl[-2]]
                if prior_sl_price > prev_sl:   # prior low was higher → uptrend
                    mss_bear = True

    return {
        "bos_bullish": bos_bull,
        "bos_bearish": bos_bear,
        "mss_bullish": mss_bull,
        "mss_bearish": mss_bear,
    }


def _detect_liquidity_sweeps(hi, lo, cl, swing_w: int = 3) -> dict:
    """
    Liquidity sweeps: price wicks through a swing level then reverses.
    Bullish sweep: wick below swing low, close above it within 1-2 bars.
    Bearish sweep: wick above swing high, close below it within 1-2 bars.
    Returns {"bullish_sweep": bool, "bearish_sweep": bool}.
    """
    n = len(cl)
    if n < 10:
        return {"bullish_sweep": False, "bearish_sweep": False}

    sh_idx = _swing_highs(hi, swing_w)
    sl_idx = _swing_lows(lo, swing_w)

    bullish_sweep = False
    bearish_sweep = False

    # Check last 10 bars for sweeps
    for i in range(max(1, n - 10), n):
        # Bullish sweep: bar wicked below swing low but closed above
        for sl_i in sl_idx:
            if sl_i >= i - 3 or sl_i < 0:
                continue
            level = lo[sl_i]
            if lo[i] < level and cl[i] > level:
                bullish_sweep = True
                break

        # Bearish sweep: bar wicked above swing high but closed below
        for sh_i in sh_idx:
            if sh_i >= i - 3 or sh_i < 0:
                continue
            level = hi[sh_i]
            if hi[i] > level and cl[i] < level:
                bearish_sweep = True
                break

    return {"bullish_sweep": bullish_sweep, "bearish_sweep": bearish_sweep}


# ── Zone scoring ───────────────────────────────────────────────────────────────

def _score_zone_raw(
    displacement: float,
    atr: float,
    vol_ratio: float,
    body_pct: float,
    retest_count: int,
    bars_ago: int,
    has_fvg: bool = False,
    ob_present: bool = False,
) -> float:
    """
    Raw zone score (0–12 before confluence).
    Points breakdown:
      Displacement (0-3) + Volume (0-2) + Body strength (0-1)
      + FVG (0-1.5) + Order Block (0-1) + Freshness (0-1.5) + Recency (0-1)
    """
    s = 0.0

    # 1. Displacement magnitude relative to ATR (0-3)
    d = displacement / atr if atr > 0 else 0
    if d >= 3.0:   s += 3.0
    elif d >= 2.0: s += 2.0
    elif d >= 1.5: s += 1.5
    elif d >= 1.0: s += 1.0
    else:          s += 0.5

    # 2. Volume confirmation (0-2)
    if vol_ratio >= 2.0:   s += 2.0
    elif vol_ratio >= 1.5: s += 1.5
    elif vol_ratio >= 1.2: s += 1.0
    elif vol_ratio >= 0.9: s += 0.5

    # 3. Body strength (0-1)
    if body_pct >= 0.75:   s += 1.0
    elif body_pct >= 0.55: s += 0.5

    # 4. Fair Value Gap (0-1.5)
    if has_fvg:
        s += 1.5

    # 5. Order Block (0-1)
    if ob_present:
        s += 1.0

    # 6. Freshness (0-1.5) — first retest is highest probability
    if retest_count == 1:   s += 1.5    # first retest — institutions defend
    elif retest_count == 0: s += 1.0    # untested — high probability
    elif retest_count == 2: s += 0.5    # second touch — weakening

    # 7. Recency (0-1) — fresh zones are more reliable
    if bars_ago <= 8:    s += 1.0
    elif bars_ago <= 15: s += 0.75
    elif bars_ago <= 30: s += 0.4
    else:                s += 0.1

    return round(s, 2)


def _raw_to_grade(raw: float) -> Optional[str]:
    """Convert raw score to letter grade. Returns None if zone is too weak."""
    if raw >= 9.0:  return "A+"
    if raw >= 7.0:  return "A"
    if raw >= 5.0:  return "B+"
    if raw >= 3.5:  return "B"
    return None


def _apply_confluence(zone: dict, stock_data: Optional[dict]) -> tuple[float, list]:
    """
    Apply confluence bonuses to zone score.
    Returns (bonus_points, confluence_factors_list).
    """
    if not stock_data:
        return 0.0, []

    bonus   = 0.0
    factors = []
    z_type  = zone["zone_type"]
    price   = stock_data.get("current_price") or 0

    # EMA alignment
    pct20 = stock_data.get("pct_from_ema20")
    pct50 = stock_data.get("pct_from_ema50")
    zone_mid = (zone["zone_top"] + zone["zone_bottom"]) / 2

    if pct20 is not None:
        if z_type == "demand" and -4 <= pct20 <= 3:
            bonus += 0.5; factors.append("Near 20 EMA")
        elif z_type == "supply" and -3 <= pct20 <= 4:
            bonus += 0.5; factors.append("Near 20 EMA resistance")

    if pct50 is not None:
        if z_type == "demand" and -6 <= pct50 <= 3:
            bonus += 0.5; factors.append("Near 50 EMA")
        elif z_type == "supply" and -3 <= pct50 <= 6:
            bonus += 0.5; factors.append("Near 50 EMA resistance")

    # VWAP alignment
    vwap = stock_data.get("vwap")
    if vwap and price:
        vwap_pct = (price - vwap) / vwap * 100
        if abs(vwap_pct) <= 1.5:
            bonus += 0.3; factors.append("Near VWAP")
        elif z_type == "demand" and vwap_pct > 0:
            bonus += 0.2; factors.append("Above VWAP")

    # HTF trend alignment
    daily_trend = stock_data.get("daily_trend") or ""
    if z_type == "demand" and daily_trend in ("Bullish", "Bullish Lean"):
        bonus += 0.5; factors.append("HTF bullish trend")
    elif z_type == "supply" and daily_trend in ("Bearish", "Bearish Lean"):
        bonus += 0.5; factors.append("HTF bearish trend")

    # Market structure
    if z_type == "demand":
        if stock_data.get("daily_hh_hl"):
            bonus += 0.3; factors.append("Daily HH/HL structure")
        if stock_data.get("h4_hh_hl"):
            bonus += 0.2; factors.append("4H structure bullish")

    # Sector / QQQ context from market_temp (if available)
    qqq_above_ema = stock_data.get("qqq_above_ema")    # bool — injected externally
    if qqq_above_ema and z_type == "demand":
        bonus += 0.3; factors.append("QQQ bullish")

    # Relative volume
    rvol = stock_data.get("rel_volume") or 1.0
    if rvol >= 2.0:
        bonus += 0.5; factors.append(f"High RVOL ({rvol:.1f}x)")
    elif rvol >= 1.5:
        bonus += 0.3; factors.append(f"Above-avg volume ({rvol:.1f}x)")

    # Catalyst
    cat_score = stock_data.get("catalyst_score") or 0
    if cat_score >= 7:
        bonus += 0.5; factors.append("Strong catalyst")
    elif cat_score >= 5:
        bonus += 0.3; factors.append("Catalyst present")

    # Fibonacci alignment (zone mid within 2% of a fib level)
    for fib_key, fib_label in [
        ("fib_618", "61.8% fib"), ("fib_50", "50% fib"),
        ("fib_382", "38.2% fib"), ("fib_705", "70.5% fib"),
    ]:
        fib_val = stock_data.get(fib_key)
        if fib_val and abs(zone_mid - fib_val) / fib_val <= 0.025:
            bonus += 0.5; factors.append(f"Aligned with {fib_label}"); break

    # Liquidity sweep / BOS flags already embedded in zone
    if zone.get("has_liq_sweep"):
        bonus += 0.5; factors.append("Liquidity sweep")
    if zone.get("has_bos") and z_type == "demand":
        bonus += 0.5; factors.append("BOS confirmed")

    return round(bonus, 2), factors


# ── Supply and demand zone detection ──────────────────────────────────────────

def _detect_supply_zones(op, hi, lo, cl, vol, atr, bull_fvgs, bear_fvgs) -> list:
    """
    Institutional supply zone detection.

    Pattern: Price rallied hard → distribution top → sharp bearish displacement.
    Zone: wick high of formation candle → body base (where sellers took control).
    """
    n = len(cl)
    if n < ZONE_T["min_bars"]:
        return []

    avg_vol = sum(vol) / n
    zones   = []

    # Pre-compute bearish FVG positions for fast lookup
    bear_fvg_set = {fvg["bar"] for fvg in bear_fvgs}

    for i in range(5, n - 1):
        # ── Displacement bar must be bearish with strong body ──────────────────
        if cl[i] >= op[i]:
            continue
        bar_range = hi[i] - lo[i]
        if bar_range <= 0:
            continue
        body      = op[i] - cl[i]
        body_pct  = body / bar_range
        if body_pct < ZONE_T["impulse_body_pct"]:
            continue
        move = body / op[i] if op[i] > 0 else 0
        if move < ZONE_T["impulse_move_pct"]:
            continue
        displacement = op[i] - cl[i]
        if displacement < atr * ZONE_T["impulse_atr_mult"]:
            continue
        vol_ratio = vol[i] / avg_vol if avg_vol > 0 else 1.0
        if vol_ratio < ZONE_T["impulse_vol_min"]:
            continue

        # ── Need prior bullish context ─────────────────────────────────────────
        prior_bull = sum(1 for k in range(max(0, i - 5), i) if cl[k] > op[k])
        if prior_bull < 1:
            continue

        # ── Formation candle: last bullish bar before the reversal ────────────
        form_idx = None
        for j in range(i - 1, max(i - 4, -1), -1):
            if j >= 0 and cl[j] > op[j]:
                form_idx = j
                break
        if form_idx is None:
            form_idx = max(0, i - 1)

        # ── Zone bounds ───────────────────────────────────────────────────────
        zone_top    = max(hi[form_idx], op[i])    # wick high of formation or displacement open
        zone_bottom = min(op[form_idx], cl[form_idx])  # body base of formation

        if zone_top <= zone_bottom or zone_bottom <= 0:
            continue
        width_pct = (zone_top - zone_bottom) / zone_bottom
        if width_pct > ZONE_T["max_zone_width_pct"] or width_pct < 0.001:
            continue

        # ── Invalidation: any future close above zone_top ─────────────────────
        if any(cl[k] > zone_top for k in range(i + 1, n)):
            continue

        # ── Retest count ──────────────────────────────────────────────────────
        retest_count = sum(
            1 for k in range(i + 1, n)
            if zone_bottom * 0.995 <= hi[k] <= zone_top * 1.005
        )

        # ── Order block: last bullish candle in look-back ─────────────────────
        ob_present = any(
            cl[j] > op[j]
            for j in range(max(0, i - ZONE_T["ob_lookback"]), i)
        )

        # ── FVG near the zone ─────────────────────────────────────────────────
        has_fvg = any(
            k in bear_fvg_set and (
                bear_fvgs[next(x for x, f in enumerate(bear_fvgs) if f["bar"] == k)]["bottom"] <= zone_top
            )
            for k in range(max(0, i - 3), min(n, i + 3))
        ) if bear_fvgs else False

        raw = _score_zone_raw(
            displacement=displacement, atr=atr, vol_ratio=vol_ratio,
            body_pct=body_pct, retest_count=retest_count,
            bars_ago=n - 1 - i, has_fvg=has_fvg, ob_present=ob_present,
        )
        if _raw_to_grade(raw) is None:
            continue

        zones.append({
            "zone_type":        "supply",
            "zone_top":         round(zone_top, 4),
            "zone_bottom":      round(zone_bottom, 4),
            "raw_score":        raw,
            "vol_ratio":        round(vol_ratio, 2),
            "displacement_atr": round(displacement / atr, 2) if atr > 0 else 0,
            "body_pct":         round(body_pct, 2),
            "retest_count":     retest_count,
            "is_fresh":         retest_count <= 1,
            "bars_ago":         n - 1 - i,
            "has_fvg":          has_fvg,
            "ob_present":       ob_present,
            "has_liq_sweep":    False,   # updated after liquidity sweep scan
            "has_bos":          False,
        })

    return zones


def _detect_demand_zones(op, hi, lo, cl, vol, atr, bull_fvgs, bear_fvgs) -> list:
    """
    Institutional demand zone detection.

    Pattern: Consolidation/base/compression → strong bullish displacement.
    Zone: wick low of formation candle → body base (where buyers took control).
    """
    n = len(cl)
    if n < ZONE_T["min_bars"]:
        return []

    avg_vol = sum(vol) / n
    zones   = []

    bull_fvg_set = {fvg["bar"] for fvg in bull_fvgs}

    for i in range(5, n - 1):
        # ── Displacement bar must be strongly bullish ──────────────────────────
        if cl[i] <= op[i]:
            continue
        bar_range = hi[i] - lo[i]
        if bar_range <= 0:
            continue
        body      = cl[i] - op[i]
        body_pct  = body / bar_range
        if body_pct < ZONE_T["impulse_body_pct"]:
            continue
        move = body / op[i] if op[i] > 0 else 0
        if move < ZONE_T["impulse_move_pct"]:
            continue
        displacement = cl[i] - op[i]
        if displacement < atr * ZONE_T["impulse_atr_mult"]:
            continue
        vol_ratio = vol[i] / avg_vol if avg_vol > 0 else 1.0
        if vol_ratio < ZONE_T["impulse_vol_min"]:
            continue

        # ── Formation candle: last bearish/doji bar before impulse ────────────
        form_idx = None
        for j in range(i - 1, max(i - 4, -1), -1):
            if j >= 0 and cl[j] <= op[j]:
                form_idx = j
                break
        if form_idx is None:
            form_idx = max(0, i - 1)

        # ── Zone bounds ───────────────────────────────────────────────────────
        zone_bottom = min(lo[form_idx], op[i])    # wick low or displacement open
        zone_top    = max(op[form_idx], cl[form_idx])  # body base/top of formation

        if zone_top <= zone_bottom or zone_bottom <= 0:
            continue
        width_pct = (zone_top - zone_bottom) / zone_bottom
        if width_pct > ZONE_T["max_zone_width_pct"] or width_pct < 0.001:
            continue

        # ── Invalidation: any future close below zone_bottom ─────────────────
        if any(cl[k] < zone_bottom for k in range(i + 1, n)):
            continue

        # ── Retest count ──────────────────────────────────────────────────────
        retest_count = sum(
            1 for k in range(i + 1, n)
            if zone_bottom * 0.995 <= lo[k] <= zone_top * 1.005
        )

        # ── Order block: last bearish candle in look-back ─────────────────────
        ob_present = any(
            cl[j] < op[j]
            for j in range(max(0, i - ZONE_T["ob_lookback"]), i)
        )

        # ── FVG near the zone ─────────────────────────────────────────────────
        has_fvg = any(k in bull_fvg_set for k in range(max(0, i - 3), min(n, i + 3))) if bull_fvgs else False

        raw = _score_zone_raw(
            displacement=displacement, atr=atr, vol_ratio=vol_ratio,
            body_pct=body_pct, retest_count=retest_count,
            bars_ago=n - 1 - i, has_fvg=has_fvg, ob_present=ob_present,
        )
        if _raw_to_grade(raw) is None:
            continue

        zones.append({
            "zone_type":        "demand",
            "zone_top":         round(zone_top, 4),
            "zone_bottom":      round(zone_bottom, 4),
            "raw_score":        raw,
            "vol_ratio":        round(vol_ratio, 2),
            "displacement_atr": round(displacement / atr, 2) if atr > 0 else 0,
            "body_pct":         round(body_pct, 2),
            "retest_count":     retest_count,
            "is_fresh":         retest_count <= 1,
            "bars_ago":         n - 1 - i,
            "has_fvg":          has_fvg,
            "ob_present":       ob_present,
            "has_liq_sweep":    False,
            "has_bos":          False,
        })

    return zones


# ── Zone selection helpers ─────────────────────────────────────────────────────

def _select_best_zones(zones: list, price: float, zone_type: str, max_keep: int = 5) -> list:
    """
    Score zones by combined grade + proximity + freshness and return top N.
    Supply zones: prefer those closest above price.
    Demand zones: prefer those closest below price.
    """
    if not zones or not price:
        return []

    grade_pts = {"A+": 4, "A": 3, "B+": 2, "B": 1}

    def _priority(z):
        g   = grade_pts.get(z.get("grade", "B"), 0)
        fresh = 2 if z.get("retest_count", 0) == 1 else (1 if z.get("retest_count", 0) == 0 else 0)
        mid = (z["zone_top"] + z["zone_bottom"]) / 2
        if zone_type == "supply":
            dist = (mid - price) / price if mid > price else 0
        else:
            dist = (price - mid) / price if mid < price else 0
        prox = max(0, 1 - dist * 10)   # closer = higher
        return g * 3 + fresh * 2 + prox

    sorted_z = sorted(zones, key=_priority, reverse=True)
    return sorted_z[:max_keep]


def _nearest_above(zones: list, price: float) -> Optional[dict]:
    """Return the nearest zone whose bottom is above price."""
    candidates = [z for z in zones if z["zone_bottom"] > price * 0.99]
    if not candidates:
        return None
    return min(candidates, key=lambda z: z["zone_bottom"])


def _nearest_below(zones: list, price: float) -> Optional[dict]:
    """Return the nearest zone whose top is below price."""
    candidates = [z for z in zones if z["zone_top"] < price * 1.01]
    if not candidates:
        return None
    return max(candidates, key=lambda z: z["zone_top"])


def _zone_containing(zones: list, price: float) -> Optional[dict]:
    """Return first zone that contains price."""
    for z in zones:
        if z["zone_bottom"] <= price <= z["zone_top"]:
            return z
    return None


# ── AI setup classification ────────────────────────────────────────────────────

def _classify_setup(
    best_demand: Optional[dict],
    best_supply: Optional[dict],
    smart_money: dict,
    stock_data: Optional[dict],
) -> tuple[str, list, int]:
    """
    Classify the current AI setup and build the reason list.
    Returns (setup_type, reasons, probability).
    """
    price       = (stock_data or {}).get("current_price") or 0
    daily_trend = (stock_data or {}).get("daily_trend") or ""
    vwap        = (stock_data or {}).get("vwap")
    rvol        = (stock_data or {}).get("rel_volume") or 1.0
    cat_score   = (stock_data or {}).get("catalyst_score") or 0
    pct20       = (stock_data or {}).get("pct_from_ema20")
    pct50       = (stock_data or {}).get("pct_from_ema50")
    swing_stype = (stock_data or {}).get("swing_setup_type") or ""
    hh_hl       = bool((stock_data or {}).get("daily_hh_hl"))

    bullish_mkt = daily_trend in ("Bullish", "Bullish Lean")
    bearish_mkt = daily_trend in ("Bearish", "Bearish Lean")

    in_demand = bool(best_demand and best_demand.get("zone_bottom", 0) <= price <= best_demand.get("zone_top", 0))
    in_supply = bool(best_supply and best_supply.get("zone_bottom", 0) <= price <= best_supply.get("zone_top", 0))

    app_demand = (
        not in_demand and best_demand and price > 0 and
        (best_demand["zone_top"] - price) / price <= ZONE_T["approach_pct"]
    ) if best_demand else False

    app_supply = (
        not in_supply and best_supply and price > 0 and
        (price - best_supply["zone_bottom"]) / price <= ZONE_T["approach_pct"]
    ) if best_supply else False

    # ── Setup classification ──────────────────────────────────────────────────
    setup = "Watch"

    if in_demand and bullish_mkt:
        grade = best_demand.get("grade", "B")
        if grade in ("A+", "A") and best_demand.get("retest_count", 0) <= 1:
            if "Continuation" in swing_stype or "Bull Flag" in swing_stype:
                setup = "Continuation Setup"
            else:
                setup = "Pullback Setup"
        else:
            setup = "Pullback Setup"

    elif in_supply and not bullish_mkt:
        setup = "Reversal Setup"

    elif in_supply and bullish_mkt:
        setup = "Failed Breakout"

    elif smart_money.get("bos_bullish") and bullish_mkt:
        setup = "Breakout Setup"

    elif smart_money.get("mss_bullish"):
        setup = "Trend Continuation"

    elif smart_money.get("bullish_sweep") and bullish_mkt:
        setup = "Breakout Setup"

    elif app_demand and bullish_mkt:
        setup = "Pullback Setup"

    elif app_supply:
        setup = "Potential Reversal"

    elif vwap and price and abs(price - vwap) / vwap <= 0.012:
        setup = "VWAP Reclaim" if bullish_mkt else "VWAP Rejection"

    elif "Continuation" in swing_stype or "Bull Flag" in swing_stype:
        setup = "Continuation Setup"

    elif "Extended" in swing_stype or "Chase" in swing_stype:
        setup = "Exhaustion Move"

    elif "Pullback" in swing_stype:
        setup = "Pullback Setup"

    # ── Reason builder ────────────────────────────────────────────────────────
    reasons = []

    if best_demand and in_demand:
        grade = best_demand.get("grade", "B")
        reasons.append(f"{grade} Institutional demand zone")
        rc = best_demand.get("retest_count", 0)
        if rc == 0:
            reasons.append("Fresh zone — first test incoming")
        elif rc == 1:
            reasons.append("First retest — highest probability entry")
        if best_demand.get("has_fvg"):
            reasons.append("Bullish Fair Value Gap in zone")
        if best_demand.get("ob_present"):
            reasons.append("Demand Order Block present")

    elif best_demand and app_demand:
        reasons.append(f"Approaching {best_demand.get('grade', 'B')} demand zone")

    if best_supply and in_supply:
        reasons.append(f"Inside {best_supply.get('grade', 'B')} supply zone — risk off")
    elif best_supply and app_supply:
        reasons.append(f"Approaching {best_supply.get('grade', 'B')} supply zone — caution")

    if smart_money.get("fvg_bullish"):
        reasons.append("Bullish FVG present")
    if smart_money.get("fvg_bearish"):
        reasons.append("Bearish FVG overhead")
    if smart_money.get("bos_bullish"):
        reasons.append("Break of structure (bullish)")
    if smart_money.get("mss_bullish"):
        reasons.append("Market structure shift bullish")
    if smart_money.get("bullish_sweep"):
        reasons.append("Liquidity sweep below lows")

    if bullish_mkt:
        reasons.append("Daily trend bullish")
    if hh_hl:
        reasons.append("Higher highs / higher lows structure")

    if pct20 is not None and -2.5 <= pct20 <= 1.5:
        reasons.append("At 20 EMA support")
    elif pct50 is not None and -4 <= pct50 <= 2:
        reasons.append("At 50 EMA support")

    if vwap and price:
        vwap_pct = (price - vwap) / vwap * 100
        if 0 <= vwap_pct <= 1.5:
            reasons.append("Holding above VWAP")

    if rvol >= 2.0:
        reasons.append(f"Strong RVOL ({rvol:.1f}x)")
    elif rvol >= 1.5:
        reasons.append(f"Above-avg volume ({rvol:.1f}x)")

    if cat_score >= 7:
        reasons.append("Strong catalyst")
    elif cat_score >= 5:
        reasons.append("Catalyst present")

    # ── Probability estimate ──────────────────────────────────────────────────
    prob = 45  # base

    if best_demand:
        prob += {"A+": 28, "A": 20, "B+": 13, "B": 6}.get(best_demand.get("grade", "B"), 0)
        if in_demand: prob += 8
        if best_demand.get("retest_count", 0) == 1: prob += 5

    if smart_money.get("fvg_bullish"):  prob += 5
    if smart_money.get("bos_bullish"):  prob += 5
    if smart_money.get("bullish_sweep"):prob += 4
    if smart_money.get("mss_bullish"):  prob += 4
    if bullish_mkt:  prob += 4
    if hh_hl:        prob += 3
    if rvol >= 1.5:  prob += 3
    if cat_score >= 6: prob += 4

    prob = min(95, max(30, prob))

    return setup, reasons[:10], prob   # cap reason list at 10


# ── yfinance bar fetch ─────────────────────────────────────────────────────────

def _fetch_bars(ticker: str, period: str, interval: str):
    """Fetch OHLCV bars silently. Returns DataFrame or None."""
    if not _YF_AVAILABLE:
        return None
    import logging as _lg
    yf_log = _lg.getLogger("yfinance")
    old = yf_log.level
    yf_log.setLevel(_lg.ERROR)
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval)
        return df if not df.empty else None
    except Exception as exc:
        logger.debug("Zone bars fetch failed %s %s/%s: %s", ticker, period, interval, exc)
        return None
    finally:
        yf_log.setLevel(old)


def _process_df(df, stock_data: Optional[dict], timeframe: str) -> tuple[list, list]:
    """
    Run full zone detection on a single OHLCV DataFrame.
    Returns (supply_zones, demand_zones) with grades applied.
    """
    if df is None or len(df) < ZONE_T["min_bars"]:
        return [], []

    op  = list(df["Open"].values.astype(float))
    hi  = list(df["High"].values.astype(float))
    lo  = list(df["Low"].values.astype(float))
    cl  = list(df["Close"].values.astype(float))
    vol = list(df["Volume"].values.astype(float))

    atr = _compute_atr(hi, lo, cl)

    bull_fvgs, bear_fvgs = _detect_fvg(hi, lo)

    supply_zones = _detect_supply_zones(op, hi, lo, cl, vol, atr, bull_fvgs, bear_fvgs)
    demand_zones = _detect_demand_zones(op, hi, lo, cl, vol, atr, bull_fvgs, bear_fvgs)

    # Apply confluence bonuses and assign final grades
    for z in supply_zones + demand_zones:
        bonus, factors = _apply_confluence(z, stock_data)
        final_score = z["raw_score"] + bonus
        grade = _raw_to_grade(final_score)
        z["final_score"]         = round(final_score, 2)
        z["grade"]               = grade or "B"
        z["confluence_bonus"]    = round(bonus, 2)
        z["confluence_factors"]  = factors
        z["timeframe"]           = timeframe

    # Filter to graded zones only and sort by final_score
    supply_zones = [z for z in supply_zones if _raw_to_grade(z["raw_score"]) is not None]
    demand_zones = [z for z in demand_zones if _raw_to_grade(z["raw_score"]) is not None]

    return supply_zones, demand_zones


# ── Zone location label ────────────────────────────────────────────────────────

def _location_label(
    price: float,
    in_demand: bool,
    in_supply: bool,
    nearest_supply: Optional[dict],
    nearest_demand: Optional[dict],
    smart_money: dict,
) -> str:
    if in_demand and not in_supply:
        return "IN DEMAND"
    if in_supply:
        return "IN SUPPLY"
    if smart_money.get("bos_bullish"):
        return "BREAKOUT — BOS"
    if smart_money.get("mss_bullish"):
        return "STRUCTURE SHIFT"
    if nearest_supply:
        dist = (nearest_supply["zone_bottom"] - price) / price if price > 0 else 1
        if 0 <= dist <= ZONE_T["approach_pct"]:
            return "APPROACHING SUPPLY"
        if price > nearest_supply["zone_top"]:
            return "ABOVE SUPPLY"
    if nearest_demand and price < nearest_demand["zone_bottom"]:
        return "BELOW DEMAND"
    return "BETWEEN ZONES"


# ── Public API ─────────────────────────────────────────────────────────────────

_EMPTY_RESULT: dict = {
    # V1 backward-compatible fields
    "nearest_supply_top":     None,
    "nearest_supply_bottom":  None,
    "nearest_demand_top":     None,
    "nearest_demand_bottom":  None,
    "distance_to_supply_pct": None,
    "distance_to_demand_pct": None,
    "zone_location":          "BETWEEN ZONES",
    "bullish_order_block":    None,
    "bearish_order_block":    None,
    "in_supply_zone":         False,
    "in_demand_zone":         False,
    # V2 institutional fields
    "zones_json":             None,
    "demand_zone_grade":      None,
    "supply_zone_grade":      None,
    "zone_ai_setup":          None,
    "zone_ai_reason":         None,
    "zone_probability":       None,
    "smart_money_json":       None,
    "fvg_bullish":            False,
    "fvg_bearish":            False,
}


def detect_zones(ticker: str, current_price: float, stock_data: Optional[dict] = None) -> dict:
    """
    Full institutional zone detection for *ticker*.

    Uses Daily (90d) bars as primary structural timeframe, supplemented
    by 1H bars (as a 4H proxy) for intraday precision.

    Returns a flat dict with all zone fields (backward-compatible with V1).
    New V2 fields: zones_json, demand_zone_grade, supply_zone_grade,
                   zone_ai_setup, zone_ai_reason, zone_probability,
                   smart_money_json, fvg_bullish, fvg_bearish.
    """
    out = dict(_EMPTY_RESULT)

    if not _YF_AVAILABLE or not current_price or current_price <= 0:
        return out

    # ── Fetch bars ──────────────────────────────────────────────────────────
    daily = _fetch_bars(ticker, "90d", "1d")
    h1    = _fetch_bars(ticker, "30d", "1h")    # 1H as 4H proxy

    # ── Detect zones per timeframe ──────────────────────────────────────────
    all_supply: list = []
    all_demand: list = []

    s_d, d_d = _process_df(daily, stock_data, "daily")
    s_h, d_h = _process_df(h1,    stock_data, "4h")

    all_supply.extend(s_d + s_h)
    all_demand.extend(d_d + d_h)

    # ── Smart money scan (daily bars) ───────────────────────────────────────
    smart_money: dict = {
        "fvg_bullish": False, "fvg_bearish": False,
        "bos_bullish": False, "bos_bearish": False,
        "mss_bullish": False, "mss_bearish": False,
        "bullish_sweep": False, "bearish_sweep": False,
        "order_block_bull": False, "order_block_bear": False,
    }

    if daily is not None and len(daily) >= ZONE_T["min_bars"]:
        hi  = list(daily["High"].values.astype(float))
        lo  = list(daily["Low"].values.astype(float))
        cl  = list(daily["Close"].values.astype(float))

        bull_fvgs, bear_fvgs = _detect_fvg(hi, lo)
        price = current_price

        # FVG near current price (within 5%)
        smart_money["fvg_bullish"] = any(
            f["bottom"] * 0.95 <= price <= f["top"] * 1.05 or price < f["bottom"] * 1.1
            for f in bull_fvgs[-5:]
        ) if bull_fvgs else False
        smart_money["fvg_bearish"] = any(
            f["bottom"] * 0.95 <= price <= f["top"] * 1.05 or price > f["top"] * 0.9
            for f in bear_fvgs[-5:]
        ) if bear_fvgs else False

        bos_mss  = _detect_bos_mss(hi, lo, cl)
        sweeps   = _detect_liquidity_sweeps(hi, lo, cl)
        smart_money.update(bos_mss)
        smart_money.update(sweeps)

        # Order block flags (V1 compat — nearest OB)
        ob_lb = ZONE_T["ob_lookback"]
        op_d  = list(daily["Open"].values.astype(float))
        for j in range(max(0, len(cl) - ob_lb - 1), len(cl) - 1):
            if cl[j] < op_d[j]:  smart_money["order_block_bull"] = True
            if cl[j] > op_d[j]:  smart_money["order_block_bear"] = True

    # ── Tag zones with BOS / liquidity sweep flags ──────────────────────────
    for z in all_demand:
        z["has_bos"]       = smart_money.get("bos_bullish", False)
        z["has_liq_sweep"] = smart_money.get("bullish_sweep", False)
    for z in all_supply:
        z["has_bos"]       = smart_money.get("bos_bearish", False)
        z["has_liq_sweep"] = smart_money.get("bearish_sweep", False)

    # ── Select best zones ───────────────────────────────────────────────────
    top_supply = _select_best_zones(all_supply, current_price, "supply", max_keep=5)
    top_demand = _select_best_zones(all_demand, current_price, "demand", max_keep=5)

    # ── In-zone detection ────────────────────────────────────────────────────
    cur_demand = _zone_containing(top_demand, current_price)
    cur_supply = _zone_containing(top_supply, current_price)
    in_demand  = cur_demand is not None
    in_supply  = cur_supply is not None

    nearest_sup = cur_supply or _nearest_above(top_supply, current_price)
    nearest_dem = cur_demand or _nearest_below(top_demand, current_price)

    # ── AI setup classification ──────────────────────────────────────────────
    best_demand = (cur_demand or nearest_dem) if top_demand else None
    best_supply = (cur_supply or nearest_sup) if top_supply else None

    # Re-apply confluence for best zones (may update factors)
    if best_demand:
        bonus, factors = _apply_confluence(best_demand, stock_data)
        best_demand["confluence_factors"] = list(set(
            best_demand.get("confluence_factors", []) + factors
        ))

    ai_setup, ai_reasons, ai_prob = _classify_setup(best_demand, best_supply, smart_money, stock_data)

    # ── Zone location label ──────────────────────────────────────────────────
    location = _location_label(current_price, in_demand, in_supply,
                               nearest_sup, nearest_dem, smart_money)

    # ── Build serialisable zone list ─────────────────────────────────────────
    zones_export = []
    for z in top_demand + top_supply:
        zones_export.append({
            "zone_type":           z["zone_type"],
            "grade":               z.get("grade", "B"),
            "zone_top":            round(z["zone_top"], 2),
            "zone_bottom":         round(z["zone_bottom"], 2),
            "final_score":         z.get("final_score", z.get("raw_score", 0)),
            "retest_count":        z.get("retest_count", 0),
            "is_fresh":            z.get("is_fresh", False),
            "has_fvg":             z.get("has_fvg", False),
            "ob_present":          z.get("ob_present", False),
            "has_bos":             z.get("has_bos", False),
            "has_liq_sweep":       z.get("has_liq_sweep", False),
            "vol_ratio":           z.get("vol_ratio", 1.0),
            "displacement_atr":    z.get("displacement_atr", 0),
            "bars_ago":            z.get("bars_ago", 0),
            "timeframe":           z.get("timeframe", "daily"),
            "confluence_factors":  z.get("confluence_factors", []),
            "confluence_bonus":    z.get("confluence_bonus", 0),
        })

    # ── V1 order block compat ────────────────────────────────────────────────
    bull_ob_dict = None
    bear_ob_dict = None
    if smart_money.get("order_block_bull") and nearest_dem:
        bull_ob_dict = {"low": nearest_dem["zone_bottom"], "high": nearest_dem["zone_top"]}
    if smart_money.get("order_block_bear") and nearest_sup:
        bear_ob_dict = {"low": nearest_sup["zone_bottom"], "high": nearest_sup["zone_top"]}

    # ── Assemble output dict ─────────────────────────────────────────────────
    out["in_demand_zone"]  = in_demand
    out["in_supply_zone"]  = in_supply
    out["zone_location"]   = location

    if nearest_sup:
        out["nearest_supply_bottom"]  = round(nearest_sup["zone_bottom"], 2)
        out["nearest_supply_top"]     = round(nearest_sup["zone_top"], 2)
        dist = (nearest_sup["zone_bottom"] - current_price) / current_price * 100
        out["distance_to_supply_pct"] = round(dist, 2)

    if nearest_dem:
        out["nearest_demand_bottom"]  = round(nearest_dem["zone_bottom"], 2)
        out["nearest_demand_top"]     = round(nearest_dem["zone_top"], 2)
        dist = (current_price - nearest_dem["zone_top"]) / current_price * 100
        out["distance_to_demand_pct"] = round(max(dist, 0), 2)

    if bull_ob_dict:
        out["bullish_order_block"] = json.dumps(bull_ob_dict)
    if bear_ob_dict:
        out["bearish_order_block"] = json.dumps(bear_ob_dict)

    # V2 fields
    out["zones_json"]      = json.dumps(zones_export) if zones_export else None
    out["demand_zone_grade"] = best_demand.get("grade") if best_demand else None
    out["supply_zone_grade"] = best_supply.get("grade") if best_supply else None
    out["zone_ai_setup"]   = ai_setup
    out["zone_ai_reason"]  = json.dumps(ai_reasons) if ai_reasons else None
    out["zone_probability"] = ai_prob
    out["smart_money_json"] = json.dumps(smart_money)
    out["fvg_bullish"]     = bool(smart_money.get("fvg_bullish"))
    out["fvg_bearish"]     = bool(smart_money.get("fvg_bearish"))

    return out


# ── Zone alert helper ──────────────────────────────────────────────────────────

def check_zone_alert_conditions(stock: dict) -> list:
    """
    Check if a stock's current price has triggered any zone alert conditions.
    Called from alerts.py — returns list of (alert_type, message, severity).
    """
    alerts = []
    ticker  = stock.get("ticker") or ""
    price   = stock.get("current_price") or 0
    grade   = stock.get("demand_zone_grade") or ""
    s_grade = stock.get("supply_zone_grade") or ""
    loc     = stock.get("zone_location") or ""
    prob    = stock.get("zone_probability") or 0

    if not ticker or not price:
        return []

    # Price entering fresh demand zone
    if stock.get("in_demand_zone") and grade in ("A+", "A"):
        rc = 0
        zones_str = stock.get("zones_json")
        if zones_str:
            try:
                for z in json.loads(zones_str):
                    if z.get("zone_type") == "demand" and z.get("grade") in ("A+", "A"):
                        rc = z.get("retest_count", 0)
                        break
            except Exception:
                pass
        fresh_str = "first retest" if rc == 1 else ("fresh zone" if rc == 0 else "")
        if fresh_str:
            msg = f"{ticker} entered {grade} demand zone — {fresh_str} | Prob {prob}%"
            alerts.append(("zone_demand_entry", msg, "high" if grade == "A+" else "medium"))

    # Price entering supply zone
    if stock.get("in_supply_zone") and s_grade in ("A+", "A"):
        msg = f"{ticker} at {s_grade} supply zone — watch for rejection"
        alerts.append(("zone_supply_entry", msg, "medium"))

    # Approaching A+ demand zone
    if loc == "APPROACHING SUPPLY" and s_grade == "A+":
        msg = f"{ticker} approaching A+ supply zone — potential reversal setup"
        alerts.append(("zone_approach_supply", msg, "low"))

    # BOS or MSS
    sm_str = stock.get("smart_money_json")
    if sm_str:
        try:
            sm = json.loads(sm_str)
            if sm.get("bos_bullish"):
                msg = f"{ticker} BREAK OF STRUCTURE — bullish BOS confirmed"
                alerts.append(("zone_bos", msg, "high"))
            if sm.get("mss_bullish"):
                msg = f"{ticker} MARKET STRUCTURE SHIFT bullish"
                alerts.append(("zone_mss", msg, "medium"))
        except Exception:
            pass

    return alerts


def zones_need_refresh(zones_fetched_at: Optional[str]) -> bool:
    """Return True when zone data is stale (older than ZONE_T['cache_minutes'])."""
    if not zones_fetched_at:
        return True
    try:
        from datetime import datetime
        fetched = datetime.fromisoformat(zones_fetched_at)
        elapsed = (datetime.now() - fetched).total_seconds() / 60
        return elapsed >= ZONE_T["cache_minutes"]
    except Exception:
        return True
