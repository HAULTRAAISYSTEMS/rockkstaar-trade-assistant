"""
mock_data.py — Stock data provider for the Rockkstaar Swing Trade Assistant.

MOCK_STOCKS contains swing-trading context templates for the primary watchlist
(NVDA, META, MRVL, AMZN, MU, INTC).  generate_stock_data() overlays live
price/volume/EMA/zone data from yfinance on top, falling back to mock values
when live data is unavailable.

Swing scoring pipeline order:
  1. Base template (trade_bias, catalyst context)
  2. Live price + volume  (data_fetcher.fetch_live_data)
  3. Daily EMA + Fibonacci  (data_fetcher.fetch_swing_data)
  4. Supply/demand zones  (zones.detect_zones)
  5. Catalyst score  (scoring.compute_catalyst_score)
  6. Swing score  (scoring.compute_swing_score)
  7. Swing setup type  (scoring.compute_swing_setup_type)
  8. Swing status  (scoring.compute_swing_status)
  9. Trade plan levels  (scoring.compute_swing_trade_plan)
"""

import json
from datetime import datetime
from data_fetcher import fetch_live_data, fetch_swing_data, swing_data_needs_refresh
from news_fetcher import fetch_headlines, needs_refresh, CATALYST_CATEGORIES
from scoring import (
    compute_catalyst_score,
    compute_swing_score,
    compute_swing_setup_type,
    compute_swing_status,
    compute_swing_trade_plan,
    SWING_SETUP_TYPES,
)
from zones import detect_zones, zones_need_refresh


# ─── Swing field defaults ─────────────────────────────────────────────────────

def _swing_defaults(data: dict) -> None:
    """Ensure all swing analysis fields exist with safe defaults."""
    data.setdefault("ema_20_daily",           None)
    data.setdefault("ema_50_daily",           None)
    data.setdefault("ema_200_daily",          None)
    data.setdefault("pct_from_ema20",         None)
    data.setdefault("pct_from_ema50",         None)
    data.setdefault("daily_trend",            "Neutral")
    data.setdefault("daily_hh_hl",            False)
    data.setdefault("daily_lh_ll",            False)
    data.setdefault("fib_high",               None)
    data.setdefault("fib_low",                None)
    data.setdefault("fib_50",                 None)
    data.setdefault("fib_618",                None)
    # 4H / 15m fields (from extended fetch_swing_data)
    data.setdefault("h4_trend",               "Neutral")
    data.setdefault("h4_ema20",               None)
    data.setdefault("h4_ema50",               None)
    data.setdefault("h4_hh_hl",               False)
    data.setdefault("m15_higher_low",         False)
    data.setdefault("m15_confirmation",       0)
    data.setdefault("swing_score",            1)
    data.setdefault("swing_reason",           None)
    data.setdefault("swing_confidence",       "Low")
    data.setdefault("swing_setup_type",       "No Setup")
    data.setdefault("swing_status",           "NOT ENOUGH EDGE")
    data.setdefault("entry_zone_low",         None)
    data.setdefault("entry_zone_high",        None)
    data.setdefault("stop_level",             None)
    data.setdefault("target_1",               None)
    data.setdefault("target_2",               None)
    data.setdefault("risk_reward",            None)
    data.setdefault("swing_data_fetched_at",  None)


def _zone_defaults(data: dict) -> None:
    """Ensure all zone fields exist in *data* with safe defaults."""
    data.setdefault("nearest_supply_top",     None)
    data.setdefault("nearest_supply_bottom",  None)
    data.setdefault("nearest_demand_top",     None)
    data.setdefault("nearest_demand_bottom",  None)
    data.setdefault("distance_to_supply_pct", None)
    data.setdefault("distance_to_demand_pct", None)
    data.setdefault("zone_location",          "BETWEEN ZONES")
    data.setdefault("bullish_order_block",    None)
    data.setdefault("bearish_order_block",    None)
    data.setdefault("in_supply_zone",         False)
    data.setdefault("in_demand_zone",         False)
    data.setdefault("zones_fetched_at",       None)


# ---------------------------------------------------------------------------
# Watchlist templates — swing trading context
# Each entry contains editorial context; live data overrides prices/volumes.
# ---------------------------------------------------------------------------

MOCK_STOCKS = {
    "NVDA": {
        "current_price":  800.0,
        "prev_close":     790.0,
        "gap_pct":        1.27,
        "avg_volume":     42_000_000,
        "rel_volume":     1.1,
        "trade_bias":     "Long Bias",
        "catalyst_summary": (
            "AI infrastructure demand remains the dominant multi-year theme. "
            "Watching for a pullback to the 20 EMA or daily demand zone as a "
            "swing long entry. Strong uptrend — patient for the right level."
        ),
        "news_headlines": [
            "NVDA: Data center revenue tracking above estimates",
            "AI capex cycle showing no signs of slowing — analysts",
        ],
        "earnings_date": "2026-05-28",
    },
    "META": {
        "current_price":  540.0,
        "prev_close":     533.0,
        "gap_pct":        1.31,
        "avg_volume":     14_000_000,
        "rel_volume":     0.9,
        "trade_bias":     "Long Bias",
        "catalyst_summary": (
            "AI advertising monetization and Reality Labs progress support the "
            "bullish thesis. Clean daily uptrend. Looking for a base near the "
            "50% retracement or 20 EMA before adding."
        ),
        "news_headlines": [
            "Meta AI reaches 1 billion monthly active users",
            "META ad revenue tracking above Q1 plan",
        ],
        "earnings_date": "2026-04-30",
    },
    "MRVL": {
        "current_price":  72.0,
        "prev_close":     70.5,
        "gap_pct":        2.13,
        "avg_volume":     18_000_000,
        "rel_volume":     1.0,
        "trade_bias":     "Long Bias",
        "catalyst_summary": (
            "Custom silicon and data center networking growth story. "
            "Stock has pulled back from highs. Watching for demand zone "
            "test or 61.8% Fibonacci level as a swing long opportunity."
        ),
        "news_headlines": [
            "Marvell wins new custom ASIC design wins with hyperscalers",
            "MRVL data center segment expected to double YoY",
        ],
        "earnings_date": "2026-05-29",
    },
    "AMZN": {
        "current_price":  190.0,
        "prev_close":     188.0,
        "gap_pct":        1.06,
        "avg_volume":     38_000_000,
        "rel_volume":     0.85,
        "trade_bias":     "Long Bias",
        "catalyst_summary": (
            "AWS growth and advertising segment support the long thesis. "
            "Strong uptrend but currently extended above the 20 EMA. "
            "Watching for a proper pullback before adding any size."
        ),
        "news_headlines": [
            "AWS growth accelerates on enterprise AI workloads",
            "AMZN advertising revenue growing at 25%+ YoY",
        ],
        "earnings_date": "2026-05-01",
    },
    "MU": {
        "current_price":  95.0,
        "prev_close":     96.5,
        "gap_pct":        -1.55,
        "avg_volume":     22_000_000,
        "rel_volume":     1.2,
        "trade_bias":     "Long Bias",
        "catalyst_summary": (
            "HBM memory demand from AI accelerators is the key catalyst. "
            "Stock has been basing after a recent correction. "
            "Watching for a demand zone test or 20 EMA reclaim for a "
            "swing long entry with defined risk."
        ),
        "news_headlines": [
            "Micron HBM3E memory on track to ship to NVDA this quarter",
            "DRAM pricing stabilising — positive for MU margins",
        ],
        "earnings_date": "2026-06-25",
    },
    "INTC": {
        "current_price":  23.0,
        "prev_close":     23.8,
        "gap_pct":        -3.36,
        "avg_volume":     55_000_000,
        "rel_volume":     1.3,
        "trade_bias":     "Short Bias",
        "catalyst_summary": (
            "Foundry execution challenges and continued market share losses "
            "to AMD and TSMC. Below all major EMAs — bearish structure. "
            "Watching for dead-cat bounce into 20 EMA resistance as a "
            "short-sale opportunity."
        ),
        "news_headlines": [
            "Intel loses another server CPU contract to AMD EPYC",
            "INTC foundry delays push key process node to 2027",
        ],
        "earnings_date": "2026-04-24",
    },
}


def generate_stock_data(ticker: str) -> dict:
    """
    Return a fully-scored swing trade data dict for *ticker*.

    Data sourcing:
      MOCK_STOCKS templates → trade bias + catalyst context (editorial)
      yfinance (live)       → price, volume, EMAs, Fibonacci, zones
      Unknown tickers       → placeholder prices + live news

    Swing scoring pipeline:
      1. Fetch live price/volume (data_fetcher.fetch_live_data)
      2. Fetch swing structure (data_fetcher.fetch_swing_data) — EMAs, Fib
      3. Fetch supply/demand zones (zones.detect_zones)
      4. Compute catalyst score (scoring.compute_catalyst_score)
      5. Compute swing score, setup type, status, trade plan
    """
    ticker = ticker.upper().strip()

    if ticker in MOCK_STOCKS:
        data = dict(MOCK_STOCKS[ticker])
    else:
        prev_close = 100.0
        data = {
            "current_price":  prev_close,
            "prev_close":     prev_close,
            "gap_pct":        0.0,
            "avg_volume":     5_000_000,
            "rel_volume":     1.0,
            "trade_bias":     "Neutral",
            "catalyst_summary": "No template — live data will populate this.",
            "news_headlines": ["No headlines loaded — add a news API key."],
            "earnings_date":  None,
        }

    data["ticker"] = ticker

    # ── Step 1: Live price + volume ───────────────────────────────────────────
    live = fetch_live_data(ticker)
    if live:
        _price_fields = [
            "current_price", "prev_close", "prev_close_date", "gap_pct",
            "premarket_high", "premarket_low",
            "prev_day_high", "prev_day_low",
            "avg_volume", "rel_volume",
            "earnings_date",
            # intraday fields (kept for backward compatibility; not used in swing scoring)
            "vwap", "momentum_breakout", "candles_above_orb",
            "orb_hold", "trend_structure", "higher_highs", "higher_lows",
            "strong_candle_bodies", "price_above_vwap",
        ]
        for field in _price_fields:
            if live.get(field) is not None:
                data[field] = live[field]
        if live.get("earnings_date"):
            data["earnings_date"] = live["earnings_date"]
        # ORB / intraday phase (kept so the DB doesn't complain)
        data["orb_phase"] = live.get("orb_phase", "pre_market")
        data["orb_high"]  = None   # not relevant for swing trading
        data["orb_low"]   = None

        # Derive bias for unknown tickers from gap direction
        if ticker not in MOCK_STOCKS:
            gap = data.get("gap_pct", 0)
            data["trade_bias"] = (
                "Long Bias"  if gap >  3 else
                "Short Bias" if gap < -3 else
                "Neutral"
            )
    else:
        data.setdefault("orb_phase", "pre_market")
        data.setdefault("orb_high", None)
        data.setdefault("orb_low", None)

    # ── Step 2: Headlines ─────────────────────────────────────────────────────
    if not data.get("headlines_fetched_at"):
        news = fetch_headlines(ticker)
        if not data.get("catalyst_summary") or ticker not in MOCK_STOCKS:
            data["catalyst_summary"] = news.summary
        if news.headlines and "No headlines" not in news.headlines[0]:
            data["news_headlines"] = news.headlines
        data["catalyst_category"]    = json.dumps(news.categories)
        data["headlines_fetched_at"] = datetime.now().isoformat()

    # ── Step 3: Swing structure (EMAs, Fibonacci, daily trend) ───────────────
    if swing_data_needs_refresh(data.get("swing_data_fetched_at")):
        swing = fetch_swing_data(ticker)
        if swing:
            data.update(swing)
    _swing_defaults(data)

    # ── Step 4: Supply/demand zones ───────────────────────────────────────────
    if zones_need_refresh(data.get("zones_fetched_at")):
        current_px = data.get("current_price") or 0
        if current_px:
            zone_data = detect_zones(ticker, current_px)
            data.update(zone_data)
            data["zones_fetched_at"] = datetime.now().isoformat()
    _zone_defaults(data)

    # Intraday structure defaults (unused in swing scoring but kept for DB compat)
    data.setdefault("vwap",                 None)
    data.setdefault("momentum_breakout",    False)
    data.setdefault("candles_above_orb",    0)
    data.setdefault("orb_hold",             False)
    data.setdefault("trend_structure",      False)
    data.setdefault("higher_highs",         False)
    data.setdefault("higher_lows",          False)
    data.setdefault("strong_candle_bodies", False)
    data.setdefault("price_above_vwap",     False)
    data.setdefault("momentum_runner",      False)
    data.setdefault("structure_momentum_score", 0)

    # ── Step 5: Catalyst score ────────────────────────────────────────────────
    cat = compute_catalyst_score(data)
    data["catalyst_score"]      = cat.score
    data["catalyst_reason"]     = cat.explanation
    data["catalyst_confidence"] = cat.confidence

    # ── Step 6: Swing setup type (no score needed) ───────────────────────────
    data["swing_setup_type"] = compute_swing_setup_type(data)

    # ── Step 7: Trade plan levels (no score needed; produces risk_reward) ────
    # Must run BEFORE swing score so R:R is available as a scoring input.
    plan = compute_swing_trade_plan(data)
    data.update(plan)

    # ── Step 8: Swing score (uses risk_reward from step 7) ───────────────────
    swing_sr = compute_swing_score(data)
    data["swing_score"]      = swing_sr.score
    data["swing_reason"]     = swing_sr.explanation
    data["swing_confidence"] = swing_sr.confidence

    # ── Step 9: Swing status (uses swing_score from step 8) ──────────────────
    data["swing_status"] = compute_swing_status(data)

    # Legacy scoring fields (kept for DB schema compatibility but deprioritised)
    data.setdefault("momentum_score",      1)
    data.setdefault("momentum_reason",     "Swing mode — intraday momentum not tracked")
    data.setdefault("momentum_confidence", "Low")
    data.setdefault("order_block",         "Neutral")
    data.setdefault("entry_quality",       "Okay")
    data.setdefault("orb_status",          "NO_ORB")
    data.setdefault("orb_ready",           "NO")
    data.setdefault("exec_state",          "WAIT")
    data.setdefault("setup_score",         data["swing_score"])
    data.setdefault("setup_reason",        data["swing_reason"])
    data.setdefault("setup_confidence",    data["swing_confidence"])
    data.setdefault("setup_type",          data["swing_setup_type"])
    data.setdefault("entry_note",          None)
    data.setdefault("position_size",       "normal")

    # Serialize list fields for SQLite
    data["news_headlines"] = json.dumps(data.get("news_headlines", []))
    if isinstance(data.get("catalyst_category"), list):
        data["catalyst_category"] = json.dumps(data["catalyst_category"])
    data.setdefault("catalyst_category",    "[]")
    data.setdefault("headlines_fetched_at", None)
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return data


def live_refresh_stock(ticker: str, existing: dict) -> dict:
    """
    Re-evaluate swing scores with fresh market data.

    Refreshes on every call:
      - Live price + volume
      - Swing structure (EMAs/Fib) if stale (60-min cache)
      - Zone data if stale (60-min cache)
      - All swing scores and trade plan levels

    Refreshes time-gated (5-min cache):
      - Headlines + catalyst score
    """
    ticker = ticker.upper().strip()
    data   = dict(existing)

    # ── Headline refresh (time-gated) ─────────────────────────────────────────
    if needs_refresh(data.get("headlines_fetched_at")):
        news = fetch_headlines(ticker)
        data["catalyst_summary"]     = news.summary
        data["news_headlines"]       = news.headlines
        data["catalyst_category"]    = json.dumps(news.categories)
        data["headlines_fetched_at"] = datetime.now().isoformat()
        cat = compute_catalyst_score(data)
        data["catalyst_score"]      = cat.score
        data["catalyst_reason"]     = cat.explanation
        data["catalyst_confidence"] = cat.confidence

    # ── Live price + volume ───────────────────────────────────────────────────
    live = fetch_live_data(ticker)
    if live:
        _live_fields = [
            "current_price", "prev_close", "gap_pct", "prev_close_date",
            "premarket_high", "premarket_low",
            "prev_day_high", "prev_day_low",
            "avg_volume", "rel_volume",
            "vwap", "momentum_breakout", "candles_above_orb",
            "orb_hold", "trend_structure", "higher_highs", "higher_lows",
            "strong_candle_bodies", "price_above_vwap",
        ]
        for field in _live_fields:
            if live.get(field) is not None:
                data[field] = live[field]
        data["orb_phase"] = live.get("orb_phase", data.get("orb_phase", "pre_market"))

    # Structure defaults
    data.setdefault("vwap",                 None)
    data.setdefault("momentum_breakout",    False)
    data.setdefault("candles_above_orb",    0)
    data.setdefault("orb_hold",             False)
    data.setdefault("trend_structure",      False)
    data.setdefault("higher_highs",         False)
    data.setdefault("higher_lows",          False)
    data.setdefault("strong_candle_bodies", False)
    data.setdefault("price_above_vwap",     False)
    data.setdefault("momentum_runner",      False)
    data.setdefault("structure_momentum_score", 0)

    # ── Swing structure (EMAs/Fib) ────────────────────────────────────────────
    if swing_data_needs_refresh(data.get("swing_data_fetched_at")):
        swing = fetch_swing_data(ticker)
        if swing:
            data.update(swing)
    _swing_defaults(data)

    # ── Zone refresh ──────────────────────────────────────────────────────────
    if zones_need_refresh(data.get("zones_fetched_at")):
        current_px = data.get("current_price") or 0
        ticker_sym = (data.get("ticker") or ticker).upper()
        if current_px and ticker_sym:
            zone_data = detect_zones(ticker_sym, current_px)
            data.update(zone_data)
            data["zones_fetched_at"] = datetime.now().isoformat()
    _zone_defaults(data)

    # ── Re-score (setup type → trade plan → score → status) ─────────────────
    # Order matters: trade plan must run before swing score (R:R is a score input).
    data["swing_setup_type"] = compute_swing_setup_type(data)

    plan = compute_swing_trade_plan(data)
    data.update(plan)

    swing_sr = compute_swing_score(data)
    data["swing_score"]      = swing_sr.score
    data["swing_reason"]     = swing_sr.explanation
    data["swing_confidence"] = swing_sr.confidence

    data["swing_status"] = compute_swing_status(data)

    # Sync legacy fields
    data["setup_score"]      = data["swing_score"]
    data["setup_reason"]     = data["swing_reason"]
    data["setup_confidence"] = data["swing_confidence"]
    data["setup_type"]       = data["swing_setup_type"]

    data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return data


def load_mock_watchlist():
    """Return the default swing watchlist tickers."""
    return list(MOCK_STOCKS.keys())
