"""
mock_data.py - Stock data provider.
MOCK_STOCKS contains curated templates (catalyst context, news, trade bias).
generate_stock_data() overlays live price/volume data from yfinance on top,
falling back gracefully to the mock values when live data is unavailable.
"""

import json
import random
from datetime import datetime
from data_fetcher import fetch_live_data, fetch_news_headlines, orb_phase_now
from scoring import (
    compute_catalyst_score, compute_momentum_score,
    compute_order_block, compute_entry_quality,
    compute_orb_price_status, compute_orb_readiness, compute_exec_state,
    compute_final_setup_score, compute_setup_type, SETUP_TYPES, MOM_T, MOM_W,
)

# ---------------------------------------------------------------------------
# Mock dataset: realistic premarket scenarios
# Each entry contains raw market data only — scores are computed, not hardcoded.
# ---------------------------------------------------------------------------

MOCK_STOCKS = {
    "NVDA": {
        "current_price": 118.45,
        "prev_close":    112.30,
        "gap_pct":       5.47,
        "premarket_high": 119.80,
        "premarket_low":  113.50,
        "prev_day_high":  113.75,
        "prev_day_low":   109.20,
        "avg_volume":     45_000_000,
        "rel_volume":     2.8,
        "orb_high":       119.20,   # 0.63% above current — NEAR_HIGH
        "orb_low":        115.80,
        "catalyst_summary": (
            "Beat Q1 earnings estimates by 18%; data center revenue +42% YoY. "
            "Analyst upgrades from Goldman and BofA. Price target raised to $160."
        ),
        "news_headlines": [
            "NVDA crushes Q1 earnings — EPS $6.12 vs $5.19 est.",
            "Goldman raises NVDA price target to $160",
            "NVIDIA data center revenue hits record $22.6B",
        ],
        "earnings_date": "2026-05-28",
        "trade_bias": "Long Bias",
    },
    "TSLA": {
        "current_price": 241.10,
        "prev_close":    248.70,
        "gap_pct":       -3.05,
        "premarket_high": 249.00,
        "premarket_low":  239.50,
        "prev_day_high":  252.40,
        "prev_day_low":   244.10,
        "avg_volume":     90_000_000,
        "rel_volume":     1.6,
        "orb_high":       246.50,   # INSIDE — watching for breakdown
        "orb_low":        240.00,   # 0.46% below current — NEAR_LOW
        "catalyst_summary": (
            "Reuters report: Elon Musk considering stepping back from Tesla CEO role. "
            "Company denies but market reacting negatively."
        ),
        "news_headlines": [
            "Reuters: Musk in talks to name Tesla CEO successor",
            "Tesla denies CEO transition plans in SEC filing",
            "TSLA falls 3% premarket on management uncertainty",
        ],
        "earnings_date": "2026-04-23",
        "trade_bias": "Short Bias",
    },
    "AMC": {
        "current_price": 4.82,
        "prev_close":    4.10,
        "gap_pct":       17.56,
        "premarket_high": 5.25,
        "premarket_low":  4.15,
        "prev_day_high":  4.35,
        "prev_day_low":   3.90,
        "avg_volume":     28_000_000,
        "rel_volume":     9.4,
        "orb_high":       5.08,   # INSIDE — big range, waiting for break direction
        "orb_low":        4.48,
        "catalyst_summary": (
            "WallStreetBets coordinated squeeze attempt. Short float at 28%. "
            "No fundamental catalyst — pure momentum/sentiment play."
        ),
        "news_headlines": [
            "AMC surges 17% premarket on Reddit squeeze buzz",
            "Short interest in AMC climbs to 28% of float",
            "Options market shows heavy call buying in AMC",
        ],
        "earnings_date": "2026-05-12",
        "trade_bias": "Long Bias",
    },
    "AAPL": {
        "current_price": 195.30,
        "prev_close":    194.80,
        "gap_pct":       0.26,
        "premarket_high": 196.10,
        "premarket_low":  194.50,
        "prev_day_high":  196.25,
        "prev_day_low":   193.40,
        "avg_volume":     55_000_000,
        "rel_volume":     0.7,
        "orb_high":       195.80,   # INSIDE — no momentum, dead range
        "orb_low":        194.60,
        "catalyst_summary": (
            "No major catalyst. Light premarket volume. "
            "Market digesting Apple Vision Pro return rate data from analyst report."
        ),
        "news_headlines": [
            "Apple Vision Pro return rates higher than expected — Analyst",
            "AAPL flat in premarket, no major news flow",
        ],
        "earnings_date": "2026-04-30",
        "trade_bias": "Neutral",
    },
    "SMCI": {
        "current_price": 38.70,
        "prev_close":    42.15,
        "gap_pct":       -8.18,
        "premarket_high": 42.20,
        "premarket_low":  37.50,
        "prev_day_high":  43.80,
        "prev_day_low":   40.60,
        "avg_volume":     18_000_000,
        "rel_volume":     4.2,
        "orb_high":       None,   # Avoid — no ORB levels (too dangerous to trade)
        "orb_low":        None,
        "catalyst_summary": (
            "SEC investigation announced into accounting irregularities. "
            "Auditor resignation confirmed. High risk — gap down on heavy volume."
        ),
        "news_headlines": [
            "SMCI auditor resigns citing accounting concerns",
            "SEC opens formal investigation into Super Micro",
            "SMCI halted twice premarket — volatility extreme",
        ],
        "earnings_date": "2026-05-07",
        "trade_bias": "Avoid",
    },
    "META": {
        "current_price": 512.60,
        "prev_close":    505.40,
        "gap_pct":       1.42,
        "premarket_high": 515.00,
        "premarket_low":  506.00,
        "prev_day_high":  508.80,
        "prev_day_low":   499.30,
        "avg_volume":     14_000_000,
        "rel_volume":     1.3,
        "orb_high":       514.50,   # 0.37% above current — NEAR_HIGH
        "orb_low":        508.00,
        "catalyst_summary": (
            "Meta AI assistant reaches 1 billion users milestone. "
            "Moderate volume, clean gap above prior day high."
        ),
        "news_headlines": [
            "Meta AI surpasses 1 billion monthly active users",
            "META gaps above prior day high on modest volume",
            "Analyst: Meta advertising revenue tracking above plan",
        ],
        "earnings_date": "2026-04-30",
        "trade_bias": "Long Bias",
    },
    "MSTR": {
        "current_price": 1740.00,
        "prev_close":    1680.00,
        "gap_pct":       3.57,
        "premarket_high": 1760.00,
        "premarket_low":  1685.00,
        "prev_day_high":  1720.00,
        "prev_day_low":   1650.00,
        "avg_volume":     2_800_000,
        "rel_volume":     1.9,
        "orb_high":       1752.00,  # 0.69% above current — NEAR_HIGH
        "orb_low":        1705.00,
        "catalyst_summary": (
            "Bitcoin rallied 4% overnight to $91k. MSTR tracking BTC closely. "
            "Clean technical setup above VWAP."
        ),
        "news_headlines": [
            "Bitcoin surges past $91,000 overnight",
            "MicroStrategy adds another 2,500 BTC to treasury",
            "MSTR premarket volume tracking 2x average",
        ],
        "earnings_date": "2026-05-06",
        "trade_bias": "Long Bias",
    },
    "PLTR": {
        "current_price": 22.85,
        "prev_close":    23.40,
        "gap_pct":       -2.35,
        "premarket_high": 23.45,
        "premarket_low":  22.60,
        "prev_day_high":  24.10,
        "prev_day_low":   22.80,
        "avg_volume":     42_000_000,
        "rel_volume":     0.9,
        "orb_high":       23.20,   # INSIDE
        "orb_low":        22.68,   # 0.75% below current — NEAR_LOW
        "catalyst_summary": (
            "No specific catalyst. Pulling back from recent highs. "
            "Low relative volume — not a high-priority setup today."
        ),
        "news_headlines": [
            "PLTR drifts lower in light premarket trade",
            "Palantir government contract renewals on track — analyst",
        ],
        "earnings_date": "2026-05-05",
        "trade_bias": "Neutral",
    },
}


def generate_stock_data(ticker: str) -> dict:
    """
    Return a fully-scored stock data dict for a ticker.

    Data sourcing:
      - MOCK_STOCKS entries   → curated catalyst context, news, trade bias (template)
      - yfinance (live)       → price, volume, gap%, premarket range, ORB levels, earnings date
      - Unknown tickers       → random placeholder prices + live news via yfinance

    Scoring (catalyst → momentum → order block → entry → ORB → exec state → setup)
    is always computed via scoring.py — never hardcoded — so logic is consistent
    across mock and live data paths.
    """
    ticker = ticker.upper().strip()

    if ticker in MOCK_STOCKS:
        data = dict(MOCK_STOCKS[ticker])
    else:
        # Unknown ticker: start with plausible random values as placeholder
        prev_close = round(random.uniform(5.0, 500.0), 2)
        gap        = round(random.uniform(-8.0, 8.0), 2)
        current    = round(prev_close * (1 + gap / 100), 2)
        data = {
            "current_price":  current,
            "prev_close":     prev_close,
            "gap_pct":        gap,
            "premarket_high": round(current * 1.01, 2),
            "premarket_low":  round(current * 0.99, 2),
            "prev_day_high":  round(prev_close * 1.015, 2),
            "prev_day_low":   round(prev_close * 0.985, 2),
            "avg_volume":     random.randint(500_000, 20_000_000),
            "rel_volume":     round(random.uniform(0.5, 3.0), 2),
            "orb_high":       None,
            "orb_low":        None,
            "catalyst_summary": "No catalyst loaded. Connect a news API to populate this field.",
            "news_headlines": ["No headlines available — connect NewsAPI or Benzinga."],
            "earnings_date":  None,
            "trade_bias":     random.choice(["Long Bias", "Short Bias", "Neutral"]),
        }

    # ------------------------------------------------------------------ #
    # Determine the current ORB phase from ET time — always, even if yfinance
    # is down. This prevents pre-market mock ORB values from being displayed
    # as real data before the market opens.
    # ------------------------------------------------------------------ #
    phase = orb_phase_now()
    data["orb_phase"] = phase

    if phase == "pre_market":
        # Before 9:30 ET: clear all ORB levels — none are valid yet
        data["orb_high"] = None
        data["orb_low"]  = None

    # ------------------------------------------------------------------ #
    # Overlay live price / volume data from yfinance (best-effort)
    # Mock values remain as fallback for any field that yfinance can't fill.
    # ------------------------------------------------------------------ #
    live = fetch_live_data(ticker)
    if live:
        # Always overlay price / volume fields when available
        _price_fields = [
            "current_price", "prev_close", "prev_close_date", "gap_pct",
            "premarket_high", "premarket_low",
            "prev_day_high", "prev_day_low",
            "avg_volume", "rel_volume",
            "vwap", "momentum_breakout", "candles_above_orb",
            "orb_hold", "trend_structure", "higher_highs", "higher_lows",
            "strong_candle_bodies", "price_above_vwap",
        ]
        for field in _price_fields:
            if live.get(field) is not None:
                data[field] = live[field]

        # Earnings date from live calendar (overrides static date in template)
        if live.get("earnings_date"):
            data["earnings_date"] = live["earnings_date"]

        # ORB levels — three-phase logic:
        #   pre_market : already cleared above; live has no ORB either
        #   forming    : overlay partial live range (9:30 to now), or clear if no bars yet
        #   locked     : overlay final live range; fall back to mock if live failed
        live_phase = live.get("orb_phase", phase)  # use live phase if returned, else time-based
        data["orb_phase"] = live_phase               # live phase is the authoritative value

        if live_phase == "pre_market":
            data["orb_high"] = None
            data["orb_low"]  = None
        elif live_phase == "forming":
            if live.get("orb_high") is not None and live.get("orb_low") is not None:
                # Partial range available — show live levels
                data["orb_high"] = live["orb_high"]
                data["orb_low"]  = live["orb_low"]
            else:
                # Window just opened, no completed bars yet
                data["orb_high"] = None
                data["orb_low"]  = None
        else:  # locked
            if live.get("orb_high") is not None and live.get("orb_low") is not None:
                # Final live ORB — use it
                data["orb_high"] = live["orb_high"]
                data["orb_low"]  = live["orb_low"]
            # else: keep mock value as fallback — it's demo data so approximate is OK

        # Unknown tickers: derive bias from live gap and fetch live news
        if ticker not in MOCK_STOCKS:
            gap = data.get("gap_pct", 0)
            data["trade_bias"] = (
                "Long Bias"  if gap >  3 else
                "Short Bias" if gap < -3 else
                "Neutral"
            )
            summary, headlines = fetch_news_headlines(ticker)
            if summary:
                data["catalyst_summary"] = summary
            if headlines:
                data["news_headlines"] = headlines

    # Ensure live price structure fields always exist (set to False/None when
    # intraday fetch failed or market is pre-open — scoring handles gracefully)
    data.setdefault("vwap", None)
    data.setdefault("momentum_breakout", False)
    data.setdefault("candles_above_orb", 0)
    data.setdefault("orb_hold", False)
    data.setdefault("trend_structure", False)
    data.setdefault("higher_highs", False)
    data.setdefault("higher_lows", False)
    data.setdefault("strong_candle_bodies", False)
    data.setdefault("price_above_vwap", False)

    # --- Compute scores via scoring.py ---
    # Each scoring function uses pre-stored values from earlier steps to avoid
    # recomputation.  Order of calls must match the dependency chain below.
    data["ticker"] = ticker

    # 1. Catalyst — fundamental reason quality
    cat = compute_catalyst_score(data)
    data["catalyst_score"]      = cat.score
    data["catalyst_reason"]     = cat.explanation
    data["catalyst_confidence"] = cat.confidence

    # 2. Momentum — structure (primary) + participation bonus (secondary)
    mom = compute_momentum_score(data)
    data["momentum_score"]      = mom.score
    data["momentum_reason"]     = mom.explanation
    data["momentum_confidence"] = mom.confidence

    # 2.5. Derive Momentum Runner flag from STRUCTURE score only.
    #      structure_momentum_score = sum of the three price-action signals.
    #      Runner fires when structure alone is strong enough — low rvol or
    #      below-VWAP reduces confidence but does NOT block the signal.
    #      Must be set before entry_quality / exec_state which read this flag.
    structure_score = (
        (MOM_W["orb_hold"]      if data.get("orb_hold")             else 0)
        + (MOM_W["trend_structure"] if data.get("trend_structure")   else 0)
        + (MOM_W["strong_bodies"]   if data.get("strong_candle_bodies") else 0)
    )
    data["structure_momentum_score"] = structure_score
    data["momentum_runner"] = (
        structure_score >= MOM_T["structure_threshold"]
        and not data.get("momentum_breakout", False)
    )

    # 3. Order block — institutional zone direction
    data["order_block"] = compute_order_block(data)

    # 4. Entry quality — uses order_block from above
    data["entry_quality"] = compute_entry_quality(data)

    # 4.5. ORB price status — compares current_price to orb_high/orb_low (raw data fields)
    data["orb_status"] = compute_orb_price_status(data)

    # 5. ORB readiness — uses momentum_score + entry_quality from above
    data["orb_ready"] = compute_orb_readiness(data)

    # 6. Execution state — uses orb_ready + entry_quality + rvol from above
    data["exec_state"] = compute_exec_state(data)

    # 7. Final setup score — combines all four signals
    setup = compute_final_setup_score(data)
    data["setup_score"]         = setup.score
    data["setup_reason"]        = setup.explanation
    data["setup_confidence"]    = setup.confidence

    # 8. Setup type classification
    data["setup_type"] = compute_setup_type(data)

    # 9. Trade guidance — derived from setup_type after full scoring.
    #    For Momentum Runner, the entry_note reflects participation quality:
    #      full confirmation (rvol + VWAP)  → trend-strength note
    #      low volume                        → caution note
    #      below VWAP                        → reclaim note
    if data["setup_type"] == "Momentum Runner":
        rvol             = data.get("rel_volume") or 0
        _above_vwap      = bool(data.get("price_above_vwap"))
        _rvol_ok         = rvol >= MOM_T["rvol_min"]
        if _above_vwap and _rvol_ok:
            data["entry_note"] = (
                "Trend strength confirmed — extended entry, wait for pullback "
                "to VWAP or ORB high, or use reduced size."
            )
        elif not _rvol_ok and _above_vwap:
            data["entry_note"] = (
                "Low volume — structure trend confirmed but participation is weak. "
                "Wait for volume to confirm before entry."
            )
        elif not _above_vwap:
            data["entry_note"] = (
                "Below VWAP — ORB hold detected, wait for VWAP reclaim "
                "before adding. Use reduced size if entering early."
            )
        else:
            data["entry_note"] = (
                "Extended entry — wait for pullback to VWAP or ORB high. "
                "Use reduced position size."
            )
        data["position_size"] = "reduced"
    elif data["setup_type"] == "Momentum Breakout":
        data["entry_note"]    = None
        data["position_size"] = "normal"
    else:
        data["entry_note"]    = None
        data["position_size"] = "normal"

    # Serialize headlines list to JSON string for SQLite storage
    data["news_headlines"] = json.dumps(data.get("news_headlines", []))
    data["last_updated"]   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return data


def load_mock_watchlist():
    """Return the default demo set of tickers."""
    return list(MOCK_STOCKS.keys())
