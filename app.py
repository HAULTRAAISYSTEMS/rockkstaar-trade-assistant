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
    DEFAULT_WATCHLISTS, PERSONAL_WATCHLISTS,
    get_setting, set_setting,
    get_user_setting, set_user_setting,
    create_user, get_user_by_id, get_user_by_username,
    get_all_users, update_user_password, delete_user, check_user_password,
    ensure_user_watchlists,
    get_all_watchlists, get_watchlist_by_id, create_watchlist,
    rename_watchlist, delete_watchlist,
    get_watchlist_stocks, get_user_tracked_tickers, get_watchlist_stock_counts, get_watchlist_structure,
    create_watchlist_section, rename_watchlist_section, delete_watchlist_section,
    move_watchlist_ticker, save_watchlist_order,
    add_ticker_to_watchlist, remove_ticker_from_watchlist,
    remove_ticker_from_defaults,
    get_ticker_watchlist_ids, set_ticker_watchlists, sync_ticker_auto_bucket,
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
    create_price_alert, get_price_alerts, set_price_alert_enabled,
    delete_price_alert,
    get_insider_alert_rules, set_insider_alert_rules,
    save_setup_outcome, get_setup_outcome_stats,
    save_study_log_entry, get_study_log, delete_study_log_entry,
    get_ai_briefing, save_ai_briefing,
    get_score_narration, save_score_narration,
    get_journal_summary, save_journal_summary,
    get_earnings_digest, save_earnings_digest,
    get_paper_account, get_paper_positions, get_paper_orders, execute_paper_order,
)
from mock_data import generate_stock_data, load_mock_watchlist, live_refresh_stock, _swing_defaults, _zone_defaults
from data_fetcher import _et_now, market_session_now, orb_phase_now
from scoring import (catalyst_score_breakdown, SETUP_TYPES, SWING_SETUP_TYPES, SWING_STATUSES,
                     compute_swing_grade, compute_continuation_score)
from classifier import classify, classify_stock, A_PLUS_READY
from alerts import generate_alerts, get_alerts, get_alert_count, clear_alerts as _clear_alerts
from news_fetcher import CATALYST_CATEGORIES as _CAT_DEFS, freshness_label as _fl
import scanner as _scanner
import intel_engine as _intel
import schwab as _schwab
from watchlist_utils import parse_watchlist_symbols
_mkt = None  # set below if market_engine is available
try:
    import market_engine as _mkt
    _MKT_AVAILABLE = True
except Exception:
    _MKT_AVAILABLE = False

app = Flask(__name__)
_is_production = bool(
    os.environ.get("RENDER")
    or os.environ.get("DATABASE_URL")
    or os.environ.get("APP_ENV", "").lower() == "production"
)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_is_production,
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)

# ---------------------------------------------------------------------------
# Secret key — always supplied by the deployment environment.
# Refuse to start without it so sessions and CSRF tokens can never be signed
# with a public, predictable fallback value.
# ---------------------------------------------------------------------------
_secret_key = os.environ.get("SECRET_KEY")
if not _secret_key:
    raise RuntimeError(
        "SECRET_KEY environment variable is required. "
        "Set it to a long, random value before starting Tradestaar Elite."
    )
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
    """The signed-in user's id.

    This defaulted to 1 — the bootstrap admin — for any caller without a
    session. Almost everything is behind _require_login, so it rarely
    mattered, but a handler reached without one operated as the admin rather
    than failing, and the public-path allowlist is the only thing standing
    between that default and an account-takeover primitive. Anonymous callers
    now get 0, which owns nothing.
    """
    return int(session.get("user_id") or 0)


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
        # A production database outage must never silently turn authentication off.
        return _is_production


def _registration_enabled() -> bool:
    """Require explicit opt-in before exposing public self-registration."""
    allow = os.environ.get("ALLOW_REGISTRATION", "").strip().lower()
    if allow in {"1", "true", "yes", "on"}:
        return True
    try:
        return not bool(get_all_users())
    except Exception:
        return False


def require_admin(f):
    """Decorator — 403 unless logged-in user is admin."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            # The conditional used to sit inside the tuple, so a non-API path
            # returned (Response, (html, 403)) — Flask read the inner tuple as
            # headers and raised, turning every 403 into a 500 and never
            # rendering the login page it was meant to show.
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "admin required"}), 403
            return render_template(
                "login.html", error="Admin access required.", next=""), 403
        return f(*args, **kwargs)
    return decorated


@app.before_request
def _require_login():
    # Always public — Schwab OAuth callback must stay reachable; callback validates PKCE/state.
    if (request.path in ("/login", "/logout", "/register", "/favicon.ico", "/health",
                         "/service-worker.js", "/schwab/callback")
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
            {"WWW-Authenticate": 'Basic realm="Tradestaar Elite"'},
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
    return send_from_directory(app.static_folder or "static", "favicon-32.png", mimetype="image/png")


@app.route("/service-worker.js")
def service_worker():
    """Serve the PWA worker from the site root so it can cover every app route."""
    response = send_from_directory(
        app.static_folder or "static", "service-worker.js", mimetype="application/javascript"
    )
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


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
    try:
        from database import get_db
        conn = get_db()
        conn.execute("SELECT 1")
        conn.close()
        return jsonify({"ok": True, "database": "ready"}), 200
    except Exception:
        logger.exception("health check failed")
        return jsonify({"ok": False, "database": "unavailable"}), 503


# ---------------------------------------------------------------------------
# Security headers — applied to every response
# ---------------------------------------------------------------------------
@app.after_request
def _add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if _is_production:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


# ---------------------------------------------------------------------------
# /debug/status — production health check (no secret values exposed)
# ---------------------------------------------------------------------------
@app.route("/debug/status")
@require_admin
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
        "NEWS_API_KEY":       os.environ.get("NEWS_API_KEY"),
        "POLYGON_API_KEY":    os.environ.get("POLYGON_API_KEY"),
        "SCHWAB_CLIENT_ID":   os.environ.get("SCHWAB_CLIENT_ID"),
        "ALPACA_OVERNIGHT":   (
            (os.environ.get("ALPACA_API_KEY") or os.environ.get("APCA_API_KEY_ID"))
            and (os.environ.get("ALPACA_SECRET_KEY") or os.environ.get("APCA_API_SECRET_KEY"))
        ),
    }
    env_detail = {}
    env_ok = True
    for k, v in _required_vars.items():
        if k == "SECRET_KEY" and not v:
            env_detail[k] = "MISSING (application startup blocked)"
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
    if _is_production:
        raise

# Importing this module started five daemon threads that hit the network and
# write to the database: the momentum scanner, the intel alert loop, two cache
# pre-warms and the demo seed. That is right for the web process and wrong
# everywhere else — a test run, a management script, or anything that imports
# app.py to read one function fires all of it. TRADESTAAR_NO_BACKGROUND=1
# keeps the routes and leaves the daemons unstarted.
_BACKGROUND_ENABLED = os.environ.get("TRADESTAAR_NO_BACKGROUND", "").strip().lower() \
    not in ("1", "true", "yes", "on")

# Start the background momentum scanner daemon (no-op if already running).
try:
    if _BACKGROUND_ENABLED:
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
    if _BACKGROUND_ENABLED:
        threading.Thread(target=_intel_alert_loop, daemon=True, name="intel-alerts").start()
except Exception as _loop_err:
    logger.error("intel alert loop failed to start: %s", _loop_err)

# Pre-warm the intel cache so the first page load is instant
try:
    if _BACKGROUND_ENABLED:
        _intel.trigger_background_refresh()
except Exception as _warm_err:
    logger.error("intel bg refresh failed at startup: %s", _warm_err)

# Pre-warm the market context cache (regime, sectors, RS baseline)
try:
    if _MKT_AVAILABLE and _BACKGROUND_ENABLED:
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


def auto_refresh_stale_closes(
    tickers: list, data_map: dict | None = None, user_id: int = 1
) -> list:
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
                    run_auto_classification(ticker, user_id)
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


def _build_entry_trigger(stock: dict):
    """
    Return (trigger_text, css_class) — a specific, actionable entry instruction
    shown on the stock detail page so the trader knows EXACTLY when to execute.

    CSS classes: entry-trigger-confirmed | entry-trigger-preconf |
                 entry-trigger-continuation | entry-trigger-wait | entry-trigger-avoid

    Handles Long Bias, Short Bias, and detects when price has already moved
    past the entry zone so the trader is never told to enter a stale setup.
    """
    swing_status = stock.get("swing_status") or ""
    bias         = stock.get("trade_bias") or "Neutral"
    ema20        = stock.get("ema_20_daily")
    pct_ema20    = stock.get("pct_from_ema20")
    entry_low    = stock.get("entry_zone_low")
    entry_high   = stock.get("entry_zone_high")
    fib_618      = stock.get("fib_618")
    current      = stock.get("current_price") or 0
    stop         = stock.get("stop_level")

    is_short    = bias in ("Short Bias", "Short")
    dir_word    = "below" if is_short else "above"
    candle_word = "bearish" if is_short else "bullish"

    if bias == "Avoid":
        return ("DO NOT TRADE — avoid bias set. No valid edge present. Remove from watchlist.",
                "entry-trigger-avoid")

    # ── READY — LEVEL HOLDS ───────────────────────────────────────────────────
    if swing_status == "READY — LEVEL HOLDS":
        if entry_low and entry_high and stop:
            return (
                f"EXECUTE NOW — Entry zone ${entry_low:.2f}–${entry_high:.2f} confirmed. "
                f"Level holding with volume. Stop: ${stop:.2f}. Enter current zone.",
                "entry-trigger-confirmed")
        if entry_low and entry_high:
            return (
                f"EXECUTE NOW — Entry zone ${entry_low:.2f}–${entry_high:.2f} confirmed. "
                "Level holding with volume. Enter current zone.",
                "entry-trigger-confirmed")
        return ("EXECUTE NOW — Key level confirmed with volume. Enter at current price.",
                "entry-trigger-confirmed")

    # ── PRE-CONFIRMATION ──────────────────────────────────────────────────────
    if swing_status == "PRE-CONFIRMATION":
        if ema20 and pct_ema20 is not None and abs(pct_ema20) <= 3.5:
            return (
                f"WAIT FOR TRIGGER — 15m candle must close {dir_word} 20 EMA (${ema20:.2f}) "
                "on volume ≥ 1.2x avg. That close = entry signal. Do not jump early.",
                "entry-trigger-preconf")
        if fib_618 and current and abs(current - fib_618) / current * 100 <= 4.5:
            return (
                f"WAIT FOR TRIGGER — Approaching 61.8% Fib (${fib_618:.2f}). "
                f"Need 15m candle close {dir_word} level + volume ≥ 1.2x avg before entry.",
                "entry-trigger-preconf")
        if entry_low and entry_high:
            return (
                f"WAIT FOR TRIGGER — Price approaching entry zone ${entry_low:.2f}–${entry_high:.2f}. "
                f"Need 15m {candle_word} confirmation candle + volume ≥ 1.2x avg. Do not enter early.",
                "entry-trigger-preconf")
        return (
            f"WAIT FOR TRIGGER — Near key level. Need 15m {candle_word} candle + volume ≥ 1.2x avg before entry.",
            "entry-trigger-preconf")

    # ── TREND CONTINUATION ────────────────────────────────────────────────────
    if swing_status == "TREND CONTINUATION":
        if is_short:
            # Short continuation: ideal entry is a bounce UP into resistance, then short on rejection.
            # If price is already BELOW the entry zone it already broke down — entry was missed.
            if entry_low and entry_high:
                if current and current < entry_low:
                    return (
                        f"MISSED ENTRY — Price already broke below entry zone (${entry_low:.2f}). "
                        f"Wait for a bounce back to ${entry_low:.2f}–${entry_high:.2f} resistance, "
                        "then enter short on 15m bearish rejection candle.",
                        "entry-trigger-wait")
                if current and current > entry_high:
                    return (
                        f"SHORT CONTINUATION — Price above entry zone. "
                        f"Wait for pullback to ${entry_low:.2f}–${entry_high:.2f} resistance, "
                        "then enter short on 15m bearish rejection candle.",
                        "entry-trigger-continuation")
                return (
                    f"SHORT CONTINUATION — Price in resistance zone ${entry_low:.2f}–${entry_high:.2f}. "
                    "Wait for 15m bearish rejection candle + volume ≥ 1.2x avg, then enter short.",
                    "entry-trigger-continuation")
            if entry_high:
                return (
                    f"SHORT CONTINUATION — Wait for bounce to ${entry_high:.2f} resistance, "
                    "then enter short on 15m bearish rejection candle.",
                    "entry-trigger-continuation")
            return ("SHORT CONTINUATION — Wait for bounce to key resistance. Short the lower high with volume confirmation.",
                    "entry-trigger-continuation")
        else:
            # Long continuation: ideal entry is a pullback DOWN to support, then buy the hold.
            # If price is already BELOW the entry zone it broke through support — entry was missed.
            if entry_low and entry_high:
                if current and current < entry_low:
                    return (
                        f"MISSED ENTRY — Price already broke below entry zone (${entry_low:.2f}). "
                        f"Wait for new setup or bounce back to ${entry_low:.2f}–${entry_high:.2f} before re-evaluating.",
                        "entry-trigger-wait")
                return (
                    f"PULLBACK ENTRY — Wait for pullback to ${entry_low:.2f}–${entry_high:.2f}. "
                    "Buy the higher low. Need 15m momentum candle showing trend resuming.",
                    "entry-trigger-continuation")
            if entry_low:
                return (
                    f"PULLBACK ENTRY — Wait for pullback to ${entry_low:.2f}. "
                    "Buy the higher low. Need 15m momentum candle showing trend resuming.",
                    "entry-trigger-continuation")
            return ("TREND CONTINUATION — Wait for pullback to key level. Buy the higher low with volume confirmation.",
                    "entry-trigger-continuation")

    # WAIT or unknown
    return ("NOT READY — No valid entry signal. Monitor for READY — LEVEL HOLDS or PRE-CONFIRMATION status.",
            "entry-trigger-wait")


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
    bos_bear     = False
    try:
        sm = _json.loads(stock.get("smart_money_json") or "{}")
        bos_bull = bool(sm.get("bos_bullish"))
        bos_bear = bool(sm.get("bos_bearish"))
    except Exception:
        pass

    # Grade — read from the canonical classify() result instead of a
    # second, independently-tuned threshold table. This is the exact field
    # shown as the grade badge everywhere else for this ticker, so the AI
    # trade plan panel can never show a grade that contradicts the
    # dashboard table / Scanner Buckets / Best Swing Candidates badge.
    _plan_classification = classify(stock)
    grade = _plan_classification["grade"]
    grade_css = {
        "A+": "plan-aplus", "A": "plan-a",
        "B+": "plan-bplus", "B": "plan-b",
        "B-": "plan-bplus", "C": "plan-b", "D": "plan-b",
    }.get(grade, "plan-b")

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
    Classify a ticker and, if auto_classify is ON, mirror it into the appropriate
    automatic setup bucket. Personal/custom memberships are preserved.

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

    if target_name not in default_wl_map:
        return
    sync_ticker_auto_bucket(ticker, user_id, target_name)


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
        _sfa, _sfa_class, _sfa_reason = compute_swing_final_action(stock.get("swing_status") or "")
        stock["final_action"]       = _sfa
        stock["final_action_class"] = _sfa_class
        stock["final_action_reason"]= _sfa_reason
        stock["exec_class"]         = _sfa_class

    # ── Trade permission (requires trading mode from settings) ───────────────
    try:
        _trade_mode = trade_mode or get_setting("trading_mode") or "SWING TRADE"
        stock["trade_permission"] = compute_trade_permission(stock, _trade_mode)
    except Exception as _tp_exc:
        logger.warning("annotate  ticker=%s  trade_permission failed: %s", stock.get("ticker", "?"), _tp_exc)
        stock["trade_permission"] = {"permission": "WATCH", "css": "perm-watch", "reason": ""}

    # ── Canonical classification (single source of truth) ───────────────────
    # Previously this block independently re-derived an "A+ READY" / "B+
    # WATCH" / etc. badge from raw score fields with its own thresholds,
    # separate from classifier.classify_stock() (which drives watchlist
    # auto-membership) and separate again from build_ai_trade_plan()'s own
    # grade thresholds. That's exactly how the same ticker could show up
    # simultaneously as A+ READY in one widget and AVOID in another: every
    # widget was computing its own answer. Now every field below comes from
    # one classify(stock) call, and every other piece of UI (Scanner
    # Buckets, the Avoid/Blocked watchlist table, the top-5 "Best Swing
    # Candidates" cards, and the alerts feed) reads these same fields
    # instead of recomputing them.
    _classification = classify(stock)
    stock["classification"]          = _classification
    stock["bucket"]                  = _classification["bucket"]
    stock["simplified_action"]       = _classification["status_label"]
    stock["simplified_action_class"] = _classification["badge_css"]
    stock["avoid_blocked"]           = _classification["avoid_blocked"]
    stock["swing_grade"]             = _classification["grade"]
    stock["classify_reason"]         = _classification["reason"]

    # ── Relative Strength & Sector (market_engine) ────────────────────────────
    if _MKT_AVAILABLE:
        try:
            _mkt_ctx   = _get_mkt_ctx()
            _qqq_1d    = _mkt_ctx.get("qqq_1d_pct")
            _stock_gap = stock.get("gap_pct")
            _rs_db     = stock.get("rs_score")   # stored 20-day RS if available

            # Fast intraday RS from today's gap vs QQQ gap
            _rs_intra = _mkt.rs_score_intraday(_stock_gap, _qqq_1d)
            # Prefer stored 20-day RS; use intraday if not computed yet
            _rs_final = _rs_db if _rs_db else _rs_intra

            stock["rs_score_display"] = _rs_final
            stock["rs_label"]         = _mkt.rs_label(_rs_final)
            stock["rs_class"]         = _mkt.rs_css_class(_rs_final)
            stock["rs_vs_qqq_display"] = (
                f"{'+' if (_stock_gap or 0) - (_qqq_1d or 0) >= 0 else ''}"
                f"{((_stock_gap or 0) - (_qqq_1d or 0)):.1f}% vs QQQ"
            )

            # Sector for this ticker
            _etf = stock.get("sector_etf") or ""
            if not _etf and _MKT_AVAILABLE:
                _etf, _ = _mkt.get_sector_for_ticker(stock.get("ticker") or "")
                if _etf:
                    stock["sector_etf"] = _etf
            _leading = _mkt_ctx.get("leading_sectors") or []
            stock["sector_name"]    = _mkt.SECTOR_ETFS.get(_etf, "")
            stock["sector_leading"] = _etf in _leading if _etf else False
            stock["sector_class"]   = "sector-chip-leading" if stock["sector_leading"] else "sector-chip"

            # Market context for templates
            stock["mkt_regime"]       = _mkt_ctx.get("regime", "NEUTRAL")
            stock["mkt_regime_label"] = _mkt_ctx.get("regime_label", "Neutral")
        except Exception as _rs_exc:
            logger.debug("annotate RS/sector failed: %s", _rs_exc)
            stock.setdefault("rs_score_display", 50)
            stock.setdefault("rs_label",  "Neutral RS")
            stock.setdefault("rs_class",  "rs-avg")
            stock.setdefault("sector_name",    "")
            stock.setdefault("sector_leading", False)
            stock.setdefault("sector_class",   "sector-chip")
    else:
        stock.setdefault("rs_score_display", 50)
        stock.setdefault("rs_label",  "Neutral RS")
        stock.setdefault("rs_class",  "rs-avg")
        stock.setdefault("sector_name",    "")
        stock.setdefault("sector_leading", False)
        stock.setdefault("sector_class",   "sector-chip")

    # ── Trade Avoidance AI — warning flags ────────────────────────────────────
    _avoid_flags = []
    _pct_ema20 = stock.get("pct_from_ema20") or 0
    _rvol      = stock.get("rel_volume") or 0
    _rr_val    = stock.get("risk_reward")
    _in_sup    = stock.get("in_supply_zone") or False
    _fvg_b     = stock.get("fvg_bearish") or False
    _lh_ll     = stock.get("daily_lh_ll") or False
    _sup_grade = stock.get("supply_zone_grade") or ""
    _sw_stat   = stock.get("swing_status") or ""
    _price     = stock.get("current_price") or 0
    _pm_high   = stock.get("premarket_high") or 0
    _prev_day_high = stock.get("prev_day_high") or 0

    if _in_sup or _sup_grade in ("A+", "A"):
        _avoid_flags.append({"icon": "⚠", "text": "At institutional supply zone — watch for rejection", "level": "high"})
    if _fvg_b:
        _avoid_flags.append({"icon": "⬛", "text": "Bearish FVG overhead — institutional resistance", "level": "medium"})
    if abs(_pct_ema20) > 8 and _pct_ema20 > 0:
        _avoid_flags.append({"icon": "📈", "text": f"Extended {_pct_ema20:.1f}% above 20 EMA — high chase risk", "level": "high"})
    if _rvol < 0.8 and _price > 0:
        _avoid_flags.append({"icon": "📉", "text": "Low relative volume — weak institutional conviction", "level": "medium"})
    if _rr_val is not None and _rr_val < 1.5:
        _avoid_flags.append({"icon": "⚖", "text": f"Risk/reward {_rr_val:.1f}:1 — below 1.5:1 minimum", "level": "high"})
    if _lh_ll:
        _avoid_flags.append({"icon": "📉", "text": "Downtrend structure (LH/LL) — against institutional flow", "level": "medium"})
    if _pm_high and _price and _prev_day_high and _price > _prev_day_high * 1.05:
        _avoid_flags.append({"icon": "🔴", "text": "Significant gap up — late entry risk if chasing open", "level": "medium"})
    if _sw_stat in ("WAIT", "NOT ENOUGH EDGE"):
        _avoid_flags.append({"icon": "⏸", "text": "No confirmed entry setup — monitor only", "level": "low"})

    stock["avoidance_flags"]     = _avoid_flags
    stock["avoidance_flag_count"]= len(_avoid_flags)
    stock["avoidance_high"]      = any(f["level"] == "high" for f in _avoid_flags)

    # ── AI Trade Plan ─────────────────────────────────────────────────────────
    try:
        stock["ai_trade_plan"] = build_ai_trade_plan(stock)
    except Exception as _tp_err:
        logger.debug("annotate  ai_trade_plan failed: %s", _tp_err)
        stock["ai_trade_plan"] = {"has_plan": False, "grade": "B", "grade_css": "plan-b",
                                   "reasons": [], "warnings": [], "probability": 0}

    # ── Entry Trigger ─────────────────────────────────────────────────────────
    try:
        _trig_text, _trig_css = _build_entry_trigger(stock)
        stock["entry_trigger"]     = _trig_text
        stock["entry_trigger_css"] = _trig_css
    except Exception as _et_err:
        logger.debug("annotate entry_trigger failed: %s", _et_err)
        stock["entry_trigger"]     = ""
        stock["entry_trigger_css"] = "entry-trigger-wait"

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


_CONT_TYPES_TOP5 = {"Breakout Continuation", "Gap and Go", "Earnings Continuation",
                    "Bull Flag", "Relative Strength Leader", "Trend Continuation"}


def compute_top5(ranked: list) -> list:
    """
    Select the "Best Swing Candidates" — the single shared definition used
    by both the server-rendered dashboard and the live WebSocket/poll
    payload (_build_dashboard_payload). These used to be two separate,
    independently-maintained copies of this same threshold logic; one of
    them excluded avoid statuses using strings without the em dash the
    live status labels actually use ("AVOID — AT RESISTANCE" vs "AVOID AT
    RESISTANCE"), so it silently failed to exclude some avoid-classified
    tickers. Having one function means the cards you see on first page
    load and the cards the 4-second auto-refresh patches in are always
    selected by the exact same rule.

    Excludes anything classify(stock) marked avoid_blocked — that flag
    already covers Avoid bias, weak R:R, low signal, and avoid swing
    statuses, so a ticker shown as AVOID / BLOCKED anywhere else in the UI
    can never also appear in Best Swing Candidates.
    """
    return [
        s for s in ranked
        if not s.get("avoid_blocked") and (
        (
            # Continuation stocks: lower score bar when setup type is actionable
            (s.get("swing_score") or 0) >= 5
            and s.get("swing_setup_type") in _CONT_TYPES_TOP5
            and s.get("trade_bias") != "Avoid"
        ) or (
            # Swing mode: good score + actionable status
            (s.get("swing_score") or 0) >= 6
            and s.get("trade_bias") != "Avoid"
        ) or (
            # Legacy day-trading fallback when swing fields absent
            not s.get("swing_score")
            and (s.get("momentum_score") or 0) >= 6
            and s.get("orb_ready") == "YES"
            and s.get("entry_quality") != "Extended"
            and s.get("trade_bias") != "Avoid"
        ))
    ][:5]


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
    max_swing = 0  # initialized here; set below if has_swing_data

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
      - classify(stock)["avoid_blocked"] is True (covers trade_bias == "Avoid"
        plus the other AVOID / BLOCKED paths — weak R:R, low signal, etc. —
        so a ticker the rest of the UI shows as AVOID can never also appear
        "on the radar")
      - exec_state == "TRIGGERED" (already highlighted in alert banner)

    Each stock gets a tier label:
      "B Setup"    — momentum ≥ 4 AND setup ≥ 5 (worthy of active monitoring)
      "Watch Only" — everything else that qualifies (on the radar but not acting)
    """
    secondary = []
    for s in ranked:
        if s.get("ticker") in top5_set:
            continue
        if s.get("avoid_blocked") or s.get("trade_bias") == "Avoid":
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
    scanner_buckets={"aplus": [], "forming": [], "chase": [], "avoid": []},
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
                 "reason": "", "action_msg": "—", "longs_ok": None,
                 "shorts_ok": None, "reduce_size": False,
                 "score": None, "meter_score": 50, "error": True,
                 "spy_price": None, "spy_pct_ema20": None, "spy_vs_vwap": None,
                 "qqq_price": None, "qqq_pct_ema20": None, "qqq_vs_vwap": None,
                 "vix_level": None, "vix_direction": None,
                 "decision_cmd": "—", "risk_pct_rec": None, "size_multiplier": None,
                 "size_zone": "unknown", "why": "",
                 "mode_desc": "—", "es_price": None, "es_change_pct": None,
                 "es_above_vwap": None, "sectors": {}},
    mkt_context={
        "regime": "NEUTRAL", "regime_label": "Neutral",
        "qqq_trend": "Unknown", "spy_trend": "Unknown",
        "qqq_1d_pct": None, "spy_1d_pct": None,
        "signal": "", "longs_ok": True, "shorts_ok": True,
        "sectors": [], "leading_sectors": [], "weak_sectors": [],
        "top_sector": None, "vix_level": None,
        "qqq_price": None, "spy_price": None,
    },
)


@app.route("/setups")
def dashboard():
    """SETUPS — swing-setup scanner (buckets, market temp, best candidates,
    radar, watchlist table). Endpoint name kept as `dashboard` so existing
    url_for('dashboard') redirects (watchlist add/remove/refresh) still land
    here after mutations."""
    try:
        return _dashboard_inner()
    except Exception as exc:
        logger.error("dashboard  route=/setups  unhandled_error=%s", exc, exc_info=True)
        flash("Scanner error — please refresh the page.", "error")
        return render_template("dashboard.html", **_DASHBOARD_EMPTY)


@app.route("/account")
def account():
    """ACCOUNT & Performance — Schwab balances/positions, journal performance
    (win rate), and discipline counters. Read-only view over the existing
    Schwab, Journal, and Risk data sources (no new logic)."""
    uid = current_user_id()

    # Schwab snapshot (buying power, P&L, positions) — live when connected
    acct = None
    try:
        tok = _schwab.token_status(uid)
        if tok.get("connected"):
            acct = _get_schwab_data(uid)
            if acct.get("error"):
                acct = None
    except Exception as _ae:
        logger.debug("account: schwab fetch skipped: %s", _ae)

    # Journal performance (same store the Journal page uses)
    entries = get_all_journal_entries(uid)
    summary = compute_journal_summary(entries)

    # Discipline counters (today) — same computation as the Risk page
    today_str     = _et_now().strftime("%Y-%m-%d")
    daily_session = get_daily_session(today_str, uid)
    today_entries = get_journal_entries_for_date(today_str, uid)
    risk_s        = get_risk_settings(uid)
    discipline    = compute_discipline_score(today_entries, risk_s,
                                             bool(daily_session.get("locked")))
    trades_today  = len(today_entries)
    losses_today  = sum(1 for e in today_entries if e.get("result") == "Loss")

    return render_template(
        "account.html",
        acct=acct,
        entries=entries,
        summary=summary,
        discipline=discipline,
        daily_session=daily_session,
        trades_today=trades_today,
        losses_today=losses_today,
        risk_settings=risk_s,
    )


def _paper_snapshot(uid: int) -> dict:
    account = get_paper_account(uid)
    positions = get_paper_positions(uid)
    quotes = {s["ticker"]: s for s in get_all_stock_data()}
    market_value = unrealized = 0.0
    for pos in positions:
        quote = quotes.get(pos["ticker"], {})
        last = float(quote.get("current_price") or pos["avg_price"])
        pos["last_price"] = last
        pos["market_value"] = round(last * pos["quantity"], 2)
        pos["unrealized_pnl"] = round((last - pos["avg_price"]) * pos["quantity"], 2)
        market_value += pos["market_value"]
        unrealized += pos["unrealized_pnl"]
    orders = get_paper_orders(uid)
    realized = round(sum(float(o.get("realized_pnl") or 0) for o in orders), 2)
    equity = round(float(account["cash_balance"]) + market_value, 2)
    total_pnl = round(equity - float(account["starting_cash"]), 2)
    return {"account": account, "positions": positions, "orders": orders,
            "market_value": round(market_value, 2), "unrealized": round(unrealized, 2),
            "realized": realized, "equity": equity, "total_pnl": total_pnl,
            "return_pct": round(total_pnl / float(account["starting_cash"]) * 100, 2)}


@app.route("/paper")
def paper_trading():
    """Account-isolated, long-only paper portfolio and performance screen."""
    return render_template("paper.html", paper=_paper_snapshot(current_user_id()))


@app.route("/paper/order", methods=["POST"])
def paper_order():
    ticker = (request.form.get("ticker") or "").strip().upper()
    side = (request.form.get("side") or "BUY").strip().upper()
    try:
        quantity = int(request.form.get("quantity") or 0)
    except (TypeError, ValueError):
        quantity = 0
    if not re.fullmatch(r"[A-Z]{1,6}", ticker) or side not in ("BUY", "SELL") or quantity < 1:
        flash("Enter a valid ticker, side, and whole-share quantity.", "error")
        return redirect(url_for("paper_trading"))
    stock = get_stock_data(ticker)
    price = float(stock.get("current_price") or 0) if stock else 0
    if price <= 0:
        flash(f"No verified quote is available for {ticker}; no paper order was filled.", "error")
        return redirect(url_for("paper_trading"))
    try:
        fill = execute_paper_order(current_user_id(), ticker, side, quantity, price)
        flash(f"Paper {side.lower()} filled: {quantity} {ticker} @ ${price:,.2f}.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("paper_trading"))


def _dashboard_inner():
    uid          = current_user_id()
    all_wls      = get_all_watchlists(uid)
    active_wl_id = get_active_wl_id()
    active_wl    = get_watchlist_by_id(active_wl_id) if active_wl_id else None
    wl_counts    = get_watchlist_stock_counts(uid)

    watchlist = get_watchlist_stocks(active_wl_id) if active_wl_id else []

    # Single DB read — reused for staleness checks, expiry, and rendering.
    all_data = get_all_stock_data()
    data_map = {s["ticker"]: s for s in all_data}
    logger.info("dashboard  route=/  wl_id=%s  tickers=%s", active_wl_id, watchlist)

    # Fetch trade_mode once — passed into annotate() to avoid N DB calls.
    _trade_mode = get_setting("trading_mode") or "SWING TRADE"

    # Pass data_map so auto_refresh and expire don't make additional DB calls.
    if watchlist:
        auto_refresh_stale_closes(watchlist, data_map=data_map, user_id=uid)
        _expire_stuck_loading(watchlist, data_map=data_map)

    # Annotate per ticker — one bad ticker must not crash the whole dashboard
    stocks = []
    for t in watchlist:
        if t not in data_map:
            continue
        try:
            stocks.append(annotate(data_map[t], trade_mode=_trade_mode))
        except Exception as exc:
            logger.error("dashboard  ticker=%s  stage=annotate  err=%s", t, exc, exc_info=True)
            s = data_map[t]
            s["ticker_state"]       = "error"
            s["ticker_state_class"] = "state-error"
            s["ticker_state_label"] = "Data Error"
            stocks.append(s)

    missing = [t for t in watchlist if t not in data_map]

    ranked     = rank_stocks(stocks)

    # Best Swing Candidates — shared with the live WebSocket/poll payload,
    # see compute_top5() for why that matters.
    top5 = compute_top5(ranked)

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

    # ── Scanner buckets: 4-quadrant view of the full watchlist ───────────────
    # Grouped strictly by the canonical classify(stock)["scanner_key"] —
    # the exact same field that produced this ticker's bucket badge and
    # avoid_blocked flag a few lines ago in annotate(). Previously this
    # grouping re-matched on simplified_action/trade_bias/setup_type
    # strings with its own ad hoc rules, which is how a ticker could be
    # bucketed AVOID here while showing A+ READY everywhere else.
    _SCANNER_CAPS = {"avoid": 6, "chase": 8, "aplus": 6, "forming": 10}
    scanner_buckets: dict = {"aplus": [], "forming": [], "chase": [], "avoid": []}
    for _sb in ranked:
        _scanner_key = (_sb.get("classification") or {}).get("scanner_key", "forming")
        if _scanner_key not in scanner_buckets:
            _scanner_key = "forming"
        if len(scanner_buckets[_scanner_key]) < _SCANNER_CAPS.get(_scanner_key, 10):
            scanner_buckets[_scanner_key].append(_sb)

    # alt_modes kept for backward compat but no_trade replaces them in template
    alt_modes = []

    # Notes: pass a set of tickers that have notes for the indicator column
    notes_map = get_all_notes(uid)

    # ── Risk engine context ──────────────────────────────────────────────────
    risk_settings   = get_risk_settings(uid)
    today_str       = _et_now().strftime("%Y-%m-%d")
    daily_session   = get_daily_session(today_str, uid)
    today_entries   = get_journal_entries_for_date(today_str, uid)

    # Auto-lock check: fires when a new journal entry pushes over limits
    _lock_update = check_auto_lock(today_entries, risk_settings, daily_session)
    if _lock_update:
        lock_daily_session(_lock_update["lock_reason"], today_str, uid)
        daily_session = get_daily_session(today_str, uid)

    discipline      = compute_discipline_score(today_entries, risk_settings,
                                               bool(daily_session.get("locked")))
    daily_banner    = compute_daily_banner(no_trade, daily_session)
    trades_today    = len(today_entries)
    losses_today    = sum(1 for e in today_entries if e.get("result") == "Loss")

    market_temp = _get_market_temperature()

    # Market regime + sector strength from market_engine (cached, 60 min TTL)
    mkt_context = _get_mkt_ctx()

    # Display sort — grade first (A+ → … → ungraded), then swing score desc.
    # Presentation only; does not alter rank_stocks / scoring.
    def _grade_sort_key(s):
        return (_ugrade_info(s.get("swing_grade"))[1], s.get("swing_score") or 0)
    ranked = sorted(ranked, key=_grade_sort_key, reverse=True)
    top5   = sorted(top5,   key=_grade_sort_key, reverse=True)

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
        scanner_buckets=scanner_buckets,
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
        mkt_context=mkt_context,
    )


def _onboard_ticker_bg(ticker: str, user_id: int = 1) -> None:
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
        if live and price > 0:
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
            run_auto_classification(ticker, user_id)
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
    """Add one or more tickers to an owned list (active list by default)."""
    uid = current_user_id()
    owned_wls = {w["id"]: w for w in get_all_watchlists(uid)}
    requested_id = request.form.get("watchlist_id", "")
    wl_id = int(requested_id) if requested_id.isdigit() and int(requested_id) in owned_wls else get_active_wl_id()
    raw    = request.form.get("tickers", "")
    queued = []
    failed = []
    already = []
    existing = set(get_watchlist_stocks(wl_id)) if wl_id else set()
    for t in parse_watchlist_symbols(raw):
        if not wl_id:
            failed.append(t)
            continue
        if t in existing:
            already.append(t)
            continue
        # Claim the watchlist slot + insert a Loading placeholder so the
        # ticker appears on the dashboard immediately.
        add_ticker_to_watchlist(wl_id, t)
        if t not in set(get_watchlist_stocks(wl_id)):
            failed.append(t)
            continue
        upsert_loading_placeholder(t)
        logger.info("watchlist_add  ticker=%s  wl_id=%s  stage=placeholder_queued", t, wl_id)
        # Fire the two-stage onboarding pipeline in a background thread so the
        # HTTP response returns immediately and the UI shows the Loading badge.
        threading.Thread(
            target=_onboard_ticker_bg,
            args=(t, uid),
            daemon=True,
            name=f"onboard-{t}",
        ).start()
        queued.append(t)

    if wl_id:
        session["active_wl_id"] = wl_id

    if queued:
        flash(
            f"Adding {', '.join(queued)}… "
            "The row shows Loading → Partial → Ready as data arrives. "
            "The page auto-refreshes when it's ready.",
            "success",
        )
        logger.info("watchlist_add  queued=%s  wl_id=%s", queued, wl_id)
    if already:
        flash(f"Already in {owned_wls.get(wl_id, {}).get('name', 'this list')}: {', '.join(already)}.", "info")
    if failed:
        flash(f"Could not add {', '.join(failed)}. Please try again.", "error")
    if not queued and not already and not failed:
        flash("No valid tickers found. Use symbols such as NVDA, BRK.B, or BF-B.", "error")
    return _wl_next()


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
        remove_ticker_from_defaults(t, current_user_id())
        remaining = get_watchlist_stocks(wl_id)
        logger.info("WATCHLIST SAVED  wl_id=%s contents=%s", wl_id, remaining)
    flash(f"Removed {t} from watchlist.", "info")
    return _wl_next()


def _refresh_all_worker(watchlist: list, user_id: int = 1) -> None:
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
                    run_auto_classification(ticker, user_id)
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
        t = threading.Thread(
            target=_refresh_all_worker,
            args=(watchlist, current_user_id()),
            daemon=True,
        )
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

    # The stock snapshot's legacy Yahoo calendar field is frequently blank on
    # cloud hosts. Reuse the multi-source Intel calendar so the profile and the
    # dedicated Calendar screen present one consistent next earnings date.
    if not stock.get("earnings_date"):
        try:
            _earnings = (_intel.get_intel_summary() or {}).get("earnings") or {}
            _matches = [
                item
                for bucket in ("today", "tomorrow", "this_week", "coming_up")
                for item in (_earnings.get(bucket) or [])
                if str(item.get("ticker") or "").upper() == ticker
            ]
            if _matches:
                _next = min(_matches, key=lambda item: item.get("date") or "9999-12-31")
                stock["earnings_date"] = _next.get("date")
                stock["earnings_time"] = _next.get("time_label") or "TBD"
                stock["earnings_source"] = _next.get("source") or "intel_calendar"
        except Exception as _earnings_err:
            logger.debug("stock_detail  ticker=%s  earnings_enrich=failed  err=%s", ticker, _earnings_err)

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

    uid  = current_user_id()
    note = get_note(ticker, uid)

    try:
        breakdown = catalyst_score_breakdown(stock) or []
    except Exception as exc:
        logger.error("stock_detail  ticker=%s  stage=breakdown  err=%s", ticker, exc)
        breakdown = []

    plan = get_trade_plan(ticker, uid)

    try:
        rr_ratio, rr_display, rr_class = compute_rr(
            plan.get("plan_bias"),
            plan.get("entry_level"),
            plan.get("stop_loss"),
            plan.get("target_price"),
        )
        stock.setdefault("risk_reward", rr_ratio)
    except Exception:
        rr_display, rr_class = "—", "rr-neutral"

    all_wls       = get_all_watchlists(uid)
    ticker_wl_ids = get_ticker_watchlist_ids(ticker, uid)
    rs            = get_risk_settings(uid)
    market_temp   = _get_market_temperature()

    try:
        coach = compute_trade_coach(stock, plan, market_temp, rs)
    except Exception as _ce:
        logger.warning("stock_detail  ticker=%s  coach_err=%s", ticker, _ce)
        coach = {
            "message": "Coach unavailable — could not evaluate signals.",
            "level": "caution", "css": "coach-caution",
            "reduce_size": False, "signals": [],
        }

    logger.info("stock_detail  ticker=%s  state=%s  coach=%s", ticker, stock.get("ticker_state"), coach.get("level"))

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
        risk_settings=rs,
        market_temp=market_temp,
        coach=coach,
    )


@app.route("/api/stock/<ticker>/chart")
def stock_chart(ticker):
    """Return a compact daily price series for the mobile stock profile."""
    ticker = ticker.upper().strip()
    if not ticker or len(ticker) > 10 or not all(c.isalnum() or c in ".-" for c in ticker):
        return jsonify({"ok": False, "error": "Invalid ticker"}), 400
    try:
        from data_fetcher import _fetch_ohlcv_via_chart_api
        bars = _fetch_ohlcv_via_chart_api(ticker, interval="1d", range_str="3mo")
        if not bars or not bars.get("closes"):
            return jsonify({"ok": False, "error": "Chart data unavailable"}), 503
        points = [
            {"t": int(ts), "c": round(float(close), 2)}
            for ts, close in zip(bars.get("timestamps", []), bars["closes"])
        ][-65:]
        return jsonify({"ok": True, "ticker": ticker, "points": points})
    except Exception as exc:
        logger.warning("stock_chart ticker=%s err=%s", ticker, exc)
        return jsonify({"ok": False, "error": "Chart data unavailable"}), 503


# ── Market Temperature cache ─────────────────────────────────────────────────
_market_temp_cache: dict = {"data": None, "ts": 0.0}
_market_temp_lock  = threading.Lock()   # guards the spawn-once check
_MARKET_TEMP_TTL   = 90    # 90-second refresh (was 5 min)


def _get_market_temperature() -> dict:
    """Return cached market regime; trigger background refresh when stale."""
    _LOADING: dict = {
        "regime": "LOADING", "label": "Loading…", "css": "mt-loading",
        "reason": "Fetching market data…",
        "longs_ok": None, "shorts_ok": None,
        "reduce_size": False, "score": None, "meter_score": 50, "error": False,
        "spy_price": None, "spy_pct_ema20": None, "spy_vs_vwap": None,
        "qqq_price": None, "qqq_pct_ema20": None, "qqq_vs_vwap": None,
        "vix_level": None, "vix_direction": None,
        "es_price": None, "es_change_pct": None, "es_above_vwap": None,
        "sectors": {}, "mode_desc": "—",
        "action_msg": "—", "decision_cmd": "Loading…", "risk_pct_rec": None,
        "size_multiplier": None, "size_zone": "unknown", "why": "Fetching market data…",
    }
    now = _time.time()
    if _market_temp_cache["ts"] and now - _market_temp_cache["ts"] < _MARKET_TEMP_TTL:
        return _market_temp_cache["data"]

    # Atomic check-and-spawn: acquire the lock before reading/writing `fetching`
    # so two simultaneous requests cannot both see fetching=False and both spawn
    # a background thread (which would double-fetch and double-write the cache).
    with _market_temp_lock:
        # Re-check inside the lock — another thread may have just refreshed.
        if _market_temp_cache["ts"] and now - _market_temp_cache["ts"] < _MARKET_TEMP_TTL:
            return _market_temp_cache["data"]
        if _market_temp_cache.get("fetching"):
            return _market_temp_cache["data"] or _LOADING
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


# ── Market Context cache (ES futures + sectors, 30 s TTL) ────────────────────
_market_ctx_cache: dict = {"data": None, "ts": 0.0}
_market_ctx_lock  = threading.Lock()
_MARKET_CTX_TTL   = 30   # 30-second refresh for live ES + sector display


def _get_market_context() -> dict:
    """Return cached ES + sector context; refresh in background when stale."""
    _EMPTY = {
        "es": {"price": None, "change_pct": None, "above_vwap": None, "error": True},
        "sectors": {},
        "after_hours": False,
    }
    now = _time.time()
    if _market_ctx_cache["ts"] and now - _market_ctx_cache["ts"] < _MARKET_CTX_TTL:
        return _market_ctx_cache["data"]

    with _market_ctx_lock:
        if _market_ctx_cache["ts"] and now - _market_ctx_cache["ts"] < _MARKET_CTX_TTL:
            return _market_ctx_cache["data"]
        if _market_ctx_cache.get("fetching"):
            return _market_ctx_cache["data"] or _EMPTY
        _market_ctx_cache["fetching"] = True

    def _bg():
        try:
            from data_fetcher import fetch_market_context
            data = fetch_market_context()
            _market_ctx_cache["data"] = data
            _market_ctx_cache["ts"]   = _time.time()
        except Exception as _e:
            logger.warning("_get_market_context bg failed: %s", _e)
        finally:
            _market_ctx_cache["fetching"] = False

    threading.Thread(target=_bg, daemon=True).start()
    return _market_ctx_cache["data"] or _EMPTY


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
            "error": "options data unavailable", "calls": [], "puts": [],
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
        user_id     = current_user_id(),
    )
    flash("Pre-market plan saved.", "success")
    return redirect(url_for("stock_detail", ticker=t) + "#plan")


@app.route("/stock/<ticker>/notes", methods=["POST"])
def save_stock_note(ticker):
    """Save trade plan notes for a stock."""
    save_note(ticker.upper(), request.form.get("note_text", ""), current_user_id())
    flash("Notes saved.", "success")
    return redirect(url_for("stock_detail", ticker=ticker.upper()))


@app.route("/api/fib-override/<ticker>", methods=["POST"])
def fib_override(ticker):
    """
    Apply manual Fibonacci anchors for a ticker.
    Accepts: fib_manual_high, fib_manual_low (float, POST form).
    Recomputes all fib levels from the given anchors and saves to DB.
    """
    t = ticker.upper()
    try:
        hi = float(request.form.get("fib_manual_high") or 0)
        lo = float(request.form.get("fib_manual_low")  or 0)
    except (TypeError, ValueError):
        flash("Invalid fib values — enter numeric prices.", "error")
        return redirect(url_for("stock_detail", ticker=t))

    if hi <= lo or hi <= 0 or lo <= 0:
        flash("Swing high must be greater than swing low.", "error")
        return redirect(url_for("stock_detail", ticker=t))

    rng = hi - lo
    from database import get_db
    update_data = {
        "ticker":        t,
        "fib_high":      round(hi, 2),
        "fib_low":       round(lo, 2),
        "fib_236":       round(hi - 0.236 * rng, 2),
        "fib_382":       round(hi - 0.382 * rng, 2),
        "fib_50":        round(hi - 0.500 * rng, 2),
        "fib_618":       round(hi - 0.618 * rng, 2),
        "fib_65":        round(hi - 0.650 * rng, 2),
        "fib_786":       round(hi - 0.786 * rng, 2),
        "fib_mode":      "manual",
        "fib_direction": "bullish",
        "fib_confidence": 10.0,
    }
    try:
        conn = get_db()
        conn.execute("""
            UPDATE stock_data SET
                fib_high      = :fib_high,
                fib_low       = :fib_low,
                fib_236       = :fib_236,
                fib_382       = :fib_382,
                fib_50        = :fib_50,
                fib_618       = :fib_618,
                fib_65        = :fib_65,
                fib_786       = :fib_786,
                fib_mode      = :fib_mode,
                fib_direction = :fib_direction,
                fib_confidence = :fib_confidence
            WHERE ticker = :ticker
        """, update_data)
        conn.commit()
        conn.close()
        flash(f"Manual fib anchors saved for {t}: High ${hi:.2f} / Low ${lo:.2f}", "success")
    except Exception as exc:
        logger.error("fib_override: %s", exc)
        flash("Failed to save fib override.", "error")

    return redirect(url_for("stock_detail", ticker=t))


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
            run_auto_classification(t, current_user_id())
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
    all_types = set(SETUP_TYPES) | set(SWING_SETUP_TYPES)
    if chosen in all_types:
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
    uid = current_user_id()
    entries = get_all_journal_entries(uid)
    summary = compute_journal_summary(entries)
    edit_entry = None
    edit_id = request.args.get("edit")
    if edit_id:
        try:
            edit_entry = get_journal_entry(int(edit_id), uid)
        except (ValueError, TypeError):
            pass
    today_str     = _et_now().strftime("%Y-%m-%d")
    risk_settings = get_risk_settings(uid)
    return render_template(
        "journal.html",
        entries=entries,
        summary=summary,
        setup_types=SETUP_TYPES + [s for s in SWING_SETUP_TYPES if s not in SETUP_TYPES],
        edit_entry=edit_entry,
        today=today_str,
        risk_settings=risk_settings,
    )


class JournalFormError(ValueError):
    """A journal field the user typed cannot be read as a number."""


# People type prices the way they read them: $182.50, 1,240, 47.90 with a
# stray space. The form has no client-side validation, so all of that arrived
# at a bare float() and returned a 500 error page with the trade unsaved and
# the form cleared.
_MONEY_STRIP = str.maketrans("", "", "$, \t\u00a0")


def _parse_money(raw: str, label: str):
    """A price field as a number, or None when the field was left empty."""
    text = (raw or "").strip().translate(_MONEY_STRIP)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise JournalFormError(
            f"{label} must be a number — {raw.strip()!r} could not be read.")


def _parse_journal_form(form) -> dict:
    """Parse all journal form fields (shared by add and edit routes).

    Raises JournalFormError when a number field cannot be read, so the route
    can say which field and keep the trade unsaved rather than crashing.
    """
    direction   = form.get("direction", "Long")
    entry_price = _parse_money(form.get("entry_price", ""), "Entry price")
    exit_price  = _parse_money(form.get("exit_price", ""), "Exit price")
    pnl_pct, result = compute_pnl(direction, entry_price or 0, exit_price or 0)

    def _int(k, label):
        v = (form.get(k, "") or "").strip().translate(_MONEY_STRIP)
        if not v:
            return None
        try:
            return int(float(v))
        except ValueError:
            raise JournalFormError(
                f"{label} must be a whole number — {form.get(k).strip()!r} "
                f"could not be read.")

    def _float(k, label):
        return _parse_money(form.get(k, ""), label)

    is_aplus = form.get("is_aplus_setup") == "1"

    return dict(
        direction      = direction,
        entry_price    = entry_price if entry_price is not None else 0,
        exit_price     = exit_price  if exit_price  is not None else 0,
        shares         = _int("shares", "Shares"),
        setup_type     = form.get("setup_type", ""),
        momentum_score = _int("momentum_score", "Momentum score"),
        pnl_pct        = pnl_pct,
        result         = result,
        notes          = form.get("notes", ""),
        trade_mode     = form.get("trade_mode") or None,
        option_side    = form.get("option_side") or None,
        option_premium = _float("option_premium", "Option premium"),
        contracts      = _int("contracts", "Contracts"),
        stop_price     = _float("stop_price", "Stop price"),
        is_aplus_setup = is_aplus,
    )


@app.route("/journal/add", methods=["POST"])
def journal_add():
    """Add a new journal entry."""
    uid = current_user_id()
    try:
        f = _parse_journal_form(request.form)
    except JournalFormError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("journal"))
    add_journal_entry(
        ticker         = request.form.get("ticker", "").upper(),
        trade_date     = request.form.get("trade_date", _et_now().strftime("%Y-%m-%d")),
        user_id        = uid,
        **f,
    )
    pnl_pct = f["pnl_pct"]
    result  = f["result"]

    # Auto-lock check after adding a trade
    today_str     = _et_now().strftime("%Y-%m-%d")
    risk_settings = get_risk_settings(uid)
    today_entries = get_journal_entries_for_date(today_str, uid)
    daily_session = get_daily_session(today_str, uid)
    lock_update   = check_auto_lock(today_entries, risk_settings, daily_session)
    if lock_update:
        lock_daily_session(lock_update["lock_reason"], today_str, uid)
        flash(f"⚠ {lock_update['lock_reason']} — Trading locked for today.", "warning")

    flash(f"Trade logged — {result} ({'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%).", "success")
    return redirect(url_for("journal"))


@app.route("/journal/<int:entry_id>/edit", methods=["POST"])
def journal_edit(entry_id):
    """Update an existing journal entry."""
    try:
        f = _parse_journal_form(request.form)
    except JournalFormError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("journal"))
    update_journal_entry(
        entry_id   = entry_id,
        ticker     = request.form.get("ticker", "").upper(),
        trade_date = request.form.get("trade_date", ""),
        user_id    = current_user_id(),
        **f,
    )
    flash("Trade updated.", "success")
    return redirect(url_for("journal"))


@app.route("/journal/<int:entry_id>/delete", methods=["POST"])
def journal_delete(entry_id):
    """Delete a journal entry."""
    delete_journal_entry(entry_id, current_user_id())
    flash("Trade removed.", "info")
    return redirect(url_for("journal"))


# ---------------------------------------------------------------------------
# Risk Settings & Daily Session routes
# ---------------------------------------------------------------------------

@app.route("/risk", methods=["GET", "POST"])
def risk_settings():
    """Risk settings page — account size, risk %, trade limits, trading mode."""
    uid           = current_user_id()
    today_str     = _et_now().strftime("%Y-%m-%d")
    daily_session = get_daily_session(today_str, uid)
    today_entries = get_journal_entries_for_date(today_str, uid)
    trades_today  = len(today_entries)
    losses_today  = sum(1 for e in today_entries if e.get("result") == "Loss")

    if request.method == "POST":
        action = request.form.get("action", "save")

        if action == "unlock":
            unlock_daily_session(today_str, uid)
            flash("Trading unlocked for today.", "success")
            return redirect(url_for("risk_settings"))

        # Save risk settings — validate numerics before storing
        def _clamp_float(key, default, lo, hi):
            try:
                v = float(request.form.get(key, default))
                return str(max(lo, min(hi, v)))
            except (ValueError, TypeError):
                return str(default)

        def _clamp_int(key, default, lo, hi):
            try:
                v = int(float(request.form.get(key, default)))
                return str(max(lo, min(hi, v)))
            except (ValueError, TypeError):
                return str(default)

        uid = current_user_id()
        tm = request.form.get("trading_mode", "SWING TRADE")
        if tm not in ("DAY TRADE", "SWING TRADE"):
            tm = "SWING TRADE"
        set_user_setting(uid, "trading_mode",       tm)
        set_user_setting(uid, "account_size",       _clamp_float("account_size",       "10000", 0, 10_000_000))
        set_user_setting(uid, "risk_pct",           _clamp_float("risk_pct",           "1.0",   0.1, 10))
        set_user_setting(uid, "max_trades_per_day", _clamp_int(  "max_trades_per_day", "3",     1,   20))
        set_user_setting(uid, "max_daily_loss_pct", _clamp_float("max_daily_loss_pct", "3.0",   0.1, 20))
        set_user_setting(uid, "stop_after_2_losses",
                         "1" if request.form.get("stop_after_2_losses") else "0")
        flash("Risk settings saved.", "success")
        return redirect(url_for("risk_settings"))

    risk_s = get_risk_settings(uid)
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
        set_user_setting(current_user_id(), "trading_mode", mode)
        return jsonify({"ok": True, "mode": mode})
    return jsonify({"ok": False, "error": "invalid mode"}), 400


# ---------------------------------------------------------------------------
# Watchlist management routes
# ---------------------------------------------------------------------------

def _wl_next():
    """Redirect back to the submitting page via a safe relative `next` form
    field (e.g. the Watchlists settings view), defaulting to the Setups
    scanner. Backward-compatible: callers that don't send `next` get dashboard."""
    nxt = request.form.get("next", "")
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect(url_for("dashboard"))


def _owned_watchlist(wl_id: int):
    """Resolve a watchlist only when it belongs to the signed-in user."""
    watchlist = get_watchlist_by_id(wl_id)
    if watchlist and watchlist.get("user_id") == current_user_id():
        return watchlist
    return None


@app.route("/watchlists")
def watchlists_page():
    """Watchlists settings — create / rename / activate / delete lists and
    add / remove tickers. Reuses the existing watchlist store and the existing
    POST routes; no new data logic."""
    uid       = current_user_id()
    all_wls   = get_all_watchlists(uid)
    active_id = get_active_wl_id()
    wl_counts = {}
    for w in all_wls:
        try:
            wl_counts[w["id"]] = len(get_watchlist_stocks(w["id"]) or [])
        except Exception:
            wl_counts[w["id"]] = 0
    active_tickers = get_watchlist_stocks(active_id) if active_id else []
    active_wl      = get_watchlist_by_id(active_id) if active_id else None
    active_structure = get_watchlist_structure(active_id) if active_id else {"sections": [], "unsectioned": []}
    can_organize = bool(active_wl and active_wl.get("name") not in DEFAULT_WATCHLISTS)
    stock_data     = {row["ticker"]: row for row in get_all_stock_data()}
    price_alerts   = get_price_alerts(uid)
    for alert in price_alerts:
        quote = stock_data.get(alert["ticker"], {})
        current = quote.get("current_price")
        alert["current_price"] = current
        alert["triggered"] = bool(
            alert["enabled"] and current is not None and
            ((alert["direction"] == "above" and current >= alert["target_price"]) or
             (alert["direction"] == "below" and current <= alert["target_price"]))
        )
    return render_template(
        "watchlists.html",
        all_wls=all_wls, active_id=active_id, active_wl=active_wl,
        wl_counts=wl_counts, active_tickers=active_tickers,
        active_structure=active_structure, can_organize=can_organize,
        price_alerts=price_alerts, personal_watchlists=PERSONAL_WATCHLISTS,
        automatic_watchlists=DEFAULT_WATCHLISTS,
    )


@app.route("/watchlists/<int:wl_id>/sections/create", methods=["POST"])
def watchlist_section_create(wl_id):
    if not _owned_watchlist(wl_id):
        return ("Not found", 404)
    name = request.form.get("name", "").strip()[:40]
    if not name:
        flash("Enter a section name such as Foundation, Growth, or Safe Haven.", "error")
    else:
        try:
            create_watchlist_section(wl_id, name)
            flash(f"Section '{name}' created.", "success")
        except Exception:
            flash("That section name already exists in this watchlist.", "error")
    session["active_wl_id"] = wl_id
    return redirect(url_for("watchlists_page") + "#organize-watchlist")


@app.route("/watchlists/<int:wl_id>/sections/<int:section_id>/rename", methods=["POST"])
def watchlist_section_rename(wl_id, section_id):
    if not _owned_watchlist(wl_id):
        return ("Not found", 404)
    name = request.form.get("name", "").strip()[:40]
    if name:
        try:
            rename_watchlist_section(section_id, wl_id, name)
            flash(f"Section renamed to '{name}'.", "success")
        except Exception:
            flash("That section name is already used.", "error")
    session["active_wl_id"] = wl_id
    return redirect(url_for("watchlists_page") + "#organize-watchlist")


@app.route("/watchlists/<int:wl_id>/sections/<int:section_id>/delete", methods=["POST"])
def watchlist_section_delete(wl_id, section_id):
    if not _owned_watchlist(wl_id):
        return ("Not found", 404)
    delete_watchlist_section(section_id, wl_id)
    session["active_wl_id"] = wl_id
    flash("Section removed. Its tickers are now in Unsorted.", "info")
    return redirect(url_for("watchlists_page") + "#organize-watchlist")


@app.route("/watchlists/<int:wl_id>/ticker/<ticker>/section", methods=["POST"])
def watchlist_ticker_section(wl_id, ticker):
    if not _owned_watchlist(wl_id):
        return ("Not found", 404)
    section_id = request.form.get("section_id") or None
    if move_watchlist_ticker(wl_id, ticker, section_id):
        flash(f"Moved {ticker.upper()}.", "success")
    else:
        flash("That ticker or section could not be found.", "error")
    session["active_wl_id"] = wl_id
    return redirect(url_for("watchlists_page") + "#organize-watchlist")


@app.route("/watchlists/<int:wl_id>/reorder", methods=["POST"])
def watchlist_reorder(wl_id):
    if not _owned_watchlist(wl_id):
        return jsonify({"ok": False, "error": "watchlist not found"}), 404
    payload = request.get_json(silent=True) or {}
    groups = payload.get("groups")
    if not isinstance(groups, list) or len(groups) > 100:
        return jsonify({"ok": False, "error": "invalid order payload"}), 400
    try:
        save_watchlist_order(wl_id, groups)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid order payload"}), 400
    session["active_wl_id"] = wl_id
    return jsonify({"ok": True})


@app.route("/price-alerts/create", methods=["POST"])
def price_alert_create():
    ticker = request.form.get("ticker", "").strip().upper()
    direction = request.form.get("direction", "above").strip().lower()
    try:
        target = float(request.form.get("target_price", ""))
    except (TypeError, ValueError):
        target = 0
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker):
        flash("Enter a valid ticker symbol.", "error")
    elif direction not in ("above", "below") or target <= 0 or target > 10_000_000:
        flash("Enter a valid alert direction and target price.", "error")
    else:
        try:
            create_price_alert(current_user_id(), ticker, direction, round(target, 4))
            flash(f"Price alert created for {ticker}.", "success")
        except Exception:
            flash("That exact price alert already exists.", "error")
    return redirect(url_for("watchlists_page") + "#price-alerts")


@app.route("/price-alerts/<int:alert_id>/toggle", methods=["POST"])
def price_alert_toggle(alert_id):
    enabled = request.form.get("enabled") == "1"
    set_price_alert_enabled(alert_id, current_user_id(), enabled)
    flash("Price alert resumed." if enabled else "Price alert paused.", "success")
    return redirect(url_for("watchlists_page") + "#price-alerts")


@app.route("/price-alerts/<int:alert_id>/delete", methods=["POST"])
def price_alert_delete(alert_id):
    delete_price_alert(alert_id, current_user_id())
    flash("Price alert deleted.", "info")
    return redirect(url_for("watchlists_page") + "#price-alerts")


@app.route("/watchlists/activate/<int:wl_id>", methods=["POST"])
def watchlist_activate(wl_id):
    """Switch the active watchlist (stored in session)."""
    if not _owned_watchlist(wl_id):
        return ("Not found", 404)
    session["active_wl_id"] = wl_id
    return _wl_next()


@app.route("/watchlists/create", methods=["POST"])
def watchlist_create():
    """Create a new named watchlist."""
    name = request.form.get("name", "").strip()[:50]
    if name:
        try:
            new_id = create_watchlist(name, current_user_id())
            session["active_wl_id"] = new_id
            flash(f"Watchlist '{name}' created.", "success")
        except Exception:
            flash("A watchlist with that name already exists.", "error")
    else:
        flash("Please enter a watchlist name.", "error")
    return _wl_next()


@app.route("/watchlists/rename/<int:wl_id>", methods=["POST"])
def watchlist_rename(wl_id):
    """Rename an existing watchlist."""
    if not _owned_watchlist(wl_id):
        return ("Not found", 404)
    name = request.form.get("name", "").strip()[:50]
    if name:
        try:
            rename_watchlist(wl_id, name)
            flash(f"Watchlist renamed to '{name}'.", "success")
        except Exception:
            flash("That name is already taken.", "error")
    return _wl_next()


@app.route("/watchlists/delete/<int:wl_id>", methods=["POST"])
def watchlist_delete(wl_id):
    """Delete a watchlist. Refuses to delete the last one."""
    uid     = current_user_id()
    if not _owned_watchlist(wl_id):
        return ("Not found", 404)
    all_wls = get_all_watchlists(uid)
    if len(all_wls) <= 1:
        flash("Cannot delete the last watchlist.", "error")
        return _wl_next()
    delete_watchlist(wl_id)
    # If the deleted list was active, fall back to the first remaining list
    if session.get("active_wl_id") == wl_id:
        remaining = get_all_watchlists(uid)
        if remaining:
            session["active_wl_id"] = remaining[0]["id"]
    flash("Watchlist deleted.", "info")
    return _wl_next()


@app.route("/stock/<ticker>/watchlists", methods=["POST"])
def stock_set_watchlists(ticker):
    """Update which watchlists a stock belongs to (from the detail page)."""
    t = ticker.upper()
    raw_ids    = request.form.getlist("watchlist_ids")
    wl_ids     = [int(i) for i in raw_ids if i.isdigit()]
    set_ticker_watchlists(t, wl_ids, current_user_id())
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
        run_auto_classification(t, current_user_id())
        flash(f"Auto-classification ON for {t}. Stock moved to its recommended list.", "success")
    else:
        flash(f"Auto-classification OFF for {t}. You control the watchlist placement.", "info")
    return redirect(url_for("stock_detail", ticker=t))


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------

@app.route("/quick")
def quick_mode():
    """Legacy Execution page — merged into SETUPS (the swing scanner is the one
    primary setups view). Kept as a permanent redirect so old links still work.
    quick.html is retained in the repo but no longer served."""
    return redirect(url_for("dashboard"))


@app.route("/terminal")
def terminal():
    """Tradestaar Elite terminal — tape, watchlist, chart, ticket and positions.

    View/layout only. Reuses the exact same existing data sources as quick_mode
    (get_watchlist_stocks / get_all_stock_data / annotate / rank_stocks /
    _get_mkt_ctx / _get_schwab_data). No new API calls, no logic changes.
    """
    wl_id     = get_active_wl_id()
    active_wl = get_watchlist_by_id(wl_id) if wl_id else None
    watchlist = get_watchlist_stocks(wl_id) if wl_id else []

    # Seed a starter watchlist the first time the Terminal is opened empty.
    # Reuses the SAME onboarding path as /watchlist/add (Execution) — no new
    # write logic. Runs once (membership is added immediately, so it won't
    # re-seed on subsequent loads).
    if wl_id and not watchlist:
        for t in ("SPY", "QQQ", "NVDA", "TSLA", "AAPL", "AMD", "MSFT", "SMH"):
            try:
                add_ticker_to_watchlist(wl_id, t)
                upsert_loading_placeholder(t)
                threading.Thread(target=_onboard_ticker_bg, args=(t, current_user_id()),
                                 daemon=True, name=f"seed-{t}").start()
            except Exception as _se:
                logger.debug("terminal seed %s failed: %s", t, _se)
        watchlist = get_watchlist_stocks(wl_id)

    _trade_mode = get_setting("trading_mode") or "SWING TRADE"
    all_data = get_all_stock_data()
    data_map = {s["ticker"]: s for s in all_data}

    if watchlist:
        auto_refresh_stale_closes(
            watchlist, data_map=data_map, user_id=current_user_id()
        )
    stocks  = [annotate(data_map[t], trade_mode=_trade_mode) for t in watchlist if t in data_map]
    ranked  = rank_stocks(stocks)
    # The left rail follows the user's saved watchlist order. Ranking remains
    # available for the setups panel without silently rearranging the list.
    valid   = [s for s in stocks if s.get("trade_bias") != "Avoid"]
    valid_map = {s["ticker"]: s for s in valid}
    saved_structure = get_watchlist_structure(wl_id) if wl_id else {"sections": [], "unsectioned": []}
    terminal_groups = []
    grouped_tickers = set()
    for section in saved_structure["sections"]:
        group_stocks = [valid_map[t] for t in section["tickers"] if t in valid_map]
        grouped_tickers.update(s["ticker"] for s in group_stocks)
        if group_stocks:
            terminal_groups.append({"name": section["name"], "stocks": group_stocks})
    ungrouped = [valid_map[t] for t in saved_structure["unsectioned"] if t in valid_map]
    grouped_tickers.update(s["ticker"] for s in ungrouped)
    ungrouped.extend(s for s in valid if s["ticker"] not in grouped_tickers)
    if ungrouped:
        terminal_groups.append({"name": "Unsorted" if saved_structure["sections"] else "", "stocks": ungrouped})

    mkt_ctx = _get_mkt_ctx()

    # Win rate — from the same Journal store the Journal tab uses (None if no
    # logged trades → template shows a NOT WIRED badge).
    win_rate = None
    try:
        _uid = session.get("user_id") or 1
        win_rate = compute_journal_summary(get_all_journal_entries(_uid)).get("win_rate")
    except Exception as _we:
        logger.debug("terminal win_rate failed: %s", _we)

       # Schwab account snapshot (buying power, P&L, positions) — live when connected
    acct = None
    try:
        uid = current_user_id()
        tok = _schwab.token_status(uid)
        if tok.get("connected"):
            acct = _get_schwab_data(uid)
            if acct.get("error"):
                acct = None
    except Exception as _ae:
        logger.debug("terminal: schwab account fetch skipped: %s", _ae)
    # Today's Setups panel — top 3 by grade then swing score (display only)
    today_setups = sorted(
        ranked,
        key=lambda s: (_ugrade_info(s.get("swing_grade"))[1], s.get("swing_score") or 0),
        reverse=True,
    )[:3]

    # Mobile discovery rails.  The momentum scanner covers a curated liquid
    # universe while the activity list uses verified relative-volume values
    # already stored for the active watchlist.  Keep the two concepts separate:
    # a fast price move is not automatically high-volume activity.
    scanner_snapshot = _scanner.get_scan_results() or {}
    trending_stocks = (scanner_snapshot.get("opportunities") or [])[:6]
    most_active = sorted(
        [s for s in valid if s.get("rel_volume") is not None],
        key=lambda s: (s.get("rel_volume") or 0, s.get("today_volume") or 0),
        reverse=True,
    )[:6]

    return render_template(
        "terminal.html",
        stocks=valid,
        active_wl=active_wl,
        terminal_groups=terminal_groups,
        orb_session=get_orb_session_banner(),
        mkt=mkt_ctx,
        acct=acct,
        win_rate=win_rate,
        today_setups=today_setups,
        trending_stocks=trending_stocks,
        most_active=most_active,
        scanner_last_scan=scanner_snapshot.get("last_scan"),
    )


# Terminal chart candle series — reuses the SAME price feed the watchlist /
# execution engine uses. Candle interval and visible history range are separate
# controls, matching professional charting conventions. The legacy tf mapping
# remains supported for sparklines and older clients.
_TERMINAL_TF_MAP = {
    "1D":  ("5m",  "1d"),
    "1W":  ("30m", "5d"),
    "1M":  ("1d",  "1mo"),
    "3M":  ("1d",  "3mo"),
    "1Y":  ("1d",  "1y"),
    "ALL": ("1d", "10y"),
}
_TERMINAL_INTERVALS = {
    "1m": {"fetch": "1m", "ranges": ("1d", "5d"), "default": "1d"},
    "5m": {"fetch": "5m", "ranges": ("1d", "5d", "1mo"), "default": "5d"},
    "15m": {"fetch": "15m", "ranges": ("1d", "5d", "1mo"), "default": "5d"},
    "1h": {"fetch": "1h", "ranges": ("5d", "1mo", "3mo", "1y"), "default": "1mo"},
    "4h": {"fetch": "1h", "ranges": ("5d", "1mo", "3mo", "1y"), "default": "3mo"},
    "1d": {"fetch": "1d", "ranges": ("1mo", "3mo", "6mo", "1y", "2y", "5y", "10y"), "default": "1y"},
}
_TERMINAL_CANDLE_CACHE: dict = {}  # (ticker, interval, range) -> {"ts": epoch, "bars": [...]}


@app.route("/api/terminal/candles/<ticker>")
def api_terminal_candles(ticker):
    """Return OHLCV bars for the terminal chart from the existing data feed."""
    requested_interval = (request.args.get("interval") or "").lower()
    legacy_tf = (request.args.get("tf") or "").upper()
    session_mode = (request.args.get("session") or "regular").lower()
    if session_mode not in {"regular", "extended"}:
        return jsonify({"ok": False, "error": "unsupported market session"}), 400
    if requested_interval:
        config = _TERMINAL_INTERVALS.get(requested_interval)
        if not config:
            return jsonify({"ok": False, "error": "unsupported interval"}), 400
        range_str = (request.args.get("range") or config["default"]).lower()
        if range_str not in config["ranges"]:
            return jsonify({"ok": False, "error": "unsupported interval/range combination"}), 400
        interval = requested_interval
        fetch_interval = config["fetch"]
    else:
        legacy_tf = legacy_tf or "1D"
        if legacy_tf not in _TERMINAL_TF_MAP:
            return jsonify({"ok": False, "error": "unsupported timeframe"}), 400
        fetch_interval, range_str = _TERMINAL_TF_MAP[legacy_tf]
        interval = fetch_interval
    if session_mode == "extended" and interval not in {"1m", "5m", "15m", "1h"}:
        return jsonify({"ok": False,
                        "error": "extended hours require an intraday interval"}), 400
    ticker = (ticker or "").upper().strip()
    if not ticker or len(ticker) > 12:
        return jsonify({"ok": False, "error": "bad ticker"}), 400

    ttl = 15 if interval in {"1m", "5m", "15m"} else 60 if interval in {"1h", "4h"} else 300
    key = (ticker, interval, range_str, session_mode)
    cached = _TERMINAL_CANDLE_CACHE.get(key)
    if cached and (_time.time() - cached["ts"]) < ttl:
        market_data = dict(cached.get("market_data") or {})
        market_data["cache_age_seconds"] = max(0, int(_time.time() - cached["ts"]))
        return jsonify({"ok": True, "ticker": ticker, "tf": legacy_tf or None,
                        "interval": interval,
                        "source_interval": cached.get("source_interval", fetch_interval),
                        "session": session_mode, "adjustment": "split-adjusted",
                        "range": range_str,
                        "bars": cached["bars"], "cached": True,
                        "market_data": market_data,
                        "extended_summary": cached.get("extended_summary"),
                        "overnight_status": cached.get("overnight_status"),
                        "event_endpoint": f"/api/terminal/intelligence/{ticker}"})

    data = None
    market_data = {}
    try:
        from market_data import fetch_chart_bars
        data, market_data = fetch_chart_bars(
            ticker,
            interval=fetch_interval,
            range_str=range_str,
            include_extended=session_mode == "extended",
        )
    except Exception as _e:
        logger.debug("terminal candles %s %s/%s failed: %s", ticker, interval, range_str, _e)

    from terminal_intelligence import (
        annotate_market_sessions,
        normalize_ohlcv_data,
        summarize_extended_sessions,
    )
    bars = normalize_ohlcv_data(data)
    extended_summary = None
    overnight_status = None
    if session_mode == "extended":
        from overnight_data import fetch_overnight_bars, merge_session_bars
        overnight_result = fetch_overnight_bars(ticker, interval, range_str)
        bars = merge_session_bars(bars, overnight_result["bars"])
        overnight_status = overnight_result["status"]
        bars = annotate_market_sessions(
            bars, (data or {}).get("exchange_timezone") or "America/New_York"
        )
        extended_summary = summarize_extended_sessions(bars)

    source_interval = (data or {}).get("data_granularity") or fetch_interval
    # Do not label silently downsampled monthly or quarterly data as daily.
    if requested_interval and interval != "4h" and source_interval != fetch_interval:
        logger.warning(
            "terminal candles %s requested %s but provider returned %s",
            ticker, fetch_interval, source_interval,
        )
        return jsonify({"ok": False,
                        "error": "provider returned a different candle interval",
                        "requested_interval": interval,
                        "source_interval": source_interval}), 502

    if interval == "4h" and bars:
        from terminal_intelligence import aggregate_ohlcv_bars
        bars = aggregate_ohlcv_bars(bars, 4)

    _TERMINAL_CANDLE_CACHE[key] = {
        "ts": _time.time(), "bars": bars, "source_interval": source_interval,
        "market_data": market_data,
        "extended_summary": extended_summary,
        "overnight_status": overnight_status,
    }
    return jsonify({"ok": True, "ticker": ticker, "tf": legacy_tf or None,
                    "interval": interval, "source_interval": source_interval,
                    "session": session_mode, "adjustment": "split-adjusted",
                    "range": range_str, "bars": bars,
                    "market_data": {**market_data, "cache_age_seconds": 0},
                    "extended_summary": extended_summary,
                    "overnight_status": overnight_status,
                    "event_endpoint": f"/api/terminal/intelligence/{ticker}"})


@app.route("/api/terminal/intelligence/<ticker>")
def api_terminal_intelligence(ticker):
    """Return fast, ticker-scoped context from data already owned by the app.

    SEC filings intentionally live behind a second endpoint so a slow public
    filing service can never delay price charts, news, earnings, or Schwab UI.
    """
    ticker = (ticker or "").upper().strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,11}", ticker):
        return jsonify({"ok": False, "error": "bad ticker"}), 400
    try:
        stock = get_stock_data(ticker) or {}
    except Exception as exc:
        logger.debug("terminal intelligence stock %s unavailable: %s", ticker, exc)
        stock = {}
    try:
        summary = _intel.get_intel_summary() or {}
    except Exception as exc:
        logger.debug("terminal intelligence intel %s unavailable: %s", ticker, exc)
        summary = {}

    from terminal_intelligence import build_terminal_intelligence
    payload = build_terminal_intelligence(
        ticker,
        stock,
        summary,
        ai_configured=bool(os.environ.get("NEBIUS_API_KEY")),
    )
    payload["links"] = {
        "stock": url_for("stock_detail", ticker=ticker),
        "fundamentals": url_for("fundamentals_page", ticker=ticker),
        "ai": url_for("tradestaar_ai", ticker=ticker),
        "smart_money": url_for("smart_money"),
    }
    return jsonify(payload)


@app.route("/api/terminal/insiders/<ticker>")
def api_terminal_insiders(ticker):
    """Return verified corporate-insider transactions from SEC Form 4 filings."""
    ticker = (ticker or "").upper().strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,11}", ticker):
        return jsonify({"ok": False, "error": "bad ticker"}), 400
    from smart_money import fetch_sec_form4
    from terminal_intelligence import build_insider_payload
    try:
        rows, status = fetch_sec_form4([ticker], limit=20)
    except Exception as exc:
        logger.warning("terminal insiders %s unavailable: %s", ticker, exc)
        rows, status = [], {"available": False, "message": "The SEC filing service is temporarily unavailable."}
    return jsonify(build_insider_payload(ticker, rows, status))


@app.route("/api/quick")
def api_quick():
    """JSON for Execution Command Center live refresh — all ranked stocks + market context."""
    try:
        return jsonify(_build_quick_payload(get_active_wl_id()))
    except Exception as exc:
        logger.error("api_quick failed: %s", exc, exc_info=True)
        return jsonify({"error": "quick refresh failed"}), 500


# ---------------------------------------------------------------------------
# AI Morning Briefing — powered by Nebius (Llama-3.3-70B)
# ---------------------------------------------------------------------------

_NEBIUS_SYSTEM_PROMPT = """You are an elite institutional trading assistant providing a pre-market morning briefing.
Analyze the provided market data and return ONLY a valid JSON object with this exact schema:

{
  "macro_bias": "risk_on" | "risk_off" | "neutral",
  "vix_level": "<one short sentence describing VIX level and what it means for volatility today>",
  "briefing": "<2-4 sentence pre-market briefing: regime, key macro drivers, sector rotation, actionable context for the trading day>",
  "tickers_flagged": ["TICK1", "TICK2"]
}

Rules:
- macro_bias must be exactly one of: risk_on, risk_off, neutral
- vix_level: one sentence max, e.g. "VIX at 18 — low fear, options cheap, momentum trades favored"
- briefing: 2-4 sentences, concise and institutional. Cover: (1) overall regime, (2) key macro headwinds/tailwinds (DXY, yields, futures), (3) which sectors are leading/lagging, (4) what it means for your watchlist
- tickers_flagged: list only tickers from the provided watchlist that have a notable setup, catalyst, or risk today. Empty array [] if none stand out.
- Return ONLY the JSON object. No markdown, no explanation, no preamble."""


def _build_briefing_market_text() -> str:
    """
    Assemble a text snapshot of current market conditions to send to Nebius.
    Pulls from the same caches used by /api/market_context and the dashboard.
    Always includes VIX, SPY, QQQ, and DXY — falls back to data_fetcher when
    market_engine cache is cold.
    """
    lines = []

    # Collect live values from both sources so we can fill gaps
    vix_val   = None
    spy_price = None
    qqq_price = None
    dxy_val   = None
    dxy_chg   = None

    # ── Market regime (SPY/QQQ/VIX) — primary source: market_engine ────────
    try:
        mkt = _get_mkt_ctx()
        lines.append(f"MARKET REGIME: {mkt.get('regime', 'NEUTRAL')}")
        lines.append(f"SPY trend: {mkt.get('spy_trend', 'Unknown')}, 1d change: {mkt.get('spy_1d_pct', 'N/A')}%")
        lines.append(f"QQQ trend: {mkt.get('qqq_trend', 'Unknown')}, 1d change: {mkt.get('qqq_1d_pct', 'N/A')}%")
        vix_val   = mkt.get("vix_level")
        spy_price = mkt.get("spy_price")
        qqq_price = mkt.get("qqq_price")
        longs_ok  = mkt.get("longs_ok", True)
        shorts_ok = mkt.get("shorts_ok", True)
        lines.append(f"Trading signal: longs_ok={longs_ok}, shorts_ok={shorts_ok}, no_trade={mkt.get('no_trade', False)}")
        leading = mkt.get("leading_sectors") or []
        weak    = mkt.get("weak_sectors") or []
        if leading:
            lines.append(f"Leading sectors: {', '.join(leading[:4])}")
        if weak:
            lines.append(f"Weak sectors: {', '.join(weak[:4])}")
    except Exception as _e:
        logger.debug("briefing: mkt_ctx failed: %s", _e)

    # ── ES futures + macro (DXY, 10Y, sector ETFs) ───────────────────────────
    try:
        ctx = _get_market_context()
        es = ctx.get("es", {})
        if es and not es.get("error"):
            vwap_note = "above VWAP" if es.get("above_vwap") else "below VWAP" if es.get("above_vwap") is False else ""
            lines.append(f"ES futures: ${es.get('price', 'N/A')} ({es.get('change_pct', 'N/A'):+.2f}%) {vwap_note}".strip())

        # DXY — data_fetcher uses dxy_price / dxy_change_pct; market_engine uses dxy / dxy_1d_chg
        dxy_val = ctx.get("dxy_price") or ctx.get("dxy")
        dxy_chg = ctx.get("dxy_change_pct") or ctx.get("dxy_1d_chg")

        # Fill VIX/SPY/QQQ from data_fetcher if market_engine cache was cold
        if vix_val is None:
            vix_val = ctx.get("vix_level")
        if spy_price is None:
            spy_price = ctx.get("spy_price")
        if qqq_price is None:
            qqq_price = ctx.get("qqq_price")

        yield_10y = ctx.get("yield_10y")
        yield_chg = ctx.get("yield_change_bps")
        if yield_10y:
            lines.append(f"10Y Treasury yield: {yield_10y}% ({yield_chg:+.1f}bps), {ctx.get('yield_trend', 'flat')}")
            lines.append(f"Yield note: {ctx.get('yield_note', '')}")
        sectors = ctx.get("sectors") or {}
        if sectors:
            top3    = sorted(((k, v) for k, v in sectors.items() if v is not None), key=lambda x: x[1], reverse=True)[:3]
            bottom3 = sorted(((k, v) for k, v in sectors.items() if v is not None), key=lambda x: x[1])[:3]
            if top3:
                lines.append("Top sectors today: " + ", ".join(f"{k} {v:+.2f}%" for k, v in top3))
            if bottom3:
                lines.append("Weak sectors today: " + ", ".join(f"{k} {v:+.2f}%" for k, v in bottom3))
    except Exception as _e:
        logger.debug("briefing: market_context failed: %s", _e)

    # ── Always emit VIX / SPY / QQQ / DXY so Nebius is never told "not provided" ──
    lines.append(f"VIX: {vix_val if vix_val is not None else 'N/A'}")
    lines.append(f"SPY price: ${spy_price if spy_price is not None else 'N/A'}")
    lines.append(f"QQQ price: ${qqq_price if qqq_price is not None else 'N/A'}")
    if dxy_val is not None:
        try:
            lines.append(f"DXY: {dxy_val} ({float(dxy_chg):+.2f}%)" if dxy_chg is not None else f"DXY: {dxy_val}")
        except Exception:
            lines.append(f"DXY: {dxy_val}")

    # ── Watchlist stocks from DB ─────────────────────────────────────────────
    try:
        wl_id = get_active_wl_id()
        if wl_id:
            stocks = get_all_stock_data(wl_id)
            if stocks:
                lines.append(f"\nWATCHLIST ({len(stocks)} tickers):")
                for s in stocks[:20]:   # cap at 20 to stay within token budget
                    ticker  = s.get("ticker", "?")
                    price   = s.get("current_price")
                    chg     = s.get("day_change_pct")
                    score   = s.get("swing_score")
                    status  = s.get("swing_status") or ""
                    bias    = s.get("trade_bias") or ""
                    grade   = s.get("swing_grade") or ""
                    cat     = s.get("catalyst_score")
                    bucket  = s.get("auto_classify") or ""
                    price_s = f"${price:.2f}" if price else "N/A"
                    chg_s   = f"{chg:+.1f}%" if chg is not None else ""
                    lines.append(
                        f"  {ticker}: {price_s} {chg_s} | "
                        f"grade={grade} score={score}/10 | "
                        f"status={status} | bias={bias} | "
                        f"catalyst={cat}/10 | bucket={bucket}"
                    )
    except Exception as _e:
        logger.debug("briefing: watchlist fetch failed: %s", _e)

    return "\n".join(lines)


def _generate_nebius_briefing(market_data_text: str) -> dict:
    """
    Call Nebius (Llama-3.3-70B) with market data and return parsed JSON.
    Raises on any error so the caller can fall back to cache.
    """
    import json as _j
    from openai import OpenAI
    client = OpenAI(
        base_url="https://api.tokenfactory.nebius.com/v1/",
        api_key=os.environ.get("NEBIUS_API_KEY"),
    )
    response = client.chat.completions.create(
        model="meta-llama/Llama-3.3-70B-Instruct",
        max_tokens=512,
        temperature=0.3,
        top_p=0.9,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _NEBIUS_SYSTEM_PROMPT},
            {"role": "user",   "content": market_data_text},
        ],
    )
    return _j.loads(response.choices[0].message.content)


@app.route("/api/ai_briefing")
def api_ai_briefing():
    """
    Return today's AI morning briefing (macro_bias, vix_level, briefing, tickers_flagged).

    Caching: one call to Nebius per calendar day (ET). Returns the cached row
    immediately on subsequent requests. Pass ?refresh=true to force a fresh call.
    """
    force_refresh = request.args.get("refresh", "").lower() == "true"
    today_et = _et_now().strftime("%Y-%m-%d")
    _last_err = None

    if not force_refresh:
        cached = get_ai_briefing(today_et)
        if cached:
            cached["cached"] = True
            cached["date"]   = today_et
            return jsonify({"ok": True, "briefing": cached})

    # Build market data snapshot and call Nebius
    try:
        market_text = _build_briefing_market_text()
        result      = _generate_nebius_briefing(market_text)
        # Validate required fields; fill defaults if LLM omits them
        result.setdefault("macro_bias",      "neutral")
        result.setdefault("vix_level",       "VIX data unavailable")
        result.setdefault("briefing",        "Briefing unavailable — market data is loading.")
        result.setdefault("tickers_flagged", [])
        result["cached"] = False
        result["date"]   = today_et
        save_ai_briefing(today_et, result)
        return jsonify({"ok": True, "briefing": result})
    except Exception as exc:
        logger.error("api_ai_briefing Nebius call failed: %s", exc, exc_info=True)
        _last_err = str(exc)

    # Fall back to today's cached briefing (if any) rather than returning an error
    fallback = get_ai_briefing(today_et)
    if fallback:
        fallback["cached"] = True
        fallback["date"]   = today_et
        fallback["error"]  = _last_err
        return jsonify({"ok": True, "briefing": fallback})

    return jsonify({
        "ok": False,
        "error": _last_err or "Briefing unavailable",
        "briefing": {
            "macro_bias": "neutral",
            "vix_level":  "Data unavailable",
            "briefing":   f"Nebius error: {_last_err or 'unknown — check NEBIUS_API_KEY on Render'}",
            "tickers_flagged": [],
            "cached": False,
            "date": today_et,
        }
    }), 503


@app.route("/api/narrate_score")
def api_narrate_score():
    """
    Return a 2-3 sentence AI narration of why a ticker scored the way it did.
    Params: ticker, total, catalyst, setup, volume, macro_gate
    Cached per (ticker, today_ET, score_key) so Nebius is only called once per unique score.
    """
    import json as _j
    from openai import OpenAI

    ticker     = (request.args.get("ticker") or "").upper().strip()
    total      = request.args.get("total",     "N/A")
    catalyst   = request.args.get("catalyst",  "N/A")
    setup      = request.args.get("setup",     "N/A")
    volume     = request.args.get("volume",    "N/A")
    macro_gate = request.args.get("macro_gate","N/A")

    if not ticker:
        return jsonify({"ok": False, "error": "ticker required"}), 400

    today_et  = _et_now().strftime("%Y-%m-%d")
    score_key = f"{total}_C{catalyst}_S{setup}_V{volume}"

    cached = get_score_narration(ticker, today_et, score_key)
    if cached:
        return jsonify({"ok": True, "narration": cached, "cached": True})

    _NARRATE_SYSTEM = (
        'You are the score narrator for a swing trading engine. '
        'You receive a ticker and its scores. '
        'Explain in 2-3 sentences WHY it scored this way and what the trader should watch. '
        'Never predict price. Never change or recalculate the scores — narrate only. '
        'Respond in JSON: {"ticker": "", "narration": "", "watch_for": ""}'
    )
    user_msg = (
        f"Ticker: {ticker}\n"
        f"Total swing score: {total}/10\n"
        f"Catalyst score: {catalyst}/10\n"
        f"Setup score: {setup}/10\n"
        f"Volume score: {volume}/10\n"
        f"Macro gate: {macro_gate}"
    )
    try:
        client = OpenAI(
            base_url="https://api.tokenfactory.nebius.com/v1/",
            api_key=os.environ.get("NEBIUS_API_KEY"),
        )
        response = client.chat.completions.create(
            model="meta-llama/Llama-3.3-70B-Instruct",
            max_tokens=256,
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _NARRATE_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
        )
        result = _j.loads(response.choices[0].message.content)
        result.setdefault("ticker",    ticker)
        result.setdefault("narration", "")
        result.setdefault("watch_for", "")
        save_score_narration(ticker, today_et, score_key, result)
        return jsonify({"ok": True, "narration": result, "cached": False})
    except Exception as exc:
        logger.error("api_narrate_score failed: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/journal_summary")
def api_journal_summary():
    """
    Return an AI weekly trading journal summary.
    Optional param: week (e.g. '2026-W27'). Defaults to current ISO week.
    Cached per week_key.
    """
    import json as _j
    from openai import OpenAI

    week_param = (request.args.get("week") or "").strip()
    if week_param:
        week_key = week_param
    else:
        now_et   = _et_now()
        iso_cal  = now_et.isocalendar()
        week_key = f"{iso_cal[0]}-W{iso_cal[1]:02d}"

    # Per user: the cache is keyed on the week alone in the table, so the key
    # itself has to carry the identity or one account's summary is served to
    # every other account asking for the same week.
    cache_key = f"u{current_user_id()}:{week_key}"
    cached = get_journal_summary(cache_key)
    if cached:
        return jsonify({"ok": True, "summary": cached, "week_key": week_key, "cached": True})

    # Pull trades for the week from the journal table
    try:
        # Derive Monday–Sunday date range from week_key
        import datetime as _dt
        year, wnum = int(week_key.split("-W")[0]), int(week_key.split("-W")[1])
        monday  = _dt.date.fromisocalendar(year, wnum, 1)
        sunday  = monday + _dt.timedelta(days=6)
        from database import get_db as _get_db
        conn = _get_db()
        # Scoped to the caller. Every other journal query in the app filters on
        # user_id; this one did not, so the summary it returned described every
        # account's trades for the week — tickers, prices, P&L and notes.
        rows = conn.execute(
            "SELECT * FROM journal WHERE user_id = ? AND trade_date >= ? "
            "AND trade_date <= ? ORDER BY trade_date ASC",
            (current_user_id(), str(monday), str(sunday)),
        ).fetchall()
        conn.close()
        trades = [dict(r) for r in rows]
    except Exception as exc:
        logger.error("api_journal_summary: failed to pull trades: %s", exc)
        trades = []

    if not trades:
        fallback = {
            "week_summary":    "No trades logged for this week.",
            "rule_adherence":  "N/A",
            "top_mistake":     "N/A",
            "one_improvement": "Log your trades consistently to unlock weekly AI reviews.",
        }
        return jsonify({"ok": True, "summary": fallback, "week_key": week_key, "cached": False})

    lines = [f"Week: {week_key}  ({len(trades)} trades)"]
    for t in trades:
        pnl  = t.get("pnl_pct")
        pnl_s = f"{pnl:+.1f}%" if pnl is not None else "N/A"
        lines.append(
            f"  {t.get('trade_date')} {t.get('ticker')} {t.get('direction','')} "
            f"entry={t.get('entry_price')} exit={t.get('exit_price')} "
            f"pnl={pnl_s} result={t.get('result','')} "
            f"setup={t.get('setup_type','')} notes={t.get('notes','')}"
        )
    trades_text = "\n".join(lines)

    _JOURNAL_SYSTEM = (
        "You are a trading journal coach reviewing a swing trader's week. "
        "Summarize: rule adherence, repeated mistakes, what worked, one specific improvement for next week. "
        "Honest, direct, no cheerleading. "
        'Respond in JSON: {"week_summary": "", "rule_adherence": "", "top_mistake": "", "one_improvement": ""}'
    )
    try:
        client = OpenAI(
            base_url="https://api.tokenfactory.nebius.com/v1/",
            api_key=os.environ.get("NEBIUS_API_KEY"),
        )
        response = client.chat.completions.create(
            model="meta-llama/Llama-3.3-70B-Instruct",
            max_tokens=512,
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _JOURNAL_SYSTEM},
                {"role": "user",   "content": trades_text},
            ],
        )
        result = _j.loads(response.choices[0].message.content)
        result.setdefault("week_summary",    "")
        result.setdefault("rule_adherence",  "")
        result.setdefault("top_mistake",     "")
        result.setdefault("one_improvement", "")
        save_journal_summary(cache_key, result)
        return jsonify({"ok": True, "summary": result, "week_key": week_key, "cached": False})
    except Exception as exc:
        logger.error("api_journal_summary Nebius call failed: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/earnings_digest")
def api_earnings_digest():
    """
    Return an AI earnings digest for swing-trading watch tickers.
    Cached per today's ET date.
    """
    import json as _j
    from openai import OpenAI

    today_et = _et_now().strftime("%Y-%m-%d")
    cached   = get_earnings_digest(today_et)
    if cached:
        return jsonify({"ok": True, "digest": cached, "cached": True})

    WATCH_TICKERS = ["AMD", "ANET", "FN", "TSM", "ISRG", "EME", "GOOG"]

    # Pull earnings dates from stock_data table
    earnings_info = []
    try:
        from database import get_db as _get_db
        conn = _get_db()
        rows = conn.execute(
            f"SELECT ticker, earnings_date, catalyst_summary, news_headlines "
            f"FROM stock_data WHERE ticker IN ({','.join('?' * len(WATCH_TICKERS))})",
            WATCH_TICKERS,
        ).fetchall()
        conn.close()
        for r in rows:
            earnings_info.append(dict(r))
    except Exception as exc:
        logger.debug("api_earnings_digest: db lookup failed: %s", exc)

    # Also pull from intel engine's earnings calendar
    try:
        intel_data = _intel.get_intel_summary()
        all_earn   = (
            intel_data.get("earnings", {}).get("today", []) +
            intel_data.get("earnings", {}).get("tomorrow", []) +
            intel_data.get("earnings", {}).get("this_week", [])
        )
        watch_set = set(WATCH_TICKERS)
        intel_earn = [e for e in all_earn if e.get("ticker", "").upper() in watch_set]
    except Exception:
        intel_earn = []

    lines = [f"Swing trader earnings watch  Date: {today_et}"]
    lines.append(f"Tickers: {', '.join(WATCH_TICKERS)}")
    lines.append("")

    for t in WATCH_TICKERS:
        db_row = next((r for r in earnings_info if r.get("ticker") == t), {})
        earn_date = db_row.get("earnings_date") or "unknown"
        summary   = db_row.get("catalyst_summary") or ""
        # Check intel calendar for precise date/time
        ie = next((e for e in intel_earn if e.get("ticker", "").upper() == t), {})
        if ie:
            earn_date = ie.get("date") or ie.get("date_label") or earn_date
        lines.append(f"{t}: earnings {earn_date}  catalyst_summary={summary[:120] if summary else 'N/A'}")

    digest_text = "\n".join(lines)

    _EARNINGS_SYSTEM = (
        "Summarize what matters for a swing trader holding none of these but watching all: "
        "which earnings this week could move these tickers, expected dates, and any pre-announcement sector reads. "
        "4 sentences max. "
        'JSON: {"digest": "", "key_dates": []}'
    )
    try:
        client = OpenAI(
            base_url="https://api.tokenfactory.nebius.com/v1/",
            api_key=os.environ.get("NEBIUS_API_KEY"),
        )
        response = client.chat.completions.create(
            model="meta-llama/Llama-3.3-70B-Instruct",
            max_tokens=384,
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _EARNINGS_SYSTEM},
                {"role": "user",   "content": digest_text},
            ],
        )
        result = _j.loads(response.choices[0].message.content)
        result.setdefault("digest",    "")
        result.setdefault("key_dates", [])
        save_earnings_digest(today_et, result)
        return jsonify({"ok": True, "digest": result, "cached": False})
    except Exception as exc:
        logger.error("api_earnings_digest Nebius call failed: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/market-story")
def api_market_story():
    """AI market narrative for the Morning Command Center header."""
    try:
        mkt_ctx   = _get_mkt_ctx()
        liq_score = None
        try:
            import liquidity_engine as _liq
            liq_score = _liq.get_liquidity_status().get("score")
        except Exception:
            pass
        if _MKT_AVAILABLE:
            story = _mkt.generate_market_story(mkt_ctx, liq_score)
        else:
            story = {
                "headline": "Market data loading…",
                "body": "Fetching regime and sector data.",
                "sentiment": "neutral",
                "bullets": [],
                "permissions": [],
                "regime": "NEUTRAL",
            }
        return jsonify({"ok": True, "story": story, "mkt_ctx": mkt_ctx})
    except Exception as exc:
        logger.error("api_market_story: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# WebSocket endpoints — real-time push updates
# ---------------------------------------------------------------------------

@sock.route("/ws/dashboard")
def ws_dashboard(ws):
    """
    WebSocket endpoint for live dashboard updates.
    Sends an immediate snapshot on connect, then pushes every 3 s.
    Payload includes ranked stocks, market_temp, scanner, and alert_count
    so the client never needs separate HTTP polls for any of those feeds.
    """
    wl_id = get_active_wl_id()
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
            if _time.monotonic() - last_push >= 3.0:
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
    # Do NOT use 'with ThreadPoolExecutor' — its __exit__ calls shutdown(wait=True)
    # which blocks indefinitely when yfinance threads hang on cloud IPs.
    pool = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = {pool.submit(_refresh_one, t): t for t in tickers}
        try:
            for future in as_completed(futures, timeout=25):
                try:
                    ticker, updated = future.result()
                except Exception as exc:
                    logger.warning("batch_refresh future result failed: %s", exc)
                    continue
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
        except Exception:
            logger.warning("batch_refresh_exec_states: timed out — returning partial results")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

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
        # Canonical classification (classifier.classify) — keep these in
        # sync with annotate()'s output so every live-polled/WebSocket
        # consumer can group/filter by the same bucket/avoid flag the
        # server-rendered page used.
        "bucket", "avoid_blocked", "classify_reason",
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
        # Needed for live badge patching on the detail page
        "trade_permission",
        # Relative Strength (needed by execution page)
        "rs_score_display", "rs_label", "rs_class", "rs_vs_qqq_display",
        # VWAP
        "vwap",
        # Sector
        "sector_etf", "sector_name", "sector_leading", "sector_class",
    ]
    return {f: s.get(f) for f in fields}


# ---------------------------------------------------------------------------
# Non-blocking background exec-state refresh
# ---------------------------------------------------------------------------
# Keeps yfinance calls entirely off the gunicorn request thread.
# Endpoints call _trigger_exec_state_refresh() and return stale DB data
# immediately; this background thread updates the DB asynchronously.

_bg_refresh_state: dict = {"active": False, "last_triggered": 0.0}
_bg_refresh_lock = threading.Lock()


def _trigger_exec_state_refresh(wl_id: int | None) -> None:
    """Fire-and-forget background refresh. No-op if one is already running or
    was triggered within the last 45 seconds."""
    with _bg_refresh_lock:
        now = _time.monotonic()
        if _bg_refresh_state["active"] or (now - _bg_refresh_state["last_triggered"]) < 45.0:
            return
        _bg_refresh_state["active"] = True
        _bg_refresh_state["last_triggered"] = now

    def _run():
        try:
            watchlist = get_watchlist_stocks(wl_id) if wl_id else []
            all_data  = get_all_stock_data()
            data_map  = {s["ticker"]: s for s in all_data}
            tickers   = [t for t in watchlist if t in data_map]
            if tickers:
                batch_refresh_exec_states(tickers, data_map)
        except Exception as exc:
            logger.warning("background exec-state refresh failed: %s", exc)
        finally:
            with _bg_refresh_lock:
                _bg_refresh_state["active"] = False

    threading.Thread(target=_run, daemon=True, name="exec-state-refresh").start()


# ---------------------------------------------------------------------------
# Shared payload builders (used by both REST endpoints and WebSocket handlers)
# ---------------------------------------------------------------------------

def _build_dashboard_payload(wl_id: int | None) -> dict:
    """Compute and return the full dashboard data dict (no request context needed)."""
    watchlist   = get_watchlist_stocks(wl_id) if wl_id else []
    _trade_mode = get_setting("trading_mode") or "SWING TRADE"
    all_data    = get_all_stock_data()
    data_map    = {s["ticker"]: s for s in all_data}
    _trigger_exec_state_refresh(wl_id)   # non-blocking; returns stale data this call
    stocks      = [annotate(data_map[t], trade_mode=_trade_mode) for t in watchlist if t in data_map]
    ranked    = rank_stocks(stocks)
    # Same compute_top5() used by the server-rendered dashboard — this used
    # to be a second, independently-tuned copy of the top5 rule, which is
    # exactly how the live-polled payload could disagree with what the page
    # showed on initial load for the same ticker a few seconds apart.
    top5      = compute_top5(ranked)
    no_trade     = compute_no_trade_assessment(ranked, top5)
    # Use display_exec_state (session-aware) so stale TRIGGERED stocks are not
    # shown in the live-alerts section outside regular market hours.
    triggered    = [] if no_trade["lock_signals"] else [s for s in ranked if s.get("display_exec_state") == "TRIGGERED"]
    top5_tickers = {s["ticker"] for s in top5}
    secondary    = compute_secondary_watchlist(ranked, top5_tickers)
    return {
        "type":           "dashboard",
        "server_time":    _et_now().strftime("%I:%M %p").lstrip("0") + " ET",
        "orb_session":    get_orb_session_banner(),
        "no_trade":       no_trade,
        "triggered":      [_stock_summary(s) for s in triggered],
        "top5":           [_stock_summary(s) for s in top5],
        "secondary":      [_stock_summary(s) for s in secondary],
        "ranked":         [_stock_summary(s) for s in ranked],
        # Bundled real-time feeds — served from cache, zero extra latency per push
        "market_temp":    _get_market_temperature(),
        "market_context": _get_market_context(),
        "scanner":        _scanner.get_scan_results(),
        "alert_count":    get_alert_count(),
        "scan_alert_count": get_unseen_scanner_alert_count(),
    }


def _smart_alerts() -> list:
    """Return recent alerts enriched with CRITICAL/HIGH/MEDIUM/WATCHLIST priority."""
    raw = get_alerts(limit=25)
    out = []
    for a in raw:
        atype    = a.get("alert_type", "")
        severity = a.get("severity", "medium")
        msg      = a.get("message", "")
        msg_up   = msg.upper()

        # Classify priority
        if atype in ("ready",) or "TRIGGERED" in msg_up or "BREAKOUT" in msg_up:
            priority = "CRITICAL"
        elif atype in ("aplus",) or severity == "high" or "A+" in msg or "READY" in msg_up:
            priority = "HIGH"
        elif atype in ("pre_confirm", "continuation") or severity == "medium":
            priority = "MEDIUM"
        else:
            priority = "WATCHLIST"

        out.append({**a, "priority": priority})
    return out


def _build_quick_payload(wl_id: int | None) -> dict:
    """Compute and return the quick-mode data dict (no request context needed)."""
    watchlist   = get_watchlist_stocks(wl_id) if wl_id else []
    _trade_mode = get_setting("trading_mode") or "SWING TRADE"
    all_data    = get_all_stock_data()
    data_map    = {s["ticker"]: s for s in all_data}
    _trigger_exec_state_refresh(wl_id)   # non-blocking; returns stale data this call
    stocks  = [annotate(data_map[t], trade_mode=_trade_mode) for t in watchlist if t in data_map]
    ranked  = rank_stocks(stocks)
    valid   = [s for s in ranked if not s.get("avoid_blocked") and s.get("trade_bias") != "Avoid"]
    return {
        "type":           "quick",
        "server_time":    _et_now().strftime("%I:%M %p").lstrip("0") + " ET",
        "orb_session":    get_orb_session_banner(),
        "stocks":         [_stock_summary(s) for s in valid],
        "mkt_ctx":        _get_mkt_ctx(),
        "smart_alerts":   _smart_alerts(),
    }


@app.route("/api/dashboard")
def api_dashboard():
    """JSON endpoint for live dashboard updates (price, state, ORB, scores)."""
    try:
        return jsonify(_build_dashboard_payload(get_active_wl_id()))
    except Exception as exc:
        logger.error("api_dashboard failed: %s", exc, exc_info=True)
        return jsonify({"error": "dashboard refresh failed"}), 500


@app.route("/api/market_context")
def api_market_context():
    """ES futures + sector ETF data for live gauge updates. Cached 30 s."""
    try:
        return jsonify(_get_market_context())
    except Exception as exc:
        logger.error("api_market_context failed: %s", exc, exc_info=True)
        return jsonify({"error": "market context unavailable"}), 500


@app.route("/api/scanner")
def api_scanner():
    """
    Live momentum scanner results — polled every 20 s by the dashboard.
    Returns current opportunities, last scan time, and market-hours flag.
    """
    try:
        return jsonify(_scanner.get_scan_results())
    except Exception as exc:
        logger.error("api_scanner failed: %s", exc, exc_info=True)
        return jsonify({"error": "scanner unavailable"}), 500


@app.route("/api/scanner/add", methods=["POST"])
def api_scanner_add():
    """
    Add a scanner-detected ticker to the active watchlist via AJAX.
    Body: {"ticker": "NVDA"}
    """
    data   = request.get_json(silent=True) or {}
    ticker = (data.get("ticker") or "").strip().upper()
    if not ticker or not ticker.isalpha() or len(ticker) > 6:
        return jsonify({"error": "invalid ticker"}), 400

    wl_id = get_active_wl_id()
    if not wl_id:
        return jsonify({"error": "no active watchlist"}), 400

    try:
        add_ticker_to_watchlist(wl_id, ticker)
        # Seed a loading placeholder so the row appears immediately
        upsert_loading_placeholder(ticker)
        logger.info("scanner_add  ticker=%s  wl=%s", ticker, wl_id)
        return jsonify({"ok": True, "ticker": ticker})
    except Exception as exc:
        logger.error("api_scanner_add failed: %s", exc, exc_info=True)
        return jsonify({"error": "could not add ticker"}), 500


@app.route("/api/scanner/alerts")
def api_scanner_alerts():
    """Return the most-recent scanner alerts (DB-persisted) as JSON."""
    try:
        return jsonify(get_scanner_alerts(limit=30))
    except Exception as exc:
        logger.error("api_scanner_alerts failed: %s", exc, exc_info=True)
        return jsonify([]), 500


@app.route("/api/scanner/alerts/seen", methods=["POST"])
def api_scanner_alerts_seen():
    """Mark all scanner alerts as seen (clears the unseen badge)."""
    try:
        mark_scanner_alerts_seen()
        return jsonify({"ok": True})
    except Exception as exc:
        logger.error("api_scanner_alerts_seen failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/scanner/alerts/clear", methods=["POST"])
def api_scanner_alerts_clear():
    """Delete all scanner alert rows."""
    try:
        clear_scanner_alerts()
        return jsonify({"ok": True})
    except Exception as exc:
        logger.error("api_scanner_alerts_clear failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


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
    # Include coach so the detail page can patch the coach card on every poll
    try:
        _uid  = current_user_id()
        _plan = get_trade_plan(ticker, _uid)
        _mt   = _get_market_temperature()
        _rs   = get_risk_settings(_uid)
        result["coach"] = compute_trade_coach(stock, _plan, _mt, _rs)
    except Exception as _ce:
        logger.warning("api_stock_live coach failed for %s: %s", ticker, _ce)
    return jsonify(result)


@app.route("/api/stock/<ticker>/profile")
def api_stock_profile(ticker):
    """JSON endpoint: company name, sector, industry, description blurb.
    Cached in the DB for 30 days — first call per ticker hits Finnhub/yfinance,
    every call after that is instant."""
    ticker = ticker.upper()
    try:
        from intel_engine import fetch_company_profile
        profile = fetch_company_profile(ticker)
        return jsonify({"ok": True, "ticker": ticker, "profile": profile})
    except Exception as exc:
        logger.warning("api_stock_profile failed for %s: %s", ticker, exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


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

# ---------------------------------------------------------------------------
# Unified grade scale (DISPLAY ONLY — does not change how swing_score or the
# engine's swing_grade are computed). Normalises the engine's effective
# swing_grade (classifier._grade_for_bucket → A+/A/B+/B/B-/C/D/…) to the single
# scale used across every surface: A+, A, B+, B, and UNGRADED (everything else,
# ranked last / no letter shown).
# ---------------------------------------------------------------------------
_UGRADE_MAP = {
    "A+": ("A+", 4, "ugrade-aplus"),
    "A":  ("A",  3, "ugrade-a"),
    "B+": ("B+", 2, "ugrade-bplus"),
    "B":  ("B",  1, "ugrade-b"),
}

def _ugrade_info(swing_grade):
    """(label, rank, css) for a raw engine grade. UNGRADED → ('', 0, 'ugrade-none')."""
    return _UGRADE_MAP.get((swing_grade or "").strip().upper(), ("", 0, "ugrade-none"))

@app.template_filter("ugrade")
def _tf_ugrade(g):
    """Unified grade letter ('' when ungraded)."""
    return _ugrade_info(g)[0]

@app.template_filter("ugrank")
def _tf_ugrank(g):
    """Sort rank for a grade (A+=4 … B=1, ungraded=0)."""
    return _ugrade_info(g)[1]

@app.template_filter("ugcss")
def _tf_ugcss(g):
    """CSS class for the unified grade badge."""
    return _ugrade_info(g)[2]


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
        _startup_wls = get_all_watchlists(None)  # None = all users, background context
        for _wl in _startup_wls:
            _tickers = get_watchlist_stocks(_wl["id"])
            logger.info(
                "STARTUP watchlist '%s' (id=%s): %s",
                _wl["name"], _wl["id"], _tickers,
            )
    except Exception as _e:
        logger.error("deferred_startup watchlist log error: %s", _e, exc_info=True)

if _BACKGROUND_ENABLED:
    threading.Thread(target=_deferred_startup, daemon=True, name="startup-seed").start()


# ---------------------------------------------------------------------------
# Schwab Account Integration  (Phase 1 — read-only)
# ---------------------------------------------------------------------------
# Required env vars:  SCHWAB_CLIENT_ID  SCHWAB_CLIENT_SECRET  SCHWAB_REDIRECT_URI
# All routes guard against missing credentials gracefully.
# NO order-placement routes exist in this phase.
# ---------------------------------------------------------------------------

_schwab_account_cache: dict = {}  # keyed by user_id → {"data": ..., "ts": ...}
_SCHWAB_CACHE_TTL = 60   # refresh account data every 60 s


def _get_schwab_data(user_id: int = 1, force: bool = False) -> dict:
    """Return cached Schwab account summary for a user, refreshing when stale."""
    now   = _time.time()
    entry = _schwab_account_cache.get(user_id, {"data": None, "ts": 0.0})
    if not force and entry["ts"] and now - entry["ts"] < _SCHWAB_CACHE_TTL:
        return entry["data"]
    try:
        import schwab as _schwab
        data = _schwab.get_account_summary(user_id)
        _schwab_account_cache[user_id] = {"data": data, "ts": now}
        return data
    except Exception as _e:
        logger.warning("schwab cache refresh failed: %s", _e)
        return (entry["data"] or {
            "connected": False, "total_value": None, "buying_power": None,
            "daily_pnl": None, "total_unrealized": None,
            "open_positions": 0, "accounts": [], "error": str(_e),
        })


@app.route("/schwab/account")
def schwab_account():
    """Schwab account overview page — read-only, Phase 1."""
    import schwab as _schwab
    uid        = current_user_id()
    configured = _schwab.is_configured()
    tok_status = _schwab.token_status(uid)

    account_data = None
    orders_by_account = {}
    error_msg = None

    if tok_status["connected"]:
        try:
            account_data = _get_schwab_data(uid)
            if account_data.get("error"):
                error_msg = account_data["error"]
            else:
                for acct in account_data.get("accounts", []):
                    ah = acct.get("account_hash", "")
                    if ah:
                        try:
                            orders_by_account[ah] = _schwab.fetch_orders(ah, days_back=1, user_id=uid)
                        except Exception as _oe:
                            logger.warning("schwab orders fetch failed hash=%s: %s", ah, _oe)
                            orders_by_account[ah] = []
        except Exception as _e:
            error_msg = str(_e)
            logger.warning("schwab_account page error: %s", _e)

    return render_template(
        "schwab_account.html",
        configured=configured,
        tok_status=tok_status,
        account_data=account_data,
        orders_by_account=orders_by_account,
        error_msg=error_msg,
    )


@app.route("/schwab/auth")
def schwab_auth():
    """Start the Schwab OAuth 2.0 PKCE flow."""
    import schwab as _schwab

    if not _schwab.is_configured():
        flash("Schwab API credentials not configured. Set SCHWAB_CLIENT_ID, "
              "SCHWAB_CLIENT_SECRET, and SCHWAB_REDIRECT_URI environment variables.", "warning")
        return redirect(url_for("schwab_account"))

    code_verifier, code_challenge = _schwab._pkce_pair()
    state = secrets.token_urlsafe(24)

    # Store PKCE verifier and state in Flask session (server-side, signed cookie)
    session["schwab_code_verifier"] = code_verifier
    session["schwab_state"]         = state

    auth_url = _schwab.build_auth_url(state, code_challenge)
    logger.info("schwab_auth  redirecting to Schwab  state=%s", state)
    return redirect(auth_url)


@app.route("/schwab/callback")
def schwab_callback():
    """OAuth callback — exchange code for tokens and store them."""
    import schwab as _schwab

    code      = request.args.get("code", "")
    state     = request.args.get("state", "")
    error     = request.args.get("error", "")
    error_desc= request.args.get("error_description", "")

    if error:
        flash(f"Schwab authorization denied: {error_desc or error}", "danger")
        return redirect(url_for("schwab_account"))

    # Validate state to prevent CSRF
    expected_state    = session.pop("schwab_state", None)
    code_verifier     = session.pop("schwab_code_verifier", None)

    if not state or state != expected_state:
        flash("OAuth state mismatch — possible CSRF. Please try again.", "danger")
        return redirect(url_for("schwab_account"))

    if not code:
        flash("No authorization code received from Schwab.", "danger")
        return redirect(url_for("schwab_account"))

    try:
        uid    = current_user_id()
        tokens = _schwab.exchange_code_for_tokens(code, code_verifier or "")
        _schwab.save_tokens(tokens, uid)
        _schwab_account_cache.pop(uid, None)  # invalidate cache for this user
        logger.info("schwab_callback  tokens saved successfully")
        flash("Schwab account connected successfully. Account data is loading.", "success")
    except Exception as e:
        logger.error("schwab_callback  token exchange failed: %s", e)
        flash(f"Failed to connect Schwab account: {e}", "danger")

    return redirect(url_for("schwab_account"))


@app.route("/schwab/disconnect", methods=["POST"])
def schwab_disconnect():
    """Clear stored Schwab tokens (read-only disconnect, no broker-side revocation)."""
    import schwab as _schwab
    uid = current_user_id()
    _schwab.clear_tokens(uid)
    _schwab_account_cache.pop(uid, None)
    flash("Schwab account disconnected. Your data has been cleared from this app.", "success")
    return redirect(url_for("schwab_account"))


@app.route("/schwab/sync-preview")
def schwab_sync_preview():
    """
    Return a JSON preview of Schwab filled trades that can be imported to the journal.
    Only returns completed round-trips (BUY → SELL pairs) from the last 30 days.
    Flags trades already imported so the UI can grey them out.
    """
    import schwab as _schwab
    uid = current_user_id()
    if not _schwab.is_connected(uid):
        return jsonify({"error": "Schwab not connected"}), 403
    try:
        trades = _schwab.match_schwab_trades(days_back=30, user_id=uid)
        return jsonify({"ok": True, "trades": trades, "count": len(trades)})
    except Exception as e:
        logger.error("schwab_sync_preview error: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/schwab/sync-import", methods=["POST"])
def schwab_sync_import():
    """
    Import confirmed Schwab trade pairs into the journal.
    Accepts JSON body: { "trades": [...] } where each trade has the same
    shape returned by /schwab/sync-preview.
    Skips trades already imported (by import_key).
    """
    import schwab as _schwab
    from database import schwab_import_exists, record_schwab_import
    uid = current_user_id()
    if not _schwab.is_connected(uid):
        return jsonify({"error": "Schwab not connected"}), 403
    try:
        body   = request.get_json(force=True) or {}
        trades = body.get("trades") or []
        imported = []
        skipped  = []
        for t in trades:
            key = t.get("import_key", "")
            if not key or schwab_import_exists(key, uid):
                skipped.append(t.get("ticker", "?"))
                continue
            try:
                pnl_pct = t.get("pnl_pct", 0.0)
                result  = t.get("result", "Break Even")
                journal_id = add_journal_entry(
                    ticker         = t["ticker"],
                    trade_date     = t["trade_date"],
                    direction      = t.get("direction", "Long"),
                    entry_price    = t["entry_price"],
                    exit_price     = t["exit_price"],
                    shares         = t.get("shares"),
                    setup_type     = t.get("setup_type"),
                    momentum_score = None,
                    pnl_pct        = pnl_pct,
                    result         = result,
                    notes          = f"[Schwab import] Buy #{t.get('buy_order_id','')} · Sell #{t.get('sell_order_id','')}",
                    trade_mode     = t.get("trade_mode", "SWING TRADE"),
                    user_id        = uid,
                )
                record_schwab_import(key, journal_id, t["ticker"], t["trade_date"], uid)
                imported.append(t["ticker"])
            except Exception as _e:
                logger.warning("schwab_sync_import: error importing %s: %s", t.get("ticker"), _e)
                skipped.append(t.get("ticker", "?"))
        return jsonify({
            "ok": True,
            "imported": imported,
            "skipped":  skipped,
            "count":    len(imported),
        })
    except Exception as e:
        logger.error("schwab_sync_import error: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/schwab/summary")
def api_schwab_summary():
    """
    JSON endpoint for account summary.
    Used by dashboard widgets to pull live account balance / buying power.
    Read-only Phase 1 — no mutations.
    """
    import schwab as _schwab
    uid = current_user_id()
    if not _schwab.token_status(uid)["connected"]:
        return jsonify({"connected": False, "error": "Not authenticated"})
    data = _get_schwab_data(uid)
    # Return only the safe summary fields (no token data)
    return jsonify({
        "connected":        data.get("connected", False),
        "total_value":      data.get("total_value"),
        "buying_power":     data.get("buying_power"),
        "daily_pnl":        data.get("daily_pnl"),
        "total_unrealized": data.get("total_unrealized"),
        "open_positions":   data.get("open_positions"),
        "error":            data.get("error"),
    })



def _intel_error_payload(msg: str) -> dict:
    """Standard JSON error payload for /api/intel."""
    return {
        "ok":              False,
        "errors":          [msg],
        "last_updated":    "—",
        "news":            [],
        "market_news":     [],
        "earnings":        {"today": [], "tomorrow": [], "this_week": []},
        "splits":          [],
        "dividends":       [],
        "economic_events": [],
        "alerts_sent":     [],
        "from_cache":      False,
        "refreshing":      False,
    }


@app.route("/api/intel")
def api_intel():
    """Returns all intel feeds as JSON — always returns JSON, never HTML."""
    try:
        if request.args.get("debug") == "1":
            return jsonify({
                "ok": True,
                "news": [{"ticker": "TEST", "headline": "Debug news item", "impact": "HIGH",
                          "time": "09:00", "reason": "Debug test payload", "source": "Debug",
                          "on_watchlist": False}],
                "market_news": [{"ticker": "TEST", "headline": "Debug news item", "impact": "HIGH",
                                 "time": "09:00", "reason": "Debug test payload", "source": "Debug",
                                 "on_watchlist": False}],
                "earnings": {
                    "today": [],
                    "tomorrow": [],
                    "this_week": [{"ticker": "TEST", "date": "2026-05-11", "date_label": "This Week",
                                   "time_label": "BMO", "days_away": 1, "on_watchlist": False}],
                },
                "splits": [{"ticker": "TEST", "ratio": "2:1", "effective_date": "2026-05-15",
                            "eff_date": "2026-05-15", "type": "Forward", "status": "Upcoming",
                            "is_new": False, "days_away": 5}],
                "dividends": [],
                "economic_events": [{"name": "Debug CPI Event", "date": "2026-05-12", "impact": "HIGH",
                                     "event": "Debug CPI Event", "date_label": "Mon May 12",
                                     "time": "8:30 AM", "reason": "Debug payload", "is_today": False}],
                "errors": [],
                "last_updated": "debug",
                "from_cache": False,
                "refreshing": False,
            })

        if request.args.get("refresh") == "1":
            namespace = {
                "news": "market_news",
                "earnings": "earnings",
            }.get(request.args.get("feed"))
            _intel.clear_intel_cache(namespace)

        data = _intel.get_intel_summary()
        return jsonify(data)

    except Exception as e:
        logger.error("api_intel error: %s", e, exc_info=True)
        return jsonify(_intel_error_payload(str(e))), 200


@app.route("/api/intel/news-refresh", methods=["POST"])
def api_intel_news_refresh():
    """Fetch news in the request and return rendered story cards.

    Unlike the general cache-first Intel endpoint, this request waits for the
    bounded provider fan-out. The browser can therefore display the result
    directly without relying on a background thread surviving a Gunicorn
    recycle or on a later page reload landing in the same process.
    """
    try:
        _intel.clear_intel_cache("market_news")
        raw_news = _intel.fetch_market_news()

        try:
            watchlist = get_user_tracked_tickers(current_user_id())
        except Exception:
            watchlist = []

        from sentiment_engine import enrich_news_article
        enriched = [enrich_news_article(row, watchlist) for row in raw_news]
        enriched.sort(key=lambda row: (-row["importance"], row.get("time") or ""))
        enriched = enriched[:24]
        coverage = {}
        for item in enriched:
            ticker = str(item.get("ticker") or "").upper()
            if not ticker:
                continue
            score = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2}.get(item.get("impact"), 1)
            entry = coverage.setdefault(ticker, {"ticker": ticker, "mentions": 0, "score": 0})
            entry["mentions"] += 1
            entry["score"] += score
        trending = sorted(
            coverage.values(),
            key=lambda row: (-row["score"], -row["mentions"], row["ticker"]),
        )[:6]

        if enriched:
            status = {
                "refreshing": False,
                "message": f"Loaded {len(enriched)} live stories.",
            }
        else:
            status = {
                "refreshing": False,
                "message": (
                    "No provider returned a usable story in this refresh. "
                    "The request completed; try again in about one minute."
                ),
            }

        return jsonify({
            "ok": bool(enriched),
            "count": len(enriched),
            "bullish_count": sum(row.get("label") == "BULLISH" for row in enriched),
            "bearish_count": sum(row.get("label") == "BEARISH" for row in enriched),
            "html": render_template(
                "_intel_news_items.html",
                news=enriched,
                intel_status=status,
            ),
            "trending_html": render_template(
                "_intel_trending.html",
                trending=trending,
            ),
        })
    except Exception as exc:
        logger.error("api_intel_news_refresh: %s", exc, exc_info=True)
        status = {
            "refreshing": False,
            "message": "The news refresh completed with a server error. Please try again.",
        }
        return jsonify({
            "ok": False,
            "count": 0,
            "bullish_count": 0,
            "bearish_count": 0,
            "html": render_template(
                "_intel_news_items.html",
                news=[],
                intel_status=status,
            ),
            "trending_html": render_template(
                "_intel_trending.html",
                trending=[],
            ),
        }), 200


@app.route("/api/intel/earnings-radar")
def api_intel_earnings_radar():
    """Refresh the compact earnings slate synchronously and return its HTML."""
    try:
        earnings = _intel.fetch_earnings_radar(limit=6)
        message = (
            ""
            if earnings else
            "The calendar request completed, but no upcoming large-, mid-cap, or watchlist reports were returned."
        )
        return jsonify({
            "ok": bool(earnings),
            "count": len(earnings),
            "html": render_template(
                "_intel_earnings_items.html",
                earnings=earnings,
                earnings_message=message,
            ),
        })
    except Exception as exc:
        logger.error("api_intel_earnings_radar: %s", exc, exc_info=True)
        return jsonify({
            "ok": False,
            "count": 0,
            "html": render_template(
                "_intel_earnings_items.html",
                earnings=[],
                earnings_message="The earnings calendar is temporarily unavailable. Please try again.",
            ),
        }), 200


@app.route("/api/ndx_watch")
def api_ndx_watch():
    """Return Nasdaq-100 constituent watch data (recent changes + top holdings)."""
    try:
        refresh = request.args.get("refresh") == "1"
        if refresh:
            with _intel._cache_lock:
                _intel._cache.pop("ndx", None)
            _intel.run_ndx_constituent_check()
        return jsonify(_intel.get_ndx_watch_data())
    except Exception as exc:
        logger.error("api_ndx_watch: %s", exc, exc_info=True)
        return jsonify({"error": str(exc), "recent_changes": [], "top_holdings": [], "total_members": 0}), 200


@app.route("/api/intel/debug")
@require_admin
def api_intel_debug():
    """Debug endpoint — shows data source health for the intel engine. No API keys exposed."""
    try:
        summary    = _intel.get_intel_summary()
        earn_dbg   = summary.get("earnings_debug", {})
        news_dbg   = summary.get("news_status", {})
        macro      = summary.get("market_environment", {})
        sector_ht  = summary.get("sector_heat", [])

        cache_ages: dict = {}
        with _intel._cache_lock:
            for key, entry in list(_intel._cache.items()):
                age_s = int(_time.monotonic() - entry["ts"])
                cache_ages[key] = f"{age_s}s ago"

        fh_limited = _intel._fh_is_rate_limited()
        fh_secs    = max(0, int(_intel._fh_rl_until - _time.monotonic())) if fh_limited else 0
        earn       = summary.get("earnings", {})

        return jsonify({
            "ok":                   summary.get("ok"),
            "finnhub_rate_limited": fh_limited,
            "finnhub_rl_remaining": f"{fh_secs}s" if fh_limited else "not limited",
            "errors":               summary.get("errors", []),
            "cache_ages":           cache_ages,
            "news_count":           len(summary.get("news", [])),
            "news_configured":      news_dbg.get("configured"),
            "news_sources":         news_dbg.get("configured_sources", []),
            "news_message":         news_dbg.get("message"),
            "earnings_today":       len(earn.get("today", [])),
            "earnings_tomorrow":    len(earn.get("tomorrow", [])),
            "earnings_this_week":   len(earn.get("this_week", [])),
            "earnings_tickers_checked": earn_dbg.get("tickers_checked", 0),
            "earnings_source":      earn_dbg.get("earnings_source_used", "—"),
            "earnings_yfinance":    earn_dbg.get("yfinance_found", 0),
            "earnings_finnhub":     earn_dbg.get("finnhub_found", 0),
            "earnings_nasdaq":      earn_dbg.get("nasdaq_found", 0),
            "earnings_overrides":   earn_dbg.get("overrides_injected", 0),
            "splits_count":         len(summary.get("splits", [])),
            "dividends_count":      len(summary.get("dividends", [])),
            "sector_heat_count":    len(sector_ht),
            "yield_10y":            macro.get("yield_10y"),
            "yield_change_bps":     macro.get("yield_change_bps"),
            "yield_trend":          macro.get("yield_trend"),
            "dxy_price":            macro.get("dxy_price"),
            "vix_level":            macro.get("vix_level"),
            "regime":               macro.get("regime"),
        })
    except Exception as exc:
        logger.error("api_intel_debug: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/institutional/market-internals")
def api_market_internals():
    """Market internals: breadth, ADD proxy, sector participation, breakout conditions."""
    try:
        import institutional_engine as _inst
        ctx = _mkt.get_market_context() if _MKT_AVAILABLE else {}
        internals = _inst.get_market_internals(ctx)
        macro     = _inst.get_macro_context(ctx)
        return jsonify({
            "ok": True,
            "internals": internals,
            "macro": macro,
            "regime": ctx.get("regime"),
            "regime_label": ctx.get("regime_label"),
            "vix": ctx.get("vix_level"),
            "qqq_1d": ctx.get("qqq_1d_pct"),
            "spy_1d": ctx.get("spy_1d_pct"),
        })
    except Exception as exc:
        logger.error("api_market_internals: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/institutional/<ticker>")
def api_institutional_ticker(ticker: str):
    """
    Full institutional analysis for a single ticker.
    Returns all 15 engine outputs: volatility compression, liquidity map,
    patterns, probability score, continuation, risk levels, discipline AI.
    """
    try:
        import institutional_engine as _inst
        ticker = ticker.upper().strip()
        ctx = _mkt.get_market_context() if _MKT_AVAILABLE else {}

        # Try to get existing stock data from DB
        stock = get_stock_data(ticker) or {"ticker": ticker}

        result = _inst.analyze_institutional(stock, ctx)
        # Append market internals and macro for completeness
        result["internals"] = _inst.get_market_internals(ctx)
        result["macro"]     = _inst.get_macro_context(ctx)
        result["ticker"]    = ticker
        result["ok"]        = True
        return jsonify(result)
    except Exception as exc:
        logger.error("api_institutional_ticker %s: %s", ticker, exc, exc_info=True)
        return jsonify({"ok": False, "ticker": ticker, "error": str(exc)}), 500


@app.route("/api/institutional/smart-watchlist")
def api_smart_watchlist():
    """
    AI-ranked smart watchlist: A+/A/B tiers, earnings plays,
    ORB candidates, continuation setups, squeeze plays.
    """
    try:
        import institutional_engine as _inst
        ctx    = _mkt.get_market_context() if _MKT_AVAILABLE else {}
        stocks = get_all_stock_data()   # list of dicts from DB

        # Ensure each stock has a prob_score (may already be set from analysis)
        scored = []
        for s in stocks:
            if not s.get("prob_score"):
                s["prob_score"] = _inst.compute_probability_score(s, ctx)["prob_score"]
            scored.append(s)

        watchlists = _inst.build_smart_watchlist(scored, ctx)
        return jsonify({"ok": True, **watchlists})
    except Exception as exc:
        logger.error("api_smart_watchlist: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/institutional/adaptive-insights")
def api_adaptive_insights():
    """Adaptive learning: setup win rates, best regimes, AI recommendations."""
    try:
        import institutional_engine as _inst
        insights    = _inst.get_adaptive_insights()
        db_stats    = get_setup_outcome_stats(current_user_id())
        return jsonify({"ok": True, **insights, "db_stats": db_stats})
    except Exception as exc:
        logger.error("api_adaptive_insights: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/institutional/record-outcome", methods=["POST"])
def api_record_outcome():
    """
    Record a trade outcome for adaptive learning.
    POST JSON: {"ticker": "NVDA", "setup_type": "Bull Flag", "outcome": "win", "regime": "RISK_ON"}
    """
    try:
        import institutional_engine as _inst
        body       = request.get_json(force=True) or {}
        setup_type = body.get("setup_type", "")
        outcome    = body.get("outcome", "")     # "win" | "loss" | "breakeven"
        regime     = body.get("regime", "")
        pattern    = body.get("pattern", "")

        if outcome not in ("win", "loss", "breakeven"):
            return jsonify({"ok": False, "error": "outcome must be win|loss|breakeven"}), 400

        ticker     = body.get("ticker", "")
        prob_score = int(body.get("prob_score", 0))
        notes      = body.get("notes", "")

        _inst.record_setup_outcome(setup_type, outcome, regime, pattern)
        save_setup_outcome(ticker, setup_type, outcome, regime, pattern, prob_score, notes,
                           current_user_id())
        return jsonify({"ok": True, "recorded": {"ticker": ticker, "setup_type": setup_type, "outcome": outcome}})
    except Exception as exc:
        logger.error("api_record_outcome: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/institutional/daily-review")
def api_daily_review():
    """End-of-day performance review across all scanned setups."""
    try:
        import institutional_engine as _inst
        ctx    = _mkt.get_market_context() if _MKT_AVAILABLE else {}
        stocks = get_all_stock_data()

        # Build analysis dicts for all stocks (fast — uses cached data)
        analyzed = []
        for s in stocks:
            try:
                result = _inst.analyze_institutional(s, ctx)
                analyzed.append({**s, **result})
            except Exception:
                analyzed.append(s)

        review = _inst.generate_daily_review(analyzed)
        return jsonify({"ok": True, **review})
    except Exception as exc:
        logger.error("api_daily_review: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/intel")
def intel():
    """Tradestaar market-news and smart-money hub.

    All cards are built from the existing cache-first engines.  Derived values
    (headline sentiment and coverage momentum) are explicitly labelled so they
    cannot be mistaken for exchange volume or a third-party sentiment feed.
    """
    if request.args.get("refresh") == "earnings":
        _intel.clear_intel_cache("earnings")
    mkt = _get_mkt_ctx()

    liq, money_flow = {}, []
    try:
        import liquidity_engine as _liq
        liq = _liq.get_liquidity_status() or {}
        money_flow = _liq.get_money_flow() or []
    except Exception as _e:
        logger.debug("intel: liquidity fetch failed: %s", _e)

    events, news, earnings = [], [], []
    earnings_status = {"refreshing": False, "message": "Upcoming reports unavailable."}
    intel_status = {"refreshing": False, "message": "News status unavailable.", "configured": False}
    try:
        summ = _intel.get_intel_summary() or {}
        intel_status = summ.get("news_status") or intel_status
        events = summ.get("economic_events") or []
        news = summ.get("market_news") or summ.get("news") or []
        earn = summ.get("earnings") or {}
        earnings = (
            list(earn.get("today") or [])
            + list(earn.get("tomorrow") or [])
            + list(earn.get("this_week") or [])
            + list(earn.get("coming_up") or [])
        )
        try:
            earnings = _intel.select_radar_rows(earnings, 6)
        except Exception as _e:
            logger.debug("intel: radar selection unavailable: %s", _e)
            earnings = earnings[:6]
        earnings_status = {
            "refreshing": bool(summ.get("refreshing") and not earnings),
            "message": (
                "Refreshing upcoming reports…" if summ.get("refreshing") and not earnings
                else "No upcoming reports were returned. The feed will retry automatically."
            ),
        }
    except Exception as _e:
        logger.debug("intel: intel_summary failed: %s", _e)

    try:
        watchlist = get_user_tracked_tickers(current_user_id())
    except Exception:
        watchlist = []

    # Elite News Scanner values are deterministic and auditable. They describe
    # headline tone and catalyst importance, not analyst consensus or advice.
    from sentiment_engine import enrich_news_article
    enriched_news = []
    coverage = {}
    for raw in news:
        item = enrich_news_article(raw, watchlist)
        ticker = str(item.get("ticker") or "").upper()
        if ticker:
            score = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2}.get(item.get("impact"), 1)
            entry = coverage.setdefault(ticker, {"ticker": ticker, "mentions": 0, "score": 0})
            entry["mentions"] += 1
            entry["score"] += score
        enriched_news.append(item)
    enriched_news.sort(key=lambda row: (-row["importance"], row.get("time") or ""))
    trending = sorted(coverage.values(), key=lambda row: (-row["score"], -row["mentions"], row["ticker"]))[:6]

    briefing = None
    try:
        briefing = get_ai_briefing(_et_now().strftime("%Y-%m-%d"))
    except Exception:
        pass
    story = {}
    if not briefing and _MKT_AVAILABLE:
        try:
            story = _mkt.generate_market_story(mkt, (liq.get("score") if liq else None))
        except Exception as _e:
            logger.debug("intel: story failed: %s", _e)

    # Command Center alert feed. Earnings alerts are derived from the slate this
    # page already loaded; insider alerts are written by /smart-money when it
    # fetches Form 4 data. Never fatal - the page renders without the card.
    command_alerts, unseen_alerts = [], 0
    try:
        import command_center_alerts as _cca
        from database import add_scanner_alert, get_scanner_alerts, get_unseen_scanner_alert_count
        existing = get_scanner_alerts(limit=120)
        _cca.sync_alerts(_cca.build_earnings_alerts(earnings), existing, add_scanner_alert)
        command_alerts = get_scanner_alerts(limit=12)
        unseen_alerts = get_unseen_scanner_alert_count()
    except Exception as _e:
        logger.debug("intel: alert feed unavailable: %s", _e)

    return render_template(
        "intel.html",
        mkt=mkt, liq=liq, money_flow=money_flow[:8],
        events=events[:8], news=enriched_news[:24], earnings=earnings,
        trending=trending, briefing=briefing, story=story, intel_status=intel_status,
        earnings_status=earnings_status,
        command_alerts=command_alerts, unseen_alerts=unseen_alerts,
    )


@app.route("/api/fundamentals/debug/<ticker>")
@require_admin
def api_fundamentals_debug(ticker):
    """What EDGAR and Finnhub actually return for one ticker.

    Neither the laptop nor the cloud sandbox can reach data.sec.gov, so the
    running app is the only place this question can be answered. Read-only:
    no cache write, no scoring.
    """
    clean = (ticker or "").strip().upper()[:12]
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,11}", clean):
        return jsonify({"ok": False, "error": "invalid ticker"}), 400
    try:
        from fundamentals_debug import inspect
        return jsonify({"ok": True, **inspect(clean)})
    except Exception as exc:
        logger.exception("fundamentals debug failed for %s", clean)
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 200


@app.route("/api/intel/alerts/seen", methods=["POST"])
def api_intel_alerts_seen():
    """Clear the Command Center unseen-alert badge."""
    try:
        from database import mark_scanner_alerts_seen
        mark_scanner_alerts_seen()
        return jsonify({"ok": True})
    except Exception as exc:
        logger.error("api_intel_alerts_seen: %s", exc, exc_info=True)
        return jsonify({"ok": False}), 200


@app.route("/sentiment")
def sentiment():
    """Transparent news tone with an explicit social-data availability state."""
    news = []
    try:
        summary = _intel.get_intel_summary() or {}
        news = summary.get("market_news") or summary.get("news") or []
    except Exception as exc:
        logger.debug("sentiment: news summary unavailable: %s", exc)

    try:
        watchlist = get_user_tracked_tickers(current_user_id())
    except Exception:
        watchlist = []

    from sentiment_engine import build_sentiment_snapshot
    snapshot = build_sentiment_snapshot(news, watchlist)
    return render_template("sentiment.html", snapshot=snapshot)


@app.route("/smart-money")
def smart_money():
    """Verified SEC insider filings and congressional trade disclosures."""
    try:
        tickers = get_user_tracked_tickers(current_user_id())
    except Exception:
        tickers = []

    from smart_money import build_insider_dashboard, clear_sec_form4_cache, fetch_congress_trades, fetch_sec_form4
    if request.args.get("refresh") == "1":
        clear_sec_form4_cache()
    filters = {
        "ticker": str(request.args.get("ticker") or "").strip().upper()[:12],
        "role": str(request.args.get("role") or "").strip()[:80],
        "transaction_type": str(request.args.get("transaction_type") or "all").strip(),
        "minimum_value": str(request.args.get("minimum_value") or "0").strip(),
        "cluster": request.args.get("cluster") == "1",
        "days": 7 if request.args.get("days") == "7" else 30,
    }
    if filters["ticker"] and not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,11}", filters["ticker"]):
        filters["ticker"] = ""
    if filters["transaction_type"] not in {"all", "buy", "sell", "non_market", "P", "S", "F", "G", "A", "M"}:
        filters["transaction_type"] = "all"
    insiders, insider_status = fetch_sec_form4(tickers, limit=None, history_days=30)
    from smart_money import resolve_alert_rules
    alert_rules = resolve_alert_rules(get_insider_alert_rules(current_user_id()))
    dashboard = build_insider_dashboard(insiders, filters=filters, alert_rules=alert_rules)
    try:
        import command_center_alerts as _cca
        from database import add_scanner_alert, get_scanner_alerts
        _cca.sync_alerts(
            _cca.build_insider_alerts(dashboard.get("events")),
            get_scanner_alerts(limit=120),
            add_scanner_alert,
        )
    except Exception as _e:
        logger.debug("smart_money: alert sync skipped: %s", _e)

    congress, congress_status = fetch_congress_trades()
    return render_template(
        "smart_money.html",
        insiders=insiders,
        dashboard=dashboard,
        congress=congress,
        insider_status=insider_status,
        congress_status=congress_status,
        watched_tickers=tickers[:10],
    )


@app.route("/smart-money/alert-rules", methods=["POST"])
def smart_money_alert_rules():
    """Persist dashboard-only Form 4 match rules; no push delivery is implied."""
    from smart_money import INSIDER_ALERT_RULES
    rules = {key: request.form.get(key) == "1" for key in INSIDER_ALERT_RULES}
    set_insider_alert_rules(current_user_id(), rules)
    flash("Insider dashboard match rules updated.", "success")
    return redirect(url_for("smart_money"))


def _build_catalyst_calendar(summary, watchlist_tickers=None):
    """Normalize cached earnings and macro events for the calendar UI.

    The intelligence engine intentionally has different schemas for company
    earnings and economic releases.  Keeping the normalization here gives the
    template one stable contract and makes the filters deterministic/offline.
    """
    watchlist = {str(t).upper() for t in (watchlist_tickers or [])}
    rows = []
    earnings = (summary or {}).get("earnings") or {}
    seen_earnings = set()
    for bucket in ("today", "tomorrow", "this_week", "coming_up"):
        for raw in earnings.get(bucket) or []:
            ticker = str(raw.get("ticker") or "").upper()
            date = str(raw.get("date") or "")[:10]
            key = (ticker, date)
            if not ticker or key in seen_earnings:
                continue
            seen_earnings.add(key)
            days = raw.get("days_away")
            try:
                days = int(days)
            except (TypeError, ValueError):
                days = 99
            rows.append({
                "kind": "EARNINGS", "date": date,
                "date_label": raw.get("date_label") or date or "Date TBD",
                "time": raw.get("time_label") or "TBD",
                "title": f"{ticker} Earnings",
                "ticker": ticker,
                "company_name": raw.get("company_name") or ticker,
                "impact": "HIGH" if ticker in watchlist else "MEDIUM",
                "reason": "Quarterly earnings can create price gaps and volatility.",
                "days_away": days,
                "on_watchlist": ticker in watchlist,
                "eps_est": raw.get("eps_est"),
                "rev_est": raw.get("rev_est"),
                "market_cap": raw.get("market_cap"),
                "cap_tier": raw.get("cap_tier") or ("Watchlist" if ticker in watchlist else ""),
            })

    for raw in (summary or {}).get("economic_events") or []:
        days = raw.get("days_away")
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = 99
        rows.append({
            "kind": "ECONOMIC", "date": str(raw.get("date") or "")[:10],
            "date_label": raw.get("date_label") or raw.get("date") or "Date TBD",
            "time": raw.get("time") or "TBD",
            "title": raw.get("event") or "Economic Event",
            "ticker": "", "company_name": "US Macro",
            "impact": str(raw.get("impact") or "MEDIUM").upper(),
            "reason": raw.get("reason") or "Macro releases can affect rates, sectors, and broad market risk.",
            "days_away": days, "on_watchlist": False,
            "eps_est": None, "rev_est": None,
            "market_cap": None, "cap_tier": "",
        })

    return sorted(rows, key=lambda row: (
        row["days_away"], row["date"], row["time"] == "TBD", row["time"], row["title"]
    ))


@app.route("/calendar")
def catalyst_calendar():
    """Mobile-first earnings and economic catalyst calendar."""
    try:
        summary = _intel.get_intel_summary() or {}
    except Exception as exc:
        logger.debug("catalyst_calendar: intel summary failed: %s", exc)
        summary = {}

    active_id = get_active_wl_id()
    try:
        active_tickers = get_watchlist_stocks(active_id) if active_id else []
    except Exception:
        active_tickers = []
    rows = _build_catalyst_calendar(summary, active_tickers)
    # The macro schedule is a list in the source, so it has an end date. Tell
    # the page how far it reaches: an exhausted calendar reads exactly like a
    # quiet fortnight otherwise.
    try:
        econ_coverage = _intel.static_econ_coverage()
    except Exception as exc:
        logger.debug("catalyst_calendar: econ coverage failed: %s", exc)
        econ_coverage = None
    return render_template(
        "catalyst_calendar.html", events=rows,
        earnings_count=sum(row["kind"] == "EARNINGS" for row in rows),
        economic_count=sum(row["kind"] == "ECONOMIC" for row in rows),
        watchlist_count=sum(row["on_watchlist"] for row in rows),
        econ_coverage=econ_coverage,
    )


# ---------------------------------------------------------------------------
# Liquidity & Opportunity Research Engine
# ---------------------------------------------------------------------------

@app.route("/")
@app.route("/opportunity")
def liquidity_page():
    """Morning Command Center — liquidity, risk, sector flow, hidden opportunities."""
    return render_template("liquidity.html")


@app.route("/api/liquidity/status")
def api_liquidity_status():
    """Fed liquidity monitor: FRED data, yield curve, liquidity score."""
    try:
        import liquidity_engine as _liq
        ctx = _liq.get_liquidity_status()
        return jsonify({"ok": True, **ctx})
    except Exception as exc:
        logger.error("api_liquidity_status: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/liquidity/money-flow")
def api_money_flow():
    """Sector ETF money flow rankings."""
    try:
        import liquidity_engine as _liq
        mflow = _liq.get_money_flow()
        ctx   = _liq.get_liquidity_status()
        return jsonify({
            "ok":        True,
            "money_flow":mflow,
            "liq_status":ctx.get("status"),
            "liq_score": ctx.get("score"),
        })
    except Exception as exc:
        logger.error("api_money_flow: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/opportunity/scan")
def api_opportunity_scan():
    """
    Run the full hidden opportunity scan.
    Query params:
      mode=both|trade|invest   (default: both)
      tickers=NVDA,CRWD,...    (extra tickers to include)
    """
    try:
        import opportunity_engine as _opp
        import liquidity_engine   as _liq
        mode          = request.args.get("mode", "both")
        extra_raw     = request.args.get("tickers", "")
        extra_tickers = [t.strip().upper() for t in extra_raw.split(",") if t.strip()]
        mkt_ctx = _mkt.get_market_context() if _MKT_AVAILABLE else {}
        liq_ctx = _liq.get_liquidity_status()
        results = _opp.run_opportunity_scan(extra_tickers, mkt_ctx, liq_ctx, mode)
        return jsonify({"ok": True, **results})
    except Exception as exc:
        logger.error("api_opportunity_scan: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/opportunity/ticker/<ticker>")
def api_opportunity_ticker(ticker: str):
    """Full opportunity analysis + research report for a single ticker."""
    try:
        import opportunity_engine as _opp
        import liquidity_engine   as _liq
        ticker  = ticker.upper().strip()
        mkt_ctx = _mkt.get_market_context() if _MKT_AVAILABLE else {}
        liq_ctx = _liq.get_liquidity_status()
        _opp.get_fundamentals_sync(ticker)   # ensure data is ready before building report
        result  = _opp.scan_ticker(ticker, mkt_ctx, liq_ctx)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        logger.error("api_opportunity_ticker %s: %s", ticker, exc, exc_info=True)
        return jsonify({"ok": False, "ticker": ticker, "error": str(exc)}), 500


@app.route("/api/opportunity/alerts")
def api_opportunity_alerts():
    """Combined opportunity + liquidity alerts."""
    try:
        import opportunity_engine as _opp
        import liquidity_engine   as _liq
        mkt_ctx = _mkt.get_market_context() if _MKT_AVAILABLE else {}
        liq_ctx = _liq.get_liquidity_status()
        # Quick scan of top tickers only for speed
        fast_universe = ["NVDA","CRWD","AMD","META","PLTR","MRVL","DDOG","NET","AMZN","COIN"]
        results = []
        for t in fast_universe:
            try:
                results.append(_opp.scan_ticker(t, mkt_ctx, liq_ctx))
            except Exception:
                pass
        alerts = _opp.generate_opportunity_alerts(results, mkt_ctx, liq_ctx)
        return jsonify({"ok": True, "alerts": alerts, "count": len(alerts)})
    except Exception as exc:
        logger.error("api_opportunity_alerts: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/opportunity/refresh", methods=["POST"])
def api_opportunity_refresh():
    """Trigger background refresh of liquidity data."""
    try:
        import liquidity_engine as _liq
        _liq.refresh_liquidity_bg()
        return jsonify({"ok": True, "msg": "Liquidity refresh triggered."})
    except Exception as exc:
        logger.error("api_opportunity_refresh: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Authentication routes
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login_page():
    """Multi-user login page."""
    if session.get("user_id"):
        return redirect(url_for("liquidity_page"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        # Throttle before checking the password. Nothing counted attempts, so
        # a known username could be guessed against as fast as the network
        # allowed. The scope pairs address with username: keying on the
        # username alone would let anyone lock a real user out on purpose, and
        # keying on the address alone would lock out a whole office behind one
        # NAT.
        import login_throttle as _throttle
        from database import get_db as _tdb
        _scope = _throttle.scope_key(
            (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
             or request.remote_addr or ""), username)
        _conn = None
        try:
            _conn = _tdb()
            _wait = _throttle.lockout_seconds(
                _throttle.recent_failures(_conn, _scope))
        except Exception:
            _wait = 0
        if _wait > 0:
            if _conn is not None:
                _conn.close()
            return render_template(
                "login.html", next=request.form.get("next", ""),
                error=_throttle.describe(_wait),
                registration_enabled=_registration_enabled()), 429

        user = check_user_password(username, password)
        try:
            if _conn is not None:
                if user:
                    _throttle.clear(_conn, _scope)
                else:
                    _throttle.record_failure(_conn, _scope)
                _conn.close()
        except Exception:
            pass

        if user:
            remember = request.form.get("remember_me") == "1"
            session.permanent = remember
            session["user_id"]   = user["id"]
            session["username"]  = user["username"]
            session["is_admin"]  = user["is_admin"]
            next_url = request.form.get("next") or url_for("liquidity_page")
            if not next_url.startswith("/") or next_url.startswith("//"):
                next_url = url_for("liquidity_page")
            return redirect(next_url)
        error = "Invalid username or password."
    return render_template("login.html",
                           next=request.args.get("next", ""),
                           error=error,
                           registration_enabled=_registration_enabled())


@app.route("/logout", methods=["POST"])
def logout():
    """Clear session and redirect to login."""
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/register", methods=["GET", "POST"])
def register_page():
    """Self-registration page. Creates a regular (non-admin) user account."""
    if not _registration_enabled():
        return render_template("login.html", next="", error="Registration is disabled.",
                               registration_enabled=False), 403
    if session.get("user_id"):
        return redirect(url_for("liquidity_page"))
    error = None
    entered_username = ""
    if request.method == "POST":
        entered_username = request.form.get("username", "").strip()
        password         = request.form.get("password", "")
        confirm          = request.form.get("confirm_password", "")

        if not entered_username:
            error = "Username is required."
        elif len(entered_username) < 3:
            error = "Username must be at least 3 characters."
        elif len(entered_username) > 50:
            error = "Username must be 50 characters or fewer."
        elif not entered_username.replace("_", "").replace("-", "").isalnum():
            error = "Username may only contain letters, numbers, hyphens, and underscores."
        elif len(password) < 12:
            error = "Password must be at least 12 characters."
        elif password != confirm:
            error = "Passwords do not match."
        elif get_user_by_username(entered_username):
            error = "That username is already taken."
        else:
            try:
                uid = create_user(entered_username, password, is_admin=False)
                session.permanent = False
                session["user_id"]  = uid
                session["username"] = entered_username.lower()
                session["is_admin"] = 0
                return redirect(url_for("liquidity_page"))
            except Exception:
                error = "Could not create account — please try again."

    return render_template("register.html", error=error, username=entered_username)


# ---------------------------------------------------------------------------
# Admin — user management
# ---------------------------------------------------------------------------

@app.route("/admin/users")
@require_admin
def admin_users():
    """Admin page: list all users."""
    users = get_all_users()
    return render_template("admin_users.html", users=users,
                           current_uid=current_user_id())


@app.route("/admin/users/create", methods=["POST"])
@require_admin
def admin_user_create():
    """Admin: create a new user."""
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    is_admin = bool(request.form.get("is_admin"))
    if not username or len(password) < 12:
        flash("Username and a password of at least 12 characters are required.", "error")
        return redirect(url_for("admin_users"))
    try:
        create_user(username, password, is_admin=is_admin)
        flash(f"User '{username}' created.", "success")
    except Exception as exc:
        flash(f"Could not create user: {exc}", "error")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:uid>/delete", methods=["POST"])
@require_admin
def admin_user_delete(uid):
    """Admin: delete a user (cannot delete admin, id=1)."""
    try:
        delete_user(uid)
        flash("User deleted.", "info")
    except ValueError as exc:
        flash(str(exc), "error")
    except Exception as exc:
        flash(f"Error deleting user: {exc}", "error")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:uid>/password", methods=["POST"])
@require_admin
def admin_user_password(uid):
    """Admin: change a user's password."""
    new_password = request.form.get("password", "").strip()
    if len(new_password) < 12:
        flash("Password must be at least 12 characters.", "error")
        return redirect(url_for("admin_users"))
    update_user_password(uid, new_password)
    flash("Password updated.", "success")
    return redirect(url_for("admin_users"))


# ---------------------------------------------------------------------------
# Research Desk
# ---------------------------------------------------------------------------

@app.route("/research")
def research_page():
    """Research Desk: 40-point fundamental scorecard plus the study log.

    The scorecard is rendered from fundamentals_engine, the same engine behind
    /fundamentals, so both pages always report the same score. Presentation is
    all that differs between them.
    """
    ticker = (request.args.get("ticker") or "").strip().upper()[:12]
    if ticker and not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,11}", ticker):
        ticker = ""
    data, error, stock = None, None, {}
    if ticker:
        try:
            from fundamentals_engine import get_fundamentals
            data = get_fundamentals(ticker, force_refresh=request.args.get("refresh") == "1")
            if data.get("error"):
                error = data["error"]
        except Exception as exc:
            logger.exception("research_page fundamentals failed for %s", ticker)
            error = str(exc)
        try:
            stock = get_stock_data(ticker) or {}
        except Exception as exc:
            logger.debug("research_page price lookup failed: %s", exc)
    return render_template("research.html", ticker=ticker, data=data, error=error, stock=stock)


@app.route("/fundamentals")
def fundamentals_page():
    """Fundamentals Analyzer + Education Module."""
    ticker = (request.args.get("ticker") or "").strip().upper()
    data   = None
    error  = None

    if ticker:
        force = request.args.get("refresh") == "1"
        try:
            from fundamentals_engine import get_fundamentals
            data = get_fundamentals(ticker, force_refresh=force)
            if data.get("error"):
                error = data["error"]
        except Exception as exc:
            logger.exception("fundamentals_page error for %s", ticker)
            error = str(exc)

    return render_template("fundamentals.html", ticker=ticker, data=data, error=error)


def _comparison_snapshot(ticker: str) -> dict:
    """Build a compact, source-labelled comparison record for one ticker."""
    from fundamentals_engine import get_fundamentals

    data = get_fundamentals(ticker)
    metrics = {}
    for section in data.get("sections", []):
        for row in section.get("rows", []):
            label = row.get("label")
            if label:
                metrics[label] = row

    stock = get_stock_data(ticker) or {}
    return {
        "ticker": ticker,
        "company_name": data.get("company_name") or ticker,
        "sector": data.get("sector"),
        "industry": data.get("industry"),
        "score": data.get("normalized_score"),
        "earned": data.get("total_earned"),
        "possible": data.get("total_possible"),
        "verdict": data.get("verdict"),
        "verdict_class": data.get("verdict_class"),
        "price": stock.get("current_price") or stock.get("price"),
        "change_pct": stock.get("change_pct"),
        "roe": data.get("roe"),
        "roic": data.get("roic"),
        "insider_pct": data.get("insider_pct"),
        "metrics": metrics,
        "red_flags": data.get("red_flags", []),
        "missing_fields": data.get("missing_fields", []),
        "error": data.get("error"),
    }


@app.route("/compare")
def stock_compare():
    """Compare two or three stocks using the existing fundamentals pipeline."""
    raw = request.args.get("symbols", "")
    symbols = []
    for value in raw.split(","):
        ticker = value.strip().upper()
        if ticker and re.fullmatch(r"[A-Z][A-Z0-9.-]{0,7}", ticker) and ticker not in symbols:
            symbols.append(ticker)
    symbols = symbols[:3]

    comparisons = []
    errors = []
    for ticker in symbols:
        try:
            snapshot = _comparison_snapshot(ticker)
            if snapshot.get("error"):
                errors.append(f"{ticker}: {snapshot['error']}")
            comparisons.append(snapshot)
        except Exception as exc:
            logger.exception("stock_compare error for %s", ticker)
            errors.append(f"{ticker}: comparison data is temporarily unavailable.")

    return render_template(
        "compare.html", symbols=symbols, comparisons=comparisons, errors=errors,
    )


@app.route("/api/research/ask", methods=["POST"])
def api_research_ask():
    """Call Anthropic with web_search enabled and return the answer."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"ok": False, "error": "ANTHROPIC_API_KEY is not configured on this server."}), 503

    data = request.get_json(force=True, silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "question is required"}), 400

    try:
        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
            messages=[{"role": "user", "content": question}],
        )
        # Collect all text blocks from the response
        answer_parts = []
        for block in response.content:
            if hasattr(block, "text"):
                answer_parts.append(block.text)
        answer = "\n\n".join(answer_parts).strip() or "(no text response)"
        return jsonify({"ok": True, "answer": answer})
    except Exception as exc:
        logger.exception("Research ask error")
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── Tradestaar AI — grounded, account-aware research assistant ────────
def _tradestaar_ai_context(user_id: int, question: str = "") -> dict:
    """Build a compact, cache-first context packet scoped to one user."""
    active_id = get_active_wl_id()
    watchlist = get_watchlist_stocks(active_id) if active_id else []
    data_map = {str(row.get("ticker", "")).upper(): row for row in get_all_stock_data()}
    ignored = {"A", "AI", "I", "THE", "FOR", "AND", "OR", "MY", "TO", "IS"}
    requested = [t for t in re.findall(r"\b[A-Z]{1,5}\b", question.upper()) if t not in ignored]
    focus = list(dict.fromkeys(requested + watchlist))[:8]
    stocks = []
    for ticker in focus:
        row = data_map.get(ticker) or get_stock_data(ticker) or {}
        if row:
            stocks.append({
                "ticker": ticker,
                "price": row.get("current_price") or row.get("price") or row.get("close"),
                "change_pct": row.get("change_pct") or row.get("daily_change_pct"),
                "relative_volume": row.get("relative_volume") or row.get("rel_volume"),
                "grade": row.get("swing_grade") or row.get("grade"),
                "setup": row.get("setup_type") or row.get("swing_setup"),
                "earnings_date": row.get("earnings_date"),
                "updated_at": row.get("updated_at") or row.get("last_updated"),
            })
    account = get_paper_account(user_id)
    positions = []
    for position in get_paper_positions(user_id)[:10]:
        quote = data_map.get(position["ticker"]) or {}
        positions.append({
            "ticker": position["ticker"], "shares": position["quantity"],
            "average_cost": position["avg_price"],
            "cached_price": quote.get("current_price") or quote.get("price") or quote.get("close"),
        })
    headlines = []
    try:
        summary = _intel.get_intel_summary() or {}
        for item in (summary.get("market_news") or summary.get("news") or [])[:12]:
            ticker = str(item.get("ticker") or "").upper()
            if not focus or not ticker or ticker in focus:
                headlines.append({"ticker": ticker or "MARKET", "headline": item.get("headline"),
                                  "source": item.get("source"),
                                  "published": item.get("published") or item.get("published_at") or item.get("time")})
            if len(headlines) == 6:
                break
    except Exception as exc:
        logger.debug("Tradestaar AI news context unavailable: %s", exc)
    market = _get_mkt_ctx()
    return {
        "as_of": _et_now().isoformat(), "watchlist": watchlist[:20], "stocks": stocks,
        "paper_account": {"cash_balance": account.get("cash_balance"), "positions": positions},
        "market": {key: market.get(key) for key in
                   ("regime", "regime_label", "spy_1d_pct", "qqq_1d_pct", "vix_level")},
        "headlines": headlines,
    }


@app.route("/ai")
def tradestaar_ai():
    """Mobile-first Tradestaar AI research workspace."""
    return render_template("tradestaar_ai.html", ai_context=_tradestaar_ai_context(current_user_id()))


@app.route("/api/ask", methods=["POST"])
def api_ask():
    """Tradestaar AI with bounded history and verified in-app context."""
    from openai import OpenAI as _OpenAI
    import datetime as _dt

    data     = request.get_json(force=True, silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "No question provided."}), 400
    if len(question) > 1200:
        return jsonify({"ok": False, "error": "Question must be 1,200 characters or less."}), 400
    if not os.environ.get("NEBIUS_API_KEY"):
        return jsonify({"ok": False, "error": "Tradestaar AI is not configured on this server."}), 503
    safe_history = []
    history = data.get("history") or []
    if isinstance(history, list):
        for item in history[-6:]:
            if isinstance(item, dict) and item.get("role") in ("user", "assistant"):
                content = str(item.get("content") or "").strip()[:2000]
                if content:
                    safe_history.append({"role": item["role"], "content": content})
    grounded = _tradestaar_ai_context(current_user_id(), question)

    # ── Build earnings context from live calendar ──────────────────────────
    try:
        from intel_engine import fetch_earnings_calendar as _fetch_earn
        cal   = _fetch_earn()
        today = _dt.date.today()

        def _fmt_bucket(items):
            if not items:
                return "  (none)"
            lines = []
            for e in items:
                name = e.get("company_name") or e.get("ticker")
                lines.append(
                    f"  • {e['ticker']} ({name}) — {e.get('date','?')} "
                    f"{e.get('time_label','TBD')} ({e.get('days_away','?')}d away)"
                )
            return "\n".join(lines)

        earn_ctx = (
            f"TODAY IS: {today.strftime('%A, %B %d, %Y')}\n\n"
            f"CONFIRMED UPCOMING EARNINGS (next 21 days from live calendar):\n"
            f"TODAY:\n{_fmt_bucket(cal.get('today', []))}\n"
            f"TOMORROW:\n{_fmt_bucket(cal.get('tomorrow', []))}\n"
            f"THIS WEEK:\n{_fmt_bucket(cal.get('this_week', []))}\n"
            f"NEXT 3 WEEKS:\n{_fmt_bucket(cal.get('coming_up', []))}\n"
        )
    except Exception:
        earn_ctx = f"TODAY IS: {_dt.date.today().strftime('%A, %B %d, %Y')}\n(Live calendar unavailable)"

    system_prompt = (
        "You are Tradestaar AI — the sharp, knowledgeable trading assistant inside Tradestaar Elite.\n\n"
        "Use the dated VERIFIED APP CONTEXT and confirmed calendar below. Cached values may be delayed.\n\n"
        "TRUTH AND SAFETY RULES:\n"
        "- Never invent a price, filing, headline, position, rating, fundamental, or earnings date.\n"
        "- Prefer VERIFIED APP CONTEXT. If a fact is absent, say it is unavailable.\n"
        "- Label interpretations and educational examples. Never promise returns.\n"
        "- Never reveal data belonging to another account.\n\n"
        "HOW TO ANSWER:\n\n"
        "EARNINGS DATE QUESTIONS:\n"
        "- First check the confirmed calendar above. If the ticker is listed, give that exact date.\n"
        "- If NOT in the calendar, say the verified date is unavailable. Do not guess a date.\n\n"
        "FUNDAMENTALS QUESTIONS:\n"
        "- Use only figures present in VERIFIED APP CONTEXT. Direct users to Fundamentals when absent.\n\n"
        "TRADE SETUP QUESTIONS:\n"
        "- Use technical reasoning: EMA alignment, VWAP relationship, structure, volume, R/R.\n\n"
        "STYLE: Direct, specific, no filler, no generic non-answers. Always give a real answer.\n\n"
        + earn_ctx + "\nVERIFIED APP CONTEXT (JSON):\n" + _json.dumps(grounded, default=str)
    )

    try:
        client = _OpenAI(
            base_url="https://api.tokenfactory.nebius.com/v1/",
            api_key=os.environ.get("NEBIUS_API_KEY"),
        )
        resp = client.chat.completions.create(
            model="meta-llama/Llama-3.3-70B-Instruct",
            max_tokens=600,
            temperature=0.3,
            messages=[{"role": "system", "content": system_prompt}, *safe_history,
                      {"role": "user", "content": question}],
        )
        answer = (resp.choices[0].message.content or "").strip() or "No response."
        return jsonify({"ok": True, "answer": answer, "context": grounded,
                        "disclaimer": "AI research can be wrong. Verify before trading."})
    except Exception as exc:
        logger.exception("Tradestaar AI ask error")
        if not os.environ.get("NEBIUS_API_KEY"):
            return jsonify({"ok": False, "error": "Tradestaar AI is not configured on this server."}), 503
        return jsonify({"ok": False, "error": "Tradestaar AI is temporarily unavailable."}), 502


@app.route("/api/study-log", methods=["GET"])
def api_study_log_get():
    entries = get_study_log(current_user_id())
    return jsonify({"ok": True, "entries": entries})


@app.route("/api/study-log", methods=["POST"])
def api_study_log_save():
    data = request.get_json(force=True, silent=True) or {}
    question = (data.get("question") or "").strip()
    answer = (data.get("answer") or "").strip()
    if not question or not answer:
        return jsonify({"ok": False, "error": "question and answer are required"}), 400
    entry_id = save_study_log_entry(current_user_id(), question, answer)
    return jsonify({"ok": True, "id": entry_id})


@app.route("/api/study-log/<int:entry_id>", methods=["DELETE"])
def api_study_log_delete(entry_id):
    delete_study_log_entry(current_user_id(), entry_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"\nTradestaar Elite running on port {port}...\n")
    app.run(host="0.0.0.0", port=port, debug=False)
