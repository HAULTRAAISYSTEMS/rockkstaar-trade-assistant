"""
app.py - Rockkstaar Trade Assistant
Flask web app for premarket stock watchlist scanning.
"""

import json as _json
import logging
import os
import re
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, Response
from flask_sock import Sock
from flask_wtf.csrf import CSRFProtect

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
    set_ticker_state, upsert_loading_placeholder,
    get_note, save_note, get_all_notes, update_setup_type,
    get_trade_plan, save_trade_plan, get_all_trade_plans,
    add_journal_entry, update_journal_entry, delete_journal_entry,
    get_journal_entry, get_all_journal_entries, get_journal_entries_for_date,
    get_daily_session, upsert_daily_session, lock_daily_session, unlock_daily_session,
)
from mock_data import generate_stock_data, load_mock_watchlist, live_refresh_stock, _swing_defaults, _zone_defaults
from data_fetcher import _et_now
from scoring import catalyst_score_breakdown, SETUP_TYPES, SWING_SETUP_TYPES, SWING_STATUSES, compute_swing_grade
from classifier import classify_stock
from alerts import generate_alerts, get_alerts, get_alert_count, clear_alerts as _clear_alerts

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Secret key — must come from SECRET_KEY env var in production.
# Warns loudly at startup if missing so it is never silently insecure.
# ---------------------------------------------------------------------------
_secret_key = os.environ.get("SECRET_KEY")
if not _secret_key:
    import warnings
    warnings.warn(
        "SECRET_KEY env var is not set — using insecure fallback. "
        "Set SECRET_KEY to a long random string in production.",
        stacklevel=1,
    )
    _secret_key = "rockkstaar-secret-key-change-in-prod"
app.secret_key = _secret_key

# ---------------------------------------------------------------------------
# CSRF protection — validates csrf_token on every POST/PUT/PATCH/DELETE form.
# The /risk/trading-mode AJAX route sends the token via X-CSRFToken header.
# ---------------------------------------------------------------------------
csrf = CSRFProtect(app)

# ---------------------------------------------------------------------------
# Write-endpoint auth — HTTP Basic Auth on every state-mutating request.
# Set APP_USER + APP_PASS env vars to enable. Both must be set; if either is
# missing the guard is disabled so local dev works without credentials.
# ---------------------------------------------------------------------------
@app.before_request
def _check_write_auth():
    _user = os.environ.get("APP_USER", "")
    _pass = os.environ.get("APP_PASS", "")
    if not _user or not _pass:
        return  # auth not configured — allow all (local dev / first deploy)
    if request.method in ("GET", "HEAD", "OPTIONS") or request.path == "/health":
        return  # read-only requests and health check always pass
    auth = request.authorization
    if not auth or auth.username != _user or auth.password != _pass:
        return Response(
            "Unauthorized",
            401,
            {"WWW-Authenticate": 'Basic realm="Rockkstaar Trade Assistant"'},
        )

sock = Sock(app)


@app.template_filter("et_time")
def et_time_filter(value: str | None) -> str:
    """Convert a stored timestamp to a clean ET time string for UI display.
    Handles both new format ("%Y-%m-%d %I:%M %p") and old UTC format ("%Y-%m-%d %H:%M:%S").
    Returns e.g. "8:05 PM".
    """
    if not value:
        return "—"
    s = str(value).strip()
    # New ET format: "2026-04-18 08:05 PM"
    try:
        dt = datetime.strptime(s[:19], "%Y-%m-%d %I:%M %p")
        return dt.strftime("%I:%M %p").lstrip("0")
    except ValueError:
        pass
    # Old server/UTC format: "2026-04-18 00:05:06" — convert naive UTC → ET
    try:
        from datetime import timezone
        import zoneinfo as _zi
        dt_utc = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        dt_et  = dt_utc.astimezone(_zi.ZoneInfo("America/New_York"))
        return dt_et.strftime("%I:%M %p").lstrip("0")
    except Exception:
        pass
    return s


# /health MUST be registered immediately — before any code that could crash
# during import. If anything below line 44 raises an exception, gunicorn
# still has this route and Render's health check succeeds.
@app.route("/health")
def health():
    return "OK", 200

# ---------------------------------------------------------------------------
# Startup initialization — idempotent schema creation.
# Wrapped in try/except so a slow or unavailable DB (e.g. PG cold start on
# Render) does not crash the import and prevent gunicorn from binding its port.
# ---------------------------------------------------------------------------
try:
    init_db()
except Exception as _init_err:
    logger.error("init_db failed at startup: %s — will retry on first request", _init_err)

# ---------------------------------------------------------------------------
# Startup migration: wipe stale mock-seeded prices from the DB.
#
# Old versions of mock_data.py seeded current_price directly from MOCK_STOCKS
# templates (NVDA=800, META=540, AMZN=190, etc.).  If these were written to
# the DB before the fix, the snapshot guard preserved them forever.
# On each startup we NULL out any price that exactly matches a known stale
# seed and set ticker_state=error so the auto-refresh retries the live fetch.
# ---------------------------------------------------------------------------
_STALE_MOCK_PRICES = {
    "NVDA": 800.0, "META": 540.0, "MRVL": 72.0,
    "AMZN": 190.0, "MU":   95.0,  "INTC": 23.0,
}

def _clear_stale_mock_prices():
    """Null out any DB prices that match the old mock seeds."""
    try:
        from database import get_db, get_stock_data
        for ticker, stale_price in _STALE_MOCK_PRICES.items():
            snap = get_stock_data(ticker)
            if snap and snap.get("current_price") == stale_price:
                conn = get_db()
                conn.execute(
                    "UPDATE stock_data SET current_price = NULL, prev_close = NULL, "
                    "gap_pct = NULL, ticker_state = 'error' WHERE ticker = ?",
                    (ticker,),
                )
                conn.commit()
                conn.close()
                logger.warning(
                    "startup migration: cleared stale mock price %.1f for %s → ticker_state=error",
                    stale_price, ticker,
                )
    except Exception as _e:
        logger.error("_clear_stale_mock_prices failed: %s", _e)

try:
    _clear_stale_mock_prices()
except Exception as _mig_err:
    logger.error("startup migration failed: %s", _mig_err)

# Global refresh lock — prevents overlapping bulk-refresh requests.
# Uses a threading.Lock() so concurrent gunicorn workers each have their own
# flag (cross-process locking is not needed for UX safety on a single user app).
_refresh_all_lock   = threading.Lock()
_refresh_all_running = False

# Per-ticker single-refresh guard — prevents double-clicking "Refresh Data"
# from spawning two simultaneous fetches for the same ticker.
_single_refresh_lock   = threading.Lock()
_single_refresh_active: set = set()   # set of ticker strings currently being refreshed

# Loading timeout: tickers stuck in 'loading' for longer than this are
# transitioned to 'error' so the Loading badge never shows forever.
LOADING_TIMEOUT_SECS = 120


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
    Identify stale tickers and kick off a background refresh for each one.

    The check (staleness detection) runs synchronously so we know which tickers
    need work, but the actual fetch (generate_stock_data) runs in a daemon
    thread so the dashboard HTTP response is NEVER blocked.

    A ticker is stale when:
      - It has not been refreshed today (last_updated date != today), OR
      - Its prev_close_date doesn't match the expected previous trading day.
    Error-state tickers always retry regardless of last_updated.

    Returns the list of ticker symbols that were queued for background refresh.
    """
    expected  = _prev_trading_day()
    today_str = _et_now().strftime("%Y-%m-%d")
    queued: list[str] = []

    for ticker in tickers:
        stock = get_stock_data(ticker)
        if not stock:
            continue

        last_updated  = (stock.get("last_updated") or "")[:10]
        current_state = stock.get("ticker_state") or "ready"
        is_error      = current_state == "error"

        if last_updated == today_str and not is_error:
            continue
        if (stock.get("prev_close_date") or "") == expected and not is_error:
            continue

        queued.append(ticker)
        logger.info(
            "auto_refresh  ticker=%s  stage=queued  prev_close_date=%s  "
            "expected=%s  state=%s",
            ticker,
            stock.get("prev_close_date") or "missing",
            expected,
            current_state,
        )

    if not queued:
        return []

    # Capture snapshot map once so the worker thread doesn't need a fresh DB
    # read per ticker (avoids a burst of DB calls from the thread).
    _all_existing = {s["ticker"]: s for s in get_all_stock_data()}

    def _worker():
        for ticker in queued:
            try:
                logger.info("auto_refresh  ticker=%s  stage=start", ticker)
                fresh  = generate_stock_data(ticker)
                result = _upsert_or_keep_snapshot(fresh, existing=_all_existing.get(ticker))
                if result == "updated":
                    run_auto_classification(ticker)
                logger.info(
                    "auto_refresh  ticker=%s  stage=complete  "
                    "prev_close_date=%s  state=%s  result=%s",
                    ticker,
                    fresh.get("prev_close_date") or "—",
                    fresh.get("ticker_state"),
                    result,
                )
            except Exception as e:
                logger.warning(
                    "auto_refresh  ticker=%s  stage=error  err=%s", ticker, e
                )
                try:
                    existing = _all_existing.get(ticker)
                    if existing and existing.get("current_price"):
                        set_ticker_state(ticker, "stale")
                    else:
                        set_ticker_state(ticker, "error")
                except Exception:
                    pass

    t = threading.Thread(target=_worker, daemon=True, name=f"auto_refresh_{','.join(queued)}")
    t.start()
    logger.info("auto_refresh  stage=bg_thread_started  tickers=%s", queued)
    return queued


def _expire_stuck_loading(watchlist: list) -> None:
    """
    Transition any ticker that has been in 'loading' state for longer than
    LOADING_TIMEOUT_SECS from 'loading' → 'error'.  Called on every dashboard
    load to prevent the Loading badge from persisting forever.

    Logs:
        expire_loading  ticker=X  age_secs=N  reason=timeout
    """
    now = _et_now().replace(tzinfo=None)
    for ticker in watchlist:
        stock = get_stock_data(ticker)
        if not stock or stock.get("ticker_state") != "loading":
            continue
        last_updated = stock.get("last_updated") or ""
        try:
            # Try current ET format first, then fall back to old UTC format
            try:
                updated_at = datetime.strptime(last_updated[:19], "%Y-%m-%d %I:%M %p")
            except ValueError:
                updated_at = datetime.strptime(last_updated[:19], "%Y-%m-%d %H:%M:%S")
            age_secs = (now - updated_at).total_seconds()
        except (ValueError, TypeError):
            age_secs = LOADING_TIMEOUT_SECS + 1  # unparseable timestamp → expire it

        if age_secs > LOADING_TIMEOUT_SECS:
            set_ticker_state(ticker, "error")
            logger.warning(
                "expire_loading  ticker=%s  age_secs=%.0f  reason=timeout  "
                "action=set_state_error",
                ticker, age_secs,
            )


def _upsert_or_keep_snapshot(fresh: dict, existing: dict | None = None) -> str:
    """
    Safe upsert: guards against overwriting a good DB snapshot when a live
    fetch fails.

    If ``fresh["ticker_state"] == "error"`` AND the existing DB record has a
    valid price, we keep the snapshot instead of writing NULL prices to the DB.
    The ticker state is set to "stale" so the UI badge updates accordingly.

    Returns one of:
        "updated"      — fresh data was upserted normally
        "stale_kept"   — live failed but a good snapshot exists; kept + marked stale
        "error_saved"  — live failed, no snapshot; error state upserted
    """
    ticker = fresh.get("ticker") or ""
    if fresh.get("ticker_state") == "error":
        snap = existing if existing is not None else get_stock_data(ticker)
        if snap and snap.get("current_price"):
            # Live fetch failed — protect the last-known-good snapshot
            fresh["data_source"] = "stale_snapshot"
            set_ticker_state(ticker, "stale")
            logger.warning(
                "_upsert_or_keep_snapshot  ticker=%s  live_failed=True  "
                "snapshot_price=%.2f  action=keep_snapshot  state=stale",
                ticker, float(snap["current_price"]),
            )
            return "stale_kept"
        else:
            # No usable snapshot — save the error state so the UI shows UNAVAILABLE
            fresh["data_source"] = "unavailable"
            upsert_stock_data(fresh)
            logger.info(
                "_upsert_or_keep_snapshot  ticker=%s  live_failed=True  "
                "snapshot=none  action=save_error",
                ticker,
            )
            return "error_saved"
    else:
        upsert_stock_data(fresh)
        return "updated"


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
    """CSS class for the setup type pill (day-trading and swing types)."""
    return {
        # Day-trading legacy types
        "Momentum Breakout":          "setup-momentum-breakout",
        "Momentum Runner":            "setup-momentum-runner",
        "Gap and Go":                 "setup-gap-go",
        "Breakdown":                  "setup-breakdown",
        "VWAP Reclaim":               "setup-vwap",
        "Range Break":                "setup-range",
        "ORB":                        "setup-orb",
        # Swing trading types
        "Pullback to Support":        "setup-pullback",
        "Breakout Retest Forming":    "setup-breakout-retest",
        "Extended Wait":              "setup-extended",
        "At Resistance Avoid":        "setup-resistance-avoid",
        "Near 50% Retracement":       "setup-fib50",
        "Near 61.8% Retracement":     "setup-fib618",
        "Order Block Test":           "setup-order-block",
        "Trend Continuation":         "setup-trend-continuation",
        "Weak Structure Avoid":       "setup-weak-structure",
        "No Setup":                   "setup-none",
    }.get(setup_type, "setup-none")


def get_swing_status_class(swing_status: str) -> str:
    """CSS class for the swing status badge."""
    return {
        # ── Current 4-mode labels ─────────────────────────────────────────────
        "READY — LEVEL HOLDS":        "swing-status-ready",
        "PRE-CONFIRMATION":           "swing-status-pre-confirm",
        "TREND CONTINUATION":         "swing-status-continuation",
        "WAIT":                       "swing-status-wait",
        # ── Legacy labels (backward compat for DB values) ─────────────────────
        "GOOD SWING CANDIDATE":       "swing-status-ready",
        "READY IF LEVEL HOLDS":       "swing-status-ready",
        "WAIT FOR 15M CONFIRMATION":  "swing-status-pre-confirm",
        "WAIT FOR PULLBACK":          "swing-status-wait",
        "TOO EXTENDED":               "swing-status-extended",
        "NOT ENOUGH EDGE":            "swing-status-no-edge",
        "AVOID AT RESISTANCE":        "swing-status-avoid",
        "AVOID WEAK STRUCTURE":       "swing-status-avoid",
    }.get(swing_status or "", "swing-status-wait")


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


# ---------------------------------------------------------------------------
# Final action — single source of truth for the UI decision label
# ---------------------------------------------------------------------------

_SWING_STATUS_ACTION = {
    # ── Current 4-mode labels ─────────────────────────────────────────────────
    "READY — LEVEL HOLDS":        ("READY",              "exec-ready",        "Level confirmed — entry valid, manage risk"),
    "PRE-CONFIRMATION":           ("PRE-CONFIRMATION",   "exec-pre-confirm",  "Potential entry forming — waiting for confirmation candle"),
    "TREND CONTINUATION":         ("TREND CONTINUATION", "exec-continuation", "Breakout entry — trade the continuation, stop below breakout"),
    "WAIT":                       ("WAIT",               "exec-wait",         "No valid setup — no actionable edge right now"),
    # ── Legacy labels (backward compat) ──────────────────────────────────────
    "GOOD SWING CANDIDATE":       ("READY",              "exec-ready",        "High-quality swing setup — watch for entry"),
    "READY IF LEVEL HOLDS":       ("READY",              "exec-ready",        "Price at key level — confirm holds before entry"),
    "WAIT FOR 15M CONFIRMATION":  ("PRE-CONFIRMATION",   "exec-pre-confirm",  "Structure in place — wait for 15m confirmation candle"),
    "WAIT FOR PULLBACK":          ("WAIT",               "exec-wait",         "Trend is right but extended — wait for pullback to level"),
    "TOO EXTENDED":               ("DO NOT CHASE",       "exec-extended",     "Price too far from entry zone — do not chase"),
    "NOT ENOUGH EDGE":            ("WAIT",               "exec-wait",         "Insufficient edge — no actionable swing setup"),
    "AVOID AT RESISTANCE":        ("WAIT",               "exec-wait",         "At resistance — poor R:R, avoid long entry"),
    "AVOID WEAK STRUCTURE":       ("WAIT",               "exec-wait",         "Weak market structure — avoid this setup"),
}


def compute_swing_final_action(swing_status: str) -> tuple:
    """Map swing_status directly to a final_action tuple (action, css, reason)."""
    row = _SWING_STATUS_ACTION.get(swing_status or "")
    if row:
        return row
    return "WAIT", "exec-wait", "Monitoring — conditions not yet met"


_FINAL_ACTION_CSS = {
    "TRIGGERED":            "exec-triggered",
    "READY":                "exec-ready",
    "PRE-CONFIRMATION":     "exec-pre-confirm",
    "TREND CONTINUATION":   "exec-continuation",
    "WAIT":                 "exec-wait",
    "WAIT (LOW CONF)":      "exec-wait-low",
    "DO NOT CHASE":         "exec-extended",
    "NO SETUP":             "exec-no-setup",
}


def get_final_action_class(final_action: str) -> str:
    return _FINAL_ACTION_CSS.get(final_action, "exec-wait")


def compute_final_action(
    setup_score: int,
    cat_score: int,
    combined_confidence: str,
    entry_quality: str | None,
    display_exec_state: str,
) -> tuple:
    """
    Derive the single final decision shown everywhere in the UI.
    Returns (final_action: str, css_class: str, reason: str).

    Priority order:
      1. TRIGGERED  — ORB system confirmed all entry conditions (session-aware)
      2. DO NOT CHASE — price already extended; never enter late
      3. READY      — scores ≥ 4 / 4 and confidence ≥ Medium
      4. WAIT (LOW CONF) — scores ≥ 4 / 4 but confidence Low
      5. READY      — ORB system says READY even if scores are borderline
      6. NO SETUP   — setup_score < 3; nothing actionable
      7. WAIT       — default / conditions not yet met

    The DB exec_state is never modified here; only the display layer changes.
    """
    # 1. Triggered by ORB system during regular hours — strongest signal
    if display_exec_state == "TRIGGERED":
        reason = "All entry conditions confirmed — act now"
        logger.debug(
            "final_action=TRIGGERED  setup=%s cat=%s conf=%s entry=%s",
            setup_score, cat_score, combined_confidence, entry_quality,
        )
        return "TRIGGERED", "exec-triggered", reason

    # 2. Extended entry — do not chase regardless of scores
    if (entry_quality or "").lower() == "extended":
        reason = "Price extended above entry zone — wait for pullback"
        logger.debug(
            "final_action=DO NOT CHASE  setup=%s cat=%s conf=%s entry=Extended",
            setup_score, cat_score, combined_confidence,
        )
        return "DO NOT CHASE", "exec-extended", reason

    # 3 & 4. Score-based decision (setup ≥ 4 and catalyst ≥ 4)
    if setup_score >= 4 and cat_score >= 4:
        if combined_confidence in ("High", "Medium"):
            reason = (
                f"Setup {setup_score}/10 · Catalyst {cat_score}/10 · "
                f"{combined_confidence} confidence"
            )
            logger.debug(
                "final_action=READY  setup=%s cat=%s conf=%s entry=%s",
                setup_score, cat_score, combined_confidence, entry_quality,
            )
            return "READY", "exec-ready", reason
        else:
            reason = (
                f"Scores strong (setup {setup_score}, catalyst {cat_score}) "
                "but confidence Low — wait for confirmation"
            )
            logger.debug(
                "final_action=WAIT (LOW CONF)  setup=%s cat=%s conf=%s entry=%s",
                setup_score, cat_score, combined_confidence, entry_quality,
            )
            return "WAIT (LOW CONF)", "exec-wait-low", reason

    # 5. ORB system says READY even if scores are borderline
    if display_exec_state == "READY":
        reason = "ORB conditions met — watching for entry signal"
        logger.debug(
            "final_action=READY (ORB)  setup=%s cat=%s conf=%s entry=%s",
            setup_score, cat_score, combined_confidence, entry_quality,
        )
        return "READY", "exec-ready", reason

    # 6. No setup
    if setup_score < 3:
        reason = f"Setup score {setup_score}/10 — no actionable pattern yet"
        logger.debug(
            "final_action=NO SETUP  setup=%s cat=%s conf=%s entry=%s",
            setup_score, cat_score, combined_confidence, entry_quality,
        )
        return "NO SETUP", "exec-no-setup", reason

    # 7. Default
    reason = "Conditions not yet met — continue monitoring"
    logger.debug(
        "final_action=WAIT  setup=%s cat=%s conf=%s entry=%s",
        setup_score, cat_score, combined_confidence, entry_quality,
    )
    return "WAIT", "exec-wait", reason


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


# ---------------------------------------------------------------------------
# Discipline & Risk Engine — pure functions (no DB, no Flask)
# ---------------------------------------------------------------------------

def get_risk_settings() -> dict:
    """Load all risk settings from the settings table with safe defaults."""
    def _f(key, default):
        v = get_setting(key)
        return v if v is not None else default
    return {
        "trading_mode":        _f("trading_mode",        "SWING TRADE"),
        "account_size":        float(_f("account_size",        "10000")),
        "risk_pct":            float(_f("risk_pct",            "1.0")),
        "max_trades_per_day":  int(float(_f("max_trades_per_day",  "3"))),
        "max_daily_loss_pct":  float(_f("max_daily_loss_pct",  "3.0")),
        "stop_after_2_losses": _f("stop_after_2_losses", "1") == "1",
    }


def compute_trade_permission(stock: dict, trade_mode: str) -> dict:
    """
    Returns {permission, css, reason}.
    permission: "TRADE ALLOWED" | "WATCH" | "BLOCKED"

    DAY TRADE:
      A+ = confirmed setup type (ORB / VWAP Reclaim / Momentum Breakout) with
           volume + momentum thresholds met. Catalyst boosts confidence but is not
           a hard gate — without it, volume and momentum thresholds are raised.
           Extension is independently checked beyond just the entry_quality label.

    SWING TRADE:
      A+ = trend aligned (4H + Daily) + valid structure (HH/HL) +
           price at tight key level (fib 61.8%/50%, pullback to 20/50 EMA) +
           R:R >= 1.5 + catalyst >= 3.
           Extension is independently detected from EMA distance.
    """
    bias  = stock.get("trade_bias") or ""
    entry = stock.get("entry_quality") or ""

    # Hard block — no directional edge
    if bias == "Avoid":
        return {"permission": "BLOCKED", "css": "perm-blocked",
                "reason": "Avoid bias — no directional edge, skip this stock"}

    # Label-based extension (existing system signal)
    if entry == "Extended":
        return {"permission": "BLOCKED", "css": "perm-blocked",
                "reason": "Entry extended — do not chase, wait for pullback to zone"}

    # ------------------------------------------------------------------ #
    # DAY TRADE                                                            #
    # ------------------------------------------------------------------ #
    if trade_mode == "DAY TRADE":
        setup_type   = (stock.get("setup_type") or "").upper()
        orb_ready    = stock.get("orb_ready") or "NO"
        orb_high     = stock.get("orb_high") or 0
        above_vwap   = bool(stock.get("price_above_vwap"))
        trend_struct = bool(stock.get("trend_structure"))   # HH + HL confirmed
        mom          = stock.get("momentum_score") or 0
        cat          = stock.get("catalyst_score") or 0
        rvol         = stock.get("rel_volume") or 0
        setup        = stock.get("setup_score") or 0
        current      = stock.get("current_price") or 0

        # Independent extension check — price >3% above ORB high = chasing
        if orb_high and current and current > orb_high * 1.03:
            pct_above = (current - orb_high) / orb_high * 100
            return {"permission": "BLOCKED", "css": "perm-blocked",
                    "reason": f"Entry extended {pct_above:.1f}% above ORB high — wait for pullback or base"}

        # ── ORB — Opening Range Breakout ──────────────────────────────
        # Catalyst >= 3: standard thresholds. No catalyst: raise volume + momentum bar.
        if orb_ready == "YES":
            if cat >= 3 and rvol >= 1.5 and mom >= 6:
                return {"permission": "TRADE ALLOWED", "css": "perm-allowed",
                        "reason": f"ORB confirmed — volume {rvol:.1f}x, momentum {mom}/10, catalyst {cat}/10"}
            if cat < 3 and rvol >= 2.0 and mom >= 7:
                return {"permission": "TRADE ALLOWED", "css": "perm-allowed",
                        "reason": f"ORB confirmed — volume {rvol:.1f}x, momentum {mom}/10 (no catalyst, higher vol/mom required)"}
            # Build exact WATCH reason
            needs = []
            if cat < 3:
                needs.append(f"catalyst {cat}/10 (need ≥3) OR volume ≥2.0x + momentum ≥7")
            else:
                if rvol < 1.5: needs.append(f"volume {rvol:.1f}x (need ≥1.5x)")
                if mom < 6:    needs.append(f"momentum {mom}/10 (need ≥6)")
            return {"permission": "WATCH", "css": "perm-watch",
                    "reason": "ORB forming — " + ", ".join(needs)}

        # ── VWAP Reclaim ──────────────────────────────────────────────
        if "VWAP" in setup_type or above_vwap:
            if not above_vwap:
                return {"permission": "WATCH", "css": "perm-watch",
                        "reason": f"VWAP setup detected but price not yet above VWAP — volume {rvol:.1f}x, momentum {mom}/10"}
            if cat >= 3 and rvol >= 1.3 and mom >= 5:
                return {"permission": "TRADE ALLOWED", "css": "perm-allowed",
                        "reason": f"VWAP reclaim confirmed — volume {rvol:.1f}x, momentum {mom}/10, catalyst {cat}/10"}
            if cat < 3 and rvol >= 1.8 and mom >= 6:
                return {"permission": "TRADE ALLOWED", "css": "perm-allowed",
                        "reason": f"VWAP reclaim confirmed — volume {rvol:.1f}x, momentum {mom}/10 (no catalyst, higher vol/mom required)"}
            needs = []
            if cat < 3:
                needs.append(f"catalyst {cat}/10 (need ≥3) OR volume ≥1.8x + momentum ≥6")
            else:
                if rvol < 1.3: needs.append(f"volume {rvol:.1f}x (need ≥1.3x)")
                if mom < 5:    needs.append(f"momentum {mom}/10 (need ≥5)")
            return {"permission": "WATCH", "css": "perm-watch",
                    "reason": "VWAP reclaim detected — " + ", ".join(needs)}

        # ── Momentum Breakout ─────────────────────────────────────────
        if "MOMENTUM" in setup_type or "BREAKOUT" in setup_type:
            if not trend_struct:
                return {"permission": "WATCH", "css": "perm-watch",
                        "reason": f"Momentum setup detected but HH/HL structure not confirmed — volume {rvol:.1f}x, momentum {mom}/10"}
            if cat >= 3 and rvol >= 1.5 and mom >= 7:
                return {"permission": "TRADE ALLOWED", "css": "perm-allowed",
                        "reason": f"Momentum breakout — HH/HL structure, volume {rvol:.1f}x, momentum {mom}/10, catalyst {cat}/10"}
            if cat < 3 and rvol >= 2.0 and mom >= 8:
                return {"permission": "TRADE ALLOWED", "css": "perm-allowed",
                        "reason": f"Momentum breakout — HH/HL structure, volume {rvol:.1f}x, momentum {mom}/10 (no catalyst, higher bar)"}
            needs = []
            if cat < 3:
                needs.append(f"catalyst {cat}/10 (need ≥3) OR volume ≥2.0x + momentum ≥8")
            else:
                if rvol < 1.5: needs.append(f"volume {rvol:.1f}x (need ≥1.5x)")
                if mom < 7:    needs.append(f"momentum {mom}/10 (need ≥7)")
            return {"permission": "WATCH", "css": "perm-watch",
                    "reason": "Momentum setup — " + ", ".join(needs)}

        # ── No confirmed setup type ───────────────────────────────────
        if setup >= 4 and (mom >= 4 or rvol >= 1.2):
            return {"permission": "WATCH", "css": "perm-watch",
                    "reason": f"Setup score {setup}/10, volume {rvol:.1f}x — no ORB/VWAP/Breakout pattern confirmed yet"}

        return {"permission": "BLOCKED", "css": "perm-blocked",
                "reason": f"No valid day trade setup — setup {setup}/10, volume {rvol:.1f}x, momentum {mom}/10"}

    # ------------------------------------------------------------------ #
    # SWING TRADE                                                          #
    # ------------------------------------------------------------------ #
    else:
        daily_trend = stock.get("daily_trend") or "Neutral"
        daily_hh_hl = bool(stock.get("daily_hh_hl"))
        h4_hh_hl    = bool(stock.get("h4_hh_hl"))
        pct_ema20   = stock.get("pct_from_ema20")   # positive = price above EMA
        pct_ema50   = stock.get("pct_from_ema50")
        fib_50      = stock.get("fib_50")
        fib_618     = stock.get("fib_618")
        current     = stock.get("current_price") or 0
        rr          = stock.get("risk_reward") or 0
        cat         = stock.get("catalyst_score") or 0
        swing       = stock.get("swing_score") or 0
        long_bias   = bias == "Long Bias"

        # Independent extension check — price too far from 20 EMA = chasing, not pullback
        # Long: >6% above 20 EMA means missed the move. Short: >6% below.
        if pct_ema20 is not None:
            if long_bias and pct_ema20 > 6.0:
                return {"permission": "BLOCKED", "css": "perm-blocked",
                        "reason": f"Entry extended — price {pct_ema20:+.1f}% above 20 EMA, wait for pullback to zone"}
            if not long_bias and pct_ema20 < -6.0:
                return {"permission": "BLOCKED", "css": "perm-blocked",
                        "reason": f"Entry extended — price {pct_ema20:+.1f}% below 20 EMA, wait for bounce to zone"}

        # Trend alignment — 4H and Daily must agree
        trend_bull    = long_bias      and daily_trend in ("Bullish", "Bullish Lean")
        trend_bear    = not long_bias  and daily_trend in ("Bearish", "Bearish Lean")
        trend_aligned = trend_bull or trend_bear

        if not trend_aligned:
            return {"permission": "BLOCKED", "css": "perm-blocked",
                    "reason": f"Trend not aligned — {bias or 'no bias'} vs {daily_trend} daily trend"}

        # Structure — HH/HL on Daily or 4H required before any entry
        structure_valid = daily_hh_hl or h4_hh_hl
        if not structure_valid:
            return {"permission": "BLOCKED", "css": "perm-blocked",
                    "reason": "No valid structure — need HH/HL confirmed on daily or 4H chart"}

        # At key level — tighter bands than before
        # Fib: within 1.5% | 20 EMA: within 2% pulling back | 50 EMA: within 3% pulling back
        # For longs, price should be AT or slightly below the EMA (pullback into zone).
        # Upper cap of +1.0% allows just-reclaimed EMA entries.
        FIB_TOL   = 1.5
        EMA20_TOL = 2.0
        EMA50_TOL = 3.0
        EMA_UPPER = 1.0   # price can be slightly above EMA on reclaim

        near_fib618 = bool(current and fib_618 and
                           abs(current - fib_618) / current * 100 <= FIB_TOL)
        near_fib50  = bool(current and fib_50 and
                           abs(current - fib_50) / current * 100 <= FIB_TOL)
        near_ema20  = (pct_ema20 is not None and
                       -EMA20_TOL <= pct_ema20 <= (EMA_UPPER if long_bias else EMA20_TOL))
        near_ema50  = (pct_ema50 is not None and
                       -EMA50_TOL <= pct_ema50 <= (EMA_UPPER if long_bias else EMA50_TOL))
        at_key_level = near_fib618 or near_fib50 or near_ema20 or near_ema50

        level_parts = []
        if near_fib618: level_parts.append("61.8% fib")
        if near_fib50:  level_parts.append("50% fib")
        if near_ema20 and pct_ema20 is not None:
            level_parts.append(f"20 EMA ({pct_ema20:+.1f}%)")
        if near_ema50 and pct_ema50 is not None:
            level_parts.append(f"50 EMA ({pct_ema50:+.1f}%)")

        # A+ — all gates pass
        if at_key_level and rr >= 1.5 and cat >= 3:
            level_str = " + ".join(level_parts) if level_parts else "key level"
            return {"permission": "TRADE ALLOWED", "css": "perm-allowed",
                    "reason": f"A+ swing — {level_str}, R:R {rr:.1f}:1, catalyst {cat}/10, score {swing}/10"}

        # WATCH — trend + structure aligned, one or more gates still open
        missing = []
        if not at_key_level:
            gap_parts = []
            if pct_ema20 is not None:
                gap_parts.append(f"20 EMA {pct_ema20:+.1f}% (pullback zone ±{EMA20_TOL}%)")
            if pct_ema50 is not None:
                gap_parts.append(f"50 EMA {pct_ema50:+.1f}% (pullback zone ±{EMA50_TOL}%)")
            if gap_parts:
                missing.append("not at level — " + ", ".join(gap_parts))
            else:
                missing.append("not at level — wait for pullback to 20/50 EMA or fib 50%/61.8%")
        if rr < 1.5:
            missing.append(f"R:R {rr:.1f}:1 (need ≥1.5:1)")
        if cat < 3:
            missing.append(f"catalyst {cat}/10 (need ≥3)")

        return {"permission": "WATCH", "css": "perm-watch",
                "reason": "Swing building — " + " · ".join(missing) if missing
                          else f"Swing score {swing}/10 — monitoring setup"}


def compute_options_risk(account_size: float, risk_pct: float,
                         premium: float | None, contracts: int | None) -> dict:
    """Calculate options risk metrics for the given account/risk parameters."""
    max_dollar_risk = round(account_size * (risk_pct / 100), 2)

    if premium and premium > 0:
        # Standard options lot = 100 shares per contract
        cost_per_contract = premium * 100
        suggested_contracts = max(1, int(max_dollar_risk / cost_per_contract))
        used_contracts  = contracts if (contracts and contracts > 0) else suggested_contracts
        total_cost      = round(used_contracts * cost_per_contract, 2)
    else:
        suggested_contracts = 0
        used_contracts      = contracts or 0
        total_cost          = 0

    return {
        "max_dollar_risk":    max_dollar_risk,
        "suggested_contracts": suggested_contracts,
        "total_cost":          total_cost,
    }


def compute_discipline_score(today_entries: list, risk_settings: dict,
                              locked: bool) -> dict:
    """
    Score today's trading discipline 0–100.
    Deductions: non-A+ setups (-15 each), excess trades (-10 each),
    broke stop (-10 each), trading locked (-25).
    """
    score      = 100
    deductions = []
    max_trades = risk_settings.get("max_trades_per_day", 3)

    if locked:
        score -= 25
        deductions.append("Daily limit hit — trading was locked (-25)")

    non_aplus = sum(1 for e in today_entries if not e.get("is_aplus_setup"))
    for _ in range(non_aplus):
        score -= 15
        deductions.append("Non-A+ setup taken (-15)")

    excess = max(0, len(today_entries) - max_trades)
    for _ in range(excess):
        score -= 10
        deductions.append("Over max trades per day (-10)")

    for e in today_entries:
        try:
            stop   = e.get("stop_price")
            exit_p = float(e.get("exit_price") or 0)
            if not stop:
                continue
            stop = float(stop)
            if e.get("direction") == "Long" and exit_p < stop - 0.01:
                score -= 10
                deductions.append(f"Stop broken on {e.get('ticker', '?')} (-10)")
            elif e.get("direction") == "Short" and exit_p > stop + 0.01:
                score -= 10
                deductions.append(f"Stop broken on {e.get('ticker', '?')} (-10)")
        except (TypeError, ValueError):
            pass

    score = max(0, min(100, score))

    if score >= 90:
        label, css = "Disciplined", "disc-high"
    elif score >= 70:
        label, css = "Average",     "disc-mid"
    else:
        label, css = "Undisciplined", "disc-low"

    return {"score": score, "label": label, "css": css, "deductions": deductions}


def check_auto_lock(today_entries: list, risk_settings: dict,
                    existing_session: dict) -> dict | None:
    """
    Check if today's journal entries trigger an auto-lock.
    Returns an updated session dict if locked, or None if no lock needed.
    """
    if existing_session.get("locked"):
        return None   # already locked — don't re-trigger

    max_trades   = risk_settings.get("max_trades_per_day", 3)
    stop_after_2 = risk_settings.get("stop_after_2_losses", True)

    if len(today_entries) >= max_trades:
        return {"locked": 1, "lock_reason": f"Max {max_trades} trades reached for today"}

    if stop_after_2:
        losses = sum(1 for e in today_entries if e.get("result") == "Loss")
        if losses >= 2:
            return {"locked": 1, "lock_reason": "2 losses reached — mandatory pause to protect capital"}

    return None


def compute_daily_banner(no_trade: dict, daily_session: dict) -> dict:
    """
    Return the top-of-dashboard banner based on trading conditions and session state.
    Priority: LOCKED > NO TRADE DAY > CAUTION > A+ ONLY
    """
    if daily_session.get("locked"):
        return {
            "type":  "locked",
            "text":  "TRADING LOCKED — PROTECT CAPITAL",
            "sub":   daily_session.get("lock_reason") or "Daily risk limit reached",
            "css":   "banner-locked",
        }

    severity = no_trade.get("severity", "none")
    reasons  = no_trade.get("reasons", [])
    sub_text = " · ".join(reasons) if reasons else ""

    if severity == "hard":
        return {
            "type": "no_trade",
            "text": "NO TRADE DAY — Protect Capital",
            "sub":  sub_text or "Market conditions are weak across the watchlist",
            "css":  "banner-no-trade",
        }

    if severity == "soft":
        return {
            "type": "caution",
            "text": "CAUTION — No A+ Setups Yet",
            "sub":  sub_text or "Wait for higher quality setups to develop",
            "css":  "banner-caution",
        }

    return {
        "type": "aplus",
        "text": "A+ ONLY MODE",
        "sub":  "Trade only the highest-quality setups — protect capital first",
        "css":  "banner-aplus",
    }


def compute_freshness(
    triggered_at: str | None,
    exec_state: str | None,
    session: str | None = None,
) -> tuple:
    """
    Determine the freshness / staleness label for a stock's exec state.
    Returns (label, css_class).  Both are None when exec_state != TRIGGERED.

    During regular market hours (session == 'regular'):
      triggered_at before 09:30 ET  → "Premarket Watch"   (reference, not live)
      elapsed < 15 min              → "Fresh Breakout"     (act now)
      elapsed 15–45 min             → "Active Move"        (still valid)
      elapsed > 45 min              → "Late Move"          (likely extended)

    Outside regular hours the trigger is stale regardless of elapsed time:
      pre_market                    → "Watch Next Session"
      after_hours / closed          → "Session Closed"
    """
    if exec_state != "TRIGGERED":
        return None, None

    # Determine session if not supplied
    if session is None:
        try:
            from data_fetcher import market_session_now
            session = market_session_now()
        except Exception:
            session = "regular"

    # Outside regular hours — trigger is stale, show display-only label
    if session == "pre_market":
        return "Watch Next Session", "fresh-premarket"
    if session in ("after_hours", "closed"):
        return "Session Closed", "fresh-expired"

    # Regular hours — age-based freshness
    if not triggered_at:
        return "Premarket Watch", "fresh-premarket"

    try:
        ts  = datetime.fromisoformat(triggered_at)
        now = datetime.now()

        # Triggered before this session's open → treat as premarket reference
        if ts.hour < 9 or (ts.hour == 9 and ts.minute < 30):
            return "Premarket Watch", "fresh-premarket"

        elapsed = (now - ts).total_seconds() / 60
        if elapsed < 15:
            return "Fresh Breakout", "fresh-breakout"
        if elapsed < 45:
            return "Active Move",    "fresh-active"
        return "Late Move", "fresh-late"
    except (ValueError, TypeError):
        return "Premarket Watch", "fresh-premarket"


def get_freshness_class(label: str | None) -> str:
    return {
        "Fresh Breakout":    "fresh-breakout",
        "Active Move":       "fresh-active",
        "Late Move":         "fresh-late",
        "Premarket Watch":   "fresh-premarket",
        "Watch Next Session":"fresh-premarket",
        "Session Closed":    "fresh-expired",
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


# ORB action directive — the trader-facing instruction for each ORB phase.
# These are the base entries used during regular market hours.
# Outside regular hours get_orb_action() overrides action/sub_label.
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

# Session-level override labels shown when market is not in regular hours.
_SESSION_OVERRIDE = {
    "pre_market":  ("WAIT",        "Pre-market — levels for reference only",         "orb-action-wait"),
    "after_hours": ("WATCH LEVELS","After hours — ORB levels for reference only",    "orb-action-wait"),
    "closed":      ("CLOSED",      "Market closed — review levels for next session", "orb-action-wait"),
}


def get_orb_action(orb_phase: str | None, session: str | None = None) -> dict:
    """
    Return the action directive dict for a given ORB phase.

    When session is provided (and is not 'regular'), the action word and
    sub_label are replaced with a display-only override so EXECUTE is never
    shown outside regular market hours.  The banner_class (colour) still
    reflects the ORB phase so the UI stays informative.

    Keys: action, sub_label, banner_class, action_class.
    """
    row = _ORB_ACTION_MAP.get(orb_phase or "locked", _ORB_ACTION_MAP["locked"])
    action_word   = row[0]
    sub_label     = row[1]
    banner_class  = row[2]
    action_class  = row[3]

    if session and session != "regular":
        override = _SESSION_OVERRIDE.get(session)
        if override:
            action_word  = override[0]
            sub_label    = override[1]
            action_class = override[2]

    return {
        "action":       action_word,
        "sub_label":    sub_label,
        "banner_class": banner_class,
        "action_class": action_class,
    }


def get_orb_session_banner() -> dict:
    """
    Compute the current global ORB session state from live ET time.
    Used by the dashboard banner — independent of any single stock.
    Includes the market session so the frontend always knows whether
    signals are currently live or display-only.
    """
    from data_fetcher import orb_phase_now, market_session_now
    phase   = orb_phase_now()
    session = market_session_now()
    label, phase_class = get_orb_phase_label(phase)
    action = get_orb_action(phase, session=session)
    return {
        "phase":        phase,
        "session":      session,
        "phase_label":  label,
        "phase_class":  phase_class,
        **action,
    }


def annotate(stock: dict) -> dict:
    """Add all display-only fields to a stock dict (non-destructive to DB fields)."""
    # ── Ticker state display ─────────────────────────────────────────────────
    _state = stock.get("ticker_state") or "ready"
    stock["ticker_state"] = _state
    stock["ticker_state_class"] = {
        "loading": "state-loading",
        "partial": "state-partial",
        "ready":   "state-ready",
        "error":   "state-error",
        "stale":   "state-stale",
    }.get(_state, "state-ready")
    stock["ticker_state_label"] = {
        "loading": "Loading",
        "partial": "Partial",
        "ready":   "",
        "error":   "Data Error",
        "stale":   "Stale",
    }.get(_state, "")

    # ── Data source — for debugging and display ──────────────────────────────
    # Tracks WHERE the price came from (live fetch vs snapshot vs unavailable).
    # "live"           — price confirmed from yfinance this session
    # "stale_snapshot" — using last-known-good DB price (live fetch failed)
    # "unavailable"    — no valid price at all
    _src = stock.get("data_source") or (
        "stale_snapshot" if _state == "stale" else
        "live"           if _state in ("ready", "partial") else
        "unavailable"
    )
    stock["data_source"] = _src
    stock["data_source_label"] = {
        "live":            "Live",
        "stale_snapshot":  "Stale snapshot",
        "unavailable":     "Unavailable",
    }.get(_src, "Unknown")

    # Score defaults are always applied — they're needed for JS filter/sort logic.
    _SCORE_DEFAULTS = {
        "catalyst_score":  0,
        "momentum_score":  0,
        "setup_score":     0,
    }
    for field, default in _SCORE_DEFAULTS.items():
        if stock.get(field) is None:
            stock[field] = default

    # Price fields: never force 0.0 — keep None so templates can show "—"/"N/A"
    # instead of misleading "$0.00". Only rel_volume gets a safe 0.0 default so
    # the "Nx" multiplier display renders without crashing.
    if stock.get("rel_volume") is None and _state not in ("error", "loading"):
        stock["rel_volume"] = 0.0

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
    # ── Session-aware display state ─────────────────────────────────────────
    # Get session once per annotate call so all derived fields are consistent.
    try:
        from data_fetcher import market_session_now
        _session = market_session_now()
    except Exception:
        _session = "regular"

    # display_exec_state: what is shown in the UI.  Never stored in the DB.
    # The DB exec_state (TRIGGERED / READY / WAIT) is preserved for audit and
    # alert detection.  Outside regular hours we downgrade the display so stale
    # TRIGGERED states are never presented as immediately actionable.
    _raw_exec = stock.get("exec_state") or "WAIT"
    if _raw_exec == "TRIGGERED" and _session != "regular":
        _display_exec = "WAIT"          # downgrade display — not actionable now
    else:
        _display_exec = _raw_exec

    stock["display_exec_state"]    = _display_exec

    # ── Combined confidence (worst-of-two: catalyst + setup) ────────────────
    _confs = [
        stock.get("catalyst_confidence") or "Low",
        stock.get("setup_confidence")    or "Low",
    ]
    _combined_conf = "Low" if "Low" in _confs else ("Medium" if "Medium" in _confs else "High")
    stock["combined_confidence"]   = _combined_conf
    stock["combined_conf_class"]   = get_confidence_class(_combined_conf)

    # ── Final action — single source of truth for all UI decision labels ────
    _fa, _fa_class, _fa_reason = compute_final_action(
        setup_score         = stock.get("setup_score")    or 0,
        cat_score           = stock.get("catalyst_score") or 0,
        combined_confidence = _combined_conf,
        entry_quality       = stock.get("entry_quality"),
        display_exec_state  = _display_exec,
    )
    stock["final_action"]          = _fa
    stock["final_action_class"]    = _fa_class
    stock["final_action_reason"]   = _fa_reason
    stock["exec_class"]            = _fa_class          # drives every exec badge in the UI
    stock["orb_status_class"]      = get_orb_status_class(stock.get("orb_status") or "NO_ORB")
    orb_phase_label, orb_phase_class = get_orb_phase_label(stock.get("orb_phase"))
    stock["orb_phase_label"]       = orb_phase_label
    stock["orb_phase_class"]       = orb_phase_class
    orb_action                     = get_orb_action(stock.get("orb_phase"), session=_session)
    stock["orb_action"]            = orb_action["action"]
    stock["orb_action_class"]      = orb_action["action_class"]
    stock["orb_action_sub"]        = orb_action["sub_label"]
    freshness, freshness_class     = compute_freshness(
        stock.get("triggered_at"), _raw_exec, session=_session
    )
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
    # ── Gap calculation ─────────────────────────────────────────────────────
    # Primary: stored gap_pct from fetch_live_data().
    # Fallback: derive on the fly from current_price + prev_close.
    # This ensures gap is never missing when both price fields are available.
    gap = stock.get("gap_pct")
    if gap is None:
        _cp = stock.get("current_price")
        _pc = stock.get("prev_close")
        if _cp and _pc and _pc > 0:
            gap = round((_cp - _pc) / _pc * 100, 2)
            stock["gap_pct"] = gap

    if gap is not None:
        stock["gap_display"] = f"{'+' if gap >= 0 else ''}{gap:.2f}%"
        stock["gap_class"]   = "positive" if gap >= 0 else "negative"
    else:
        stock["gap_display"] = "—"
        stock["gap_class"]   = ""

    # ── Data availability flags (used by detail page to guard stale sections) ─
    # swing_data_available: True if EMA/fib data was fetched (swing pipeline ran)
    # swing_plan_valid:     True if a computed trade plan exists AND swing data is fresh
    # swing_plan_stale:     True if plan fields are in DB but swing data is missing
    _has_ema        = bool(stock.get("ema_20_daily"))
    _has_fibs       = bool(stock.get("fib_high") and stock.get("fib_low"))
    _has_plan_fields = bool(stock.get("entry_zone_low") or stock.get("stop_level"))
    stock["swing_data_available"] = _has_ema
    stock["fib_data_available"]   = _has_fibs
    stock["swing_plan_valid"]     = _has_plan_fields and _has_ema
    stock["swing_plan_stale"]     = _has_plan_fields and not _has_ema

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

    # ── Swing trading display fields ─────────────────────────────────────────
    _swing_score = stock.get("swing_score")
    if _swing_score is None:
        stock["swing_score"] = 0
    stock["swing_score_class"]      = get_score_class(stock.get("swing_score"))
    stock["swing_status_class"]     = get_swing_status_class(stock.get("swing_status"))
    stock["swing_setup_type_class"] = get_setup_type_class(stock.get("swing_setup_type") or "No Setup")
    stock["swing_grade"]            = compute_swing_grade(stock.get("swing_score") or 1)

    # ── Plan mode display helpers ─────────────────────────────────────────────
    _plan_mode = stock.get("plan_mode") or "none"
    stock["plan_mode_label"] = {
        "confirmed":        "CONFIRMED",
        "pre_confirmation": "PRE-CONFIRMATION SETUP",
        "continuation":     "TREND CONTINUATION",
        "watching":         "WATCHING",
    }.get(_plan_mode, "")
    stock["plan_mode_class"] = {
        "confirmed":        "plan-confirmed",
        "pre_confirmation": "plan-pre-confirm",
        "continuation":     "plan-continuation",
        "watching":         "plan-watching",
    }.get(_plan_mode, "")

    # ── Swing confidence display (1-3=Low, 4-6=Medium, 7-10=High) ────────────
    _sc = stock.get("swing_score") or 0
    stock["swing_confidence_label"] = (
        "High"   if _sc >= 7 else
        "Medium" if _sc >= 4 else
        "Low"
    )

    # ── Entry zone distance (how far current price is from the entry zone) ────
    _cur   = stock.get("current_price") or 0
    _ez_lo = stock.get("entry_zone_low")
    if _cur and _ez_lo:
        _d = (_cur - _ez_lo) / _ez_lo * 100
        if abs(_d) < 0.5:
            stock["entry_distance_pct"]     = 0.0
            stock["entry_distance_display"] = "AT ZONE"
            stock["entry_distance_class"]   = "dist-at-zone"
        elif 0 < _d <= 3.0:
            stock["entry_distance_pct"]     = round(_d, 1)
            stock["entry_distance_display"] = f"+{_d:.1f}%"
            stock["entry_distance_class"]   = "dist-near"
        elif _d > 3.0:
            stock["entry_distance_pct"]     = round(_d, 1)
            stock["entry_distance_display"] = f"+{_d:.1f}% above"
            stock["entry_distance_class"]   = "dist-extended"
        else:
            stock["entry_distance_pct"]     = round(_d, 1)
            stock["entry_distance_display"] = f"{abs(_d):.1f}% to zone"
            stock["entry_distance_class"]   = "dist-below"
    else:
        stock["entry_distance_pct"]     = None
        stock["entry_distance_display"] = "—"
        stock["entry_distance_class"]   = ""

    # Distance to T1 / first resistance target
    _t1 = stock.get("target_1")
    if _cur and _t1 and _t1 > _cur:
        stock["resistance_distance_display"] = f"+{(_t1 - _cur) / _cur * 100:.1f}% to T1"
    elif _cur and _t1 and _t1 < _cur:
        stock["resistance_distance_display"] = f"T1 below price"
    else:
        stock["resistance_distance_display"] = "—"

    # Extension flag (for dashboard filter: price >8% from 20 EMA in trend direction)
    _pct20 = stock.get("pct_from_ema20")
    _bias  = stock.get("trade_bias") or "Neutral"
    if _pct20 is not None:
        _ext_dir = (_bias == "Long Bias" and _pct20 > 0) or (_bias == "Short Bias" and _pct20 < 0)
        stock["is_extended"] = bool(_ext_dir and abs(_pct20) > 8.0)
    else:
        stock["is_extended"] = False

    # Pullback quality label (clean vs moderate vs weak)
    _stype  = stock.get("swing_setup_type") or ""
    _in_dem = stock.get("in_demand_zone", False)
    _hh_hl  = stock.get("daily_hh_hl", False)
    _h4_hh  = stock.get("h4_hh_hl", False)
    _clean_types = {"Order Block Test", "Near 61.8% Retracement", "Breakout Retest"}
    _mod_types   = {"Near 50% Retracement", "Pullback to 20 EMA", "Pullback to 50 EMA"}
    if _stype in _clean_types and (_hh_hl or _h4_hh or _in_dem):
        stock["pullback_quality"]       = "Clean"
        stock["pullback_quality_class"] = "pq-clean"
    elif _stype in _clean_types or (_stype in _mod_types and (_hh_hl or _h4_hh)):
        stock["pullback_quality"]       = "Good"
        stock["pullback_quality_class"] = "pq-good"
    elif _stype in _mod_types:
        stock["pullback_quality"]       = "Moderate"
        stock["pullback_quality_class"] = "pq-moderate"
    elif _stype in ("Extended — Wait", "At Resistance — Avoid", "Weak Structure — Avoid"):
        stock["pullback_quality"]       = "Weak"
        stock["pullback_quality_class"] = "pq-weak"
    else:
        stock["pullback_quality"]       = "Watch"
        stock["pullback_quality_class"] = "pq-watch"

    # Format entry zone as "low – high" display string
    ez_low  = stock.get("entry_zone_low")
    ez_high = stock.get("entry_zone_high")
    if ez_low and ez_high:
        stock["entry_zone_display"] = f"${ez_low:.2f} – ${ez_high:.2f}"
    elif ez_low:
        stock["entry_zone_display"] = f"~${ez_low:.2f}"
    else:
        stock["entry_zone_display"] = "—"

    # Format risk/reward
    rr = stock.get("risk_reward")
    if rr:
        stock["risk_reward_display"] = f"{rr:.1f}:1"
        stock["risk_reward_class"]   = "rr-good" if rr >= 2.0 else ("rr-okay" if rr >= 1.0 else "rr-poor")
    else:
        stock["risk_reward_display"] = "—"
        stock["risk_reward_class"]   = "rr-neutral"

    # R:R quality label — shown as a warning when R:R is poor
    if rr is not None:
        if rr < 1.0:
            stock["rr_quality_label"] = "Poor R:R — avoid"
            stock["rr_quality_class"] = "rr-poor-label"
        elif rr < 1.5:
            stock["rr_quality_label"] = "Weak R:R"
            stock["rr_quality_class"] = "rr-weak-label"
        else:
            stock["rr_quality_label"] = ""
            stock["rr_quality_class"] = ""
    else:
        stock["rr_quality_label"] = ""
        stock["rr_quality_class"] = ""

    # If swing_score is populated, override final_action from swing_status
    if stock.get("swing_score"):
        _sfa, _sfa_class, _sfa_reason = compute_swing_final_action(stock.get("swing_status"))
        stock["final_action"]       = _sfa
        stock["final_action_class"] = _sfa_class
        stock["final_action_reason"]= _sfa_reason
        stock["exec_class"]         = _sfa_class

    # ── Trade permission (requires trading mode from settings) ───────────────
    try:
        _trade_mode = get_setting("trading_mode") or "SWING TRADE"
        stock["trade_permission"] = compute_trade_permission(stock, _trade_mode)
    except Exception:
        stock["trade_permission"] = {"permission": "WATCH", "css": "perm-watch", "reason": ""}

    # ── Simplified 4-state decision badge ────────────────────────────────────
    # Maps all conditions to one clear morning label: A+ READY / WATCH / EXTENDED / REJECTED
    # Rules (in priority order):
    #   REJECTED  — Avoid bias, structural avoid statuses, or swing_score < 3
    #   EXTENDED  — price is_extended flag, entry_quality=Extended, or >5% above entry zone
    #   A+ READY  — swing_score ≥ 7, actionable status, catalyst ≥ 4, R:R ≥ 1.5
    #   WATCH     — everything else (decent setup, conditions not fully aligned)
    _sfa_ss    = stock.get("swing_status") or ""
    _sfa_sw    = stock.get("swing_score") or 0
    _sfa_rr    = stock.get("risk_reward")
    _sfa_ext   = stock.get("is_extended", False)
    _sfa_eq    = stock.get("entry_quality") or ""
    _sfa_bias  = stock.get("trade_bias") or ""
    _sfa_cat   = stock.get("catalyst_score") or 0
    _sfa_edist = stock.get("entry_distance_pct") or 0

    _sfa_is_rejected = (
        _sfa_bias == "Avoid" or
        _sfa_ss in ("AVOID AT RESISTANCE", "AVOID WEAK STRUCTURE") or
        (_sfa_sw > 0 and _sfa_sw < 3)
    )
    _sfa_is_extended = (
        _sfa_ext or
        _sfa_eq == "Extended" or
        (isinstance(_sfa_edist, (int, float)) and _sfa_edist > 5.0)
    )
    _sfa_is_aplus = (
        not _sfa_is_rejected and
        not _sfa_is_extended and
        _sfa_sw >= 7 and
        _sfa_ss in ("READY — LEVEL HOLDS", "PRE-CONFIRMATION", "TREND CONTINUATION") and
        _sfa_cat >= 4 and
        (_sfa_rr is None or _sfa_rr >= 1.5)
    )

    if _sfa_is_rejected:
        stock["simplified_action"]       = "REJECTED"
        stock["simplified_action_class"] = "sfa-rejected"
    elif _sfa_is_extended:
        stock["simplified_action"]       = "EXTENDED"
        stock["simplified_action_class"] = "sfa-extended"
    elif _sfa_is_aplus:
        stock["simplified_action"]       = "A+ READY"
        stock["simplified_action_class"] = "sfa-aplus"
    else:
        stock["simplified_action"]       = "WATCH"
        stock["simplified_action_class"] = "sfa-watch"

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
        # Swing score is primary when populated; fall back to day-trading setup_score
        _swing = s.get("swing_score") or 0
        _setup = s.get("setup_score") or 0
        primary  = (_swing * 8) if _swing else (_setup * 8)
        catalyst = (s.get("catalyst_score") or 0) * 2
        rvol     = min((s.get("rel_volume") or 0) * 1.5, 10)
        # Penalise extended/avoid statuses
        _status  = s.get("swing_status") or ""
        penalty  = -20 if _status in (
            "WAIT", "TOO EXTENDED", "AVOID AT RESISTANCE", "AVOID WEAK STRUCTURE"
        ) else (-5 if _status == "TREND CONTINUATION" else 0)
        return primary + catalyst + rvol + penalty

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

    # 1. Swing score check (primary signal in swing mode)
    swing_scores = [s.get("swing_score") or 0 for s in tradeable]
    has_swing_data = any(swing_scores)

    if has_swing_data:
        avg_swing = sum(swing_scores) / len(swing_scores) if swing_scores else 0
        max_swing = max(swing_scores) if swing_scores else 0
        low_swing = avg_swing < 4
        if low_swing:
            reasons.append(f"Low swing score across watchlist (avg {avg_swing:.1f}/10, best {max_swing}/10)")
        elif max_swing < 6:
            reasons.append(f"Best swing score is {max_swing}/10 — below the 6/10 threshold for A+ setups")

        # Check for structural issues
        avoid_count = sum(1 for s in tradeable if s.get("swing_status") in
                          ("TOO EXTENDED", "AVOID AT RESISTANCE", "AVOID WEAK STRUCTURE"))
        if avoid_count == len(tradeable):
            reasons.append("All stocks are extended or at resistance — wait for pullbacks")
        elif not any((s.get("swing_score") or 0) >= 6 for s in tradeable):
            reasons.append("No stocks have swing score ≥ 6 — no A+ setups forming")
    else:
        # Fall back to day-trading momentum assessment
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

    low_volume = avg_rvol < 0.8
    if low_volume:
        reasons.append(f"Low relative volume (avg {avg_rvol:.1f}x) — market participation weak")

    # Cap at 3 reasons
    reasons = reasons[:3]

    # ── Severity ─────────────────────────────────────────────────────────────
    if has_swing_data:
        hard = (not tradeable) or (max_swing < 4)
    else:
        avg_mom_val = sum(s.get("momentum_score") or 0 for s in tradeable) / max(len(tradeable), 1)
        hard = (avg_mom_val < 4 and avg_rvol < 1.2) or (not tradeable)
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
        if s.get("display_exec_state") == "TRIGGERED":
            continue

        swing = s.get("swing_score")    or 0
        mom   = s.get("momentum_score") or 0
        rvol  = s.get("rel_volume")     or 0
        setup = s.get("setup_score")    or 0

        # Swing mode: score ≥ 4 qualifies; day-trading fallback otherwise
        if swing:
            qualifies = swing >= 4 or rvol >= 1.0
        else:
            qualifies = mom >= 4 or rvol >= 1.5 or setup >= 5
        if not qualifies:
            continue

        # Tier assignment
        if swing >= 6:
            s["secondary_tier"]       = "B Setup"
            s["secondary_tier_class"] = "tier-b-setup"
        elif swing >= 4 or (not swing and mom >= 4 and setup >= 5):
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
        "best_setup":         max(tradeable, key=lambda s:     s.get("swing_score") or s.get("setup_score") or 0),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
# ROUTE REFERENCE — keep this list in sync when adding/renaming routes.
# Every url_for() call in templates MUST use one of these endpoint names.
#
#   Endpoint name          Method   Path
#   ─────────────────────────────────────────────────────────────────────
#   dashboard              GET      /
#   watchlist_add          POST     /watchlist/add
#   watchlist_remove       POST     /watchlist/remove/<ticker>
#   refresh_all            POST     /refresh
#   stock_detail           GET      /stock/<ticker>
#   refresh_single         POST     /stock/<ticker>/refresh
#   save_stock_plan        POST     /stock/<ticker>/plan
#   save_stock_note        POST     /stock/<ticker>/notes
#   set_setup_type         POST     /stock/<ticker>/setup_type
#   stock_set_watchlists   POST     /stock/<ticker>/watchlists
#   toggle_auto_classify   POST     /stock/<ticker>/auto_classify
#   watchlist_activate     POST     /watchlists/activate/<wl_id>
#   watchlist_create       POST     /watchlists/create
#   watchlist_rename       POST     /watchlists/rename/<wl_id>
#   watchlist_delete       POST     /watchlists/delete/<wl_id>
#   journal                GET      /journal
#   journal_add            POST     /journal/add
#   journal_edit           POST     /journal/<entry_id>/edit
#   journal_delete         POST     /journal/<entry_id>/delete
#   quick_mode             GET      /quick
#   api_dashboard          GET      /api/dashboard
#   api_stock_live         GET      /api/stock/<ticker>/live
#   api_watchlist          GET      /api/watchlist
# ---------------------------------------------------------------------------

_DASHBOARD_EMPTY = dict(
    ranked=[], top5=[], triggered=[], summary={},
    missing=[], watchlist=[], notes={}, secondary=[],
    alt_modes=[], all_wls=[], active_wl=None, wl_counts={},
    no_trade={"is_no_trade": False, "lock_signals": False, "verdict": "",
              "reasons": [], "severity": "none"},
    orb_session={},
    alerts=[],
    risk_settings={"trading_mode": "SWING TRADE", "account_size": 10000,
                   "risk_pct": 1.0, "max_trades_per_day": 3,
                   "max_daily_loss_pct": 3.0, "stop_after_2_losses": True},
    daily_session={"locked": 0, "lock_reason": None},
    discipline={"score": 100, "label": "Disciplined", "css": "disc-high", "deductions": []},
    daily_banner={"type": "aplus", "text": "A+ ONLY MODE",
                  "sub": "Trade only the highest-quality setups", "css": "banner-aplus"},
    trades_today=0,
    losses_today=0,
    market_temp={"regime": "UNKNOWN", "label": "—", "css": "mt-unknown",
                 "reason": "", "longs_ok": None, "shorts_ok": None,
                 "reduce_size": False, "score": None, "error": True,
                 "spy_price": None, "spy_pct_ema20": None, "spy_vs_vwap": None,
                 "qqq_price": None, "qqq_pct_ema20": None, "qqq_vs_vwap": None,
                 "vix_level": None, "vix_direction": None},
)


@app.route("/")
def dashboard():
    """Main dashboard — summary cards, top 5, and full ranked watchlist."""
    try:
        return _dashboard_inner()
    except Exception as exc:
        logger.error("dashboard  route=/  unhandled_error=%s", exc, exc_info=True)
        flash("Dashboard error — please refresh the page.", "error")
        return render_template("dashboard.html", **_DASHBOARD_EMPTY)


def _dashboard_inner():
    all_wls     = get_all_watchlists()
    active_wl_id = get_active_wl_id()
    active_wl    = get_watchlist_by_id(active_wl_id) if active_wl_id else None
    wl_counts    = get_watchlist_stock_counts()

    watchlist = get_watchlist_stocks(active_wl_id) if active_wl_id else []

    # Auto-refresh any stocks whose prev_close_date is stale (new trading day)
    if watchlist:
        auto_refresh_stale_closes(watchlist)
        # Expire any tickers stuck in 'loading' for longer than LOADING_TIMEOUT_SECS
        _expire_stuck_loading(watchlist)

    all_data = get_all_stock_data()
    logger.info("dashboard  route=/  wl_id=%s  tickers=%s", active_wl_id, watchlist)

    data_map = {s["ticker"]: s for s in all_data}

    # Annotate per ticker — one bad ticker must not crash the whole dashboard
    stocks = []
    for t in watchlist:
        if t not in data_map:
            continue
        try:
            stocks.append(annotate(data_map[t]))
        except Exception as exc:
            logger.error("dashboard  ticker=%s  stage=annotate  err=%s", t, exc, exc_info=True)
            s = data_map[t]
            s["ticker_state"]       = "error"
            s["ticker_state_class"] = "state-error"
            s["ticker_state_label"] = "Data Error"
            stocks.append(s)

    missing = [t for t in watchlist if t not in data_map]

    ranked     = rank_stocks(stocks)
    # Top candidates: swing_score ≥ 6 and status not extended/avoid, OR fall back
    # to day-trading criteria if swing fields are absent
    top5       = [
        s for s in ranked
        if (
            # Swing mode: good score + actionable status
            (s.get("swing_score") or 0) >= 6
            and s.get("swing_status") not in (
                "WAIT", "TOO EXTENDED", "AVOID AT RESISTANCE", "AVOID WEAK STRUCTURE", "NOT ENOUGH EDGE"
            )
            and s.get("trade_bias") != "Avoid"
        ) or (
            # Legacy day-trading fallback when swing fields absent
            not s.get("swing_score")
            and (s.get("momentum_score") or 0) >= 6
            and s.get("orb_ready") == "YES"
            and s.get("entry_quality") != "Extended"
            and s.get("trade_bias") != "Avoid"
        )
    ][:5]

    # Generate swing alerts from the current ranked list
    generate_alerts(ranked)
    dashboard_alerts = get_alerts(limit=10)

    # No-trade assessment — must run before triggered list is built
    no_trade = compute_no_trade_assessment(ranked, top5)

    # Triggered: suppress entirely when signal lock is active (no-trade day)
    if no_trade["lock_signals"]:
        triggered = []
    else:
        triggered = [s for s in ranked if s.get("display_exec_state") == "TRIGGERED"]

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
    notes_map = get_all_notes()

    # ── Risk engine context ──────────────────────────────────────────────────
    risk_settings   = get_risk_settings()
    today_str       = _et_now().strftime("%Y-%m-%d")
    daily_session   = get_daily_session(today_str)
    today_entries   = get_journal_entries_for_date(today_str)

    # Auto-lock check: fires when a new journal entry pushes over limits
    _lock_update = check_auto_lock(today_entries, risk_settings, daily_session)
    if _lock_update:
        lock_daily_session(_lock_update["lock_reason"], today_str)
        daily_session = get_daily_session(today_str)

    discipline      = compute_discipline_score(today_entries, risk_settings,
                                               bool(daily_session.get("locked")))
    daily_banner    = compute_daily_banner(no_trade, daily_session)
    trades_today    = len(today_entries)
    losses_today    = sum(1 for e in today_entries if e.get("result") == "Loss")

    market_temp = _get_market_temperature()

    return render_template(
        "dashboard.html",
        ranked=ranked,
        top5=top5,
        triggered=triggered,
        summary=summary,
        missing=missing,
        watchlist=watchlist,
        notes=notes_map,
        secondary=secondary,
        alt_modes=alt_modes,
        no_trade=no_trade,
        all_wls=all_wls,
        active_wl=active_wl,
        wl_counts=wl_counts,
        orb_session=get_orb_session_banner(),
        alerts=dashboard_alerts,
        risk_settings=risk_settings,
        daily_session=daily_session,
        discipline=discipline,
        daily_banner=daily_banner,
        trades_today=trades_today,
        losses_today=losses_today,
        market_temp=market_temp,
    )


def _onboard_ticker_bg(ticker: str) -> None:
    """
    Background onboarding pipeline for a newly added ticker.

    Stage 1 — Core data (fast):
        Fetch current_price, prev_close, gap_pct, volume from yfinance.
        If price > 0  → save a 'partial' snapshot so the row shows real data.
        If price = 0  → set state = 'error' and return.

    Stage 2 — Full analysis (slow, may take 15-30 s on Render):
        Run the full generate_stock_data() pipeline (EMAs, Fib, zones, scoring).
        Result is 'ready', 'partial', or 'error' depending on what succeeded.

    Logs at every transition so Render logs can be followed in real time.

    State flow:  loading → partial (after Stage 1) → ready/partial/error (after Stage 2)
    """
    logger.info("onboard_bg  ticker=%s  stage=start", ticker)

    # ── Stage 1: Core price data ───────────────────────────────────────────
    stage1_ok = False
    try:
        from data_fetcher import fetch_live_data as _fetch_live
        live = _fetch_live(ticker)
        price = float(live.get("current_price") or 0) if live else 0.0
        if price > 0:
            gap = float(live.get("gap_pct") or 0)
            partial = {
                "ticker":               ticker,
                "current_price":        price,
                "prev_close":           live.get("prev_close"),
                "gap_pct":              gap,
                "prev_close_date":      live.get("prev_close_date"),
                "premarket_high":       live.get("premarket_high"),
                "premarket_low":        live.get("premarket_low"),
                "prev_day_high":        live.get("prev_day_high"),
                "prev_day_low":         live.get("prev_day_low"),
                "avg_volume":           live.get("avg_volume", 0),
                "rel_volume":           live.get("rel_volume", 1.0),
                "earnings_date":        live.get("earnings_date"),
                "vwap":                 live.get("vwap"),
                "orb_phase":            live.get("orb_phase", "pre_market"),
                "orb_high":             None,
                "orb_low":              None,
                "trade_bias":           ("Long Bias"  if gap >  3 else
                                         "Short Bias" if gap < -3 else "Neutral"),
                # Scoring defaults — analysis not yet complete
                "catalyst_summary":         "Analysis pending…",
                "news_headlines":           "[]",
                "catalyst_category":        "[]",
                "headlines_fetched_at":     None,
                "catalyst_score":           0,
                "catalyst_reason":          "Pending",
                "catalyst_confidence":      "Low",
                "momentum_score":           0,
                "momentum_reason":          None,
                "momentum_confidence":      "Low",
                "setup_score":              0,
                "setup_reason":             None,
                "setup_confidence":         "Low",
                "setup_type":               "No Setup",
                "swing_score":              1,
                "swing_reason":             None,
                "swing_confidence":         "Low",
                "swing_setup_type":         "No Setup",
                "swing_status":             "NOT ENOUGH EDGE",
                "exec_state":               "WAIT",
                "orb_ready":                "NO",
                "orb_status":               "NO_ORB",
                "order_block":              "Neutral",
                "entry_quality":            "Okay",
                "position_size":            "normal",
                "entry_note":               None,
                "momentum_breakout":        False,
                "candles_above_orb":        0,
                "orb_hold":                 False,
                "trend_structure":          False,
                "higher_highs":             False,
                "higher_lows":              False,
                "strong_candle_bodies":     False,
                "price_above_vwap":         False,
                "momentum_runner":          False,
                "structure_momentum_score": 0,
                "ticker_state":             "partial",
                "last_updated":             _et_now().strftime("%Y-%m-%d %I:%M %p"),
            }
            # Fill all swing/zone analysis keys so upsert_stock_data doesn't
            # fail on named-param binding for missing columns.
            _swing_defaults(partial)
            _zone_defaults(partial)
            upsert_stock_data(partial)
            stage1_ok = True
            logger.info(
                "onboard_bg  ticker=%s  stage=1_complete  state=partial  price=%.2f",
                ticker, price,
            )
        else:
            logger.warning(
                "onboard_bg  ticker=%s  stage=1_failed  reason=no_price  live=%s",
                ticker, bool(live),
            )
    except Exception as exc:
        logger.error(
            "onboard_bg  ticker=%s  stage=1_error  err=%s",
            ticker, exc, exc_info=True,
        )

    if not stage1_ok:
        set_ticker_state(ticker, "error")
        logger.warning("onboard_bg  ticker=%s  stage=1_complete  state=error", ticker)
        return

    # ── Stage 2: Full analysis (EMAs, Fib, zones, scoring) ────────────────
    # Use Stage 1 snapshot as the "existing" reference so that if live fetch
    # fails again in Stage 2 we preserve the good Stage 1 price (not error).
    _stage1_snap = get_stock_data(ticker)
    try:
        fresh  = generate_stock_data(ticker)
        result = _upsert_or_keep_snapshot(fresh, existing=_stage1_snap)
        if result == "updated":
            run_auto_classification(ticker)
        logger.info(
            "onboard_bg  ticker=%s  stage=2_complete  state=%s  result=%s",
            ticker, fresh.get("ticker_state"), result,
        )
    except Exception as exc:
        logger.error(
            "onboard_bg  ticker=%s  stage=2_error  err=%s",
            ticker, exc, exc_info=True,
        )
        # Stage 1 data is still in the DB — keep it as partial, not error.
        if _stage1_snap and _stage1_snap.get("current_price"):
            set_ticker_state(ticker, "partial")
            logger.warning(
                "onboard_bg  ticker=%s  stage=2_complete  state=partial  "
                "reason=analysis_failed",
                ticker,
            )
        else:
            set_ticker_state(ticker, "error")


@app.route("/watchlist/add", methods=["POST"])
def watchlist_add():
    """Add one or more tickers to the active watchlist."""
    wl_id  = get_active_wl_id()
    raw    = request.form.get("tickers", "")
    queued = []
    for t in re.split(r"[\s,]+", raw.upper()):
        t = t.strip()
        if not (t and t.isalpha() and 1 <= len(t) <= 5 and wl_id):
            continue
        # Claim the watchlist slot + insert a Loading placeholder so the
        # ticker appears on the dashboard immediately.
        add_ticker_to_watchlist(wl_id, t)
        upsert_loading_placeholder(t)
        logger.info("watchlist_add  ticker=%s  wl_id=%s  stage=placeholder_queued", t, wl_id)
        # Fire the two-stage onboarding pipeline in a background thread so the
        # HTTP response returns immediately and the UI shows the Loading badge.
        threading.Thread(
            target=_onboard_ticker_bg,
            args=(t,),
            daemon=True,
            name=f"onboard-{t}",
        ).start()
        queued.append(t)

    if queued:
        flash(
            f"Adding {', '.join(queued)}… "
            "The row shows Loading → Partial → Ready as data arrives. "
            "The page auto-refreshes when it's ready.",
            "success",
        )
        logger.info("watchlist_add  queued=%s  wl_id=%s", queued, wl_id)
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


def _refresh_all_worker(watchlist: list) -> None:
    """
    Background worker for refresh_all.  Runs in a daemon thread so the HTTP
    response returns immediately (no gunicorn timeout).
    """
    global _refresh_all_running
    try:
        _all_existing = {s["ticker"]: s for s in get_all_stock_data()}
        logger.info("refresh_all  bg_worker  tickers=%s", watchlist)
        for ticker in watchlist:
            try:
                fresh  = generate_stock_data(ticker)
                result = _upsert_or_keep_snapshot(fresh, existing=_all_existing.get(ticker))
                if result == "updated":
                    run_auto_classification(ticker)
                logger.info(
                    "refresh_all  ticker=%s  state=%s  result=%s",
                    ticker, fresh.get("ticker_state"), result,
                )
            except Exception as exc:
                logger.error("refresh_all  ticker=%s  err=%s", ticker, exc, exc_info=True)
                try:
                    existing = get_stock_data(ticker)
                    if existing and existing.get("current_price"):
                        set_ticker_state(ticker, "stale")
                    else:
                        set_ticker_state(ticker, "error")
                except Exception:
                    pass
    finally:
        _refresh_all_running = False
        logger.info("refresh_all  bg_worker  done")


@app.route("/refresh", methods=["POST"])
def refresh_all():
    """
    Kick off a background refresh of all tickers and return immediately.
    The actual data fetch runs in a daemon thread so gunicorn never times out.

    Uses _refresh_all_lock.acquire(blocking=False) so the in-progress check
    and flag-set are atomic — prevents two near-simultaneous POST requests
    (e.g. double-click) from spawning two background workers.
    """
    global _refresh_all_running

    if not _refresh_all_lock.acquire(blocking=False):
        # Lock already held — a refresh is actively running
        flash("Refresh already in progress — check back in a moment.", "warning")
        logger.warning("refresh_all  skipped=lock_held")
        return redirect(url_for("dashboard"))

    try:
        if _refresh_all_running:
            flash("Refresh already in progress — check back in a moment.", "warning")
            logger.warning("refresh_all  skipped=flag_set")
            return redirect(url_for("dashboard"))

        wl_id     = get_active_wl_id()
        watchlist = get_watchlist_stocks(wl_id) if wl_id else []
        if not watchlist:
            flash("No tickers in watchlist to refresh.", "warning")
            return redirect(url_for("dashboard"))

        _refresh_all_running = True
        t = threading.Thread(target=_refresh_all_worker, args=(watchlist,), daemon=True)
        t.start()
        logger.info("refresh_all  stage=bg_thread_started  tickers=%s", watchlist)
    finally:
        _refresh_all_lock.release()

    flash(
        f"Refreshing {len(watchlist)} tickers in the background — "
        "prices will update automatically. Reload in ~30 s.",
        "info",
    )
    return redirect(url_for("dashboard"))


@app.route("/stock/<ticker>")
def stock_detail(ticker):
    """Detailed view for a single stock."""
    ticker = ticker.upper()
    logger.info("stock_detail  ticker=%s  route=/stock/%s", ticker, ticker)
    stock  = get_stock_data(ticker)
    if stock is None:
        flash(f"No data found for {ticker}.", "error")
        return redirect(url_for("dashboard"))

    # ── Live price enrichment pass ───────────────────────────────────────────
    # On the detail page, always try the chart API to fill in any missing price
    # fields so the user always sees current data regardless of DB state.
    # This is a lightweight read-only call (~200ms) — it does NOT write to the DB.
    try:
        from data_fetcher import _fetch_price_via_chart_api
        _chart = _fetch_price_via_chart_api(ticker)
        if _chart:
            _src_map = {}
            for _field in ("current_price", "prev_close", "prev_day_high", "prev_day_low"):
                _db_val   = stock.get(_field)
                _live_val = _chart.get(_field)
                if _live_val:
                    if _db_val != _live_val:
                        stock[_field] = _live_val   # always prefer freshly-fetched
                        _src_map[_field] = "chart_api"
                    else:
                        _src_map[_field] = "db_matches_live"
                else:
                    _src_map[_field] = "db_only" if _db_val else "unavailable"
            # Also recompute gap_pct from live current_price + prev_close
            _cp, _pc = stock.get("current_price"), stock.get("prev_close")
            if _cp and _pc and _pc > 0:
                stock["gap_pct"] = round((_cp - _pc) / _pc * 100, 2)
            logger.info(
                "stock_detail  ticker=%s  live_enrich=ok  sources=%s  "
                "price=%.2f  prev_close=%s  gap_pct=%s  "
                "ema_20=%s  fib_high=%s",
                ticker, _src_map,
                stock.get("current_price") or 0,
                stock.get("prev_close"),
                stock.get("gap_pct"),
                stock.get("ema_20_daily"),
                stock.get("fib_high"),
            )
    except Exception as _enrich_err:
        logger.warning("stock_detail  ticker=%s  live_enrich=failed  err=%s", ticker, _enrich_err)

    # Annotate — guarded so a single bad field can't crash the whole detail page
    try:
        annotate(stock)
    except Exception as exc:
        logger.error("stock_detail  ticker=%s  stage=annotate  err=%s", ticker, exc, exc_info=True)
        stock.setdefault("ticker_state",       "error")
        stock.setdefault("ticker_state_class", "state-error")
        stock.setdefault("ticker_state_label", "Data Error")
        # Apply critical display defaults so the template doesn't crash
        for _f, _v in [
            ("final_action", "WAIT"), ("exec_class", "exec-wait"),
            ("final_action_class", "exec-wait"), ("final_action_reason", ""),
            ("bias_class", "bias-neutral"), ("swing_score_class", "neutral"),
            ("swing_status_class", "swing-status-wait"),
            ("swing_setup_type_class", "setup-none"),
            ("swing_grade", "F"), ("gap_display", "—"), ("gap_class", ""),
            ("entry_zone_display", "—"), ("entry_distance_display", "—"),
            ("entry_distance_class", ""), ("risk_reward_display", "—"),
            ("risk_reward_class", "rr-neutral"), ("resistance_distance_display", "—"),
            ("pullback_quality", "Watch"), ("pullback_quality_class", "pq-watch"),
            ("headline_freshness", ""), ("catalyst_tags", []),
            ("combined_confidence", "Low"), ("combined_conf_class", "conf-low"),
            ("orb_price_pct", 50.0), ("display_exec_state", "WAIT"),
        ]:
            stock.setdefault(_f, _v)

    note = get_note(ticker)

    try:
        breakdown = catalyst_score_breakdown(stock) or []
    except Exception as exc:
        logger.error("stock_detail  ticker=%s  stage=breakdown  err=%s", ticker, exc)
        breakdown = []

    plan = get_trade_plan(ticker)

    try:
        _, rr_display, rr_class = compute_rr(
            plan.get("plan_bias"),
            plan.get("entry_level"),
            plan.get("stop_loss"),
            plan.get("target_price"),
        )
    except Exception:
        rr_display, rr_class = "—", "rr-neutral"

    all_wls       = get_all_watchlists()
    ticker_wl_ids = get_ticker_watchlist_ids(ticker)
    logger.info("stock_detail  ticker=%s  state=%s", ticker, stock.get("ticker_state"))

    return render_template(
        "stock_detail.html",
        stock=stock,
        note=note,
        breakdown=breakdown,
        setup_types=SWING_SETUP_TYPES + [s for s in SETUP_TYPES if s not in SWING_SETUP_TYPES],
        plan=plan,
        rr_display=rr_display,
        rr_class=rr_class,
        all_wls=all_wls,
        ticker_wl_ids=ticker_wl_ids,
        get_setup_type_class=get_setup_type_class,
        risk_settings=get_risk_settings(),
    )


# ── Market Temperature cache ─────────────────────────────────────────────────
_market_temp_cache: dict = {"data": None, "ts": 0.0, "fetching": False}
_MARKET_TEMP_TTL = 300   # 5-minute refresh


def _get_market_temperature() -> dict:
    """Return cached market regime; trigger background refresh when stale."""
    _LOADING: dict = {
        "regime": "LOADING", "label": "Loading…", "css": "mt-loading",
        "reason": "Fetching market data…", "longs_ok": None, "shorts_ok": None,
        "reduce_size": False, "score": None, "error": False,
        "spy_price": None, "spy_pct_ema20": None, "spy_vs_vwap": None,
        "qqq_price": None, "qqq_pct_ema20": None, "qqq_vs_vwap": None,
        "vix_level": None, "vix_direction": None,
    }
    now = _time.time()
    if _market_temp_cache["ts"] and now - _market_temp_cache["ts"] < _MARKET_TEMP_TTL:
        return _market_temp_cache["data"]
    if not _market_temp_cache["fetching"]:
        _market_temp_cache["fetching"] = True

        def _bg():
            try:
                from data_fetcher import compute_market_temperature
                data = compute_market_temperature()
                _market_temp_cache["data"] = data
                _market_temp_cache["ts"]   = _time.time()
                logger.info(
                    "market_temperature  regime=%s  score=%s",
                    data.get("regime"), data.get("score"),
                )
            except Exception as _e:
                logger.warning("_get_market_temperature bg failed: %s", _e)
            finally:
                _market_temp_cache["fetching"] = False

        threading.Thread(target=_bg, daemon=True).start()
    return _market_temp_cache["data"] or _LOADING


# ── Options contract server-side cache ───────────────────────────────────────
_options_cache: dict    = {}   # {ticker: {"data": dict, "ts": float}}
_options_rl_until: dict = {}   # {ticker: float} — epoch when rate-limit backoff expires

# TTL and backoff scale with market session so we never hammer Yahoo after hours
_OPT_TTL = {
    "regular":     90,    # market open — refresh up to every 90 s
    "pre_market":  180,   # pre-market — prices move, but slower
    "after_hours": 600,   # after hours — data barely changes, don't re-fetch for 10 min
    "closed":      900,   # overnight / weekend — 15 min TTL, serve from cache
}
_OPT_RL_BACKOFF = {
    "regular":     120,   # 2 min backoff after 429 during market hours
    "pre_market":  300,   # 5 min backoff pre-market
    "after_hours": 600,   # 10 min backoff after hours — Yahoo is throttled hardest here
    "closed":      900,   # 15 min backoff overnight
}


def _options_session_ttl() -> tuple[int, int, str, bool]:
    """Return (cache_ttl, rl_backoff, session_label, is_after_hours)."""
    try:
        from data_fetcher import market_session_now
        session = market_session_now()
    except Exception:
        session = "closed"
    ttl      = _OPT_TTL.get(session, 300)
    backoff  = _OPT_RL_BACKOFF.get(session, 300)
    after_hours = session in ("after_hours", "closed")
    return ttl, backoff, session, after_hours


@app.route("/api/options/<ticker>")
def api_option_contracts(ticker):
    """
    Return filtered option contracts for the options contract selector.

    Caching strategy scales with market session:
      regular     → TTL  90 s, RL backoff  120 s
      pre_market  → TTL 180 s, RL backoff  300 s
      after_hours → TTL 600 s, RL backoff  600 s
      closed      → TTL 900 s, RL backoff  900 s  (weekend / overnight)

    Every response includes `market_session` and `after_hours` so the client
    can show the "After hours — options data may be delayed" label without
    any extra request.

    Calls/Puts/All filtering is entirely client-side — this route always
    returns both lists regardless of the `mode` query param.
    Dashboard auto-refresh never calls this route.
    """
    ticker     = ticker.upper()
    trade_mode = request.args.get("mode", "SWING TRADE")
    now        = _time.time()

    cache_ttl, rl_backoff, session, after_hours = _options_session_ttl()
    cached_entry = _options_cache.get(ticker)

    def _annotate(d: dict, *, is_cached: bool, is_stale: bool) -> dict:
        """Stamp session / after-hours context onto every outgoing response."""
        d["market_session"] = session
        d["after_hours"]    = after_hours
        d["cached"]         = is_cached
        d["stale"]          = is_stale
        return d

    # ── Fresh cache hit ───────────────────────────────────────────────
    if cached_entry and (now - cached_entry["ts"]) < cache_ttl:
        age = int(now - cached_entry["ts"])
        logger.info(
            "options  ticker=%s  CACHE HIT  session=%s  age=%ds  ttl=%ds  "
            "calls=%d  puts=%d",
            ticker, session, age, cache_ttl,
            len(cached_entry["data"].get("calls", [])),
            len(cached_entry["data"].get("puts",  [])),
        )
        result = dict(cached_entry["data"])
        result["cache_age_s"] = age
        return jsonify(_annotate(result, is_cached=True, is_stale=False))

    # ── Rate-limit backoff still active ──────────────────────────────
    rl_until = _options_rl_until.get(ticker, 0)
    if now < rl_until:
        wait = int(rl_until - now)
        logger.warning(
            "options  ticker=%s  RATE LIMIT BACKOFF  session=%s  wait=%ds  "
            "after_hours=%s  cache_exists=%s",
            ticker, session, wait, after_hours, bool(cached_entry),
        )
        if cached_entry:
            result = dict(cached_entry["data"])
            result["cache_age_s"]   = int(now - cached_entry["ts"])
            result["rate_limited"]  = True
            result["retry_after_s"] = wait
            logger.info(
                "options  ticker=%s  serving STALE cache during backoff  age=%ds",
                ticker, result["cache_age_s"],
            )
            return jsonify(_annotate(result, is_cached=True, is_stale=True))
        return jsonify(_annotate({
            "error":          "Options source rate-limited — try again later",
            "calls": [], "puts": [], "price": None,
            "best_day": None, "best_swing": None,
            "rate_limited":   True,
            "retry_after_s":  wait,
        }, is_cached=False, is_stale=False))

    # ── Upstream call ─────────────────────────────────────────────────
    logger.info(
        "options  ticker=%s  UPSTREAM CALL  session=%s  after_hours=%s  "
        "trade_mode=%s",
        ticker, session, after_hours, trade_mode,
    )
    try:
        stock = get_stock_data(ticker)
        price = float(stock.get("current_price") or 0) if stock else 0.0
        from data_fetcher import fetch_option_contracts
        result = fetch_option_contracts(ticker, current_price=price or None,
                                        trade_mode=trade_mode)

        if result.get("rate_limited"):
            _options_rl_until[ticker] = now + rl_backoff
            logger.warning(
                "options  ticker=%s  RATE LIMITED  session=%s  after_hours=%s  "
                "backoff=%ds",
                ticker, session, after_hours, rl_backoff,
            )
            if cached_entry:
                stale = dict(cached_entry["data"])
                stale["cache_age_s"]   = int(now - cached_entry["ts"])
                stale["rate_limited"]  = True
                stale["retry_after_s"] = rl_backoff
                logger.info(
                    "options  ticker=%s  serving STALE cache after rate limit  age=%ds",
                    ticker, stale["cache_age_s"],
                )
                return jsonify(_annotate(stale, is_cached=True, is_stale=True))
            result["retry_after_s"] = rl_backoff
            return jsonify(_annotate(result, is_cached=False, is_stale=False))

        # Success — cache it
        if not result.get("error"):
            _options_cache[ticker] = {"data": result, "ts": now}
            logger.info(
                "options  ticker=%s  CACHED  session=%s  calls=%d  puts=%d  "
                "partial=%s  ttl=%ds",
                ticker, session,
                len(result.get("calls", [])), len(result.get("puts", [])),
                result.get("partial", False), cache_ttl,
            )

        return jsonify(_annotate(result, is_cached=False, is_stale=False))

    except Exception as exc:
        logger.warning(
            "options  ticker=%s  EXCEPTION  session=%s  err=%s", ticker, session, exc,
        )
        if cached_entry:
            stale = dict(cached_entry["data"])
            stale["cache_age_s"]  = int(now - cached_entry["ts"])
            stale["rate_limited"] = False
            return jsonify(_annotate(stale, is_cached=True, is_stale=True))
        return jsonify(_annotate({
            "error": str(exc), "calls": [], "puts": [],
            "price": None, "best_day": None, "best_swing": None,
            "rate_limited": False,
        }, is_cached=False, is_stale=False))


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
    global _single_refresh_active
    t = ticker.upper()

    # ── Per-ticker overlap guard ─────────────────────────────────────────────
    # Prevents a double-click or rapid reload from spawning two simultaneous
    # fetches for the same ticker.  If a refresh is already in progress for
    # this ticker, redirect immediately with a warning.
    with _single_refresh_lock:
        if t in _single_refresh_active:
            logger.warning("refresh_single  ticker=%s  skipped=already_in_progress", t)
            flash(f"Refresh already in progress for {t} — please wait.", "warning")
            referrer = request.referrer or ""
            if "stock/" not in referrer:
                return redirect(url_for("dashboard"))
            return redirect(url_for("stock_detail", ticker=t))
        _single_refresh_active.add(t)

    logger.info("refresh_single  ticker=%s  stage=start", t)
    _existing = get_stock_data(t)
    try:
        fresh  = generate_stock_data(t)
        result = _upsert_or_keep_snapshot(fresh, existing=_existing)
        if result == "updated":
            run_auto_classification(t)
        logger.info(
            "refresh_single  ticker=%s  stage=complete  state=%s  result=%s  "
            "price=%s  ema_20=%s  fib_high=%s",
            t, fresh.get("ticker_state"), result,
            fresh.get("current_price"), fresh.get("ema_20_daily"), fresh.get("fib_high"),
        )
        if result == "stale_kept":
            flash(f"Live data unavailable for {t}. Showing last known data (STALE).", "warning")
        else:
            flash(f"Refreshed {t}.", "success")
    except Exception as exc:
        logger.error(
            "refresh_single  ticker=%s  stage=error  err=%s  "
            "snapshot_price=%s",
            t, exc, _existing.get("current_price") if _existing else None,
            exc_info=True,
        )
        if _existing and _existing.get("current_price"):
            set_ticker_state(t, "stale")
            flash(f"Refresh failed for {t}. Showing last known data.", "warning")
        else:
            set_ticker_state(t, "error")
            flash(f"Refresh failed for {t}. Data unavailable.", "error")
    finally:
        with _single_refresh_lock:
            _single_refresh_active.discard(t)
        logger.debug("refresh_single  ticker=%s  stage=lock_released", t)

    # If we came from the dashboard (missing-ticker row), stay on dashboard
    referrer = request.referrer or ""
    if "stock/" not in referrer:
        return redirect(url_for("dashboard"))
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
    today_str     = _et_now().strftime("%Y-%m-%d")
    risk_settings = get_risk_settings()
    return render_template(
        "journal.html",
        entries=entries,
        summary=summary,
        setup_types=SETUP_TYPES + [s for s in SWING_SETUP_TYPES if s not in SETUP_TYPES],
        edit_entry=edit_entry,
        today=today_str,
        risk_settings=risk_settings,
    )


def _parse_journal_form(form) -> dict:
    """Parse all journal form fields (shared by add and edit routes)."""
    direction   = form.get("direction", "Long")
    entry_price = form.get("entry_price", "")
    exit_price  = form.get("exit_price", "")
    pnl_pct, result = compute_pnl(direction, entry_price, exit_price)

    def _int(k):
        v = form.get(k, "")
        try: return int(v) if v else None
        except ValueError: return None

    def _float(k):
        v = form.get(k, "")
        try: return float(v) if v else None
        except ValueError: return None

    is_aplus = form.get("is_aplus_setup") == "1"

    return dict(
        direction      = direction,
        entry_price    = float(entry_price) if entry_price else 0,
        exit_price     = float(exit_price)  if exit_price  else 0,
        shares         = _int("shares"),
        setup_type     = form.get("setup_type", ""),
        momentum_score = _int("momentum_score"),
        pnl_pct        = pnl_pct,
        result         = result,
        notes          = form.get("notes", ""),
        trade_mode     = form.get("trade_mode") or None,
        option_side    = form.get("option_side") or None,
        option_premium = _float("option_premium"),
        contracts      = _int("contracts"),
        stop_price     = _float("stop_price"),
        is_aplus_setup = is_aplus,
    )


@app.route("/journal/add", methods=["POST"])
def journal_add():
    """Add a new journal entry."""
    f = _parse_journal_form(request.form)
    add_journal_entry(
        ticker         = request.form.get("ticker", "").upper(),
        trade_date     = request.form.get("trade_date", _et_now().strftime("%Y-%m-%d")),
        **f,
    )
    pnl_pct = f["pnl_pct"]
    result  = f["result"]

    # Auto-lock check after adding a trade
    today_str     = _et_now().strftime("%Y-%m-%d")
    risk_settings = get_risk_settings()
    today_entries = get_journal_entries_for_date(today_str)
    daily_session = get_daily_session(today_str)
    lock_update   = check_auto_lock(today_entries, risk_settings, daily_session)
    if lock_update:
        lock_daily_session(lock_update["lock_reason"], today_str)
        flash(f"⚠ {lock_update['lock_reason']} — Trading locked for today.", "warning")

    flash(f"Trade logged — {result} ({'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%).", "success")
    return redirect(url_for("journal"))


@app.route("/journal/<int:entry_id>/edit", methods=["POST"])
def journal_edit(entry_id):
    """Update an existing journal entry."""
    f = _parse_journal_form(request.form)
    update_journal_entry(
        entry_id   = entry_id,
        ticker     = request.form.get("ticker", "").upper(),
        trade_date = request.form.get("trade_date", ""),
        **f,
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
# Risk Settings & Daily Session routes
# ---------------------------------------------------------------------------

@app.route("/risk", methods=["GET", "POST"])
def risk_settings():
    """Risk settings page — account size, risk %, trade limits, trading mode."""
    today_str     = _et_now().strftime("%Y-%m-%d")
    daily_session = get_daily_session(today_str)
    today_entries = get_journal_entries_for_date(today_str)
    trades_today  = len(today_entries)
    losses_today  = sum(1 for e in today_entries if e.get("result") == "Loss")

    if request.method == "POST":
        action = request.form.get("action", "save")

        if action == "unlock":
            unlock_daily_session(today_str)
            flash("Trading unlocked for today.", "success")
            return redirect(url_for("risk_settings"))

        # Save risk settings
        set_setting("trading_mode",       request.form.get("trading_mode", "SWING TRADE"))
        set_setting("account_size",       request.form.get("account_size",       "10000"))
        set_setting("risk_pct",           request.form.get("risk_pct",           "1.0"))
        set_setting("max_trades_per_day", request.form.get("max_trades_per_day", "3"))
        set_setting("max_daily_loss_pct", request.form.get("max_daily_loss_pct", "3.0"))
        set_setting("stop_after_2_losses",
                    "1" if request.form.get("stop_after_2_losses") else "0")
        flash("Risk settings saved.", "success")
        return redirect(url_for("risk_settings"))

    risk_s = get_risk_settings()
    discipline = compute_discipline_score(
        today_entries, risk_s, bool(daily_session.get("locked"))
    )
    return render_template(
        "risk_settings.html",
        risk_settings=risk_s,
        daily_session=daily_session,
        discipline=discipline,
        trades_today=trades_today,
        losses_today=losses_today,
        today=today_str,
    )


@app.route("/risk/trading-mode", methods=["POST"])
def set_trading_mode():
    """AJAX: Switch DAY TRADE / SWING TRADE mode. Returns JSON."""
    mode = request.json.get("mode", "SWING TRADE") if request.is_json else request.form.get("mode", "SWING TRADE")
    if mode in ("DAY TRADE", "SWING TRADE"):
        set_setting("trading_mode", mode)
        return jsonify({"ok": True, "mode": mode})
    return jsonify({"ok": False, "error": "invalid mode"}), 400


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

    # combined_confidence and final_action are already set by annotate()
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
            if _time.monotonic() - last_push >= 5.0:
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
            if _time.monotonic() - last_push >= 5.0:
                ws.send(_json.dumps(_build_quick_payload(wl_id)))
                last_push = _time.monotonic()
    except Exception as exc:
        logger.debug("ws_quick closed (wl_id=%s): %s", wl_id, exc)


@app.route("/api/alerts")
def api_alerts():
    """Return the most recent swing alerts as JSON."""
    return jsonify(get_alerts())


@app.route("/alerts/clear", methods=["POST"])
def alerts_clear():
    """Dismiss all pending alerts."""
    _clear_alerts()
    return redirect(url_for("dashboard"))


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
        "exec_state", "display_exec_state", "exec_class",
        "final_action", "final_action_class", "final_action_reason",
        "combined_confidence", "combined_conf_class",
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
        # Simplified 4-state decision
        "simplified_action", "simplified_action_class",
        # Swing fields (needed for live top-5 card patching)
        "swing_score", "swing_score_class", "swing_grade",
        "swing_status", "swing_status_class",
        "swing_setup_type", "swing_setup_type_class",
        "swing_confidence_label",
        "pullback_quality", "pullback_quality_class",
        "entry_zone_display",
        "entry_distance_display", "entry_distance_class",
        "resistance_distance_display",
        "risk_reward", "risk_reward_display", "risk_reward_class",
        "rr_quality_label", "rr_quality_class",
        "stop_level", "target_1", "target_2",
        "daily_trend", "h4_trend",
        "is_extended", "swing_data_available",
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
    # Use display_exec_state (session-aware) so stale TRIGGERED stocks are not
    # shown in the live-alerts section outside regular market hours.
    triggered    = [] if no_trade["lock_signals"] else [s for s in ranked if s.get("display_exec_state") == "TRIGGERED"]
    top5_tickers = {s["ticker"] for s in top5}
    secondary    = compute_secondary_watchlist(ranked, top5_tickers)
    return {
        "type":        "dashboard",
        "server_time": _et_now().strftime("%I:%M %p").lstrip("0") + " ET",
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
        # combined_confidence and final_action are already set by annotate()
        out.append(_stock_summary(s))
    return {
        "type":        "quick",
        "server_time": _et_now().strftime("%I:%M %p").lstrip("0") + " ET",
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
    result = _stock_summary(stock)
    result["server_time"] = _et_now().strftime("%I:%M %p").lstrip("0") + " ET"
    return jsonify(result)


@app.route("/api/ticker-states")
def api_ticker_states():
    """
    Return the current ticker_state for every ticker in the active watchlist.

    Used by the dashboard JS to poll for state changes on loading/partial
    tickers and trigger a page reload when they transition to a stable state.

    Response:
        { "states": { "AMD": "partial", "LMT": "ready", ... } }
    """
    wl_id     = get_active_wl_id()
    watchlist = get_watchlist_stocks(wl_id) if wl_id else []
    all_data  = get_all_stock_data()
    data_map  = {s["ticker"]: s for s in all_data}
    states = {
        t: (data_map[t].get("ticker_state") or "loading") if t in data_map else "loading"
        for t in watchlist
    }
    return jsonify({"states": states})


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
# Deferred startup — seed demo data in a background thread so gunicorn can
# bind to its port immediately.  seed_demo_data() makes yfinance API calls
# which can take 30-120 s; running it synchronously at import time blocks
# gunicorn from ever opening a socket, causing Render to time-out the deploy.
# The demo_seeded flag inside the function prevents re-seeding on restart.
# ---------------------------------------------------------------------------
def _deferred_startup():
    try:
        seed_demo_data()
    except Exception as _e:
        logger.error("deferred_startup seed error: %s", _e, exc_info=True)
    try:
        _startup_wls = get_all_watchlists()
        for _wl in _startup_wls:
            _tickers = get_watchlist_stocks(_wl["id"])
            logger.info(
                "STARTUP watchlist '%s' (id=%s): %s",
                _wl["name"], _wl["id"], _tickers,
            )
    except Exception as _e:
        logger.error("deferred_startup watchlist log error: %s", _e, exc_info=True)

threading.Thread(target=_deferred_startup, daemon=True, name="startup-seed").start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"\nRockkstaar Trade Assistant running on port {port}...\n")
    app.run(host="0.0.0.0", port=port, debug=False)
