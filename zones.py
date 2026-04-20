"""
zones.py — Supply/Demand Zone and Order Block Detection

Detects institutional buyer/seller zones from Daily and 1H price data.
Uses yfinance for historical bars.

V1 rules (simple, explainable, no smart-money complexity):
  Demand zone  = small base/consolidation before a strong impulse UP
  Supply zone  = small base/consolidation before a strong impulse DOWN
  Bullish OB   = last down candle before the impulse up
  Bearish OB   = last up candle before the impulse down

Invalidation:
  Demand zone broken if any close went BELOW demand_bottom after formation.
  Supply zone broken if any close went ABOVE supply_top after formation.

Only the nearest active zones/OBs relative to current price are kept.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_YF_AVAILABLE = False
try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    pass


# ─── Tuning constants ─────────────────────────────────────────────────────────

ZONE_T = {
    # Base detection: a bar qualifies as "quiet" if its range is smaller
    # than this fraction of the preceding 10-bar rolling average range.
    "base_quiet_pct":   0.65,

    # Impulse confirmation: candle body must be > this fraction of its range.
    # Lower = more permissive; catches directional bars with moderate wicks.
    "impulse_body_pct": 0.45,

    # Impulse move: minimum % move (|close - open| / open) to count as impulsive.
    "impulse_move_pct": 0.010,   # 1.0% — daily bars move 1%+ on meaningful days

    # Volume: impulse candle volume vs rolling 10-bar average volume.
    # We only need "not unusually low" — exclude holiday/dead sessions.
    # 0.8 = must be at least 80% of the rolling 10-bar avg volume.
    "impulse_vol_mult": 0.80,

    # Approaching supply: price within this % below supply_bottom.
    "approach_pct":     0.03,    # 3%

    # Order block look-back: scan this many bars before the impulse for the OB candle.
    "ob_lookback":      4,

    # Minimum bars needed before zone detection begins.
    "min_bars":         20,

    # Zone cache TTL: refresh zone data after this many minutes.
    # Daily/4H structure doesn't change intraday — 60 min is sufficient.
    "cache_minutes":    60,
}


# ─── yfinance fetch helpers ───────────────────────────────────────────────────

def _fetch_bars(ticker: str, period: str, interval: str):
    """
    Fetch OHLCV bars from yfinance silently.
    Returns a DataFrame or None on failure.
    """
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


# ─── Zone detection (single timeframe) ───────────────────────────────────────

def _detect_zones_from_df(df) -> dict:
    """
    Scan a single OHLCV DataFrame for supply/demand zones and order blocks.

    Returns:
        {
            "demand": [(bottom, top), ...],   # active demand zones
            "supply": [(bottom, top), ...],   # active supply zones
            "bull_ob": [(low, high), ...],    # bullish order blocks
            "bear_ob": [(low, high), ...],    # bearish order blocks
        }
    All lists may be empty.
    """
    result = {"demand": [], "supply": [], "bull_ob": [], "bear_ob": []}

    if df is None or len(df) < ZONE_T["min_bars"]:
        return result

    hi  = df["High"].values.astype(float)
    lo  = df["Low"].values.astype(float)
    cl  = df["Close"].values.astype(float)
    op  = df["Open"].values.astype(float)
    vol = df["Volume"].values.astype(float)
    n   = len(df)

    for i in range(10, n - 1):
        # ── Rolling 10-bar average range AND volume (local context) ─────
        window_slice = slice(i - 10, i)
        avg_range = float((hi[window_slice] - lo[window_slice]).mean())
        if avg_range <= 0:
            continue
        avg_vol_local = float(vol[window_slice].mean())
        if avg_vol_local <= 0:
            avg_vol_local = 1.0

        bar_range = hi[i] - lo[i]

        # ── Is bar i a "quiet" base bar? ─────────────────────────────────
        if bar_range >= avg_range * ZONE_T["base_quiet_pct"]:
            continue   # too wide — not a consolidation bar

        # ── Is bar i+1 a strong impulse candle? ──────────────────────────
        ni = i + 1
        nb = abs(cl[ni] - op[ni])           # body size
        nr = hi[ni] - lo[ni]               # bar range
        nm = nb / (op[ni] + 1e-6)          # % move

        if nr <= 0:
            continue
        if nb / nr < ZONE_T["impulse_body_pct"]:
            continue   # weak-bodied candle — indecisive
        if nm < ZONE_T["impulse_move_pct"]:
            continue   # didn't move enough
        if vol[ni] < avg_vol_local * ZONE_T["impulse_vol_mult"]:
            continue   # volume not confirming (vs rolling avg, not period avg)

        bullish = cl[ni] > op[ni]
        bearish = cl[ni] < op[ni]

        if not bullish and not bearish:
            continue

        # ── Zone bounds = the quiet base bar ────────────────────────────
        z_bottom = lo[i]
        z_top    = hi[i]

        # ── Invalidation check ───────────────────────────────────────────
        # A zone is invalid if price later closed through it after formation.
        if ni + 1 < n:
            future_cl = cl[ni + 1:]
            if bullish and any(c < z_bottom for c in future_cl):
                continue   # demand zone busted
            if bearish and any(c > z_top for c in future_cl):
                continue   # supply zone busted

        # ── Order block: last opposing candle before the impulse ─────────
        ob_start = max(0, i - ZONE_T["ob_lookback"] + 1)

        if bullish:
            result["demand"].append((z_bottom, z_top))
            # Bullish OB = last DOWN candle in the look-back window
            for j in range(i, ob_start - 1, -1):
                if cl[j] < op[j]:
                    result["bull_ob"].append((lo[j], hi[j]))
                    break

        elif bearish:
            result["supply"].append((z_bottom, z_top))
            # Bearish OB = last UP candle in the look-back window
            for j in range(i, ob_start - 1, -1):
                if cl[j] > op[j]:
                    result["bear_ob"].append((lo[j], hi[j]))
                    break

    return result


# ─── Zone selection helpers ───────────────────────────────────────────────────

def _zone_containing(zones: list, price: float):
    """Return first (bottom, top) zone that contains price, or (None, None)."""
    for b, t in zones:
        if b <= price <= t:
            return b, t
    return None, None


def _nearest_above(zones: list, price: float):
    """Return (bottom, top) zone with lowest bottom strictly above price, or (None, None)."""
    candidates = [(b, t) for b, t in zones if b > price]
    if not candidates:
        return None, None
    return min(candidates, key=lambda z: z[0])


def _nearest_below(zones: list, price: float):
    """Return (bottom, top) zone with highest top strictly below price, or (None, None)."""
    candidates = [(b, t) for b, t in zones if t < price]
    if not candidates:
        return None, None
    return max(candidates, key=lambda z: z[1])


def _nearest_ob(obs: list, price: float, direction: str):
    """
    Return the nearest order block relative to price.
    direction='below': highest OB with top <= price*1.02 (bullish — below or at price)
    direction='above': lowest OB with bottom >= price*0.98 (bearish — above or at price)
    """
    if direction == "below":
        candidates = [(b, t) for b, t in obs if t <= price * 1.02]
        if not candidates:
            return None, None
        return max(candidates, key=lambda z: z[1])
    else:
        candidates = [(b, t) for b, t in obs if b >= price * 0.98]
        if not candidates:
            return None, None
        return min(candidates, key=lambda z: z[0])


# ─── Zone location label ──────────────────────────────────────────────────────

def _label(
    price: float,
    in_demand: bool,
    in_supply: bool,
    supply_bottom,
    demand_top,
    bull_ob: Optional[dict],
    bear_ob: Optional[dict],
) -> str:
    """
    Map price position into a plain-English zone label.

    Priority:
      1. IN DEMAND — inside a demand zone
      2. IN SUPPLY — inside a supply zone (price is at the supply base)
      3. IN BULLISH OB — inside a bullish order block
      4. IN BEARISH OB — inside a bearish order block
      5. APPROACHING SUPPLY — within 3% below supply bottom
      6. ABOVE SUPPLY — price already pushed above known supply top
      7. BELOW DEMAND — price broke below known demand bottom
      8. BETWEEN ZONES — everything else
    """
    if in_demand and not in_supply:
        return "IN DEMAND"
    if in_supply:
        return "IN SUPPLY"

    if bull_ob and bull_ob["low"] <= price <= bull_ob["high"]:
        return "IN BULLISH OB"
    if bear_ob and bear_ob["low"] <= price <= bear_ob["high"]:
        return "IN BEARISH OB"

    if supply_bottom is not None:
        dist = (supply_bottom - price) / price
        if 0 <= dist <= ZONE_T["approach_pct"]:
            return "APPROACHING SUPPLY"
        if price > supply_bottom:      # price pushed into / above supply zone
            return "ABOVE SUPPLY"

    if demand_top is not None and price < demand_top:
        return "BELOW DEMAND"

    return "BETWEEN ZONES"


# ─── Public API ───────────────────────────────────────────────────────────────

def detect_zones(ticker: str, current_price: float) -> dict:
    """
    Detect supply/demand zones and order blocks for *ticker*.

    Uses Daily (90 d) bars as the primary structural timeframe, supplemented
    by 1H bars (as a 4H proxy — yfinance doesn't offer native 4H) for
    intraday-level precision.

    Returns a flat dict with all zone fields.  All fields are present even
    when no zones are found (None / False / "BETWEEN ZONES" as defaults).
    """
    empty: dict = {
        "nearest_supply_top":     None,
        "nearest_supply_bottom":  None,
        "nearest_demand_top":     None,
        "nearest_demand_bottom":  None,
        "distance_to_supply_pct": None,
        "distance_to_demand_pct": None,
        "zone_location":          "BETWEEN ZONES",
        "bullish_order_block":    None,   # JSON string when present
        "bearish_order_block":    None,   # JSON string when present
        "in_supply_zone":         False,
        "in_demand_zone":         False,
    }

    if not _YF_AVAILABLE or not current_price or current_price <= 0:
        return empty

    # ── Fetch bars ────────────────────────────────────────────────────────
    daily = _fetch_bars(ticker, "90d", "1d")
    h1    = _fetch_bars(ticker, "60d", "1h")   # 1H bars; use as 4H proxy

    all_demand:  list = []
    all_supply:  list = []
    all_bull_ob: list = []
    all_bear_ob: list = []

    for df in [daily, h1]:
        z = _detect_zones_from_df(df)
        all_demand.extend(z["demand"])
        all_supply.extend(z["supply"])
        all_bull_ob.extend(z["bull_ob"])
        all_bear_ob.extend(z["bear_ob"])

    if not all_demand and not all_supply and not all_bull_ob and not all_bear_ob:
        return empty

    # ── Price in zone? ────────────────────────────────────────────────────
    db, dt = _zone_containing(all_demand, current_price)
    sb, st = _zone_containing(all_supply, current_price)
    in_demand = db is not None
    in_supply = sb is not None

    # ── Nearest supply above ──────────────────────────────────────────────
    sup_b, sup_t = _nearest_above(all_supply, current_price)

    # ── Nearest demand below ──────────────────────────────────────────────
    dem_b, dem_t = _nearest_below(all_demand, current_price)

    # ── Nearest OBs ───────────────────────────────────────────────────────
    bull_b, bull_t = _nearest_ob(all_bull_ob, current_price, "below")
    bear_b, bear_t = _nearest_ob(all_bear_ob, current_price, "above")

    bull_ob_dict = {"low": round(bull_b, 2), "high": round(bull_t, 2)} if bull_b is not None else None
    bear_ob_dict = {"low": round(bear_b, 2), "high": round(bear_t, 2)} if bear_b is not None else None

    # ── Zone location label ───────────────────────────────────────────────
    location = _label(
        price=current_price,
        in_demand=in_demand,
        in_supply=in_supply,
        supply_bottom=sup_b,
        demand_top=dem_t,
        bull_ob=bull_ob_dict,
        bear_ob=bear_ob_dict,
    )

    # ── Build result ──────────────────────────────────────────────────────
    out = dict(empty)
    out["in_demand_zone"] = in_demand
    out["in_supply_zone"] = in_supply
    out["zone_location"]  = location

    if sup_b is not None:
        out["nearest_supply_bottom"]  = round(sup_b, 2)
        out["nearest_supply_top"]     = round(sup_t, 2)
        out["distance_to_supply_pct"] = round((sup_b - current_price) / current_price * 100, 2)

    if dem_t is not None:
        out["nearest_demand_bottom"]  = round(dem_b, 2)
        out["nearest_demand_top"]     = round(dem_t, 2)
        out["distance_to_demand_pct"] = round((current_price - dem_t) / current_price * 100, 2)

    if bull_ob_dict:
        out["bullish_order_block"] = json.dumps(bull_ob_dict)
    if bear_ob_dict:
        out["bearish_order_block"] = json.dumps(bear_ob_dict)

    return out


def zones_need_refresh(zones_fetched_at: Optional[str]) -> bool:
    """
    Return True if zone data is stale (older than ZONE_T["cache_minutes"]).
    Zones are derived from daily/4H bars — no need to recalculate every 15 s.
    """
    if not zones_fetched_at:
        return True
    try:
        from datetime import datetime
        fetched = datetime.fromisoformat(zones_fetched_at)
        elapsed = (datetime.now() - fetched).total_seconds() / 60
        return elapsed >= ZONE_T["cache_minutes"]
    except Exception:
        return True
