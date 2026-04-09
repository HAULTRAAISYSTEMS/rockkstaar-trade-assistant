"""
app.py - Rockkstaar Trade Assistant
Flask web app for premarket stock watchlist scanning.
"""

import json as _json
import logging
import re
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sock import Sock

logger = logging.getLogger(__name__)
from database import (
    init_db,
    DEFAULT_WATCHLISTS,
    get_setting, set_setting,
    get_all_watchlists, get_watchlist_by_id, create_watchlist,
    rename_watchlist, delete_watchlist,
    get_watchlist_stocks, get_watchlist_stock_counts,
    add_ticker_to_watchlist, remove_ticker_from_watchlist,
    remove_ticker_from_defaults,
    get_ticker_watchlist_ids, set_ticker_watchlists,
    upsert_stock_data, get_stock_data, get_all_stock_data,
    update_live_fields,
    set_stock_classify, set_auto_classify,
    get_note, save_note, get_all_notes, update_setup_type,
    get_trade_plan, save_trade_plan, get_all_trade_plans,
    add_journal_entry, update_journal_entry, delete_journal_entry,
    get_journal_entry, get_all_journal_entries,
)
from mock_data import generate_stock_data, load_mock_watchlist, live_refresh_stock
from scoring import catalyst_score_breakdown, SETUP_TYPES
from classifier import classify_stock

app = Flask(__name__)
app.secret_key = "rockkstaar-secret-key-change-in-prod"
sock = Sock(app)

# ---------------------------------------------------------------------------
# Startup initialization — runs on every Python process start, including
# gunicorn workers.  init_db() is idempotent (CREATE TABLE IF NOT EXISTS).
# seed_demo_data() is called after its definition below.
# ---------------------------------------------------------------------------
init_db()


# ---------------------------------------------------------------------------
# App initialization
# ---------------------------------------------------------------------------

def _prev_trading_day() -> str:
    """
    Return the most recent past trading day as YYYY-MM-DD (Mon–Fri, weekend-aware).
    Uses US/Eastern time so the date is correct before/after midnight ET.
    Does not account for public holidays — weekend skipping is sufficient for
    the staleness check (a 3-day holiday gap still triggers a refresh which is fine).
    """
    try:
        import zoneinfo
        today = datetime.now(zoneinfo.ZoneInfo("America/New_York")).date()
    except Exception:
        from datetime import timezone
        today = datetime.now(timezone(timedelta(hours=-4))).date()
    candidate = today - timedelta(days=1)
    while candidate.weekday() >= 5:   # 5 = Saturday, 6 = Sunday
        candidate -= timedelta(days=1)
    return candidate.isoformat()


def auto_refresh_stale_closes(tickers: list) -> list:
    """
    For each ticker whose prev_close_date doesn't match the expected previous
    trading day, auto-refresh market data from yfinance.

    Only refreshes tickers that haven't been updated today (avoids redundant
    API calls when an intra-day manual refresh has already run).

    Returns the list of tickers that were refreshed.
    """
    expected  = _prev_trading_day()
    today_str = datetime.now().strftime("%Y-%m-%d")   # matches last_updated format
    refreshed = []

    for ticker in tickers:
        stock = get_stock_data(ticker)
        if not stock:
            continue
        # Skip tickers already refreshed today
        last_updated = (stock.get("last_updated") or "")[:10]
        if last_updated == today_str:
            continue
        # Skip tickers whose prev_close is already up-to-date
        if (stock.get("prev_close_date") or "") == expected:
            continue
        # Auto-refresh
        try:
            fresh = generate_stock_data(ticker)
            upsert_stock_data(fresh)
            run_auto_classification(ticker)
            refreshed.append(ticker)
            logger.info(
                "Auto-refreshed %s: prev_close_date was '%s', expected '%s'",
                ticker, stock.get("prev_close_date") or "missing", expected,
            )
        except Exception as e:
            logger.warning("Auto-refresh failed for %s: %s", ticker, e)

    return refreshed


def get_active_wl_id() -> int | None:
    """
    Return the active watchlist ID from the session.
    Falls back to the first watchlist if the session value is missing or stale.
    Returns None only when no watchlists exist at all.
    """
    all_wls = get_all_watchlists()
    if not all_wls:
        return None
    wl_id = session.get("active_wl_id")
    if wl_id and any(w["id"] == wl_id for w in all_wls):
        return wl_id
    return all_wls[0]["id"]


def run_auto_classification(ticker: str):
    """
    Classify a ticker and, if auto_classify is ON, move it to the appropriate
    default watchlist. Only reorganizes memberships within the four DEFAULT_WATCHLISTS;
    never touches user-created custom watchlists.

    Called after every upsert_stock_data (add, refresh, refresh-single).
    """
    stock = get_stock_data(ticker)
    if not stock:
        return

    target_name, reason = classify_stock(stock)

    # Always persist the reason (visible even when auto_classify is OFF)
    set_stock_classify(ticker, reason)

    # Respect manual override — do not move if auto_classify is OFF
    if stock.get("auto_classify", 1) == 0:
        return

    # Build a map of {name → id} for every default watchlist that exists in DB
    all_wls = get_all_watchlists()
    default_wl_map = {wl["name"]: wl["id"] for wl in all_wls
                      if wl["name"] in DEFAULT_WATCHLISTS}
    if not default_wl_map:
        return

    target_id = default_wl_map.get(target_name)
    if not target_id:
        return

    # Only reorganize if the stock is already in at least one default list
    current_ids = set(get_ticker_watchlist_ids(ticker))
    default_ids = set(default_wl_map.values())
    in_defaults = current_ids & default_ids

    if not in_defaults:
        # Stock lives only in custom lists — don't auto-insert into defaults
        return

    if target_id in in_defaults and len(in_defaults) == 1:
        # Already in the correct list and only that list — nothing to do
        return

    # Move: add to target first (preserves stock_data), then remove from others
    add_ticker_to_watchlist(target_id, ticker)
    for wid in in_defaults:
        if wid != target_id:
            remove_ticker_from_watchlist(wid, ticker)


def seed_demo_data():
    """
    Populate the first watchlist with mock data on the very first run only.

    The 'demo_seeded' flag in the settings table ensures this runs exactly once,
    even if the user later deletes all tickers (which would otherwise make the
    first watchlist appear empty and trigger a re-seed on the next server start).
    """
    if get_setting("demo_seeded") == "1":
        return   # Already seeded — never re-seed, even if watchlist is empty

    all_wls = get_all_watchlists()
    if not all_wls:
        return
    first_id = all_wls[0]["id"]
    for ticker in load_mock_watchlist():
        add_ticker_to_watchlist(first_id, ticker)
        upsert_stock_data(generate_stock_data(ticker))

    set_setting("demo_seeded", "1")


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def get_score_class(score):
    """CSS class for a 1-10 score (used for both setup_score and catalyst_score)."""
    if score is None:
        return "neutral"
    if score >= 7:
        return "strong"
    if score >= 4:
        return "moderate"
    return "weak"


def get_bias_class(bias):
    """CSS class for the trade bias label."""
    return {
        "Long Bias":  "bias-long",
        "Short Bias": "bias-short",
        "Neutral":    "bias-neutral",
        "Avoid":      "bias-avoid",
    }.get(bias, "bias-neutral")


def get_setup_type_class(setup_type):
    """CSS class for the setup type pill."""
    return {
        "Momentum Breakout": "setup-momentum-breakout",
        "Momentum Runner":   "setup-momentum-runner",
        "Gap and Go":        "setup-gap-go",
        "Breakdown":         "setup-breakdown",
        "VWAP Reclaim":      "setup-vwap",
        "Range Break":       "setup-range",
        "ORB":               "setup-orb",
        "No Setup":          "setup-none",
    }.get(setup_type, "setup-none")


def get_confidence_class(confidence):
    """CSS class for the confidence level badge."""
    return {
        "High":   "conf-high",
        "Medium": "conf-medium",
        "Low":    "conf-low",
    }.get(confidence, "conf-low")


def get_orb_class(orb_ready):
    """CSS class for the ORB readiness badge."""
    return "orb-yes" if orb_ready == "YES" else "orb-no"


def get_ob_class(order_block):
    """CSS class for the order block badge."""
    return {
        "Demand":  "ob-demand",
        "Supply":  "ob-supply",
        "Neutral": "ob-neutral",
    }.get(order_block, "ob-neutral")


def get_entry_class(entry_quality):
    """CSS class for the entry quality badge."""
    return {
        "Perfect":  "entry-perfect",
        "Okay":     "entry-okay",
        "Extended": "entry-extended",
    }.get(entry_quality, "entry-okay")


def get_exec_class(exec_state):
    """CSS class for the execution state badge."""
    return {
        "TRIGGERED": "exec-triggered",
        "READY":     "exec-ready",
        "WAIT":      "exec-wait",
    }.get(exec_state, "exec-wait")


def compute_pnl(direction: str, entry: float, exit_: float) -> tuple:
    """
    Compute directional P&L% and result label from a closed trade.
    Returns (pnl_pct: float, result: str).
    """
    try:
        entry  = float(entry)
        exit_  = float(exit_)
    except (TypeError, ValueError):
        return 0.0, "Break Even"

    if entry == 0:
        return 0.0, "Break Even"

    if direction == "Long":
        pnl_pct = (exit_ - entry) / entry * 100
    else:  # Short
        pnl_pct = (entry - exit_) / entry * 100

    pnl_pct = round(pnl_pct, 2)
    if pnl_pct > 0:
        result = "Win"
    elif pnl_pct < 0:
        result = "Loss"
    else:
        result = "Break Even"
    return pnl_pct, result


def compute_journal_summary(entries: list) -> dict:
    """
    Derive win-rate, P&L stats, and per-setup breakdown from journal entries.
    Returns a dict consumed directly by the journal template.
    """
    if not entries:
        return {"total": 0, "wins": 0, "losses": 0, "be": 0,
                "win_rate": None, "avg_win": None, "avg_loss": None,
                "total_pnl": 0.0, "setups": [], "momentum_bands": []}

    wins   = [e for e in entries if e.get("result") == "Win"]
    losses = [e for e in entries if e.get("result") == "Loss"]
    bes    = [e for e in entries if e.get("result") == "Break Even"]

    win_rate  = round(len(wins) / len(entries) * 100, 1) if entries else None
    avg_win   = round(sum(e["pnl_pct"] for e in wins)   / len(wins),   2) if wins   else None
    avg_loss  = round(sum(e["pnl_pct"] for e in losses) / len(losses), 2) if losses else None
    total_pnl = round(sum(e.get("pnl_pct") or 0 for e in entries), 2)

    # Per-setup breakdown — rank by win rate (min 2 trades to appear in ranked list)
    from collections import defaultdict
    setup_map = defaultdict(list)
    for e in entries:
        st = e.get("setup_type") or "Untagged"
        setup_map[st].append(e)

    setups = []
    for st, trades in setup_map.items():
        st_wins = [t for t in trades if t.get("result") == "Win"]
        st_wr   = round(len(st_wins) / len(trades) * 100, 1)
        st_pnl  = round(sum(t.get("pnl_pct") or 0 for t in trades) / len(trades), 2)
        setups.append({
            "setup_type": st,
            "count":      len(trades),
            "win_rate":   st_wr,
            "avg_pnl":    st_pnl,
            "wins":       len(st_wins),
            "losses":     len(trades) - len(st_wins),
        })
    setups.sort(key=lambda s: (s["win_rate"], s["avg_pnl"]), reverse=True)

    # Momentum score bands: group 1-3 / 4-6 / 7-8 / 9-10
    bands = [("1-3", 1, 3), ("4-6", 4, 6), ("7-8", 7, 8), ("9-10", 9, 10)]
    momentum_bands = []
    for label, lo, hi in bands:
        band = [e for e in entries
                if e.get("momentum_score") and lo <= e["momentum_score"] <= hi]
        if not band:
            continue
        bw = [t for t in band if t.get("result") == "Win"]
        momentum_bands.append({
            "label":    label,
            "count":    len(band),
            "win_rate": round(len(bw) / len(band) * 100, 1),
            "avg_pnl":  round(sum(t.get("pnl_pct") or 0 for t in band) / len(band), 2),
        })

    return {
        "total":    len(entries),
        "wins":     len(wins),
        "losses":   len(losses),
        "be":       len(bes),
        "win_rate": win_rate,
        "avg_win":  avg_win,
        "avg_loss": avg_loss,
        "total_pnl": total_pnl,
        "setups":    setups,
        "momentum_bands": momentum_bands,
    }


def compute_rr(plan_bias, entry, stop, target):
    """
    Compute risk/reward ratio from plan fields.
    Returns (rr_ratio: float, rr_display: str, rr_class: str) or (None, '—', 'rr-neutral').
    Long:  reward = target - entry,  risk = entry - stop
    Short: reward = entry - target,  risk = stop  - entry
    """
    try:
        entry  = float(entry)
        stop   = float(stop)
        target = float(target)
    except (TypeError, ValueError):
        return None, "—", "rr-neutral"

    if plan_bias == "Long":
        reward = target - entry
        risk   = entry  - stop
    elif plan_bias == "Short":
        reward = entry  - target
        risk   = stop   - entry
    else:
        return None, "—", "rr-neutral"

    if risk <= 0 or reward < 0:
        return None, "Invalid", "rr-warn"

    ratio = reward / risk
    display = f"{ratio:.1f}:1"
    if ratio >= 2:
        css = "rr-good"
    elif ratio >= 1:
        css = "rr-okay"
    else:
        css = "rr-poor"
    return ratio, display, css


def compute_freshness(triggered_at: str | None, exec_state: str | None) -> tuple:
    """
    Determine the freshness label for a triggered stock.
    Returns (label, css_class) — both None when exec_state != TRIGGERED.

    Thresholds (minutes since triggered_at):
      < 15 min  → Fresh Breakout  (act now — highest priority)
      15–45 min → Active Move     (still valid but watch extension)
      > 45 min  → Late Move       (likely extended, caution)

    If triggered_at is null or was stamped before 9:30 ET → Premarket Watch.
    """
    if exec_state != "TRIGGERED":
        return None, None

    if not triggered_at:
        return "Premarket Watch", "fresh-premarket"

    try:
        ts  = datetime.fromisoformat(triggered_at)
        now = datetime.now()

        # Triggered before market open → premarket watch
        if ts.hour < 9 or (ts.hour == 9 and ts.minute < 30):
            return "Premarket Watch", "fresh-premarket"

        elapsed = (now - ts).total_seconds() / 60
        if elapsed < 15:
            return "Fresh Breakout", "fresh-breakout"
        if elapsed < 45:
            return "Active Move", "fresh-active"
        return "Late Move", "fresh-late"
    except (ValueError, TypeError):
        return "Premarket Watch", "fresh-premarket"


def get_freshness_class(label: str | None) -> str:
    return {
        "Fresh Breakout":  "fresh-breakout",
        "Active Move":     "fresh-active",
        "Late Move":       "fresh-late",
        "Premarket Watch": "fresh-premarket",
    }.get(label or "", "")


def get_orb_status_class(orb_status):
    """CSS class for the ORB price level status badge."""
    return {
        "ABOVE":     "orbs-above",
        "NEAR_HIGH": "orbs-near-high",
        "INSIDE":    "orbs-inside",
        "NEAR_LOW":  "orbs-near-low",
        "BELOW":     "orbs-below",
        "NO_ORB":    "orbs-none",
    }.get(orb_status, "orbs-none")


def get_orb_phase_label(orb_phase: str | None) -> tuple:
    """
    Return (label, css_class) for the ORB phase badge.
    Used in both stock detail and dashboard table.
    """
    return {
        "pre_market": ("Waiting for Open",  "orbp-pre"),
        "forming":    ("ORB Forming",        "orbp-forming"),
        "locked":     ("ORB Locked",         "orbp-locked"),
    }.get(orb_phase or "", ("", ""))


# ORB action directive — the trader-facing instruction for each phase.
# Each entry: (action_word, sub_label, banner_class, action_class)
_ORB_ACTION_MAP = {
    "pre_market": (
        "WAIT",
        "Market opens at 9:30 AM ET — no ORB data yet",
        "orb-banner-pre",
        "orb-action-wait",
    ),
    "forming": (
        "OBSERVE",
        "ORB forming 9:30–10:00 AM ET — watch the range, do not enter yet",
        "orb-banner-forming",
        "orb-action-observe",
    ),
    "locked": (
        "EXECUTE",
        "ORB locked — use levels for breakout entries",
        "orb-banner-locked",
        "orb-action-execute",
    ),
}


def get_orb_action(orb_phase: str | None) -> dict:
    """
    Return the action directive dict for a given ORB phase.
    Keys: action, sub_label, banner_class, action_class.
    """
    row = _ORB_ACTION_MAP.get(orb_phase or "locked", _ORB_ACTION_MAP["locked"])
    return {
        "action":       row[0],
        "sub_label":    row[1],
        "banner_class": row[2],
        "action_class": row[3],
    }


def get_orb_session_banner() -> dict:
    """
    Compute the current global ORB session state from live ET time.
    Used by the dashboard banner — independent of any single stock.
    """
    from data_fetcher import orb_phase_now
    phase = orb_phase_now()
    label, phase_class = get_orb_phase_label(phase)
    action = get_orb_action(phase)
    return {
        "phase":        phase,
        "phase_label":  label,
        "phase_class":  phase_class,
        **action,
    }


def annotate(stock: dict) -> dict:
    """Add all display-only fields to a stock dict (non-destructive to DB fields)."""
    # Enforce numeric defaults so templates never receive None for numeric fields.
    # Price/volume fields default to 0.0; score fields default to 0.
    _PRICE_DEFAULTS = {
        "current_price":  0.0,
        "prev_close":     0.0,
        "gap_pct":        0.0,
        "premarket_high": 0.0,
        "premarket_low":  0.0,
        "prev_day_high":  0.0,
        "prev_day_low":   0.0,
        "rel_volume":     0.0,
        "avg_volume":     0,
    }
    _SCORE_DEFAULTS = {
        "catalyst_score":  0,
        "momentum_score":  0,
        "setup_score":     0,
    }
    for field, default in {**_PRICE_DEFAULTS, **_SCORE_DEFAULTS}.items():
        if stock.get(field) is None:
            stock[field] = default

    stock["score_class"]           = get_score_class(stock.get("setup_score"))
    stock["cat_score_class"]       = get_score_class(stock.get("catalyst_score"))
    stock["mom_score_class"]       = get_score_class(stock.get("momentum_score"))
    stock["bias_class"]            = get_bias_class(stock.get("trade_bias"))
    stock["setup_type_class"]      = get_setup_type_class(stock.get("setup_type") or "No Setup")
    stock["cat_conf_class"]        = get_confidence_class(stock.get("catalyst_confidence") or "Low")
    stock["setup_conf_class"]      = get_confidence_class(stock.get("setup_confidence") or "Low")
    stock["mom_conf_class"]        = get_confidence_class(stock.get("momentum_confidence") or "Low")
    stock["orb_class"]             = get_orb_class(stock.get("orb_ready") or "NO")
    stock["ob_class"]              = get_ob_class(stock.get("order_block") or "Neutral")
    stock["entry_class"]           = get_entry_class(stock.get("entry_quality") or "Okay")
    stock["exec_class"]            = get_exec_class(stock.get("exec_state") or "WAIT")
    stock["orb_status_class"]      = get_orb_status_class(stock.get("orb_status") or "NO_ORB")
    orb_phase_label, orb_phase_class = get_orb_phase_label(stock.get("orb_phase"))
    stock["orb_phase_label"]       = orb_phase_label
    stock["orb_phase_class"]       = orb_phase_class
    orb_action                     = get_orb_action(stock.get("orb_phase"))
    stock["orb_action"]            = orb_action["action"]
    stock["orb_action_class"]      = orb_action["action_class"]
    stock["orb_action_sub"]        = orb_action["sub_label"]
    freshness, freshness_class     = compute_freshness(stock.get("triggered_at"), stock.get("exec_state"))
    stock["freshness"]             = freshness
    stock["freshness_class"]       = freshness_class or ""
    # ORB range visualization: position of current price on a 0-100% scale
    # Extended range = 40% padding on each side of the ORB range (1.8x total width)
    orb_h = stock.get("orb_high")
    orb_l = stock.get("orb_low")
    cur   = stock.get("current_price") or 0
    if orb_h and orb_l and cur and orb_h > orb_l:
        rng      = orb_h - orb_l
        vis_low  = orb_l - 0.4 * rng
        vis_rng  = rng * 1.8
        pct      = (cur - vis_low) / vis_rng * 100
        stock["orb_price_pct"] = round(max(2, min(98, pct)), 1)
    else:
        stock["orb_price_pct"] = 50.0
    gap = stock.get("gap_pct") or 0
    stock["gap_display"]           = f"{'+' if gap >= 0 else ''}{gap:.2f}%"
    stock["gap_class"]             = "positive" if gap >= 0 else "negative"

    # Decode catalyst_category JSON → list of {key, label} dicts for templates
    import json as _json
    from news_fetcher import CATALYST_CATEGORIES as _CAT_DEFS, freshness_label as _fl
    raw_cats = stock.get("catalyst_category") or "[]"
    try:
        cat_keys = _json.loads(raw_cats) if isinstance(raw_cats, str) else list(raw_cats)
    except Exception:
        cat_keys = []
    stock["catalyst_tags"] = [
        {"key": k, "label": _CAT_DEFS[k]["label"]}
        for k in cat_keys if k in _CAT_DEFS
    ]
    # Human-readable headline freshness ("2m ago", "1h ago", …)
    stock["headline_freshness"] = _fl(
        (lambda hfa: int((datetime.now() - datetime.fromisoformat(hfa)).total_seconds() / 60)
         if hfa else None)(stock.get("headlines_fetched_at"))
    )
    return stock


# ---------------------------------------------------------------------------
# Ranking & summary logic
# ---------------------------------------------------------------------------

def rank_stocks(stocks: list) -> list:
    """
    Rank stocks from strongest to weakest opportunity.
    Composite score weights:
      - setup_score    (primary — final composite: momentum + ORB + OB + entry)
      - momentum_score (secondary — raw energy/follow-through)
      - catalyst_score (tertiary — fundamental reason)
      - relative volume (market interest)
      - absolute gap % (size of the move)
      - ORB ready stocks get a tiebreaker bonus
      - Avoid stocks are always last
    """
    def composite(s):
        if s.get("trade_bias") == "Avoid":
            return -999
        setup    = (s.get("setup_score")    or 0) * 8
        momentum = (s.get("momentum_score") or 0) * 3
        catalyst = (s.get("catalyst_score") or 0) * 2
        rvol     = min((s.get("rel_volume") or 0) * 1.5, 10)
        gap      = min(abs(s.get("gap_pct") or 0) * 0.3, 6)
        orb_bonus = 4 if s.get("orb_ready") == "YES" else 0
        return setup + momentum + catalyst + rvol + gap + orb_bonus

    return sorted(stocks, key=composite, reverse=True)


def compute_no_trade_assessment(ranked: list, top5: list) -> dict:
    """
    Analyse the full ranked watchlist to decide whether this is a no-trade day
    and to explain *why* conditions are poor.

    Returns a dict with these keys:
      is_no_trade   bool   — True when no A+ setups exist
      lock_signals  bool   — True when signal quality is so low that TRIGGERED
                             states should be suppressed (prevents false urgency)
      verdict       str    — Short headline ("NO TRADE DAY" or "")
      reasons       list   — Up to 3 specific reason strings
      severity      str    — "hard" | "soft" | "none"
                             hard → lock signals, show red panel
                             soft → show amber warning, do not lock
                             none → normal trading conditions

    Severity rules:
      hard  — top5 empty AND (avg momentum < 4 OR avg rvol < 1.2)
               Conditions are genuinely bad; locking signals protects discipline.
      soft  — top5 empty but some secondary setups exist with decent scores
               Environment is marginal; worth watching but not forcing trades.
      none  — top5 exists; normal flow.
    """
    if top5:
        return {
            "is_no_trade":  False,
            "lock_signals": False,
            "verdict":      "",
            "reasons":      [],
            "severity":     "none",
        }

    tradeable = [s for s in ranked if s.get("trade_bias") != "Avoid"]

    # ── Diagnose each weakness ───────────────────────────────────────────────
    reasons = []

    # 1. Momentum check
    if tradeable:
        avg_mom = sum(s.get("momentum_score") or 0 for s in tradeable) / len(tradeable)
        max_mom = max((s.get("momentum_score") or 0) for s in tradeable)
    else:
        avg_mom = 0
        max_mom = 0

    low_momentum = avg_mom < 4
    if low_momentum:
        reasons.append(f"Low momentum across the board (avg {avg_mom:.1f}/10, best {max_mom}/10)")
    elif max_mom < 6:
        reasons.append(f"Best momentum is {max_mom}/10 — below the 6/10 threshold for A+ setups")

    # 2. Volume check
    if tradeable:
        avg_rvol = sum(s.get("rel_volume") or 0 for s in tradeable) / len(tradeable)
        max_rvol = max(s.get("rel_volume") or 0 for s in tradeable)
    else:
        avg_rvol = 0
        max_rvol = 0

    low_volume = avg_rvol < 1.2
    if low_volume:
        reasons.append(f"Low relative volume (avg {avg_rvol:.1f}x) — market participation weak")
    elif max_rvol < 1.5:
        reasons.append(f"Best relative volume is {max_rvol:.1f}x — not enough conviction")

    # 3. Structure / setup check
    orb_ready_count  = sum(1 for s in tradeable if s.get("orb_ready") == "YES")
    clean_entry_count = sum(1 for s in tradeable
                            if s.get("entry_quality") in ("Perfect", "Okay")
                            and s.get("trade_bias") != "Avoid")
    if orb_ready_count == 0:
        reasons.append("No stocks meet ORB readiness criteria (momentum ≥ 6, volume ≥ 1.5x, clean structure)")
    elif clean_entry_count == 0:
        reasons.append("No clean entries available — all setups are extended or structure is absent")

    # Cap at 3 reasons — most important already surfaced above
    reasons = reasons[:3]

    # ── Severity ─────────────────────────────────────────────────────────────
    # Hard lock: both momentum and volume are genuinely weak — real no-trade day
    hard = (avg_mom < 4 and avg_rvol < 1.2) or (not tradeable)
    severity = "hard" if hard else "soft"

    return {
        "is_no_trade":  True,
        "lock_signals": hard,
        "verdict":      "NO TRADE DAY — Protect Capital" if hard else "No A+ Setups — Caution",
        "reasons":      reasons,
        "severity":     severity,
    }


def compute_secondary_watchlist(ranked: list, top5_set: set) -> list:
    """
    Return B-setup / Watch-Only stocks that missed the Top 5 cut but are still worth tracking.

    Inclusion criteria (any one of):
      - momentum_score >= 4  (some energy but not A+)
      - rel_volume >= 1.5    (market is paying attention)
      - setup_score >= 5     (decent structure)

    Exclusion:
      - Already in Top 5
      - trade_bias == "Avoid"
      - exec_state == "TRIGGERED" (already highlighted in alert banner)

    Each stock gets a tier label:
      "B Setup"    — momentum ≥ 4 AND setup ≥ 5 (worthy of active monitoring)
      "Watch Only" — everything else that qualifies (on the radar but not acting)
    """
    secondary = []
    for s in ranked:
        if s.get("ticker") in top5_set:
            continue
        if s.get("trade_bias") == "Avoid":
            continue
        if s.get("exec_state") == "TRIGGERED":
            continue

        mom   = s.get("momentum_score") or 0
        rvol  = s.get("rel_volume")     or 0
        setup = s.get("setup_score")    or 0

        qualifies = mom >= 4 or rvol >= 1.5 or setup >= 5
        if not qualifies:
            continue

        if mom >= 4 and setup >= 5:
            s["secondary_tier"]       = "B Setup"
            s["secondary_tier_class"] = "tier-b-setup"
        else:
            s["secondary_tier"]       = "Watch Only"
            s["secondary_tier_class"] = "tier-watch"

        secondary.append(s)

    return secondary


def compute_summary_cards(stocks: list) -> dict:
    """
    Find the four featured stocks for the summary card row:
      best_gapper       — largest absolute gap %
      strongest_catalyst — highest catalyst_score
      highest_volume    — highest relative volume
      best_setup        — highest setup_score (Avoid excluded)
    Returns None for a slot if no suitable stock exists.
    """
    tradeable = [s for s in stocks if s.get("trade_bias") != "Avoid"]
    if not tradeable:
        return {"best_gapper": None, "strongest_catalyst": None,
                "highest_volume": None, "best_setup": None}
    return {
        "best_gapper":        max(tradeable, key=lambda s: abs(s.get("gap_pct")        or 0)),
        "strongest_catalyst": max(tradeable, key=lambda s:     s.get("catalyst_score") or 0),
        "highest_volume":     max(tradeable, key=lambda s:     s.get("rel_volume")     or 0),
        "best_setup":         max(tradeable, key=lambda s:     s.get("setup_score")    or 0),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    """Main dashboard — summary cards, top 5, and full ranked watchlist."""
    all_wls     = get_all_watchlists()
    active_wl_id = get_active_wl_id()
    active_wl    = get_watchlist_by_id(active_wl_id) if active_wl_id else None
    wl_counts    = get_watchlist_stock_counts()

    watchlist = get_watchlist_stocks(active_wl_id) if active_wl_id else []

    # Auto-refresh any stocks whose prev_close_date is stale (new trading day)
    if watchlist:
        auto_refresh_stale_closes(watchlist)

    all_data  = get_all_stock_data()

    data_map = {s["ticker"]: s for s in all_data}
    stocks   = [annotate(data_map[t]) for t in watchlist if t in data_map]
    missing  = [t for t in watchlist if t not in data_map]

    ranked     = rank_stocks(stocks)
    # Top 5: must have momentum ≥ 6, ORB ready, and entry not extended
    top5       = [
        s for s in ranked
        if (s.get("momentum_score") or 0) >= 6
        and s.get("orb_ready") == "YES"
        and s.get("entry_quality") != "Extended"
        and s.get("trade_bias") != "Avoid"
    ][:5]

    # No-trade assessment — must run before triggered list is built
    no_trade = compute_no_trade_assessment(ranked, top5)

    # Triggered: suppress entirely when signal lock is active (no-trade day)
    if no_trade["lock_signals"]:
        triggered = []
    else:
        triggered = [s for s in ranked if s.get("exec_state") == "TRIGGERED"]

    summary = compute_summary_cards(stocks)

    # Secondary watchlist — B setups and watch-only when Top 5 is thin/empty
    top5_tickers = {s["ticker"] for s in top5}
    secondary    = compute_secondary_watchlist(ranked, top5_tickers)

    # alt_modes kept for backward compat but no_trade replaces them in template
    alt_modes = []

    # Annotate summary cards (they're already annotated, but ensure consistency)
    for card in summary.values():
        if card:
            annotate(card)

    # Notes: pass a set of tickers that have notes for the indicator column
    notes_map    = get_all_notes()
    tickers_with_notes = set(notes_map.keys())

    return render_template(
        "dashboard.html",
        ranked=ranked,
        top5=top5,
        triggered=triggered,
        summary=summary,
        missing=missing,
        watchlist=watchlist,
        tickers_with_notes=tickers_with_notes,
        secondary=secondary,
        alt_modes=alt_modes,
        no_trade=no_trade,
        all_wls=all_wls,
        active_wl=active_wl,
        wl_counts=wl_counts,
        orb_session=get_orb_session_banner(),
    )


@app.route("/watchlist/add", methods=["POST"])
def watchlist_add():
    """Add one or more tickers to the active watchlist."""
    wl_id = get_active_wl_id()
    raw   = request.form.get("tickers", "")
    added = []
    for t in re.split(r"[\s,]+", raw.upper()):
        t = t.strip()
        if t and t.isalpha() and 1 <= len(t) <= 5 and wl_id:
            add_ticker_to_watchlist(wl_id, t)
            upsert_stock_data(generate_stock_data(t))
            run_auto_classification(t)
            added.append(t)

    if added:
        remaining = get_watchlist_stocks(wl_id)
        logger.info("WATCHLIST ADD  tickers=%s wl_id=%s", added, wl_id)
        logger.info("WATCHLIST SAVED  wl_id=%s contents=%s", wl_id, remaining)
        flash(f"Added: {', '.join(added)}", "success")
    else:
        flash("No valid tickers found. Use 1–5 letter stock symbols.", "error")
    return redirect(url_for("dashboard"))


@app.route("/watchlist/remove/<ticker>", methods=["POST"])
def watchlist_remove(ticker):
    """
    Remove a ticker from the active watchlist.

    Also removes it from ALL other default watchlists so auto-classification
    cannot silently move it back into a different default list after deletion.
    The ticker stays in any user-created custom watchlists (those are never
    touched by auto-classification anyway).
    """
    t = ticker.upper()
    wl_id = get_active_wl_id()
    if wl_id:
        logger.info("WATCHLIST REMOVE  ticker=%s wl_id=%s", t, wl_id)
        # Remove from the specific watchlist the user is viewing
        remove_ticker_from_watchlist(wl_id, t)
        # Remove from all other default lists so auto-classify can't re-add it
        remove_ticker_from_defaults(t)
        remaining = get_watchlist_stocks(wl_id)
        logger.info("WATCHLIST SAVED  wl_id=%s contents=%s", wl_id, remaining)
    flash(f"Removed {t} from watchlist.", "info")
    return redirect(url_for("dashboard"))


@app.route("/refresh", methods=["POST"])
def refresh_all():
    """Re-fetch and re-score all tickers in the active watchlist."""
    wl_id    = get_active_wl_id()
    watchlist = get_watchlist_stocks(wl_id) if wl_id else []
    for ticker in watchlist:
        upsert_stock_data(generate_stock_data(ticker))
        run_auto_classification(ticker)
    flash(f"Refreshed data for {len(watchlist)} tickers.", "success")
    return redirect(url_for("dashboard"))


@app.route("/stock/<ticker>")
def stock_detail(ticker):
    """Detailed view for a single stock."""
    ticker = ticker.upper()
    stock  = get_stock_data(ticker)
    if stock is None:
        flash(f"No data found for {ticker}.", "error")
        return redirect(url_for("dashboard"))

    annotate(stock)
    note       = get_note(ticker)
    breakdown  = catalyst_score_breakdown(stock)
    plan       = get_trade_plan(ticker)

    _, rr_display, rr_class = compute_rr(
        plan.get("plan_bias"),
        plan.get("entry_level"),
        plan.get("stop_loss"),
        plan.get("target_price"),
    )

    all_wls           = get_all_watchlists()
    ticker_wl_ids     = get_ticker_watchlist_ids(ticker)

    return render_template(
        "stock_detail.html",
        stock=stock,
        note=note,
        breakdown=breakdown,
        setup_types=SETUP_TYPES,
        plan=plan,
        rr_display=rr_display,
        rr_class=rr_class,
        all_wls=all_wls,
        ticker_wl_ids=ticker_wl_ids,
    )


@app.route("/stock/<ticker>/plan", methods=["POST"])
def save_stock_plan(ticker):
    """Save the pre-market structured trade plan for a ticker."""
    t = ticker.upper()
    save_trade_plan(
        ticker      = t,
        plan_bias   = request.form.get("plan_bias", ""),
        entry_level = request.form.get("entry_level", ""),
        stop_loss   = request.form.get("stop_loss", ""),
        target_price= request.form.get("target_price", ""),
    )
    flash("Pre-market plan saved.", "success")
    return redirect(url_for("stock_detail", ticker=t) + "#plan")


@app.route("/stock/<ticker>/notes", methods=["POST"])
def save_stock_note(ticker):
    """Save trade plan notes for a stock."""
    save_note(ticker.upper(), request.form.get("note_text", ""))
    flash("Notes saved.", "success")
    return redirect(url_for("stock_detail", ticker=ticker.upper()))


@app.route("/stock/<ticker>/refresh", methods=["POST"])
def refresh_single(ticker):
    """Refresh and re-score a single ticker."""
    t = ticker.upper()
    upsert_stock_data(generate_stock_data(t))
    run_auto_classification(t)
    flash(f"Refreshed {t}.", "success")
    return redirect(url_for("stock_detail", ticker=t))


@app.route("/stock/<ticker>/setup_type", methods=["POST"])
def set_setup_type(ticker):
    """
    Persist a manual setup type override for a ticker.
    Only updates the setup_type column — leaves all other data intact.
    The override survives refreshes until the user changes it again.
    """
    chosen = request.form.get("setup_type", "").strip()
    if chosen in SETUP_TYPES:
        update_setup_type(ticker.upper(), chosen)
        flash(f"Setup type updated to '{chosen}'.", "success")
    else:
        flash("Invalid setup type.", "error")
    return redirect(url_for("stock_detail", ticker=ticker.upper()))


# ---------------------------------------------------------------------------
# Trade Journal routes
# ---------------------------------------------------------------------------

@app.route("/journal")
def journal():
    """Trade journal — full history + summary stats."""
    entries = get_all_journal_entries()
    summary = compute_journal_summary(entries)
    edit_entry = None
    edit_id = request.args.get("edit")
    if edit_id:
        edit_entry = get_journal_entry(int(edit_id))
    return render_template(
        "journal.html",
        entries=entries,
        summary=summary,
        setup_types=SETUP_TYPES,
        edit_entry=edit_entry,
        today=datetime.now().strftime("%Y-%m-%d"),
    )


@app.route("/journal/add", methods=["POST"])
def journal_add():
    """Add a new journal entry."""
    direction   = request.form.get("direction", "Long")
    entry_price = request.form.get("entry_price", "")
    exit_price  = request.form.get("exit_price", "")
    pnl_pct, result = compute_pnl(direction, entry_price, exit_price)

    try:
        shares = int(request.form.get("shares", "")) if request.form.get("shares") else None
    except ValueError:
        shares = None
    try:
        momentum_score = int(request.form.get("momentum_score", "")) if request.form.get("momentum_score") else None
    except ValueError:
        momentum_score = None

    add_journal_entry(
        ticker         = request.form.get("ticker", ""),
        trade_date     = request.form.get("trade_date", datetime.now().strftime("%Y-%m-%d")),
        direction      = direction,
        entry_price    = float(entry_price) if entry_price else 0,
        exit_price     = float(exit_price)  if exit_price  else 0,
        shares         = shares,
        setup_type     = request.form.get("setup_type", ""),
        momentum_score = momentum_score,
        pnl_pct        = pnl_pct,
        result         = result,
        notes          = request.form.get("notes", ""),
    )
    flash(f"Trade logged — {result} ({'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%).", "success")
    return redirect(url_for("journal"))


@app.route("/journal/<int:entry_id>/edit", methods=["POST"])
def journal_edit(entry_id):
    """Update an existing journal entry."""
    direction   = request.form.get("direction", "Long")
    entry_price = request.form.get("entry_price", "")
    exit_price  = request.form.get("exit_price", "")
    pnl_pct, result = compute_pnl(direction, entry_price, exit_price)

    try:
        shares = int(request.form.get("shares", "")) if request.form.get("shares") else None
    except ValueError:
        shares = None
    try:
        momentum_score = int(request.form.get("momentum_score", "")) if request.form.get("momentum_score") else None
    except ValueError:
        momentum_score = None

    update_journal_entry(
        entry_id       = entry_id,
        ticker         = request.form.get("ticker", ""),
        trade_date     = request.form.get("trade_date", ""),
        direction      = direction,
        entry_price    = float(entry_price) if entry_price else 0,
        exit_price     = float(exit_price)  if exit_price  else 0,
        shares         = shares,
        setup_type     = request.form.get("setup_type", ""),
        momentum_score = momentum_score,
        pnl_pct        = pnl_pct,
        result         = result,
        notes          = request.form.get("notes", ""),
    )
    flash("Trade updated.", "success")
    return redirect(url_for("journal"))


@app.route("/journal/<int:entry_id>/delete", methods=["POST"])
def journal_delete(entry_id):
    """Delete a journal entry."""
    delete_journal_entry(entry_id)
    flash("Trade removed.", "info")
    return redirect(url_for("journal"))


# ---------------------------------------------------------------------------
# Watchlist management routes
# ---------------------------------------------------------------------------

@app.route("/watchlists/activate/<int:wl_id>", methods=["POST"])
def watchlist_activate(wl_id):
    """Switch the active watchlist (stored in session)."""
    session["active_wl_id"] = wl_id
    return redirect(url_for("dashboard"))


@app.route("/watchlists/create", methods=["POST"])
def watchlist_create():
    """Create a new named watchlist."""
    name = request.form.get("name", "").strip()
    if name:
        try:
            new_id = create_watchlist(name)
            session["active_wl_id"] = new_id
            flash(f"Watchlist '{name}' created.", "success")
        except Exception:
            flash("A watchlist with that name already exists.", "error")
    else:
        flash("Please enter a watchlist name.", "error")
    return redirect(url_for("dashboard"))


@app.route("/watchlists/rename/<int:wl_id>", methods=["POST"])
def watchlist_rename(wl_id):
    """Rename an existing watchlist."""
    name = request.form.get("name", "").strip()
    if name:
        try:
            rename_watchlist(wl_id, name)
            flash(f"Watchlist renamed to '{name}'.", "success")
        except Exception:
            flash("That name is already taken.", "error")
    return redirect(url_for("dashboard"))


@app.route("/watchlists/delete/<int:wl_id>", methods=["POST"])
def watchlist_delete(wl_id):
    """Delete a watchlist. Refuses to delete the last one."""
    all_wls = get_all_watchlists()
    if len(all_wls) <= 1:
        flash("Cannot delete the last watchlist.", "error")
        return redirect(url_for("dashboard"))
    delete_watchlist(wl_id)
    # If the deleted list was active, fall back to the first remaining list
    if session.get("active_wl_id") == wl_id:
        remaining = get_all_watchlists()
        if remaining:
            session["active_wl_id"] = remaining[0]["id"]
    flash("Watchlist deleted.", "info")
    return redirect(url_for("dashboard"))


@app.route("/stock/<ticker>/watchlists", methods=["POST"])
def stock_set_watchlists(ticker):
    """Update which watchlists a stock belongs to (from the detail page)."""
    t = ticker.upper()
    raw_ids    = request.form.getlist("watchlist_ids")
    wl_ids     = [int(i) for i in raw_ids if i.isdigit()]
    set_ticker_watchlists(t, wl_ids)
    flash("Watchlist assignment updated.", "success")
    return redirect(url_for("stock_detail", ticker=t))


@app.route("/stock/<ticker>/auto_classify", methods=["POST"])
def toggle_auto_classify(ticker):
    """Toggle the auto-classification flag for a ticker."""
    t       = ticker.upper()
    enabled = request.form.get("auto_classify") == "1"
    set_auto_classify(t, enabled)
    if enabled:
        # Run classification immediately so the user sees the result
        run_auto_classification(t)
        flash(f"Auto-classification ON for {t}. Stock moved to its recommended list.", "success")
    else:
        flash(f"Auto-classification OFF for {t}. You control the watchlist placement.", "info")
    return redirect(url_for("stock_detail", ticker=t))


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------

@app.route("/quick")
def quick_mode():
    """Mobile Quick Mode — top 3 priority stocks for fast decision-making."""
    wl_id     = get_active_wl_id()
    watchlist = get_watchlist_stocks(wl_id) if wl_id else []

    if watchlist:
        auto_refresh_stale_closes(watchlist)

    all_data = get_all_stock_data()
    data_map = {s["ticker"]: s for s in all_data}
    stocks   = [annotate(data_map[t]) for t in watchlist if t in data_map]
    ranked   = rank_stocks(stocks)
    top3     = [s for s in ranked if s.get("trade_bias") != "Avoid"][:3]

    # Worst-of-two confidence for display
    for s in top3:
        confs = [s.get("catalyst_confidence") or "Low", s.get("setup_confidence") or "Low"]
        s["combined_confidence"] = (
            "Low" if "Low" in confs else ("Medium" if "Medium" in confs else "High")
        )
        s["combined_conf_class"] = get_confidence_class(s["combined_confidence"])

    return render_template(
        "quick.html",
        stocks=top3,
        orb_session=get_orb_session_banner(),
    )


@app.route("/api/quick")
def api_quick():
    """JSON for Quick Mode live refresh — top 3 priority stocks."""
    try:
        return jsonify(_build_quick_payload(get_active_wl_id()))
    except Exception as exc:
        logger.error("api_quick failed: %s", exc, exc_info=True)
        return jsonify({"error": "quick refresh failed", "detail": str(exc)}), 500


# ---------------------------------------------------------------------------
# WebSocket endpoints — real-time push updates
# ---------------------------------------------------------------------------

@sock.route("/ws/dashboard")
def ws_dashboard(ws):
    """
    WebSocket endpoint for live dashboard updates.
    Sends an immediate snapshot on connect, then pushes fresh data every 15 s.
    Falls silent when the client disconnects.
    """
    wl_id = get_active_wl_id()   # session available during the WS handshake
    try:
        ws.send(_json.dumps(_build_dashboard_payload(wl_id)))
        last_push = _time.monotonic()
        while True:
            try:
                msg = ws.receive(timeout=1.0)
                if msg is None:
                    break   # clean client close
            except Exception:
                break       # network error or close
            if _time.monotonic() - last_push >= 15.0:
                ws.send(_json.dumps(_build_dashboard_payload(wl_id)))
                last_push = _time.monotonic()
    except Exception as exc:
        logger.debug("ws_dashboard closed (wl_id=%s): %s", wl_id, exc)


@sock.route("/ws/quick")
def ws_quick(ws):
    """
    WebSocket endpoint for Quick Mode live updates.
    Sends an immediate snapshot on connect, then pushes fresh data every 15 s.
    """
    wl_id = get_active_wl_id()
    try:
        ws.send(_json.dumps(_build_quick_payload(wl_id)))
        last_push = _time.monotonic()
        while True:
            try:
                msg = ws.receive(timeout=1.0)
                if msg is None:
                    break
            except Exception:
                break
            if _time.monotonic() - last_push >= 15.0:
                ws.send(_json.dumps(_build_quick_payload(wl_id)))
                last_push = _time.monotonic()
    except Exception as exc:
        logger.debug("ws_quick closed (wl_id=%s): %s", wl_id, exc)


@app.route("/api/watchlist")
def api_watchlist():
    """Return active watchlist as ranked JSON."""
    wl_id    = get_active_wl_id()
    watchlist = get_watchlist_stocks(wl_id) if wl_id else []
    data_map  = {s["ticker"]: s for s in get_all_stock_data()}
    stocks    = [data_map[t] for t in watchlist if t in data_map]
    return jsonify(rank_stocks(stocks))


def batch_refresh_exec_states(tickers: list[str], data_map: dict) -> dict:
    """
    Re-fetch live data and re-evaluate exec_state for all tickers in parallel.

    Uses a thread pool so yfinance calls run concurrently (one thread per ticker).
    If a ticker's exec_state or key live fields changed, persists the update via
    update_live_fields() so triggered_at timestamps stay accurate.

    Returns an updated data_map {ticker: refreshed_stock_dict}.
    """
    if not tickers:
        return data_map

    refreshed_map = dict(data_map)

    def _refresh_one(ticker):
        existing = data_map.get(ticker)
        if not existing:
            return ticker, None
        try:
            updated = live_refresh_stock(ticker, existing)
            return ticker, updated
        except Exception as exc:
            logger.warning("live_refresh_stock failed for %s: %s", ticker, exc)
            return ticker, None

    max_workers = min(len(tickers), 8)   # cap at 8 concurrent yfinance calls
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_refresh_one, t): t for t in tickers}
        for future in as_completed(futures):
            ticker, updated = future.result()
            if updated is None:
                continue
            refreshed_map[ticker] = updated
            # Persist if exec_state or any scored field changed
            old = data_map.get(ticker, {})
            _changed_fields = (
                "exec_state", "momentum_score", "setup_score", "orb_status",
                "orb_ready", "entry_quality", "order_block", "setup_type",
            )
            if any(updated.get(f) != old.get(f) for f in _changed_fields):
                try:
                    update_live_fields(updated)
                except Exception as exc:
                    logger.warning("update_live_fields failed for %s: %s", ticker, exc)

    return refreshed_map


def _stock_summary(s: dict) -> dict:
    """Return a JSON-safe subset of an annotated stock dict for live updates."""
    fields = [
        "ticker", "current_price", "gap_pct", "gap_display", "gap_class",
        "rel_volume", "avg_volume",
        "momentum_score", "momentum_reason", "momentum_confidence",
        "setup_score", "setup_reason", "setup_confidence", "setup_type",
        "catalyst_score", "catalyst_reason", "catalyst_confidence", "catalyst_summary",
        "catalyst_category", "headlines_fetched_at",
        "catalyst_tags", "headline_freshness",
        "exec_state", "exec_class",
        "orb_ready", "orb_class", "orb_high", "orb_low", "orb_status",
        "orb_status_class", "orb_phase", "orb_phase_label", "orb_phase_class",
        "orb_action", "orb_action_class", "orb_action_sub", "orb_price_pct",
        "order_block", "ob_class",
        "entry_quality", "entry_class", "entry_note",
        "trade_bias", "bias_class",
        "score_class", "cat_score_class", "mom_score_class",
        "freshness", "freshness_class",
        "setup_type_class",
        "last_updated",
        "position_size",
        "prev_close", "premarket_high", "premarket_low",
        "prev_day_high", "prev_day_low",
        "secondary_tier", "secondary_tier_class",
    ]
    return {f: s.get(f) for f in fields}


# ---------------------------------------------------------------------------
# Shared payload builders (used by both REST endpoints and WebSocket handlers)
# ---------------------------------------------------------------------------

def _build_dashboard_payload(wl_id: int | None) -> dict:
    """Compute and return the full dashboard data dict (no request context needed)."""
    watchlist = get_watchlist_stocks(wl_id) if wl_id else []
    all_data  = get_all_stock_data()
    data_map  = {s["ticker"]: s for s in all_data}
    data_map  = batch_refresh_exec_states([t for t in watchlist if t in data_map], data_map)
    stocks    = [annotate(data_map[t]) for t in watchlist if t in data_map]
    ranked    = rank_stocks(stocks)
    top5      = [
        s for s in ranked
        if (s.get("momentum_score") or 0) >= 6
        and s.get("orb_ready") == "YES"
        and s.get("entry_quality") != "Extended"
        and s.get("trade_bias") != "Avoid"
    ][:5]
    no_trade     = compute_no_trade_assessment(ranked, top5)
    triggered    = [] if no_trade["lock_signals"] else [s for s in ranked if s.get("exec_state") == "TRIGGERED"]
    top5_tickers = {s["ticker"] for s in top5}
    secondary    = compute_secondary_watchlist(ranked, top5_tickers)
    return {
        "type":        "dashboard",
        "server_time": datetime.now().strftime("%H:%M:%S"),
        "orb_session": get_orb_session_banner(),
        "no_trade":    no_trade,
        "triggered":   [_stock_summary(s) for s in triggered],
        "top5":        [_stock_summary(s) for s in top5],
        "secondary":   [_stock_summary(s) for s in secondary],
        "ranked":      [_stock_summary(s) for s in ranked],
    }


def _build_quick_payload(wl_id: int | None) -> dict:
    """Compute and return the quick-mode data dict (no request context needed)."""
    watchlist = get_watchlist_stocks(wl_id) if wl_id else []
    all_data  = get_all_stock_data()
    data_map  = {s["ticker"]: s for s in all_data}
    data_map  = batch_refresh_exec_states([t for t in watchlist if t in data_map], data_map)
    stocks    = [annotate(data_map[t]) for t in watchlist if t in data_map]
    ranked    = rank_stocks(stocks)
    top3      = [s for s in ranked if s.get("trade_bias") != "Avoid"][:3]
    out = []
    for s in top3:
        d = _stock_summary(s)
        confs = [s.get("catalyst_confidence") or "Low", s.get("setup_confidence") or "Low"]
        d["combined_confidence"] = "Low" if "Low" in confs else ("Medium" if "Medium" in confs else "High")
        d["combined_conf_class"] = get_confidence_class(d["combined_confidence"])
        out.append(d)
    return {
        "type":        "quick",
        "server_time": datetime.now().strftime("%H:%M:%S"),
        "orb_session": get_orb_session_banner(),
        "stocks":      out,
    }


@app.route("/api/dashboard")
def api_dashboard():
    """JSON endpoint for live dashboard updates (price, state, ORB, scores)."""
    try:
        return jsonify(_build_dashboard_payload(get_active_wl_id()))
    except Exception as exc:
        logger.error("api_dashboard failed: %s", exc, exc_info=True)
        return jsonify({"error": "dashboard refresh failed", "detail": str(exc)}), 500


@app.route("/api/stock/<ticker>/live")
def api_stock_live(ticker):
    """JSON endpoint for live single-stock detail updates."""
    ticker = ticker.upper()
    stock  = get_stock_data(ticker)
    if not stock:
        return jsonify({"error": "not found"}), 404
    # Re-evaluate exec_state with fresh live data
    try:
        stock = live_refresh_stock(ticker, stock)
        update_live_fields(stock)
    except Exception as exc:
        logger.warning("live_refresh_stock failed for %s: %s", ticker, exc)
    annotate(stock)
    return jsonify(_stock_summary(stock))


# ---------------------------------------------------------------------------
# Template context — helpers available in every template
# ---------------------------------------------------------------------------

@app.context_processor
def inject_helpers():
    return {
        "get_score_class":      get_score_class,
        "get_bias_class":       get_bias_class,
        "get_setup_type_class": get_setup_type_class,
        "get_confidence_class": get_confidence_class,
        "get_orb_class":        get_orb_class,
        "get_ob_class":         get_ob_class,
        "get_entry_class":      get_entry_class,
        "get_exec_class":       get_exec_class,
        "get_orb_status_class":  get_orb_status_class,
        "get_orb_phase_label":   get_orb_phase_label,
        "get_orb_action":        get_orb_action,
        "get_freshness_class":   get_freshness_class,
    }


# ---------------------------------------------------------------------------
# Deferred startup — seed demo data after all functions are defined.
# Safe to call every startup: the demo_seeded flag prevents re-seeding.
# ---------------------------------------------------------------------------
seed_demo_data()

_startup_wls = get_all_watchlists()
for _wl in _startup_wls:
    _tickers = get_watchlist_stocks(_wl["id"])
    logger.info(
        "STARTUP watchlist '%s' (id=%s): %s",
        _wl["name"], _wl["id"], _tickers,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\nRockkstaar Trade Assistant running...\n")
    app.run(host="0.0.0.0", port=5000, debug=False)