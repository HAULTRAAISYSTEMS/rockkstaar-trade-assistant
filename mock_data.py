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


import logging as _logging
_log = _logging.getLogger(__name__)


def generate_stock_data(ticker: str) -> dict:
    """
    Return a fully-scored swing trade data dict for *ticker*.

    Every external I/O step is wrapped in try/except.  A single failing step
    (yfinance outage, DNS error, scoring bug) does NOT crash the whole fetch —
    the remaining steps run with safe defaults instead.

    ticker_state set at end:
      'ready'  — price > 0 AND EMA data present AND scoring completed
      'stale'  — price > 0 but some fetch steps failed (partial data)
      'error'  — current_price = 0 / None (no usable data)
    """
    ticker = ticker.upper().strip()
    _errors: list[str] = []   # track which steps failed

    if ticker in MOCK_STOCKS:
        # Copy editorial context (trade_bias, catalyst_summary, headlines, earnings_date)
        # but DO NOT carry over any hardcoded price/volume seeds.  Those values are
        # outdated placeholders — live data is the only source of truth for prices.
        # If live fetch fails the ticker will show ERROR/STALE, never a fake price.
        _tmpl = MOCK_STOCKS[ticker]
        data = {
            "current_price":    None,   # must come from live fetch
            "prev_close":       None,   # must come from live fetch
            "gap_pct":          0.0,    # recalculated from live data
            "avg_volume":       _tmpl.get("avg_volume", 5_000_000),   # order-of-magnitude seed
            "rel_volume":       1.0,    # recalculated from live data
            # Editorial context — these do NOT come from live fetch:
            "trade_bias":       _tmpl.get("trade_bias", "Neutral"),
            "catalyst_summary": _tmpl.get("catalyst_summary", ""),
            "news_headlines":   _tmpl.get("news_headlines", []),
            "earnings_date":    _tmpl.get("earnings_date"),
        }
    else:
        data = {
            "current_price":  None,         # populated by fetch_live_data; None = no fake price
            "prev_close":     None,         # populated by fetch_live_data
            "gap_pct":        0.0,
            "avg_volume":     5_000_000,
            "rel_volume":     1.0,
            "trade_bias":     "Neutral",
            "catalyst_summary": "No template — live data will populate this.",
            "news_headlines": ["No headlines loaded — add a news API key."],
            "earnings_date":  None,
        }

    data["ticker"]      = ticker
    data["data_source"] = "unavailable"   # overridden below on successful live fetch

    # ── Step 1: Live price + volume ───────────────────────────────────────────
    try:
        live = fetch_live_data(ticker)
        if live and live.get("current_price"):
            _price_fields = [
                "current_price", "prev_close", "prev_close_date", "gap_pct",
                "premarket_high", "premarket_low",
                "prev_day_high", "prev_day_low",
                "avg_volume", "rel_volume",
                "earnings_date",
                "vwap", "momentum_breakout", "candles_above_orb",
                "orb_hold", "trend_structure", "higher_highs", "higher_lows",
                "strong_candle_bodies", "price_above_vwap",
            ]
            for field in _price_fields:
                if live.get(field) is not None:
                    data[field] = live[field]
            if live.get("earnings_date"):
                data["earnings_date"] = live["earnings_date"]
            data["orb_phase"]   = live.get("orb_phase", "pre_market")
            data["orb_high"]    = None
            data["orb_low"]     = None
            data["data_source"] = "live"   # confirmed: live price in hand
            if ticker not in MOCK_STOCKS:
                gap = data.get("gap_pct", 0)
                data["trade_bias"] = (
                    "Long Bias"  if gap >  3 else
                    "Short Bias" if gap < -3 else
                    "Neutral"
                )
        else:
            # live is None OR returned no current_price — treat as failed
            _log.warning(
                "generate_stock_data  stage=live_data  ticker=%s  result=%s",
                ticker, "no_price" if live else "None",
            )
            _errors.append("live_data")
    except Exception as exc:
        _log.error("generate_stock_data  stage=live_data  ticker=%s  err=%s", ticker, exc)
        _errors.append("live_data")
    data.setdefault("orb_phase", "pre_market")
    data.setdefault("orb_high", None)
    data.setdefault("orb_low",  None)
    # These price fields are only added by fetch_live_data when it returns them.
    # Without setdefault, upsert_stock_data fails on named-param binding when
    # live data is unavailable (yfinance timeout / rate limit on prod).
    data.setdefault("prev_close_date",  None)
    data.setdefault("premarket_high",   None)
    data.setdefault("premarket_low",    None)
    data.setdefault("prev_day_high",    None)
    data.setdefault("prev_day_low",     None)

    _log.debug(
        "generate_stock_data  ticker=%s  stage=live_data  "
        "price=%s  prev_close_date=%s  live_failed=%s",
        ticker,
        data.get("current_price"),
        data.get("prev_close_date"),
        "live_data" in _errors,
    )

    # ── Step 2: Headlines ─────────────────────────────────────────────────────
    try:
        if not data.get("headlines_fetched_at"):
            news = fetch_headlines(ticker)
            if not data.get("catalyst_summary") or ticker not in MOCK_STOCKS:
                data["catalyst_summary"] = news.summary
            if news.headlines and "No headlines" not in news.headlines[0]:
                data["news_headlines"] = news.headlines
            data["catalyst_category"]    = json.dumps(news.categories)
            data["headlines_fetched_at"] = datetime.now().isoformat()
    except Exception as exc:
        _log.warning("generate_stock_data  stage=headlines  ticker=%s  err=%s", ticker, exc)
        _errors.append("headlines")

    # ── Step 3: Swing structure (EMAs, Fibonacci, daily trend) ───────────────
    try:
        if swing_data_needs_refresh(data.get("swing_data_fetched_at")):
            swing = fetch_swing_data(ticker)
            if swing:
                data.update(swing)
            else:
                _log.warning("generate_stock_data  stage=swing_data  ticker=%s  result=None", ticker)
                _errors.append("swing_data")
    except Exception as exc:
        _log.error("generate_stock_data  stage=swing_data  ticker=%s  err=%s", ticker, exc)
        _errors.append("swing_data")
    _swing_defaults(data)   # always apply — ensures all swing keys exist

    # ── Step 4: Supply/demand zones ───────────────────────────────────────────
    try:
        if zones_need_refresh(data.get("zones_fetched_at")):
            current_px = data.get("current_price") or 0
            if current_px:
                zone_data = detect_zones(ticker, current_px)
                data.update(zone_data)
                data["zones_fetched_at"] = datetime.now().isoformat()
    except Exception as exc:
        _log.warning("generate_stock_data  stage=zones  ticker=%s  err=%s", ticker, exc)
        _errors.append("zones")
    _zone_defaults(data)    # always apply

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
    try:
        cat = compute_catalyst_score(data)
        data["catalyst_score"]      = cat.score
        data["catalyst_reason"]     = cat.explanation
        data["catalyst_confidence"] = cat.confidence
    except Exception as exc:
        _log.error("generate_stock_data  stage=catalyst_score  ticker=%s  err=%s", ticker, exc)
        data.setdefault("catalyst_score",      0)
        data.setdefault("catalyst_reason",     "Score unavailable")
        data.setdefault("catalyst_confidence", "Low")
        _errors.append("catalyst_score")

    # ── Step 6: Swing setup type ──────────────────────────────────────────────
    try:
        data["swing_setup_type"] = compute_swing_setup_type(data)
    except Exception as exc:
        _log.error("generate_stock_data  stage=swing_setup_type  ticker=%s  err=%s", ticker, exc)
        data.setdefault("swing_setup_type", "No Setup")
        _errors.append("swing_setup_type")

    # ── Step 7: Trade plan levels ─────────────────────────────────────────────
    try:
        plan = compute_swing_trade_plan(data)
        data.update(plan)
    except Exception as exc:
        _log.error("generate_stock_data  stage=trade_plan  ticker=%s  err=%s", ticker, exc)
        _errors.append("trade_plan")

    # ── Step 8: Swing score ───────────────────────────────────────────────────
    try:
        swing_sr = compute_swing_score(data)
        data["swing_score"]      = swing_sr.score
        data["swing_reason"]     = swing_sr.explanation
        data["swing_confidence"] = swing_sr.confidence
    except Exception as exc:
        _log.error("generate_stock_data  stage=swing_score  ticker=%s  err=%s", ticker, exc)
        data.setdefault("swing_score",      1)
        data.setdefault("swing_reason",     "Score unavailable")
        data.setdefault("swing_confidence", "Low")
        _errors.append("swing_score")

    # ── Step 9: Swing status ──────────────────────────────────────────────────
    try:
        data["swing_status"] = compute_swing_status(data)
    except Exception as exc:
        _log.error("generate_stock_data  stage=swing_status  ticker=%s  err=%s", ticker, exc)
        data.setdefault("swing_status", "NOT ENOUGH EDGE")
        _errors.append("swing_status")

    # Legacy scoring fields (kept for DB schema compatibility but deprioritised)
    data.setdefault("momentum_score",      1)
    data.setdefault("momentum_reason",     "Swing mode — intraday momentum not tracked")
    data.setdefault("momentum_confidence", "Low")
    data.setdefault("order_block",         "Neutral")
    data.setdefault("entry_quality",       "Okay")
    data.setdefault("orb_status",          "NO_ORB")
    data.setdefault("orb_ready",           "NO")
    data.setdefault("exec_state",          "WAIT")
    data.setdefault("setup_score",         data.get("swing_score", 1))
    data.setdefault("setup_reason",        data.get("swing_reason", ""))
    data.setdefault("setup_confidence",    data.get("swing_confidence", "Low"))
    data.setdefault("setup_type",          data.get("swing_setup_type", "No Setup"))
    data.setdefault("entry_note",          None)
    data.setdefault("position_size",       "normal")

    # Serialize list fields for SQLite
    data["news_headlines"] = json.dumps(data.get("news_headlines", []))
    if isinstance(data.get("catalyst_category"), list):
        data["catalyst_category"] = json.dumps(data["catalyst_category"])
    data.setdefault("catalyst_category",    "[]")
    data.setdefault("headlines_fetched_at", None)
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Determine ticker_state ────────────────────────────────────────────────
    _price_ok = bool(data.get("current_price") and float(data.get("current_price") or 0) > 0)
    _ema_ok   = data.get("ema_20_daily") is not None
    _score_ok = "swing_score" not in _errors and "swing_status" not in _errors
    if _price_ok and _ema_ok and _score_ok:
        data["ticker_state"] = "ready"
    elif _price_ok:
        data["ticker_state"] = "partial"  # price obtained but analysis incomplete
    else:
        data["ticker_state"] = "error"    # no usable price data

    if _errors:
        _log.info(
            "generate_stock_data  ticker=%s  state=%s  failed_steps=%s",
            ticker, data["ticker_state"], _errors,
        )
    else:
        _log.info("generate_stock_data  ticker=%s  state=%s", ticker, data["ticker_state"])

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
    _errors: list[str] = []

    # ── Headline refresh (time-gated) ─────────────────────────────────────────
    try:
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
    except Exception as exc:
        _log.warning("live_refresh_stock  stage=headlines  ticker=%s  err=%s", ticker, exc)
        _errors.append("headlines")

    # ── Live price + volume ───────────────────────────────────────────────────
    try:
        live = fetch_live_data(ticker)
        if live and live.get("current_price"):
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
            data["orb_phase"]   = live.get("orb_phase", data.get("orb_phase", "pre_market"))
            data["data_source"] = "live"
        else:
            # live failed — keep the existing snapshot price (data was pre-loaded from DB)
            data["data_source"] = "stale_snapshot"
            _errors.append("live_data")
    except Exception as exc:
        _log.error("live_refresh_stock  stage=live_data  ticker=%s  err=%s", ticker, exc)
        data["data_source"] = "stale_snapshot"
        _errors.append("live_data")

    # Price field defaults — prevent upsert_stock_data from failing on named-param
    # binding when fetch_live_data doesn't return these keys (timeout / rate limit).
    data.setdefault("prev_close_date",  None)
    data.setdefault("premarket_high",   None)
    data.setdefault("premarket_low",    None)
    data.setdefault("prev_day_high",    None)
    data.setdefault("prev_day_low",     None)

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
    try:
        if swing_data_needs_refresh(data.get("swing_data_fetched_at")):
            swing = fetch_swing_data(ticker)
            if swing:
                data.update(swing)
            else:
                _errors.append("swing_data")
    except Exception as exc:
        _log.error("live_refresh_stock  stage=swing_data  ticker=%s  err=%s", ticker, exc)
        _errors.append("swing_data")
    _swing_defaults(data)

    # ── Zone refresh ──────────────────────────────────────────────────────────
    try:
        if zones_need_refresh(data.get("zones_fetched_at")):
            current_px = data.get("current_price") or 0
            ticker_sym = (data.get("ticker") or ticker).upper()
            if current_px and ticker_sym:
                zone_data = detect_zones(ticker_sym, current_px)
                data.update(zone_data)
                data["zones_fetched_at"] = datetime.now().isoformat()
    except Exception as exc:
        _log.warning("live_refresh_stock  stage=zones  ticker=%s  err=%s", ticker, exc)
        _errors.append("zones")
    _zone_defaults(data)

    # ── Re-score (setup type → trade plan → score → status) ─────────────────
    try:
        data["swing_setup_type"] = compute_swing_setup_type(data)
    except Exception as exc:
        _log.error("live_refresh_stock  stage=swing_setup_type  ticker=%s  err=%s", ticker, exc)
        data.setdefault("swing_setup_type", "No Setup")
        _errors.append("swing_setup_type")

    try:
        plan = compute_swing_trade_plan(data)
        data.update(plan)
    except Exception as exc:
        _log.error("live_refresh_stock  stage=trade_plan  ticker=%s  err=%s", ticker, exc)
        _errors.append("trade_plan")

    try:
        swing_sr = compute_swing_score(data)
        data["swing_score"]      = swing_sr.score
        data["swing_reason"]     = swing_sr.explanation
        data["swing_confidence"] = swing_sr.confidence
    except Exception as exc:
        _log.error("live_refresh_stock  stage=swing_score  ticker=%s  err=%s", ticker, exc)
        data.setdefault("swing_score",      1)
        data.setdefault("swing_reason",     "Score unavailable")
        data.setdefault("swing_confidence", "Low")
        _errors.append("swing_score")

    try:
        data["swing_status"] = compute_swing_status(data)
    except Exception as exc:
        _log.error("live_refresh_stock  stage=swing_status  ticker=%s  err=%s", ticker, exc)
        data.setdefault("swing_status", "NOT ENOUGH EDGE")
        _errors.append("swing_status")

    # Sync legacy fields
    data["setup_score"]      = data.get("swing_score", 1)
    data["setup_reason"]     = data.get("swing_reason", "")
    data["setup_confidence"] = data.get("swing_confidence", "Low")
    data["setup_type"]       = data.get("swing_setup_type", "No Setup")

    data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Determine ticker_state
    _price_ok = bool(data.get("current_price") and float(data.get("current_price") or 0) > 0)
    _ema_ok   = data.get("ema_20_daily") is not None
    _score_ok = "swing_score" not in _errors and "swing_status" not in _errors
    if _price_ok and _ema_ok and _score_ok:
        data["ticker_state"] = "ready"
    elif _price_ok:
        data["ticker_state"] = "partial"  # price obtained but analysis incomplete
    else:
        data["ticker_state"] = "error"

    if _errors:
        _log.info(
            "live_refresh_stock  ticker=%s  state=%s  failed_steps=%s",
            ticker, data["ticker_state"], _errors,
        )

    return data


def load_mock_watchlist():
    """Return the default swing watchlist tickers."""
    return list(MOCK_STOCKS.keys())
