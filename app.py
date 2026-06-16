https://raw.githubusercontent.com/HAULTRAAISYSTEMS/rockkstaar-trade-assistant/main/app.py
→ https://raw.githubusercontent.com/HAULTRAAISYSTEMS/rockkstaar-trade-assistant/main/app.py
Content-Type: text/plain; charset=utf-8

"""
app.py - Rockkstaar Trade Assistant
Flask web app for premarket stock watchlist scanning.
"""

import json as _json
import logging
import os
import pathlib
import re
import secrets
import threading
import time as _time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, Response, send_from_directory
from flask_sock import Sock
from flask_wtf.csrf import CSRFProtect  # type: ignore[import-untyped]
from werkzeug.exceptions import HTTPException

logger = logging.getLogger(__name__)
from database import (
    init_db,
    DEFAULT_WATCHLISTS,
    get_setting, set_setting,
    get_user_setting, set_user_setting,
    create_user, get_user_by_id, get_user_by_username,
    get_all_users, update_user_password, delete_user, check_user_password,
    ensure_user_watchlists,
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
    add_scanner_alert, get_scanner_alerts, mark_scanner_alerts_seen,
    get_unseen_scanner_alert_count, clear_scanner_alerts,
    save_setup_outcome, get_setup_outcome_stats,
)
from mock_data import generate_stock_data, load_mock_watchlist, live_refresh_stock, _swing_defaults, _zone_defaults
from data_fetcher import _et_now, market_session_now, orb_phase_now
from scoring import (catalyst_score_breakdown, SETUP_TYPES, SWING_SETUP_TYPES, SWING_STATUSES,
                     compute_swing_grade, compute_continuation_score)
from classifier import classify_stock
from alerts import generate_alerts, get_alerts, get_alert_count, clear_alerts as _clear_alerts
from news_fetcher import CATALYST_CATEGORIES as _CAT_DEFS, freshness_label as _fl
import scanner as _scanner
import intel_engine as _intel
_mkt = None  # set below if market_engine is available
try:
    import market_engine as _mkt
    _MKT_AVAILABLE = True
except Exception:
    _MKT_AVAILABLE = False

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
app.permanent_session_lifetime = timedelta(days=30)

# ---------------------------------------------------------------------------
# CSRF protection — validates csrf_token on every POST/PUT/PATCH/DELETE form.
# The /risk/trading-mode AJAX route sends the token via X-CSRFToken header.
# ---------------------------------------------------------------------------
csrf = CSRFProtect(app)

# ---------------------------------------------------------------------------
# Multi-user session auth helpers
# ---------------------------------------------------------------------------

def current_user_id() -> int:
    """Return the logged-in user_id from session (default 1 for backward compat)."""
    return session.get("user_id", 1)


def current_user() -> dict | None:
    """Return the full user dict from session, or None if not logged in."""
    uid = session.get("user_id")
    if uid is None:
        return None
    return {"id": uid, "username": session.get("username", ""), "is_admin": session.get("is_admin", 0)}


def _auth_required() -> bool:
    """Return True if the users table has at least one user (auth is active)."""
    try:
        return bool(get_all_users())
    except Exception:
        return False


def require_admin(f):
    """Decorator — 403 unless logged-in user is admin."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return jsonify({"ok": False, "error": "admin required"}), 403 \
                if request.path.startswith("/api/") \
                else (render_template("login.html", error="Admin access required.", next=""), 403)
        return f(*args, **kwargs)
    return decorated


@app.before_request
def _require_login():
    # Always public — Schwab OAuth callback must stay reachable; callback validates PKCE/state.
    if (request.path in ("/login", "/logout", "/register", "/favicon.ico", "/health", "/schwab/callback")
            or request.path.startswith("/static/")):
        return
    if session.get("user_id"):
        return  # Already authenticated
    # If no users exist yet, allow open access (fresh unconfigured deploy)
    if not _auth_required():
        return
    # API / WebSocket — JSON 401
    if request.path.startswith(("/api/", "/ws/")):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    # Page routes — redirect to login
    return redirect(url_for("login_page", next=request.path))


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


# ---------------------------------------------------------------------------
# Global JSON error handler — ensures /api/* routes NEVER return HTML
# ---------------------------------------------------------------------------
@app.errorhandler(Exception)
def _handle_all_errors(e):
    """Return JSON for /api/ errors; let Flask handle HTTP errors on frontend routes."""
    code = e.code if isinstance(e, HTTPException) else 500
    if request.path.startswith("/api/"):
        if code != 404:
            logger.error("Unhandled error on %s: %s", request.path, e, exc_info=True)
        return jsonify({
            "ok": False,
            "errors": [f"{type(e).__name__}: {e}"],
            "news": [], "market_news": [],
            "earnings": {"today": [], "tomorrow": [], "this_week": []},
            "splits": [], "dividends": [], "economic_events": [],
            "from_cache": False, "refreshing": False, "last_updated": "—",
        }), code
    # For HTTP errors (404, 403, etc.) on frontend routes, return Flask's default response
    if isinstance(e, HTTPException):
        return e.get_response()
    # Unexpected server errors on frontend routes — log and show a simple message
    logger.error("Unhandled error on %s: %s", request.path, e, exc_info=True)
    return "Internal server error", 500


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder or "static", "logo.png", mimetype="image/png")


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
    return jsonify({"ok": True}), 200


# ---------------------------------------------------------------------------
# Security headers — applied to every response
# ---------------------------------------------------------------------------
@app.after_request
def _add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


# ---------------------------------------------------------------------------
# /debug/status — production health check (no secret values exposed)
# ---------------------------------------------------------------------------
@app.route("/debug/status")
def debug_status():
    """
    Read-only diagnostics endpoint.
    Returns PASS/FAIL for each system component.
    Never exposes secret values — only checks presence and reachability.
    """
    checks = {}

    # 1. Database connected
    try:
        from database import get_db
        conn = get_db()
        conn.execute("SELECT 1")
        conn.close()
        checks["database"] = {"status": "PASS", "detail": "Connection OK"}
    except Exception as _e:
        checks["database"] = {"status": "FAIL", "detail": str(_e), "file": "database.py"}

    # 2. Required env vars
    _required_vars = {
        "SECRET_KEY":   os.environ.get("SECRET_KEY"),
        "DATABASE_URL": os.environ.get("DATABASE_URL"),  # optional — SQLite fallback
    }
    _optional_vars = {
        "TELEGRAM_BOT_TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN"),
        "TELEGRAM_CHAT_ID":   os.environ.get("TELEGRAM_CHAT_ID"),
        "FINNHUB_API_KEY":    os.environ.get("FINNHUB_API_KEY"),
        "POLYGON_API_KEY":    os.environ.get("POLYGON_API_KEY"),
        "SCHWAB_CLIENT_ID":   os.environ.get("SCHWAB_CLIENT_ID"),
    }
    env_detail = {}
    env_ok = True
    for k, v in _required_vars.items():
        if k == "SECRET_KEY" and not v:
            env_detail[k] = "MISSING (using insecure fallback)"
            env_ok = False
        elif k == "DATABASE_URL":
            env_detail[k] = "set" if v else "not set (SQLite fallback)"
        else:
            env_detail[k] = "set" if v else "missing"
    checks["env_required"] = {
        "status": "PASS" if env_ok else "WARN",
        "detail": env_detail,
    }

    # 3. Optional env vars (PASS if set, WARN if missing)
    opt_detail = {k: ("set" if v else "not set") for k, v in _optional_vars.items()}
    checks["env_optional"] = {
        "status": "PASS" if all(_optional_vars.values()) else "WARN",
        "detail": opt_detail,
    }

    # 4. Telegram configured
    _tg_token  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    _tg_chat   = os.environ.get("TELEGRAM_CHAT_ID", "")
    checks["telegram"] = {
        "status": "PASS" if (_tg_token and _tg_chat) else "WARN",
        "detail": "configured" if (_tg_token and _tg_chat) else "not configured (alerts will be skipped)",
    }

    # 5. Finnhub key loaded
    _fh_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    checks["finnhub"] = {
        "status": "PASS" if _fh_key else "WARN",
        "detail": "key present" if _fh_key else "key missing (static fallback will be used)",
    }

    # 6. static/logo.png exists
    _logo = pathlib.Path(app.static_folder or "static") / "logo.png"
    checks["static_logo"] = {
        "status": "PASS" if _logo.exists() else "FAIL",
        "detail": str(_logo) if _logo.exists() else f"missing: {_logo}",
        "file": "static/logo.png",
    }

    # 7. Intel cache / API reachable (checks in-process only, no network call)
    try:
        data = _intel.get_intel_summary()
        intel_ok = isinstance(data, dict) and "earnings" in data
        checks["intel_api"] = {
            "status": "PASS" if intel_ok else "FAIL",
            "detail": f"ok  from_cache={data.get('from_cache')}  refreshing={data.get('refreshing')}",
        }
    except Exception as _ie:
        checks["intel_api"] = {"status": "FAIL", "detail": str(_ie), "file": "intel_engine.py"}

    # 8. Scanner running
    try:
        scan = _scanner.get_scan_results()
        checks["scanner"] = {
            "status": "PASS",
            "detail": f"ok  running={scan.get('running', False)}  results={len(scan.get('results', []))}",
        }
    except Exception as _se:
        checks["scanner"] = {"status": "FAIL", "detail": str(_se), "file": "scanner.py"}

    # 9. Watchlist / DB round-trip
    try:
        wls = get_all_watchlists()
        checks["watchlists"] = {
            "status": "PASS",
            "detail": f"{len(wls)} watchlist(s) found",
        }
    except Exception as _we:
        checks["watchlists"] = {"status": "FAIL", "detail": str(_we), "file": "database.py"}

    overall = "PASS" if all(
        c["status"] in ("PASS", "WARN") for c in checks.values()
    ) else "FAIL"
    failures = [k for k, c in checks.items() if c["status"] == "FAIL"]

    return jsonify({
        "overall": overall,
        "failures": failures,
        "checks": checks,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    })


# ---------------------------------------------------------------------------
# Startup initialization — idempotent schema creation.
# Wrapped in try/except so a slow or unavailable DB (e.g. PG cold start on
# Render) does not crash the import and prevent gunicorn from binding its port.
# ---------------------------------------------------------------------------
try:
    init_db()
except Exception as _init_err:
    logger.error("init_db failed at startup: %s — will retry on first request", _init_err)

# Start the background momentum scanner daemon (no-op if already running).
try:
    _scanner.start_scanner()
except Exception as _scan_err:
    logger.error("scanner start failed at startup: %s", _scan_err)

# Start the background intel alert daemon — checks every 30 min during market hours.
def _intel_alert_loop():
    while True:
        try:
            now = _intel._et_now()
            # Run during extended market hours (7 AM – 6 PM ET, weekdays)
            if now.weekday() < 5 and 7 <= now.hour < 18:
                _intel.check_and_send_intel_alerts()
        except Exception as _ie:
            logger.warning("intel alert loop error: %s", _ie)
        _time.sleep(1800)  # every 30 minutes

try:
    threading.Thread(target=_intel_alert_loop, daemon=True, name="intel-alerts").start()
except Exception as _loop_err:
    logger.error("intel alert loop failed to start: %s", _loop_err)

# Pre-warm the intel cache so the first page load is instant
try:
    _intel.trigger_background_refresh()
except Exception as _warm_err:
    logger.error("intel bg refresh failed at startup: %s", _warm_err)

# Pre-warm the market context cache (regime, sectors, RS baseline)
try:
    if _MKT_AVAILABLE:
        _mkt.refresh_market_context_bg()
except Exception as _mkt_warm_err:
    logger.error("market context bg refresh failed at startup: %s", _mkt_warm_err)

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
                try:
                    conn.execute(
                        "UPDATE stock_data SET current_price = NULL, prev_close = NULL, "
                        "gap_pct = NULL, ticker_state = 'error' WHERE ticker = ?",
                        (ticker,),
                    )
                    conn.commit()
                finally:
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


def auto_refresh_stale_closes(tickers: list, data_map: dict | None = None) -> list:
    """
    Identify stale tickers and kick off a background refresh for each one.

    The check (staleness detection) runs synchronously so we know which tickers
    need work, but the actual fetch (generate_stock_data) runs in a daemon
    thread so the dashboard HTTP response is NEVER blocked.

    A ticker is stale when:
      - It has not been refreshed today (last_updated date != today), OR
      - Its prev_close_date doesn't match the expected previous trading day.
    Error-state tickers always retry regardless of last_updated.

    ``data_map`` — optional pre-loaded {ticker: stock_dict} from the caller.
    When supplied, skips the per-ticker get_stock_data() calls entirely so the
    dashboard doesn't pay N individual DB round-trips just to check staleness.

    Returns the list of ticker symbols that were queued for background refresh.
    """
    expected  = _prev_trading_day()
    today_str = _et_now().strftime("%Y-%m-%d")
    queued: list[str] = []

    # Use the caller's already-loaded map; fall back to fetching only if needed.
    _snapshot = data_map if data_map is not None else {
        s["ticker"]: s for s in get_all_stock_data()
    }

    for ticker in tickers:
        stock = _snapshot.get(ticker)
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

    # Reuse the same snapshot in the worker thread — avoids a second full-table
    # scan. A second get_all_stock_data() here was the old double-fetch.
    _all_existing = _snapshot

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


def _expire_stuck_loading(watchlist: list, data_map: dict | None = None) -> None:
    """
    Transition any ticker that has been in 'loading' state for longer than
    LOADING_TIMEOUT_SECS from 'loading' → 'error'.  Called on every dashboard
    load to prevent the Loading badge from persisting forever.

    Logs:
        expire_loading  ticker=X  age_secs=N  reason=timeout
    """
    now = _et_now().replace(tzinfo=None)
    for ticker in watchlist:
        stock = data_map.get(ticker) if data_map is not None else get_stock_data(ticker)
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


def _get_mkt_ctx() -> dict:
    """Return cached market context (regime, RS, sectors). Never raises."""
    if _MKT_AVAILABLE:
        try:
            return _mkt.get_market_context()
        except Exception:
            pass
    return {
        "regime": "NEUTRAL", "regime_label": "Neutral",
        "qqq_trend": "Unknown", "spy_trend": "Unknown",
        "qqq_1d_pct": None, "spy_1d_pct": None,
        "signal": "", "longs_ok": True, "shorts_ok": True,
        "reduce_size": False, "no_trade": False,
        "sectors": [], "leading_sectors": [], "weak_sectors": [],
        "top_sector": None, "qqq_price": None, "spy_price": None,
        "vix_level": None,
    }


def build_ai_trade_plan(stock: dict) -> dict:
    """
    Build an institutional-style AI trade plan from existing stock data fields.
    Returns a dict consumed by the stock_detail template's trade plan panel.
    """
    swing_score  = stock.get("swing_score")  or 0
    cat_score    = stock.get("catalyst_score") or 0
    rr           = stock.get("risk_reward")
    swing_status = stock.get("swing_status") or ""
    swing_type   = stock.get("swing_setup_type") or ""
    zone_prob    = stock.get("zone_probability")
    zone_setup   = stock.get("zone_ai_setup") or ""
    cat_summary  = (stock.get("catalyst_summary") or "").strip()
    rvol         = stock.get("rel_volume") or 0
    daily_trend  = stock.get("daily_trend") or ""
    h4_trend     = stock.get("h4_trend") or ""
    fvg_bull     = stock.get("fvg_bullish") or False
    fvg_bear     = stock.get("fvg_bearish") or False
    demand_grade = stock.get("demand_zone_grade") or ""
    supply_grade = stock.get("supply_zone_grade") or ""
    rs_score     = stock.get("rs_score") or 50
    sector_etf   = stock.get("sector_etf") or ""
    pct_ema20    = stock.get("pct_from_ema20") or 0
    in_supply    = stock.get("in_supply_zone") or False
    bos_bull     = False
    try:
        sm = _json.loads(stock.get("smart_money_json") or "{}")
        bos_bull = bool(sm.get("bos_bullish"))
    except Exception:
        pass

    # Grade
    if swing_score >= 8 and cat_score >= 6 and rr and rr >= 2:
        grade, grade_css = "A+", "plan-aplus"
    elif swing_score >= 7 and cat_score >= 5:
        grade, grade_css = "A",  "plan-a"
    elif swing_score >= 5 or cat_score >= 5:
        grade, grade_css = "B+", "plan-bplus"
    else:
        grade, grade_css = "B",  "plan-b"

    # Probability
    prob = zone_prob or max(30, min(90, 30 + swing_score * 4 + cat_score * 3))

    # Reasons (green signals)
    reasons = []
    if cat_summary:
        reasons.append(cat_summary[:90])
    if rvol >= 4:
        reasons.append(f"RVOL {rvol:.1f}x — institutional momentum")
    elif rvol >= 2:
        reasons.append(f"RVOL {rvol:.1f}x — above average volume")
    elif rvol >= 1.3:
        reasons.append(f"RVOL {rvol:.1f}x — moderate interest")
    if "Bullish" in daily_trend:
        reasons.append("Daily trend bullish — higher highs / higher lows")
    if "Bullish" in h4_trend:
        reasons.append("4H trend bullish — momentum aligning")
    if demand_grade in ("A+", "A"):
        reasons.append(f"Institutional demand zone ({demand_grade}) below")
    if fvg_bull:
        reasons.append("Bullish Fair Value Gap — liquidity void support")
    if bos_bull:
        reasons.append("Break of structure bullish — trend confirmed")
    if rs_score >= 75:
        reasons.append(f"Outperforming QQQ (RS {rs_score})")
    if sector_etf:
        reasons.append(f"Sector: {sector_etf} — check sector strength")

    # Warnings (red flags / avoidance)
    warnings = []
    if in_supply or supply_grade in ("A+", "A"):
        warnings.append("Near supply zone — watch for rejection")
    if fvg_bear:
        warnings.append("Bearish Fair Value Gap overhead — possible resistance")
    if rvol < 0.8:
        warnings.append("Low relative volume — weak institutional interest")
    if rr and rr < 1.5:
        warnings.append(f"R:R {rr:.1f}:1 is too weak — minimum 1.5:1 needed")
    if pct_ema20 > 8:
        warnings.append(f"Extended {pct_ema20:.1f}% above 20 EMA — wait for pullback")
    if swing_status == "WAIT":
        warnings.append("No confirmed entry signal — monitor for setup")
    if rs_score < 30:
        warnings.append(f"Weak RS ({rs_score}) — underperforming QQQ")

    entry_low  = stock.get("entry_zone_low")
    entry_high = stock.get("entry_zone_high")
    entry_mid  = None
    if entry_low and entry_high:
        entry_mid = round((entry_low + entry_high) / 2, 2)

    return {
        "grade":       grade,
        "grade_css":   grade_css,
        "setup_label": zone_setup or swing_type or "Setup Forming",
        "probability": prob,
        "entry_low":   entry_low,
        "entry_high":  entry_high,
        "entry_mid":   entry_mid,
        "stop":        stock.get("stop_level"),
        "target1":     stock.get("target_1"),
        "target2":     stock.get("target_2"),
        "rr":          rr,
        "reasons":     reasons[:7],
        "warnings":    warnings[:4],
        "has_plan":    bool(entry_low or stock.get("stop_level") or stock.get("target_1")),
        "swing_score": swing_score,
        "cat_score":   cat_score,
    }


def get_active_wl_id() -> int | None:
    """
    Return the active watchlist ID from the session.
    Falls back to the first watchlist if the session value is missing or stale.
    Returns None only when no watchlists exist at all.
    """
    all_wls = get_all_watchlists(current_user_id())
    if not all_wls:
        return None
    wl_id = session.get("active_wl_id")
    if wl_id and any(w["id"] == wl_id for w in all_wls):
        return wl_id
    return all_wls[0]["id"]


def run_auto_classification(ticker: str, user_id: int = 1):
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
    all_wls = get_all_watchlists(user_id)
    default_wl_map = {wl["name"]: wl["id"] for wl in all_wls
                      if wl["name"] in DEFAULT_WATCHLISTS}
    if not default_wl_map:
        return

    target_id = default_wl_map.get(target_name)
    if not target_id:
        return

    # Only reorganize if the stock is already in at least one default list
    current_ids = set(get_ticker_watchlist_ids(ticker, user_id))
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

    all_wls = get_all_watchlists(1)
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
        "Momentum Breakout":             "setup-momentum-breakout",
        "Momentum Runner":               "setup-momentum-runner",
        "Gap and Go":                    "setup-gap-go",
        "Breakdown":                     "setup-breakdown",
        "VWAP Reclaim":                  "setup-vwap",
        "Range Break":                   "setup-range",
        "ORB":                           "setup-orb",
        # Swing pullback / entry types
        "Pullback to Support":           "setup-pullback",
        "Pullback to 20 EMA":            "setup-pullback",
        "Pullback to 50 EMA":            "setup-pullback",
        "Breakout Retest":               "setup-breakout-retest",
        "Breakout Retest Forming":       "setup-breakout-retest",
        "Near 50% Retracement":          "setup-fib50",
        "Near 61.8% Retracement":        "setup-fib618",
        "Order Block Test":              "setup-order-block",
        # Continuation / run-up types (green/teal accent)
        "Breakout Continuation":         "setup-continuation",
        "Earnings Continuation":         "setup-continuation",
        "Bull Flag":                     "setup-bull-flag",
        "Relative Strength Leader":      "setup-rs-leader",
        "Trend Continuation":            "setup-trend-continuation",
        # Extended / chase (amber accent)
        "Extended — Wait for Pullback":  "setup-extended",
        "Extended — Wait":               "setup-extended",
        "Chase Zone — Do Not Enter":     "setup-chase",
        # Avoid
        "At Resistance — Avoid":         "setup-resistance-avoid",
        "At Resistance Avoid":           "setup-resistance-avoid",
        "Weak Structure — Avoid":        "setup-weak-structure",
        "Weak Structure Avoid":          "setup-weak-structure",
        "No Setup":                      "setup-none",
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

    if exit_ == 0:
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


def compute_trade_coach(stock: dict, plan: dict, market_temp: dict, risk_settings: dict) -> dict:
    """
    Return a coaching verdict for the current stock + plan + market conditions.
    Output: {coach_status, message, level, css, reduce_size, signals}
    coach_status : "TRADE ALLOWED" | "WATCH" | "REDUCE SIZE" | "BLOCKED"
    level        : "go" | "watch" | "reduce" | "blocked"
    """
    mt        = market_temp or {}
    regime    = mt.get("regime", "NEUTRAL")
    longs_ok  = mt.get("longs_ok", True)
    shorts_ok = mt.get("shorts_ok", True)
    reduce_sz = mt.get("reduce_size", False)
    decision  = mt.get("decision_cmd", "")
    if decision in ("Loading…", "—", "", None):
        decision = ""

    tp       = stock.get("trade_permission") or {}
    perm     = tp.get("permission", "WATCH")
    perm_rsn = tp.get("reason", "")

    bias          = stock.get("trade_bias", "")
    swing_sc      = float(stock.get("swing_score") or 0)
    cat_sc        = float(stock.get("catalyst_score") or 0)
    rr            = float(stock.get("risk_reward") or 0)
    trend         = stock.get("daily_trend") or ""
    extended      = bool(stock.get("is_extended", False))
    entry_quality = stock.get("entry_quality") or ""
    momentum_sc   = float(stock.get("momentum_score") or 0)

    # Setup label: prefer mode-specific type, fallback to generic
    trading_mode = risk_settings.get("trading_mode", "SWING TRADE")
    is_day_trade = "DAY" in trading_mode.upper()
    setup_raw    = stock.get("setup_type") if is_day_trade else stock.get("swing_setup_type")
    setup_raw    = setup_raw or stock.get("setup_type") or stock.get("swing_setup_type") or ""
    setup_label  = setup_raw.strip() if setup_raw else "this setup"
    mode_label   = "Day trade" if is_day_trade else "Swing trade"

    has_entry  = bool(plan.get("entry_level"))
    has_stop   = bool(plan.get("stop_loss"))
    has_target = bool(plan.get("target_price"))

    signals = []
    missing = []

    # ── Hard blocks ──────────────────────────────────────────────────────────
    if regime == "NO_TRADE":
        return {
            "coach_status": "BLOCKED",
            "message": "Blocked. VIX is spiking and market conditions do not support any entries today. Sit out.",
            "level": "blocked", "css": "coach-blocked", "reduce_size": False,
            "signals": ["No-trade regime active (VIX spike)", "Wait for volatility to normalize"],
        }

    if perm == "BLOCKED":
        rsn = perm_rsn[:120] if perm_rsn else "Setup does not meet minimum entry criteria."
        return {
            "coach_status": "BLOCKED",
            "message": f"Blocked. {rsn}",
            "level": "blocked", "css": "coach-blocked", "reduce_size": False,
            "signals": [perm_rsn] if perm_rsn else ["Review setup quality and entry conditions"],
        }

    if bias == "Long" and longs_ok is False:
        return {
            "coach_status": "BLOCKED",
            "message": "Blocked. Market regime is risk-off — longs are not supported right now. Wait for a regime shift.",
            "level": "blocked", "css": "coach-blocked", "reduce_size": True,
            "signals": [f"Regime: {regime} — longs suppressed", "Consider waiting or flipping to short bias"],
        }

    if bias == "Short" and shorts_ok is False:
        return {
            "coach_status": "BLOCKED",
            "message": "Blocked. Market is trending up — shorting here is against the tape. Wait for a topping structure.",
            "level": "blocked", "css": "coach-blocked", "reduce_size": True,
            "signals": [f"Regime: {regime} — shorts suppressed", "Wait for clear topping structure or trend shift"],
        }

    # ── Missing plan elements ────────────────────────────────────────────────
    if has_entry and not has_stop:
        missing.append("no stop loss — cannot size position")
    if has_entry and has_stop and not has_target:
        missing.append("no target — R:R unknown")

    # ── Quality signals ──────────────────────────────────────────────────────
    if swing_sc >= 8:
        signals.append(f"A+ setup ({swing_sc:.0f}/10)")
    elif swing_sc >= 6:
        signals.append(f"Quality setup ({swing_sc:.0f}/10)")
    elif swing_sc > 0:
        signals.append(f"Weak setup ({swing_sc:.0f}/10)")

    if momentum_sc >= 7:
        signals.append(f"Strong momentum ({momentum_sc:.0f}/10)")
    elif momentum_sc >= 4:
        signals.append(f"Moderate momentum ({momentum_sc:.0f}/10)")
    elif momentum_sc > 0:
        signals.append(f"Weak momentum ({momentum_sc:.0f}/10)")

    if cat_sc >= 7:
        signals.append(f"Strong catalyst ({cat_sc:.0f}/10)")
    elif cat_sc >= 4:
        signals.append(f"Catalyst present ({cat_sc:.0f}/10)")
    elif cat_sc > 0:
        signals.append(f"Weak catalyst ({cat_sc:.0f}/10)")

    if rr >= 3:
        signals.append(f"Excellent R:R ({rr:.1f}:1)")
    elif rr >= 2:
        signals.append(f"Good R:R ({rr:.1f}:1)")
    elif rr >= 1:
        signals.append(f"Acceptable R:R ({rr:.1f}:1)")
    elif rr > 0:
        signals.append(f"Poor R:R ({rr:.1f}:1 — consider passing)")

    if trend:
        signals.append(f"Trend: {trend}")

    is_extended_entry = extended or entry_quality == "Extended"
    if is_extended_entry:
        signals.append("Price extended from ideal entry zone")

    _regime_labels = {
        "RISK_ON":  "Market: Risk-on (favorable)",
        "NEUTRAL":  "Market: Neutral",
        "CAUTION":  "Market: Caution/chop zone",
        "RISK_OFF": "Market: Risk-off",
    }
    if regime in _regime_labels:
        signals.append(_regime_labels[regime])

    # ── Determine if market requires size reduction ──────────────────────────
    reduce_flag = reduce_sz or regime in ("CAUTION", "RISK_OFF")

    # ── Build verdict ────────────────────────────────────────────────────────
    if perm == "TRADE ALLOWED":

        # Weak R:R hard gate
        if rr > 0 and rr < 1.5:
            if missing:
                signals.append("Note: " + "; ".join(missing))
            return {
                "coach_status": "BLOCKED",
                "message": f"Blocked. Risk/reward is too weak ({rr:.1f}:1) — need at least 1.5:1 to justify entry.",
                "level": "blocked", "css": "coach-blocked", "reduce_size": False,
                "signals": signals[:6],
            }

        # Extended entry: downgrade to WATCH regardless of permission
        if is_extended_entry:
            msg = (f"Watch only. {mode_label} entry is extended from the ideal zone. "
                   f"Do not chase — wait for a pullback into the entry zone.")
            if missing:
                msg += " Note: " + "; ".join(missing) + "."
            return {
                "coach_status": "WATCH",
                "message": msg,
                "level": "watch", "css": "coach-watch", "reduce_size": False,
                "signals": signals[:6],
            }

        # A+ setup fully confirmed
        if swing_sc >= 8 and cat_sc >= 5 and rr >= 2:
            if regime == "RISK_ON" and "Bullish" in trend and bias == "Long":
                msg = (f"Trade allowed. A+ {setup_label} — trend, structure, and market all aligned for longs. "
                       f"Execute with standard size.")
            elif "Bearish" in trend and bias == "Short":
                msg = (f"Trade allowed. A+ {setup_label} aligned for the short side. "
                       f"Confirm structure break before entry.")
            else:
                msg = (f"Trade allowed. A+ {setup_label} — quality setup with solid R:R. "
                       f"Confirm entry trigger before executing.")
            level, css, status = "go", "coach-go", "TRADE ALLOWED"

        # Solid setup with good R:R
        elif swing_sc >= 6 and rr >= 1.5:
            msg = (f"Trade allowed. Quality {setup_label} with acceptable R:R. "
                   f"Standard size, disciplined execution.")
            level, css, status = "go", "coach-go", "TRADE ALLOWED"

        # Below-average quality → REDUCE SIZE
        else:
            msg = (f"Trade allowed but {setup_label} quality is below ideal. "
                   f"Use reduced size and tight stop discipline.")
            level, css, status = "reduce", "coach-reduce", "REDUCE SIZE"
            reduce_flag = True

        # Override to REDUCE SIZE when market conditions are weak
        if reduce_flag and level == "go":
            size_note = f" ({decision})" if decision else ""
            msg += f" Reduce position size — market conditions are not fully supportive{size_note}."
            level, css, status = "reduce", "coach-reduce", "REDUCE SIZE"

    elif perm == "WATCH":
        if "Bullish pullback" in perm_rsn:
            msg = (
                "Watch — Bullish pullback in progress. Price is above both 20 EMA and 50 EMA "
                "with a healthy Fibonacci retracement. Trend structure (HH/HL) not yet confirmed "
                "— wait for a higher low to print before entering. Strong continuation candidate "
                "if demand holds here."
            )
        elif is_extended_entry:
            msg = ("Watch only. Price is extended from ideal entry — do not chase. "
                   "Wait for a pullback into the zone.")
        elif swing_sc >= 7:
            msg = (f"Watch only. {setup_label} looks promising but entry is not yet confirmed. "
                   f"Be patient and let the setup come to you.")
        elif swing_sc >= 5:
            msg = ("Watch only. Setup is forming but not confirmed. "
                   "Wait for a clearer signal before committing capital.")
        elif not has_entry:
            msg = ("Watch only. No entry level defined — build a trade plan before considering this.")
        else:
            msg = ("Watch only. Setup quality is too low to justify entry right now. "
                   "Check back when conditions improve.")
        level, css, status = "watch", "coach-watch", "WATCH"

        # Weak market makes WATCH more of a REDUCE SIZE warning
        if regime in ("RISK_OFF", "CAUTION"):
            msg += " Market conditions add extra reason to stay on the sidelines or go very small."
            level, css, status = "reduce", "coach-reduce", "REDUCE SIZE"

    else:
        msg = ("No clear action. Review setup quality and current market conditions before committing capital.")
        level, css, status = "watch", "coach-watch", "WATCH"

    if missing:
        msg += " Note: " + "; ".join(missing) + "."

    return {
        "coach_status": status,
        "message": msg,
        "level": level,
        "css": css,
        "reduce_size": reduce_flag,
        "signals": signals[:6],
    }


# ---------------------------------------------------------------------------
# Discipline & Risk Engine — pure functions (no DB, no Flask)
# ---------------------------------------------------------------------------

def get_risk_settings(user_id: int = 1) -> dict:
    """Load all risk settings from user_settings with safe defaults."""
    def _f(key, default):
        v = get_user_setting(user_id, key)
        if v is not None:
            return v
        # Fallback to global settings for backward compat during migration
        gv = get_setting(key)
        return gv if gv is not None else default

    def _sf(key, default):
        try:
            return float(_f(key, default))
        except (ValueError, TypeError):
            return float(default)

    def _si(key, default):
        try:
            return int(float(_f(key, default)))
        except (ValueError, TypeError):
            return int(default)

    return {
        "trading_mode":        _f("trading_mode",        "SWING TRADE"),
        "account_size":        _sf("account_size",        "10000"),
        "risk_pct":            _sf("risk_pct",            "1.0"),
        "max_trades_per_day":  _si("max_trades_per_day",  "3"),
        "max_daily_loss_pct":  _sf("max_daily_loss_pct",  "3.0"),
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

    # Label-based extension — hard block for day trades only.
    # Swing trades use the independent EMA-distance check below so they aren't
    # double-penalised when the label fires on a healthy pullback leg.
    if entry == "Extended" and trade_mode != "SWING TRADE":
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
            # Classify as bullish pullback (WATCH) when price is above both EMAs
            # and within a healthy fib retracement — trend still intact, structure forming
            above_20ema = pct_ema20 is not None and pct_ema20 > 0
            above_50ema = pct_ema50 is not None and pct_ema50 > 0
            fib_705_val = stock.get("fib_705")
            in_fib_zone = bool(
                current and fib_618 and fib_705_val and
                fib_705_val <= current <= fib_618 * 1.02
            ) or bool(
                current and fib_50 and current >= fib_50 * 0.97
            )
            if long_bias and above_20ema and above_50ema and in_fib_zone:
                return {"permission": "WATCH", "css": "perm-watch",
                        "reason": ("Bullish pullback / continuation watch — price above 20 & 50 EMA "
                                   "within healthy fib zone; wait for HH/HL structure to confirm")}
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
            if not e.get("exit_price"):
                continue
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
        ts = datetime.fromisoformat(triggered_at)
        # If the stored timestamp is timezone-aware, compare against a UTC-aware now
        # to avoid TypeError: can't subtract offset-naive and offset-aware datetimes.
        if ts.tzinfo is not None:
            from datetime import timezone as _tz
            now = datetime.now(tz=_tz.utc)
        else:
            now = datetime.now()

        # Triggered before this session's open → treat as premarket reference
        import zoneinfo
        ts_et = ts.astimezone(zoneinfo.ZoneInfo("America/New_York"))
        if ts_et.hour < 9 or (ts_et.hour == 9 and ts_et.minute < 30):
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


def annotate(stock: dict, trade_mode: str | None = None) -> dict:
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
    def _headline_age_min(hfa):
        if not hfa:
            return None
        try:
            dt = datetime.fromisoformat(hfa)
            now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
            return int((now - dt).total_seconds() / 60)
        except Exception:
            return None
    stock["headline_freshness"] = _fl(_headline_age_min(stock.get("headlines_fetched_at")))

    # ── Swing trading display fields ─────────────────────────────────────────
    _swing_score = stock.get("swing_score")
    if _swing_score is None:
        stock["swing_score"] = 0
    stock["swing_score_class"]      = get_score_class(stock.get("swing_score"))
    stock["swing_status_class"]     = get_swing_status_class(stock.get("swing_status") or "")
    stock["swing_setup_type_class"] = get_setup_type_class(stock.get("swing_setup_type") or "No Setup")
    stock["swing_grade"]            = compute_swing_grade(stock.get("swing_score") or 1)
    stock["continuation_score"]     = compute_continuation_score(stock)

    # ── Institutional zone display fields ─────────────────────────────────────
    _zones_str = stock.get("zones_json")
    try:
        stock["parsed_zones"] = _json.loads(_zones_str) if _zones_str else []
    except Exception:
        stock["parsed_zones"] = []

    _sm_str = stock.get("smart_money_json")
    try:
        stock["smart_money"] = _json.loads(_sm_str) if _sm_str else {}
    except Exception:
        stock["smart_money"] = {}

    _reason_str = stock.get("zone_ai_reason")
    try:
        stock["zone_ai_reasons"] = _json.loads(_reason_str) if _reason_str else []
    except Exception:
        stock["zone_ai_reasons"] = []

    # Separate demand/supply zones for template convenience
    _pz = stock.get("parsed_zones") or []
    stock["demand_zones"] = sorted(
        [z for z in _pz if z.get("zone_type") == "demand"],
        key=lambda z: z.get("final_score", 0), reverse=True
    )
    stock["supply_zones"] = sorted(
        [z for z in _pz if z.get("zone_type") == "supply"],
        key=lambda z: z.get("final_score", 0), reverse=True
    )

    # Zone grade CSS mapping
    _grade_css = {"A+": "zone-grade-aplus", "A": "zone-grade-a",
                  "B+": "zone-grade-bplus", "B": "zone-grade-b"}
    stock["demand_zone_grade_css"] = _grade_css.get(stock.get("demand_zone_grade") or "", "")
    stock["supply_zone_grade_css"] = _grade_css.get(stock.get("supply_zone_grade") or "", "")

    # ── Plan mode displa