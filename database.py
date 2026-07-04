"""
database.py — Database helpers for Rockkstaar Trade Assistant.

Supports both SQLite (local dev, DATABASE_URL not set) and
PostgreSQL (production on Render, DATABASE_URL set).

The connection wrapper in this module auto-translates:
  - ? positional params  →  %s
  - :name named params   →  %(name)s
  - INSERT OR IGNORE     →  INSERT … ON CONFLICT DO NOTHING
  - AUTOINCREMENT        →  SERIAL PRIMARY KEY  (DDL only)
  - ADD COLUMN           →  ADD COLUMN IF NOT EXISTS  (PG only)

All caller code above this module is unchanged.
"""

import os
import re
import logging
import sqlite3
import json
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash


def _et_now() -> datetime:
    try:
        import zoneinfo
        return datetime.now(zoneinfo.ZoneInfo("America/New_York"))
    except Exception:
        from datetime import timezone
        return datetime.now(timezone(timedelta(hours=-4)))

logger = logging.getLogger(__name__)

# ─── Backend selection ────────────────────────────────────────────────────────

DB_PATH = "rockkstaar.db"
_DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Render (and Heroku) supply postgres:// but psycopg2 2.9+ requires postgresql://
if _DATABASE_URL.startswith("postgres://"):
    _DATABASE_URL = _DATABASE_URL.replace("postgres://", "postgresql://", 1)

_USE_POSTGRES = bool(_DATABASE_URL)

# Guard the psycopg2 import so a missing binary doesn't crash the whole app.
psycopg2 = None  # type: ignore[assignment]
if _USE_POSTGRES:
    try:
        import psycopg2            # type: ignore[no-redef]
        import psycopg2.extras     # type: ignore[no-redef]
        logger.info("DB  backend=postgresql")
    except ImportError as _pg_err:
        logger.error("psycopg2 not available: %s — falling back to SQLite", _pg_err)
        _USE_POSTGRES = False
else:
    logger.info("DB  backend=sqlite  path=%s", DB_PATH)

# ─── SQL translation ──────────────────────────────────────────────────────────

_INSERT_OR_IGNORE_RE = re.compile(r"\bINSERT\s+OR\s+IGNORE\b", re.IGNORECASE)
_INSERT_START_RE     = re.compile(r"^\s*INSERT\b",              re.IGNORECASE)


def _adapt_sql(sql: str, params=None):
    """Translate SQLite-style SQL and params to psycopg2 style (PG only)."""
    if not _USE_POSTGRES:
        return sql, params

    # Named params  :name  →  %(name)s  (avoid matching :: PG cast syntax)
    if isinstance(params, dict):
        sql = re.sub(r"(?<![:])[:]([A-Za-z_]\w*)", r"%(\1)s", sql)
    # Positional params  ?  →  %s
    elif params is not None:
        sql = sql.replace("?", "%s")

    # INSERT OR IGNORE  →  INSERT … ON CONFLICT DO NOTHING
    if _INSERT_OR_IGNORE_RE.search(sql):
        sql = _INSERT_OR_IGNORE_RE.sub("INSERT", sql)
        sql = sql.rstrip().rstrip(";") + "\nON CONFLICT DO NOTHING"

    return sql, params


def _adapt_ddl(sql: str) -> str:
    """Translate SQLite DDL to PostgreSQL DDL (CREATE TABLE statements)."""
    if not _USE_POSTGRES:
        return sql
    sql = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "SERIAL PRIMARY KEY",
        sql, flags=re.IGNORECASE,
    )
    return sql


def _normalize_value(v):
    """Convert a single value to a native Python type safe for both SQLite and psycopg2.

    Two classes of problem are handled here:

    1. NumPy scalars (numpy.bool_, numpy.int*, numpy.float*) — psycopg2 does
       not register adapters for them and raises "can't adapt type".

    2. Python bool — psycopg2 maps Python bool → PostgreSQL BOOLEAN, but every
       flag column in our schema is INTEGER (e.g. momentum_breakout INTEGER
       DEFAULT 0).  Passing True/False causes a DatatypeMismatch error.
       Converting bool → int (True→1, False→0) matches the INTEGER schema and
       is safe for SQLite too (which stores booleans as 0/1 anyway).
    """
    # None, int, float, str, bytes need no conversion
    if v is None or type(v) in (int, float, str, bytes):
        return v
    # Python bool: convert to int so psycopg2 sends 0/1, not BOOLEAN
    if type(v) is bool:
        return int(v)
    # NumPy scalars — lazy check so numpy is not a hard dependency
    type_name = type(v).__name__
    module = getattr(type(v), "__module__", "")
    if module.startswith("numpy"):
        if type_name.startswith("bool"):
            return int(v)           # numpy.bool_ → int (same reason as above)
        if type_name.startswith(("int", "uint")):
            return int(v)
        if type_name.startswith("float"):
            return float(v)
        # Generic fallback for other numpy scalars (e.g. numpy.str_)
        return v.item()
    return v


def _normalize_params(params):
    """Recursively normalise query params (dict or sequence) to native Python types."""
    if params is None:
        return None
    if isinstance(params, dict):
        return {k: _normalize_value(v) for k, v in params.items()}
    return tuple(_normalize_value(v) for v in params)


# ─── Connection wrapper ───────────────────────────────────────────────────────

class _Cursor:
    """Normalises sqlite3 / psycopg2 cursor interfaces."""

    def __init__(self, raw_cursor):
        self._c = raw_cursor
        self._pg_lastrowid = None  # populated after RETURNING id

    def fetchone(self):
        row = self._c.fetchone()
        if row is None:
            return None
        return dict(row) if _USE_POSTGRES else row  # sqlite3.Row supports both

    def fetchall(self):
        rows = self._c.fetchall()
        return [dict(r) for r in rows] if _USE_POSTGRES else rows

    @property
    def lastrowid(self):
        return self._pg_lastrowid if _USE_POSTGRES else self._c.lastrowid

    def __iter__(self):
        return iter(self.fetchall())


class _Conn:
    """Wraps sqlite3 / psycopg2 connection with a uniform execute() interface."""

    def __init__(self, raw_conn):
        self._conn = raw_conn

    def execute(self, sql: str, params=None, returning_id: bool = False) -> _Cursor:
        """Execute a query.

        Pass returning_id=True only when the caller needs cursor.lastrowid on
        PostgreSQL.  This appends RETURNING id to the SQL, which requires the
        target table to have an id column.  Do NOT pass returning_id=True for
        tables whose primary key is not named id (e.g. settings.key).
        """
        sql, params = _adapt_sql(sql, params)
        params = _normalize_params(params)

        # Append RETURNING id only when the caller explicitly requests it.
        # The old approach (appending to every INSERT) broke tables without
        # an id column (e.g. settings) with UndefinedColumn errors.
        if _USE_POSTGRES and returning_id:
            sql = sql.rstrip().rstrip(";") + "\nRETURNING id"

        if _USE_POSTGRES:
            raw_cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            raw_cur = self._conn.cursor()

        try:
            if params is None:
                raw_cur.execute(sql)
            else:
                raw_cur.execute(sql, params)
        except Exception:
            logger.debug("DB execute failed  sql=%s", sql[:200])
            raise

        cursor = _Cursor(raw_cur)

        # Pre-fetch the returned id so cursor.lastrowid works on PostgreSQL
        if _USE_POSTGRES and returning_id:
            try:
                row = raw_cur.fetchone()
                if row:
                    cursor._pg_lastrowid = (
                        row["id"] if isinstance(row, dict) else row[0]
                    )
            except Exception:
                pass

        return cursor

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __del__(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            try:
                self._conn.rollback()
            except Exception:
                pass
        self.close()
        return False


def get_db() -> _Conn:
    """Return a wrapped database connection (PG or SQLite based on env)."""
    if _USE_POSTGRES and psycopg2 is not None:
        try:
            conn = psycopg2.connect(_DATABASE_URL, connect_timeout=10)
            return _Conn(conn)
        except Exception as exc:
            logger.error("DB  PostgreSQL connection failed: %s", exc)
            raise
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return _Conn(conn)


DEFAULT_WATCHLISTS = [
    "A+ READY",
    "SETUPS FORMING",
    "TREND WATCH",
    "EXTENDED / CHASE ZONE",
    "AVOID / BLOCKED",
]


# ---------------------------------------------------------------------------
# App settings helpers
# ---------------------------------------------------------------------------

def get_setting(key: str):
    """Return the value for a settings key, or None if not set."""
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def set_setting(key: str, value: str):
    """Upsert a settings key/value pair."""
    conn = get_db()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value)
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def init_db():
    """Create all tables if they don't exist, run migrations, seed defaults."""
    conn = get_db()
    cursor = conn  # _Conn.execute() is a superset of cursor.execute()

    # App settings — key/value store for persistent flags (e.g. demo_seeded)
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """))

    # Users — one row per account (admin creates additional accounts; no self-registration)
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    UNIQUE NOT NULL,
            password_hash TEXT    NOT NULL,
            created_at    TEXT    NOT NULL,
            is_admin      INTEGER NOT NULL DEFAULT 0
        )
    """))

    # Per-user key/value settings — risk prefs, Schwab tokens, etc.
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER NOT NULL,
            key     TEXT    NOT NULL,
            value   TEXT,
            PRIMARY KEY (user_id, key)
        )
    """))

    # Legacy watchlist table — kept for migration reading only, no longer written
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT UNIQUE NOT NULL,
            added_date TEXT NOT NULL
        )
    """))

    # Named watchlists (user_id scopes lists to the owning user)
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS watchlists (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL DEFAULT 1,
            name       TEXT    NOT NULL,
            created_at TEXT    NOT NULL
        )
    """))

    # Watchlist membership (many-to-many: watchlist ↔ ticker)
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS watchlist_stocks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            watchlist_id INTEGER NOT NULL,
            ticker       TEXT    NOT NULL,
            added_date   TEXT    NOT NULL,
            UNIQUE(watchlist_id, ticker),
            FOREIGN KEY(watchlist_id) REFERENCES watchlists(id)
        )
    """))

    # Stock data: stores enriched data for each ticker (refreshed on demand)
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS stock_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT UNIQUE NOT NULL,
            current_price REAL,
            prev_close REAL,
            gap_pct REAL,
            premarket_high REAL,
            premarket_low REAL,
            prev_day_high REAL,
            prev_day_low REAL,
            avg_volume INTEGER,
            rel_volume REAL,
            catalyst_summary TEXT,
            news_headlines TEXT,
            earnings_date TEXT,
            trade_bias TEXT,
            catalyst_score      INTEGER,
            catalyst_reason     TEXT,
            catalyst_confidence TEXT,
            momentum_score      INTEGER,
            momentum_reason     TEXT,
            momentum_confidence TEXT,
            order_block         TEXT,
            entry_quality       TEXT,
            orb_high            REAL,
            orb_low             REAL,
            orb_status          TEXT,
            orb_ready           TEXT,
            exec_state          TEXT,
            setup_score         INTEGER,
            setup_reason        TEXT,
            setup_confidence    TEXT,
            setup_type          TEXT,
            last_updated        TEXT
        )
    """))

    # Safe migration: add missing columns (IF NOT EXISTS for PG; try/except for SQLite)
    _new_columns = [
        ("catalyst_score",           "INTEGER"),
        ("catalyst_reason",          "TEXT"),
        ("catalyst_confidence",      "TEXT"),
        ("momentum_score",           "INTEGER"),
        ("momentum_reason",          "TEXT"),
        ("momentum_confidence",      "TEXT"),
        ("order_block",              "TEXT"),
        ("entry_quality",            "TEXT"),
        ("orb_high",                 "REAL"),
        ("orb_low",                  "REAL"),
        ("orb_status",               "TEXT"),
        ("orb_ready",                "TEXT"),
        ("exec_state",               "TEXT"),
        ("setup_score",              "INTEGER"),
        ("setup_reason",             "TEXT"),
        ("setup_confidence",         "TEXT"),
        ("setup_type",               "TEXT"),
        ("triggered_at",             "TEXT"),
        ("orb_phase",                "TEXT"),
        ("auto_classify",            "INTEGER DEFAULT 1"),
        ("classify_reason",          "TEXT"),
        ("prev_close_date",          "TEXT"),
        ("vwap",                     "REAL"),
        ("momentum_breakout",        "INTEGER DEFAULT 0"),
        ("candles_above_orb",        "INTEGER DEFAULT 0"),
        ("momentum_runner",          "INTEGER DEFAULT 0"),
        ("entry_note",               "TEXT"),
        ("position_size",            "TEXT"),
        ("orb_hold",                 "INTEGER DEFAULT 0"),
        ("trend_structure",          "INTEGER DEFAULT 0"),
        ("higher_highs",             "INTEGER DEFAULT 0"),
        ("higher_lows",              "INTEGER DEFAULT 0"),
        ("strong_candle_bodies",     "INTEGER DEFAULT 0"),
        ("price_above_vwap",         "INTEGER DEFAULT 0"),
        ("structure_momentum_score", "INTEGER DEFAULT 0"),
        ("catalyst_category",        "TEXT"),
        ("headlines_fetched_at",     "TEXT"),
        # Supply / demand zone fields (v1)
        ("nearest_supply_top",       "REAL"),
        ("nearest_supply_bottom",    "REAL"),
        ("nearest_demand_top",       "REAL"),
        ("nearest_demand_bottom",    "REAL"),
        ("distance_to_supply_pct",   "REAL"),
        ("distance_to_demand_pct",   "REAL"),
        ("zone_location",            "TEXT"),
        ("bullish_order_block",      "TEXT"),
        ("bearish_order_block",      "TEXT"),
        ("in_supply_zone",           "INTEGER DEFAULT 0"),
        ("in_demand_zone",           "INTEGER DEFAULT 0"),
        ("zones_fetched_at",         "TEXT"),
        # Supply/demand zone fields (v2 institutional)
        ("zones_json",               "TEXT"),
        ("demand_zone_grade",        "TEXT"),
        ("supply_zone_grade",        "TEXT"),
        ("zone_ai_setup",            "TEXT"),
        ("zone_ai_reason",           "TEXT"),
        ("zone_probability",         "INTEGER"),
        ("smart_money_json",         "TEXT"),
        ("fvg_bullish",              "INTEGER DEFAULT 0"),
        ("fvg_bearish",              "INTEGER DEFAULT 0"),
        # Swing trading fields (v1)
        ("ema_20_daily",             "REAL"),
        ("ema_50_daily",             "REAL"),
        ("ema_200_daily",            "REAL"),
        ("pct_from_ema20",           "REAL"),
        ("pct_from_ema50",           "REAL"),
        ("daily_trend",              "TEXT"),
        ("daily_hh_hl",              "INTEGER DEFAULT 0"),
        ("daily_lh_ll",              "INTEGER DEFAULT 0"),
        ("fib_high",                 "REAL"),
        ("fib_low",                  "REAL"),
        ("fib_50",                   "REAL"),
        ("fib_618",                  "REAL"),
        ("swing_score",              "INTEGER"),
        ("swing_reason",             "TEXT"),
        ("swing_confidence",         "TEXT"),
        ("swing_setup_type",         "TEXT"),
        ("swing_status",             "TEXT"),
        ("entry_zone_low",           "REAL"),
        ("entry_zone_high",          "REAL"),
        ("stop_level",               "REAL"),
        ("target_1",                 "REAL"),
        ("target_2",                 "REAL"),
        ("risk_reward",              "REAL"),
        ("swing_data_fetched_at",    "TEXT"),
        # 4H / 15m fields for the 7-category weighted swing score
        ("h4_trend",         "TEXT"),
        ("h4_ema20",         "REAL"),
        ("h4_ema50",         "REAL"),
        ("h4_hh_hl",         "INTEGER DEFAULT 0"),
        ("m15_higher_low",   "INTEGER DEFAULT 0"),
        ("m15_confirmation", "INTEGER DEFAULT 0"),
        # Ticker state: loading | ready | error | stale
        ("ticker_state",     "TEXT DEFAULT 'ready'"),
        # Active Swing Fibonacci engine — extended levels and metadata
        ("fib_236",          "REAL"),
        ("fib_382",          "REAL"),
        ("fib_65",           "REAL"),
        ("fib_705",          "REAL"),
        ("fib_786",          "REAL"),
        ("fib_confidence",   "REAL"),
        ("fib_direction",    "TEXT"),
        ("fib_mode",         "TEXT"),
        # Macro structure fib (20-bar simple swing — institutional context)
        ("macro_fib_high",   "REAL"),
        ("macro_fib_low",    "REAL"),
        ("macro_fib_50",     "REAL"),
        ("macro_fib_618",    "REAL"),
        # 4H timeframe fib levels (from 1h bar data)
        ("h4_fib_high",      "REAL"),
        ("h4_fib_low",       "REAL"),
        ("h4_fib_50",        "REAL"),
        ("h4_fib_618",       "REAL"),
        # Relative strength & sector rotation (market_engine.py)
        ("rs_score",         "INTEGER"),
        ("rs_vs_qqq",        "REAL"),
        ("sector_etf",       "TEXT"),
        # Company profile (name / sector / industry / description) — refreshed rarely
        ("company_name",         "TEXT"),
        ("company_sector",       "TEXT"),
        ("company_industry",     "TEXT"),
        ("company_description",  "TEXT"),
        ("company_logo_url",     "TEXT"),
        ("profile_fetched_at",   "TEXT"),
    ]
    for col, col_type in _new_columns:
        if _USE_POSTGRES:
            cursor.execute(
                f"ALTER TABLE stock_data ADD COLUMN IF NOT EXISTS {col} {col_type}"
            )
        else:
            try:
                cursor.execute(
                    f"ALTER TABLE stock_data ADD COLUMN {col} {col_type}"
                )
            except sqlite3.OperationalError:
                pass  # Column already exists

    # Notes: user's trade plan notes per ticker (scoped per user)
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS notes (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL DEFAULT 1,
            ticker    TEXT    NOT NULL,
            note_text TEXT,
            updated_at TEXT
        )
    """))

    # Trade journal: one row per executed trade (scoped per user)
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS journal (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL DEFAULT 1,
            ticker         TEXT NOT NULL,
            trade_date     TEXT NOT NULL,
            direction      TEXT,
            entry_price    REAL NOT NULL,
            exit_price     REAL NOT NULL,
            shares         INTEGER,
            setup_type     TEXT,
            momentum_score INTEGER,
            pnl_pct        REAL,
            result         TEXT,
            notes          TEXT,
            created_at     TEXT
        )
    """))

    # Pre-market plans: structured trade plan fields per ticker (scoped per user)
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS trade_plans (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL DEFAULT 1,
            ticker       TEXT    NOT NULL,
            plan_bias    TEXT,
            entry_level  REAL,
            stop_loss    REAL,
            target_price REAL,
            updated_at   TEXT
        )
    """))

    # Schwab trade import tracking — prevents duplicate imports (scoped per user)
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS schwab_imports (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL DEFAULT 1,
            import_key    TEXT    NOT NULL,
            journal_id    INTEGER,
            ticker        TEXT,
            trade_date    TEXT,
            imported_at   TEXT
        )
    """))

    # Daily trading sessions — one row per date per user, tracks trades/losses
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS daily_sessions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL DEFAULT 1,
            session_date TEXT    NOT NULL,
            locked       INTEGER DEFAULT 0,
            lock_reason  TEXT,
            updated_at   TEXT
        )
    """))

    # Journal options/risk columns (safe migration)
    _journal_new_columns = [
        ("trade_mode",     "TEXT"),
        ("option_side",    "TEXT"),
        ("option_premium", "REAL"),
        ("contracts",      "INTEGER"),
        ("stop_price",     "REAL"),
        ("is_aplus_setup", "INTEGER DEFAULT 0"),
    ]
    for col, col_type in _journal_new_columns:
        if _USE_POSTGRES:
            cursor.execute(
                f"ALTER TABLE journal ADD COLUMN IF NOT EXISTS {col} {col_type}"
            )
        else:
            try:
                cursor.execute(f"ALTER TABLE journal ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass

    # Schwab OAuth tokens — stored as individual settings keys (prefixed schwab_)
    # No dedicated table needed: access_token, refresh_token, expires_at, rt_expires_at
    # are written via set_setting("schwab_*", ...) so the existing settings table suffices.
    # This comment is the migration marker; no DDL required.

    # Scanner alerts — persisted momentum/breakout/volume events from the background scanner
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS scanner_alerts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker     TEXT    NOT NULL,
            alert_type TEXT    NOT NULL,
            message    TEXT    NOT NULL,
            severity   TEXT    NOT NULL DEFAULT 'medium',
            seen       INTEGER NOT NULL DEFAULT 0,
            created_at TEXT    NOT NULL
        )
    """))

    # Setup outcome tracking for adaptive AI learning (scoped per user)
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS setup_outcomes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL DEFAULT 1,
            ticker     TEXT    NOT NULL,
            setup_type TEXT    NOT NULL,
            pattern    TEXT,
            outcome    TEXT    NOT NULL,
            regime     TEXT,
            prob_score INTEGER,
            notes      TEXT,
            created_at TEXT    NOT NULL
        )
    """))

    # Nasdaq-100 constituent snapshots — one row per (date, ticker)
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS ndx_constituents (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT    NOT NULL,
            ticker        TEXT    NOT NULL,
            weight_pct    REAL,
            company_name  TEXT,
            UNIQUE(snapshot_date, ticker)
        )
    """))

    # Nasdaq-100 constituent change events (additions / removals)
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS ndx_changes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker        TEXT    NOT NULL,
            change_type   TEXT    NOT NULL,
            detected_date TEXT    NOT NULL,
            company_name  TEXT,
            created_at    TEXT    NOT NULL
        )
    """))

    # AI research study log — saved Q&A pairs per user
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS study_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            question   TEXT    NOT NULL,
            answer     TEXT    NOT NULL,
            created_at TEXT    NOT NULL
        )
    """))

    # Fundamentals cache — stores yfinance fundamental data per ticker, 24-hr TTL
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS fundamentals_cache (
            ticker     TEXT PRIMARY KEY,
            data_json  TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
    """))

    # AI Briefings cache — stores daily Nebius AI morning briefing, one row per date
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS ai_briefings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            date          TEXT    NOT NULL UNIQUE,
            json_response TEXT    NOT NULL,
            created_at    TEXT    NOT NULL
        )
    """))

    # AI Score Narrations — one row per ticker+date+score_key combination
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS score_narrations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker        TEXT    NOT NULL,
            date          TEXT    NOT NULL,
            score_key     TEXT    NOT NULL,
            json_response TEXT    NOT NULL,
            created_at    TEXT    NOT NULL,
            UNIQUE (ticker, date, score_key)
        )
    """))

    # AI Journal Summaries — one row per week
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS journal_summaries (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            week_key      TEXT    NOT NULL UNIQUE,
            json_response TEXT    NOT NULL,
            created_at    TEXT    NOT NULL
        )
    """))

    # AI Earnings Digests — one row per calendar date
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS earnings_digests (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            date          TEXT    NOT NULL UNIQUE,
            json_response TEXT    NOT NULL,
            created_at    TEXT    NOT NULL
        )
    """))

    # ── Multi-user migration: add user_id columns to all user-data tables ───
    _user_tables = [
        "watchlists", "notes", "trade_plans", "journal",
        "daily_sessions", "schwab_imports", "setup_outcomes",
    ]
    for _tbl in _user_tables:
        if _USE_POSTGRES:
            cursor.execute(
                f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS user_id INTEGER"
            )
        else:
            try:
                cursor.execute(f"ALTER TABLE {_tbl} ADD COLUMN user_id INTEGER")
            except sqlite3.OperationalError:
                pass  # Column already exists

    # ── Create admin user from APP_PASSWORD if users table is empty ──────────
    admin_count = cursor.execute(
        "SELECT COUNT(*) AS cnt FROM users"
    ).fetchone()["cnt"]
    admin_id = 1  # will always be 1 on a fresh table
    if admin_count == 0:
        _admin_pass = (
            os.environ.get("APP_PASSWORD") or
            os.environ.get("LOGIN_PASS") or
            "changeme"
        )
        _admin_hash = generate_password_hash(_admin_pass)
        _now_u = datetime.now().isoformat()
        cursor.execute(
            "INSERT INTO users (username, password_hash, created_at, is_admin) "
            "VALUES (?, ?, ?, 1)",
            ("rockkstaar", _admin_hash, _now_u),
        )
        # Fetch the id in case SERIAL doesn't start at 1
        admin_row = cursor.execute(
            "SELECT id FROM users WHERE username = 'rockkstaar'"
        ).fetchone()
        admin_id = admin_row["id"] if admin_row else 1
        logger.info("DB  admin user created  username=rockkstaar  id=%s", admin_id)
    else:
        admin_row = cursor.execute(
            "SELECT id FROM users ORDER BY id LIMIT 1"
        ).fetchone()
        admin_id = admin_row["id"] if admin_row else 1

    # ── Backfill user_id for all existing rows (assign to admin) ────────────
    for _tbl in _user_tables:
        cursor.execute(f"UPDATE {_tbl} SET user_id = ? WHERE user_id IS NULL", (admin_id,))

    # ── Migrate global risk settings to user_settings for admin ─────────────
    _risk_keys = (
        "trading_mode", "account_size", "risk_pct",
        "max_trades_per_day", "max_daily_loss_pct", "stop_after_2_losses",
    )
    for _rk in _risk_keys:
        _rv = cursor.execute(
            "SELECT value FROM settings WHERE key = ?", (_rk,)
        ).fetchone()
        if _rv:
            _existing_us = cursor.execute(
                "SELECT 1 FROM user_settings WHERE user_id = ? AND key = ?",
                (admin_id, _rk)
            ).fetchone()
            if not _existing_us:
                cursor.execute(
                    "INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?)",
                    (admin_id, _rk, _rv["value"])
                )

    # ── Migrate Schwab tokens from global settings to user_settings ──────────
    _schwab_keys = (
        "schwab_access_token", "schwab_refresh_token",
        "schwab_expires_at",   "schwab_rt_expires_at",
    )
    for _sk in _schwab_keys:
        _sv = cursor.execute(
            "SELECT value FROM settings WHERE key = ?", (_sk,)
        ).fetchone()
        if _sv and _sv["value"]:
            _existing_us = cursor.execute(
                "SELECT 1 FROM user_settings WHERE user_id = ? AND key = ?",
                (admin_id, _sk)
            ).fetchone()
            if not _existing_us:
                cursor.execute(
                    "INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?)",
                    (admin_id, _sk, _sv["value"])
                )

    # ── For PostgreSQL: update unique constraints to be (user_id, col) ───────
    if _USE_POSTGRES:
        _pg_constraint_ops = [
            # (table, old_constraint, new_cols)
            ("watchlists",     "watchlists_name_key",              "user_id, name"),
            ("notes",          "notes_ticker_key",                 "user_id, ticker"),
            ("trade_plans",    "trade_plans_ticker_key",           "user_id, ticker"),
            ("daily_sessions", "daily_sessions_session_date_key",  "user_id, session_date"),
            ("schwab_imports", "schwab_imports_import_key_key",    "user_id, import_key"),
        ]
        for _tbl, _old_con, _new_cols in _pg_constraint_ops:
            _new_con = f"{_tbl}_user_unique"
            _con_exists = cursor.execute(
                "SELECT 1 FROM pg_catalog.pg_constraint WHERE conname = ?",
                (_new_con,)
            ).fetchone()
            if not _con_exists:
                cursor.execute(
                    f"ALTER TABLE {_tbl} DROP CONSTRAINT IF EXISTS {_old_con}"
                )
                cursor.execute(
                    f"ALTER TABLE {_tbl} ADD CONSTRAINT {_new_con} "
                    f"UNIQUE ({_new_cols})"
                )

    # ── Seed default watchlists for admin user on first run ──────────────────
    now_iso = datetime.now().isoformat()
    wl_count = cursor.execute(
        "SELECT COUNT(*) AS cnt FROM watchlists WHERE user_id = ?", (admin_id,)
    ).fetchone()["cnt"]

    if wl_count == 0:
        for name in DEFAULT_WATCHLISTS:
            existing_wl = cursor.execute(
                "SELECT id FROM watchlists WHERE user_id = ? AND name = ?",
                (admin_id, name)
            ).fetchone()
            if not existing_wl:
                cursor.execute(
                    "INSERT INTO watchlists (user_id, name, created_at) VALUES (?, ?, ?)",
                    (admin_id, name, now_iso)
                )
        # Migrate legacy watchlist table data into first list
        first_row = cursor.execute(
            "SELECT id FROM watchlists WHERE user_id = ? ORDER BY id LIMIT 1",
            (admin_id,)
        ).fetchone()
        if first_row:
            first_id = first_row["id"]
            old_rows = cursor.execute(
                "SELECT ticker, added_date FROM watchlist"
            ).fetchall()
            for row in old_rows:
                cursor.execute(
                    "INSERT OR IGNORE INTO watchlist_stocks "
                    "(watchlist_id, ticker, added_date) VALUES (?, ?, ?)",
                    (first_id, row["ticker"], row["added_date"])
                )

    # ── Rename any legacy watchlist names to the current bucket labels ────────
    _LEGACY_RENAMES = {
        "A+ Momentum":           "A+ Swing Setups",
        "Secondary Watch":       "Secondary Swing Watch",
        "Swing Watchlist":       "Extended",
        "Core":                  "Core Swing Plays",
        "Swing Ready":           "A+ Swing Setups",
        "Pullback Zone":         "Secondary Swing Watch",
        "Core List":             "Core Swing Plays",
        "A+ Swing Setups":       "A+ READY",
        "Secondary Swing Watch": "SETUPS FORMING",
        "Extended":              "EXTENDED / CHASE ZONE",
        "Core Swing Plays":      "AVOID / BLOCKED",
    }
    for old_name, new_name in _LEGACY_RENAMES.items():
        cursor.execute(
            "UPDATE watchlists SET name = ? WHERE name = ?",
            (new_name, old_name),
        )
    # Ensure TREND WATCH bucket exists for admin (new in v4)
    if not cursor.execute(
        "SELECT 1 FROM watchlists WHERE user_id = ? AND name = 'TREND WATCH'",
        (admin_id,)
    ).fetchone():
        cursor.execute(
            "INSERT INTO watchlists (user_id, name, created_at) VALUES (?, ?, ?)",
            (admin_id, "TREND WATCH", now_iso),
        )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# User management helpers
# ---------------------------------------------------------------------------

def create_user(username: str, password: str, is_admin: bool = False) -> int:
    """Create a new user account. Returns the new user_id."""
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, created_at, is_admin) VALUES (?, ?, ?, ?)",
        (username.strip().lower(), generate_password_hash(password),
         datetime.now().isoformat(), 1 if is_admin else 0),
        returning_id=True,
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    # Seed default watchlists for the new user
    ensure_user_watchlists(new_id)
    return new_id


def get_user_by_id(user_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_username(username: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username.strip().lower(),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_users() -> list:
    conn = get_db()
    rows = conn.execute("SELECT id, username, created_at, is_admin FROM users ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_user_password(user_id: int, new_password: str) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (generate_password_hash(new_password), user_id)
    )
    conn.commit()
    conn.close()


def delete_user(user_id: int) -> None:
    """Delete a user and all their data. Cannot delete user_id=1 (original admin)."""
    if user_id == 1:
        raise ValueError("Cannot delete the primary admin user (id=1).")
    conn = get_db()
    # Cascade-delete all user-scoped data
    for tbl in ("watchlist_stocks",):
        # watchlist_stocks via watchlist FK
        wl_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM watchlists WHERE user_id = ?", (user_id,)
        ).fetchall()]
        for wl_id in wl_ids:
            conn.execute("DELETE FROM watchlist_stocks WHERE watchlist_id = ?", (wl_id,))
    for tbl in ("watchlists", "notes", "trade_plans", "journal",
                "daily_sessions", "schwab_imports", "setup_outcomes", "user_settings"):
        conn.execute(f"DELETE FROM {tbl} WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def check_user_password(username: str, password: str) -> dict | None:
    """Return user dict if credentials match, else None."""
    user = get_user_by_username(username)
    if user and check_password_hash(user["password_hash"], password):
        return user
    return None


# ---------------------------------------------------------------------------
# Per-user settings helpers (risk prefs, Schwab tokens, etc.)
# ---------------------------------------------------------------------------

def get_user_setting(user_id: int, key: str, default=None):
    """Return the value of a per-user setting key, or default if not set."""
    conn = get_db()
    row = conn.execute(
        "SELECT value FROM user_settings WHERE user_id = ? AND key = ?",
        (user_id, key)
    ).fetchone()
    conn.close()
    return row["value"] if row else default


def set_user_setting(user_id: int, key: str, value: str) -> None:
    """Upsert a per-user setting key/value pair."""
    conn = get_db()
    existing = conn.execute(
        "SELECT 1 FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE user_settings SET value = ? WHERE user_id = ? AND key = ?",
            (value, user_id, key)
        )
    else:
        conn.execute(
            "INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?)",
            (user_id, key, value)
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Ensure default watchlists for a user (called on new account creation)
# ---------------------------------------------------------------------------

def ensure_user_watchlists(user_id: int) -> None:
    """Seed the 5 default watchlists for a user if they have none yet."""
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM watchlists WHERE user_id = ?", (user_id,)
    ).fetchone()["cnt"]
    if count == 0:
        now_iso = datetime.now().isoformat()
        for name in DEFAULT_WATCHLISTS:
            existing = conn.execute(
                "SELECT 1 FROM watchlists WHERE user_id = ? AND name = ?",
                (user_id, name)
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO watchlists (user_id, name, created_at) VALUES (?, ?, ?)",
                    (user_id, name, now_iso)
                )
        # Ensure TREND WATCH too
        if not conn.execute(
            "SELECT 1 FROM watchlists WHERE user_id = ? AND name = 'TREND WATCH'",
            (user_id,)
        ).fetchone():
            conn.execute(
                "INSERT INTO watchlists (user_id, name, created_at) VALUES (?, ?, ?)",
                (user_id, "TREND WATCH", now_iso)
            )
        conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Named watchlist helpers
# ---------------------------------------------------------------------------

def get_all_watchlists(user_id=None) -> list:
    """Return watchlists ordered by creation.
    If user_id is given, return only that user's lists.
    If user_id is None, return all (used by background tasks)."""
    conn = get_db()
    if user_id is not None:
        rows = conn.execute(
            "SELECT * FROM watchlists WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM watchlists ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_watchlist_by_id(wl_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM watchlists WHERE id = ?", (wl_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_watchlist(name: str, user_id: int = 1) -> int:
    """Create a new named watchlist for a user. Returns its id."""
    conn = get_db()
    # Check for duplicate name under same user
    existing = conn.execute(
        "SELECT id FROM watchlists WHERE user_id = ? AND name = ?",
        (user_id, name.strip())
    ).fetchone()
    if existing:
        conn.close()
        raise ValueError(f"Watchlist '{name}' already exists.")
    cur = conn.execute(
        "INSERT INTO watchlists (user_id, name, created_at) VALUES (?, ?, ?)",
        (user_id, name.strip(), datetime.now().isoformat()),
        returning_id=True,
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id


def rename_watchlist(wl_id: int, name: str):
    conn = get_db()
    conn.execute("UPDATE watchlists SET name = ? WHERE id = ?", (name.strip(), wl_id))
    conn.commit()
    conn.close()


def delete_watchlist(wl_id: int):
    """Delete a watchlist and its memberships. Stock data kept (may be in other lists)."""
    conn = get_db()
    conn.execute("DELETE FROM watchlist_stocks WHERE watchlist_id = ?", (wl_id,))
    conn.execute("DELETE FROM watchlists WHERE id = ?", (wl_id,))
    conn.commit()
    conn.close()


def get_watchlist_stocks(wl_id: int) -> list:
    """Return list of tickers in a watchlist, newest first."""
    conn = get_db()
    rows = conn.execute(
        "SELECT ticker FROM watchlist_stocks WHERE watchlist_id = ? ORDER BY added_date DESC",
        (wl_id,)
    ).fetchall()
    conn.close()
    tickers = [r["ticker"] for r in rows]
    logger.debug("DB LOAD  wl_id=%s tickers=%s", wl_id, tickers)
    return tickers


def get_watchlist_stock_counts(user_id=None) -> dict:
    """Return {watchlist_id: count} for the given user's watchlists (all if user_id=None)."""
    conn = get_db()
    if user_id is not None:
        rows = conn.execute(
            "SELECT ws.watchlist_id, COUNT(*) AS cnt "
            "FROM watchlist_stocks ws "
            "JOIN watchlists wl ON wl.id = ws.watchlist_id "
            "WHERE wl.user_id = ? "
            "GROUP BY ws.watchlist_id",
            (user_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT watchlist_id, COUNT(*) AS cnt FROM watchlist_stocks GROUP BY watchlist_id"
        ).fetchall()
    conn.close()
    return {r["watchlist_id"]: r["cnt"] for r in rows}


def add_ticker_to_watchlist(wl_id: int, ticker: str):
    """Add a ticker to a specific watchlist. Silently ignores duplicates."""
    conn = get_db()
    t = ticker.upper().strip()
    # Check first to avoid cross-driver IntegrityError differences
    existing = conn.execute(
        "SELECT id FROM watchlist_stocks WHERE watchlist_id = ? AND ticker = ?",
        (wl_id, t)
    ).fetchone()
    if existing:
        logger.debug("DB ADD (already exists)  ticker=%s wl_id=%s", t, wl_id)
        conn.close()
        return
    try:
        conn.execute(
            "INSERT INTO watchlist_stocks (watchlist_id, ticker, added_date) VALUES (?, ?, ?)",
            (wl_id, t, datetime.now().isoformat())
        )
        conn.commit()
        logger.info("DB ADD  ticker=%s wl_id=%s", t, wl_id)
    except Exception as exc:
        logger.warning("DB ADD failed  ticker=%s wl_id=%s err=%s", t, wl_id, exc)
    finally:
        conn.close()


def remove_ticker_from_watchlist(wl_id: int, ticker: str):
    """Remove a ticker from a specific watchlist.
    Stock data is removed only if the ticker no longer belongs to any watchlist.
    Notes and trade plans are never auto-deleted."""
    conn = get_db()
    t = ticker.upper().strip()
    conn.execute(
        "DELETE FROM watchlist_stocks WHERE watchlist_id = ? AND ticker = ?", (wl_id, t)
    )
    remaining = conn.execute(
        "SELECT COUNT(*) AS cnt FROM watchlist_stocks WHERE ticker = ?", (t,)
    ).fetchone()["cnt"]
    if remaining == 0:
        conn.execute("DELETE FROM stock_data WHERE ticker = ?", (t,))
        logger.info("DB REMOVE  ticker=%s wl_id=%s  (stock_data purged — no other memberships)", t, wl_id)
    else:
        logger.info("DB REMOVE  ticker=%s wl_id=%s  (stock_data kept — still in %d other list(s))", t, wl_id, remaining)
    conn.commit()
    conn.close()


def remove_ticker_from_defaults(ticker: str, user_id: int = 1):
    """
    Remove a ticker from all default watchlists for a user.

    Called when the user explicitly deletes a ticker so it cannot be
    re-inserted by auto-classification.  The ticker stays in any user-created
    custom watchlists; stock_data is deleted only when no memberships remain.
    """
    conn = get_db()
    t = ticker.upper().strip()
    logger.info("DB REMOVE FROM DEFAULTS  ticker=%s  user_id=%s", t, user_id)

    # Resolve IDs of the default lists belonging to this user
    placeholders = ",".join(["?"] * len(DEFAULT_WATCHLISTS))
    rows = conn.execute(
        f"SELECT id FROM watchlists WHERE user_id = ? AND name IN ({placeholders})",
        (user_id, *DEFAULT_WATCHLISTS),
    ).fetchall()
    default_ids = [r["id"] for r in rows]

    for wl_id in default_ids:
        conn.execute(
            "DELETE FROM watchlist_stocks WHERE watchlist_id = ? AND ticker = ?",
            (wl_id, t),
        )

    # Remove stock_data if the ticker is now in no watchlist at all (any user)
    remaining = conn.execute(
        "SELECT COUNT(*) AS cnt FROM watchlist_stocks WHERE ticker = ?", (t,)
    ).fetchone()["cnt"]
    if remaining == 0:
        conn.execute("DELETE FROM stock_data WHERE ticker = ?", (t,))

    conn.commit()
    conn.close()


def get_ticker_watchlist_ids(ticker: str, user_id=None) -> list:
    """Return list of watchlist IDs that contain this ticker.
    If user_id given, restricts to that user's watchlists only."""
    conn = get_db()
    if user_id is not None:
        rows = conn.execute(
            "SELECT ws.watchlist_id FROM watchlist_stocks ws "
            "JOIN watchlists wl ON wl.id = ws.watchlist_id "
            "WHERE ws.ticker = ? AND wl.user_id = ?",
            (ticker.upper(), user_id)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT watchlist_id FROM watchlist_stocks WHERE ticker = ?",
            (ticker.upper(),)
        ).fetchall()
    conn.close()
    return [r["watchlist_id"] for r in rows]


def set_ticker_watchlists(ticker: str, watchlist_ids: list):
    """Replace all watchlist memberships for a ticker with the provided list."""
    conn = get_db()
    t = ticker.upper().strip()
    conn.execute("DELETE FROM watchlist_stocks WHERE ticker = ?", (t,))
    now_iso = datetime.now().isoformat()
    for wl_id in watchlist_ids:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist_stocks "
            "(watchlist_id, ticker, added_date) VALUES (?, ?, ?)",
            (int(wl_id), t, now_iso)
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Legacy single-watchlist helpers (kept for backward compatibility only)
# ---------------------------------------------------------------------------

def get_watchlist():
    """Return tickers from the legacy watchlist table (migration only)."""
    conn = get_db()
    rows = conn.execute("SELECT ticker FROM watchlist ORDER BY added_date DESC").fetchall()
    conn.close()
    return [row["ticker"] for row in rows]


def add_ticker(ticker: str):
    """Legacy: kept so existing seeding code does not break."""
    pass  # Replaced by add_ticker_to_watchlist


def remove_ticker(ticker: str):
    """Legacy: kept for any remaining references."""
    pass  # Replaced by remove_ticker_from_watchlist


# ---------------------------------------------------------------------------
# Stock data helpers
# ---------------------------------------------------------------------------

def upsert_stock_data(data: dict):
    """Insert or update stock data for a ticker."""
    conn = get_db()

    ticker = data.get("ticker", "").upper()
    existing = conn.execute(
        "SELECT exec_state, triggered_at FROM stock_data WHERE ticker = ?", (ticker,)
    ).fetchone()

    new_state = data.get("exec_state")
    if new_state == "TRIGGERED":
        if existing and existing["exec_state"] == "TRIGGERED" and existing["triggered_at"]:
            data["triggered_at"] = existing["triggered_at"]
        else:
            data["triggered_at"] = datetime.now().isoformat()
    else:
        data["triggered_at"] = None

    data.setdefault("ticker_state", "ready")

    conn.execute("""
        INSERT INTO stock_data
            (ticker, current_price, prev_close, gap_pct, premarket_high,
             premarket_low, prev_day_high, prev_day_low, avg_volume, rel_volume,
             catalyst_summary, news_headlines, earnings_date, trade_bias,
             catalyst_score, catalyst_reason, catalyst_confidence,
             momentum_score, momentum_reason, momentum_confidence,
             order_block, entry_quality,
             orb_high, orb_low, orb_status, orb_ready, orb_phase, exec_state,
             setup_score, setup_reason, setup_confidence,
             setup_type, triggered_at, last_updated, prev_close_date,
             vwap, momentum_breakout, candles_above_orb,
             momentum_runner, entry_note, position_size,
             orb_hold, trend_structure, higher_highs, higher_lows, strong_candle_bodies,
             price_above_vwap, structure_momentum_score,
             catalyst_category, headlines_fetched_at,
             nearest_supply_top, nearest_supply_bottom,
             nearest_demand_top, nearest_demand_bottom,
             distance_to_supply_pct, distance_to_demand_pct,
             zone_location, bullish_order_block, bearish_order_block,
             in_supply_zone, in_demand_zone, zones_fetched_at,
             zones_json, demand_zone_grade, supply_zone_grade,
             zone_ai_setup, zone_ai_reason, zone_probability,
             smart_money_json, fvg_bullish, fvg_bearish,
             ema_20_daily, ema_50_daily, ema_200_daily,
             pct_from_ema20, pct_from_ema50, daily_trend,
             daily_hh_hl, daily_lh_ll,
             fib_high, fib_low, fib_50, fib_618,
             fib_236, fib_382, fib_65, fib_705, fib_786,
             fib_confidence, fib_direction, fib_mode,
             macro_fib_high, macro_fib_low, macro_fib_50, macro_fib_618,
             h4_fib_high, h4_fib_low, h4_fib_50, h4_fib_618,
             swing_score, swing_reason, swing_confidence,
             swing_setup_type, swing_status,
             entry_zone_low, entry_zone_high, stop_level,
             target_1, target_2, risk_reward, swing_data_fetched_at,
             h4_trend, h4_ema20, h4_ema50, h4_hh_hl,
             m15_higher_low, m15_confirmation, ticker_state,
             rs_score, rs_vs_qqq, sector_etf)
        VALUES
            (:ticker, :current_price, :prev_close, :gap_pct, :premarket_high,
             :premarket_low, :prev_day_high, :prev_day_low, :avg_volume, :rel_volume,
             :catalyst_summary, :news_headlines, :earnings_date, :trade_bias,
             :catalyst_score, :catalyst_reason, :catalyst_confidence,
             :momentum_score, :momentum_reason, :momentum_confidence,
             :order_block, :entry_quality,
             :orb_high, :orb_low, :orb_status, :orb_ready, :orb_phase, :exec_state,
             :setup_score, :setup_reason, :setup_confidence,
             :setup_type, :triggered_at, :last_updated, :prev_close_date,
             :vwap, :momentum_breakout, :candles_above_orb,
             :momentum_runner, :entry_note, :position_size,
             :orb_hold, :trend_structure, :higher_highs, :higher_lows, :strong_candle_bodies,
             :price_above_vwap, :structure_momentum_score,
             :catalyst_category, :headlines_fetched_at,
             :nearest_supply_top, :nearest_supply_bottom,
             :nearest_demand_top, :nearest_demand_bottom,
             :distance_to_supply_pct, :distance_to_demand_pct,
             :zone_location, :bullish_order_block, :bearish_order_block,
             :in_supply_zone, :in_demand_zone, :zones_fetched_at,
             :zones_json, :demand_zone_grade, :supply_zone_grade,
             :zone_ai_setup, :zone_ai_reason, :zone_probability,
             :smart_money_json, :fvg_bullish, :fvg_bearish,
             :ema_20_daily, :ema_50_daily, :ema_200_daily,
             :pct_from_ema20, :pct_from_ema50, :daily_trend,
             :daily_hh_hl, :daily_lh_ll,
             :fib_high, :fib_low, :fib_50, :fib_618,
             :fib_236, :fib_382, :fib_65, :fib_705, :fib_786,
             :fib_confidence, :fib_direction, :fib_mode,
             :macro_fib_high, :macro_fib_low, :macro_fib_50, :macro_fib_618,
             :h4_fib_high, :h4_fib_low, :h4_fib_50, :h4_fib_618,
             :swing_score, :swing_reason, :swing_confidence,
             :swing_setup_type, :swing_status,
             :entry_zone_low, :entry_zone_high, :stop_level,
             :target_1, :target_2, :risk_reward, :swing_data_fetched_at,
             :h4_trend, :h4_ema20, :h4_ema50, :h4_hh_hl,
             :m15_higher_low, :m15_confirmation, :ticker_state,
             :rs_score, :rs_vs_qqq, :sector_etf)
        ON CONFLICT(ticker) DO UPDATE SET
            current_price        = excluded.current_price,
            prev_close           = excluded.prev_close,
            gap_pct              = excluded.gap_pct,
            premarket_high       = excluded.premarket_high,
            premarket_low        = excluded.premarket_low,
            prev_day_high        = excluded.prev_day_high,
            prev_day_low         = excluded.prev_day_low,
            avg_volume           = excluded.avg_volume,
            rel_volume           = excluded.rel_volume,
            catalyst_summary     = excluded.catalyst_summary,
            news_headlines       = excluded.news_headlines,
            earnings_date        = excluded.earnings_date,
            trade_bias           = excluded.trade_bias,
            catalyst_score       = excluded.catalyst_score,
            catalyst_reason      = excluded.catalyst_reason,
            catalyst_confidence  = excluded.catalyst_confidence,
            momentum_score       = excluded.momentum_score,
            momentum_reason      = excluded.momentum_reason,
            momentum_confidence  = excluded.momentum_confidence,
            order_block          = excluded.order_block,
            entry_quality        = excluded.entry_quality,
            orb_high             = excluded.orb_high,
            orb_low              = excluded.orb_low,
            orb_status           = excluded.orb_status,
            orb_ready            = excluded.orb_ready,
            orb_phase            = excluded.orb_phase,
            exec_state           = excluded.exec_state,
            setup_score          = excluded.setup_score,
            setup_reason         = excluded.setup_reason,
            setup_confidence     = excluded.setup_confidence,
            setup_type           = excluded.setup_type,
            triggered_at         = excluded.triggered_at,
            last_updated         = excluded.last_updated,
            prev_close_date      = excluded.prev_close_date,
            vwap                 = excluded.vwap,
            momentum_breakout    = excluded.momentum_breakout,
            candles_above_orb    = excluded.candles_above_orb,
            momentum_runner      = excluded.momentum_runner,
            entry_note           = excluded.entry_note,
            position_size        = excluded.position_size,
            orb_hold                 = excluded.orb_hold,
            trend_structure          = excluded.trend_structure,
            higher_highs             = excluded.higher_highs,
            higher_lows              = excluded.higher_lows,
            strong_candle_bodies     = excluded.strong_candle_bodies,
            price_above_vwap         = excluded.price_above_vwap,
            structure_momentum_score = excluded.structure_momentum_score,
            catalyst_category        = excluded.catalyst_category,
            headlines_fetched_at     = excluded.headlines_fetched_at,
            nearest_supply_top       = excluded.nearest_supply_top,
            nearest_supply_bottom    = excluded.nearest_supply_bottom,
            nearest_demand_top       = excluded.nearest_demand_top,
            nearest_demand_bottom    = excluded.nearest_demand_bottom,
            distance_to_supply_pct   = excluded.distance_to_supply_pct,
            distance_to_demand_pct   = excluded.distance_to_demand_pct,
            zone_location            = excluded.zone_location,
            bullish_order_block      = excluded.bullish_order_block,
            bearish_order_block      = excluded.bearish_order_block,
            in_supply_zone           = excluded.in_supply_zone,
            in_demand_zone           = excluded.in_demand_zone,
            zones_fetched_at         = excluded.zones_fetched_at,
            zones_json               = excluded.zones_json,
            demand_zone_grade        = excluded.demand_zone_grade,
            supply_zone_grade        = excluded.supply_zone_grade,
            zone_ai_setup            = excluded.zone_ai_setup,
            zone_ai_reason           = excluded.zone_ai_reason,
            zone_probability         = excluded.zone_probability,
            smart_money_json         = excluded.smart_money_json,
            fvg_bullish              = excluded.fvg_bullish,
            fvg_bearish              = excluded.fvg_bearish,
            ema_20_daily             = excluded.ema_20_daily,
            ema_50_daily             = excluded.ema_50_daily,
            ema_200_daily            = excluded.ema_200_daily,
            pct_from_ema20           = excluded.pct_from_ema20,
            pct_from_ema50           = excluded.pct_from_ema50,
            daily_trend              = excluded.daily_trend,
            daily_hh_hl              = excluded.daily_hh_hl,
            daily_lh_ll              = excluded.daily_lh_ll,
            fib_high                 = excluded.fib_high,
            fib_low                  = excluded.fib_low,
            fib_50                   = excluded.fib_50,
            fib_618                  = excluded.fib_618,
            fib_236                  = excluded.fib_236,
            fib_382                  = excluded.fib_382,
            fib_65                   = excluded.fib_65,
            fib_705                  = excluded.fib_705,
            fib_786                  = excluded.fib_786,
            fib_confidence           = excluded.fib_confidence,
            fib_direction            = excluded.fib_direction,
            fib_mode                 = excluded.fib_mode,
            macro_fib_high           = excluded.macro_fib_high,
            macro_fib_low            = excluded.macro_fib_low,
            macro_fib_50             = excluded.macro_fib_50,
            macro_fib_618            = excluded.macro_fib_618,
            h4_fib_high              = excluded.h4_fib_high,
            h4_fib_low               = excluded.h4_fib_low,
            h4_fib_50                = excluded.h4_fib_50,
            h4_fib_618               = excluded.h4_fib_618,
            swing_score              = excluded.swing_score,
            swing_reason             = excluded.swing_reason,
            swing_confidence         = excluded.swing_confidence,
            swing_setup_type         = excluded.swing_setup_type,
            swing_status             = excluded.swing_status,
            entry_zone_low           = excluded.entry_zone_low,
            entry_zone_high          = excluded.entry_zone_high,
            stop_level               = excluded.stop_level,
            target_1                 = excluded.target_1,
            target_2                 = excluded.target_2,
            risk_reward              = excluded.risk_reward,
            swing_data_fetched_at    = excluded.swing_data_fetched_at,
            h4_trend                 = excluded.h4_trend,
            h4_ema20                 = excluded.h4_ema20,
            h4_ema50                 = excluded.h4_ema50,
            h4_hh_hl                 = excluded.h4_hh_hl,
            m15_higher_low           = excluded.m15_higher_low,
            m15_confirmation         = excluded.m15_confirmation,
            ticker_state             = excluded.ticker_state,
            rs_score                 = excluded.rs_score,
            rs_vs_qqq                = excluded.rs_vs_qqq,
            sector_etf               = excluded.sector_etf
    """, data)
    conn.commit()
    conn.close()


def set_ticker_state(ticker: str, state: str):
    """
    Update only the ticker_state for a stock.
    Valid states: 'loading' | 'ready' | 'error' | 'stale'
    """
    conn = get_db()
    t = ticker.upper().strip()
    conn.execute(
        "UPDATE stock_data SET ticker_state = ?, last_updated = ? WHERE ticker = ?",
        (state, _et_now().strftime("%Y-%m-%d %I:%M %p"), t),
    )
    conn.commit()
    conn.close()
    logger.debug("set_ticker_state  ticker=%s  state=%s", t, state)


def upsert_loading_placeholder(ticker: str):
    """
    Insert a minimal 'loading' placeholder for a ticker.
    Uses INSERT OR IGNORE so it never overwrites existing data —
    safe to call even if the ticker already has a full row.
    """
    conn = get_db()
    t = ticker.upper().strip()
    now = _et_now().strftime("%Y-%m-%d %I:%M %p")
    conn.execute(
        "INSERT OR IGNORE INTO stock_data (ticker, ticker_state, last_updated) "
        "VALUES (?, 'loading', ?)",
        (t, now),
    )
    conn.commit()
    conn.close()
    logger.debug("upsert_loading_placeholder  ticker=%s", t)


def set_stock_classify(ticker: str, reason: str):
    """Update only the classify_reason for a ticker."""
    conn = get_db()
    conn.execute(
        "UPDATE stock_data SET classify_reason = ? WHERE ticker = ?",
        (reason, ticker.upper().strip())
    )
    conn.commit()
    conn.close()


def set_auto_classify(ticker: str, enabled: bool):
    """Toggle the auto_classify flag for a ticker."""
    conn = get_db()
    conn.execute(
        "UPDATE stock_data SET auto_classify = ? WHERE ticker = ?",
        (1 if enabled else 0, ticker.upper().strip())
    )
    conn.commit()
    conn.close()


def update_setup_type(ticker: str, setup_type: str):
    """Persist a manually-chosen setup type override for a ticker."""
    conn = get_db()
    conn.execute(
        "UPDATE stock_data SET setup_type = ? WHERE ticker = ?",
        (setup_type, ticker.upper().strip())
    )
    conn.commit()
    conn.close()


def get_stock_data(ticker: str):
    """Return stock data dict for a single ticker, or None."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM stock_data WHERE ticker = ?", (ticker.upper(),)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    d = dict(row)
    try:
        d["news_headlines"] = json.loads(d["news_headlines"] or "[]")
    except (json.JSONDecodeError, TypeError):
        d["news_headlines"] = []
    return d


def get_all_stock_data():
    """Return all stock data rows as a list of dicts."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM stock_data").fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["news_headlines"] = json.loads(d["news_headlines"] or "[]")
        except (json.JSONDecodeError, TypeError):
            d["news_headlines"] = []
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Live exec-state update (used by auto-refresh cycle)
# ---------------------------------------------------------------------------

def update_live_fields(data: dict) -> None:
    """
    Update live-changing fields for a stock without touching catalyst/news.

    The triggered_at timestamp logic mirrors upsert_stock_data():
      - When newly TRIGGERED: stamp now()
      - When already TRIGGERED: preserve original timestamp
      - When leaving TRIGGERED: clear to None
    """
    ticker = (data.get("ticker") or "").upper()
    if not ticker:
        return

    conn = get_db()
    existing = conn.execute(
        "SELECT exec_state, triggered_at FROM stock_data WHERE ticker = ?", (ticker,)
    ).fetchone()

    new_state = data.get("exec_state")
    if new_state == "TRIGGERED":
        if existing and existing["exec_state"] == "TRIGGERED" and existing["triggered_at"]:
            triggered_at = existing["triggered_at"]
        else:
            triggered_at = datetime.now().isoformat()
    else:
        triggered_at = None

    conn.execute("""
        UPDATE stock_data SET
            current_price            = :current_price,
            gap_pct                  = :gap_pct,
            rel_volume               = :rel_volume,
            avg_volume               = :avg_volume,
            orb_high                 = :orb_high,
            orb_low                  = :orb_low,
            orb_status               = :orb_status,
            orb_ready                = :orb_ready,
            orb_phase                = :orb_phase,
            vwap                     = :vwap,
            momentum_score           = :momentum_score,
            momentum_reason          = :momentum_reason,
            momentum_confidence      = :momentum_confidence,
            order_block              = :order_block,
            entry_quality            = :entry_quality,
            exec_state               = :exec_state,
            setup_score              = :setup_score,
            setup_reason             = :setup_reason,
            setup_confidence         = :setup_confidence,
            setup_type               = :setup_type,
            entry_note               = :entry_note,
            position_size            = :position_size,
            momentum_breakout        = :momentum_breakout,
            momentum_runner          = :momentum_runner,
            candles_above_orb        = :candles_above_orb,
            orb_hold                 = :orb_hold,
            trend_structure          = :trend_structure,
            higher_highs             = :higher_highs,
            higher_lows              = :higher_lows,
            strong_candle_bodies     = :strong_candle_bodies,
            price_above_vwap         = :price_above_vwap,
            structure_momentum_score = :structure_momentum_score,
            catalyst_score           = :catalyst_score,
            catalyst_reason          = :catalyst_reason,
            catalyst_confidence      = :catalyst_confidence,
            catalyst_summary         = :catalyst_summary,
            catalyst_category        = :catalyst_category,
            news_headlines           = :news_headlines,
            headlines_fetched_at     = :headlines_fetched_at,
            nearest_supply_top       = :nearest_supply_top,
            nearest_supply_bottom    = :nearest_supply_bottom,
            nearest_demand_top       = :nearest_demand_top,
            nearest_demand_bottom    = :nearest_demand_bottom,
            distance_to_supply_pct   = :distance_to_supply_pct,
            distance_to_demand_pct   = :distance_to_demand_pct,
            zone_location            = :zone_location,
            bullish_order_block      = :bullish_order_block,
            bearish_order_block      = :bearish_order_block,
            in_supply_zone           = :in_supply_zone,
            in_demand_zone           = :in_demand_zone,
            zones_fetched_at         = :zones_fetched_at,
            zones_json               = :zones_json,
            demand_zone_grade        = :demand_zone_grade,
            supply_zone_grade        = :supply_zone_grade,
            zone_ai_setup            = :zone_ai_setup,
            zone_ai_reason           = :zone_ai_reason,
            zone_probability         = :zone_probability,
            smart_money_json         = :smart_money_json,
            fvg_bullish              = :fvg_bullish,
            fvg_bearish              = :fvg_bearish,
            ema_20_daily             = :ema_20_daily,
            ema_50_daily             = :ema_50_daily,
            ema_200_daily            = :ema_200_daily,
            pct_from_ema20           = :pct_from_ema20,
            pct_from_ema50           = :pct_from_ema50,
            daily_trend              = :daily_trend,
            daily_hh_hl              = :daily_hh_hl,
            daily_lh_ll              = :daily_lh_ll,
            fib_high                 = :fib_high,
            fib_low                  = :fib_low,
            fib_50                   = :fib_50,
            fib_618                  = :fib_618,
            fib_236                  = :fib_236,
            fib_382                  = :fib_382,
            fib_65                   = :fib_65,
            fib_705                  = :fib_705,
            fib_786                  = :fib_786,
            fib_confidence           = :fib_confidence,
            fib_direction            = :fib_direction,
            fib_mode                 = :fib_mode,
            macro_fib_high           = :macro_fib_high,
            macro_fib_low            = :macro_fib_low,
            macro_fib_50             = :macro_fib_50,
            macro_fib_618            = :macro_fib_618,
            h4_fib_high              = :h4_fib_high,
            h4_fib_low               = :h4_fib_low,
            h4_fib_50                = :h4_fib_50,
            h4_fib_618               = :h4_fib_618,
            swing_score              = :swing_score,
            swing_reason             = :swing_reason,
            swing_confidence         = :swing_confidence,
            swing_setup_type         = :swing_setup_type,
            swing_status             = :swing_status,
            entry_zone_low           = :entry_zone_low,
            entry_zone_high          = :entry_zone_high,
            stop_level               = :stop_level,
            target_1                 = :target_1,
            target_2                 = :target_2,
            risk_reward              = :risk_reward,
            swing_data_fetched_at    = :swing_data_fetched_at,
            h4_trend                 = :h4_trend,
            h4_ema20                 = :h4_ema20,
            h4_ema50                 = :h4_ema50,
            h4_hh_hl                 = :h4_hh_hl,
            m15_higher_low           = :m15_higher_low,
            m15_confirmation         = :m15_confirmation,
            rs_score                 = :rs_score,
            rs_vs_qqq                = :rs_vs_qqq,
            sector_etf               = :sector_etf,
            triggered_at             = :triggered_at,
            last_updated             = :last_updated
        WHERE ticker = :ticker
    """, {
        "ticker":                   ticker,
        "current_price":            data.get("current_price"),
        "gap_pct":                  data.get("gap_pct"),
        "rel_volume":               data.get("rel_volume"),
        "avg_volume":               data.get("avg_volume"),
        "orb_high":                 data.get("orb_high"),
        "orb_low":                  data.get("orb_low"),
        "orb_status":               data.get("orb_status"),
        "orb_ready":                data.get("orb_ready"),
        "orb_phase":                data.get("orb_phase"),
        "vwap":                     data.get("vwap"),
        "momentum_score":           data.get("momentum_score"),
        "momentum_reason":          data.get("momentum_reason"),
        "momentum_confidence":      data.get("momentum_confidence"),
        "order_block":              data.get("order_block"),
        "entry_quality":            data.get("entry_quality"),
        "exec_state":               data.get("exec_state"),
        "setup_score":              data.get("setup_score"),
        "setup_reason":             data.get("setup_reason"),
        "setup_confidence":         data.get("setup_confidence"),
        "setup_type":               data.get("setup_type"),
        "entry_note":               data.get("entry_note"),
        "position_size":            data.get("position_size"),
        "momentum_breakout":        int(bool(data.get("momentum_breakout"))),
        "momentum_runner":          int(bool(data.get("momentum_runner"))),
        "candles_above_orb":        data.get("candles_above_orb") or 0,
        "orb_hold":                 int(bool(data.get("orb_hold"))),
        "trend_structure":          int(bool(data.get("trend_structure"))),
        "higher_highs":             int(bool(data.get("higher_highs"))),
        "higher_lows":              int(bool(data.get("higher_lows"))),
        "strong_candle_bodies":     int(bool(data.get("strong_candle_bodies"))),
        "price_above_vwap":         int(bool(data.get("price_above_vwap"))),
        "structure_momentum_score": data.get("structure_momentum_score") or 0,
        "catalyst_score":           data.get("catalyst_score"),
        "catalyst_reason":          data.get("catalyst_reason"),
        "catalyst_confidence":      data.get("catalyst_confidence"),
        "catalyst_summary":         data.get("catalyst_summary"),
        "catalyst_category":        data.get("catalyst_category"),
        "news_headlines":           json.dumps(data.get("news_headlines") or [])
                                    if isinstance(data.get("news_headlines"), list)
                                    else (data.get("news_headlines") or "[]"),
        "headlines_fetched_at":     data.get("headlines_fetched_at"),
        "nearest_supply_top":       data.get("nearest_supply_top"),
        "nearest_supply_bottom":    data.get("nearest_supply_bottom"),
        "nearest_demand_top":       data.get("nearest_demand_top"),
        "nearest_demand_bottom":    data.get("nearest_demand_bottom"),
        "distance_to_supply_pct":   data.get("distance_to_supply_pct"),
        "distance_to_demand_pct":   data.get("distance_to_demand_pct"),
        "zone_location":            data.get("zone_location") or "BETWEEN ZONES",
        "bullish_order_block":      data.get("bullish_order_block"),
        "bearish_order_block":      data.get("bearish_order_block"),
        "in_supply_zone":           int(bool(data.get("in_supply_zone"))),
        "in_demand_zone":           int(bool(data.get("in_demand_zone"))),
        "zones_fetched_at":         data.get("zones_fetched_at"),
        "zones_json":               data.get("zones_json"),
        "demand_zone_grade":        data.get("demand_zone_grade"),
        "supply_zone_grade":        data.get("supply_zone_grade"),
        "zone_ai_setup":            data.get("zone_ai_setup"),
        "zone_ai_reason":           data.get("zone_ai_reason"),
        "zone_probability":         data.get("zone_probability"),
        "smart_money_json":         data.get("smart_money_json"),
        "fvg_bullish":              int(bool(data.get("fvg_bullish"))),
        "fvg_bearish":              int(bool(data.get("fvg_bearish"))),
        "ema_20_daily":             data.get("ema_20_daily"),
        "ema_50_daily":             data.get("ema_50_daily"),
        "ema_200_daily":            data.get("ema_200_daily"),
        "pct_from_ema20":           data.get("pct_from_ema20"),
        "pct_from_ema50":           data.get("pct_from_ema50"),
        "daily_trend":              data.get("daily_trend"),
        "daily_hh_hl":              int(bool(data.get("daily_hh_hl"))),
        "daily_lh_ll":              int(bool(data.get("daily_lh_ll"))),
        "fib_high":                 data.get("fib_high"),
        "fib_low":                  data.get("fib_low"),
        "fib_50":                   data.get("fib_50"),
        "fib_618":                  data.get("fib_618"),
        "fib_236":                  data.get("fib_236"),
        "fib_382":                  data.get("fib_382"),
        "fib_65":                   data.get("fib_65"),
        "fib_705":                  data.get("fib_705"),
        "fib_786":                  data.get("fib_786"),
        "fib_confidence":           data.get("fib_confidence"),
        "fib_direction":            data.get("fib_direction"),
        "fib_mode":                 data.get("fib_mode"),
        "macro_fib_high":           data.get("macro_fib_high"),
        "macro_fib_low":            data.get("macro_fib_low"),
        "macro_fib_50":             data.get("macro_fib_50"),
        "macro_fib_618":            data.get("macro_fib_618"),
        "h4_fib_high":              data.get("h4_fib_high"),
        "h4_fib_low":               data.get("h4_fib_low"),
        "h4_fib_50":                data.get("h4_fib_50"),
        "h4_fib_618":               data.get("h4_fib_618"),
        "swing_score":              data.get("swing_score"),
        "swing_reason":             data.get("swing_reason"),
        "swing_confidence":         data.get("swing_confidence"),
        "swing_setup_type":         data.get("swing_setup_type"),
        "swing_status":             data.get("swing_status"),
        "entry_zone_low":           data.get("entry_zone_low"),
        "entry_zone_high":          data.get("entry_zone_high"),
        "stop_level":               data.get("stop_level"),
        "target_1":                 data.get("target_1"),
        "target_2":                 data.get("target_2"),
        "risk_reward":              data.get("risk_reward"),
        "swing_data_fetched_at":    data.get("swing_data_fetched_at"),
        "h4_trend":                 data.get("h4_trend"),
        "h4_ema20":                 data.get("h4_ema20"),
        "h4_ema50":                 data.get("h4_ema50"),
        "h4_hh_hl":                 int(bool(data.get("h4_hh_hl"))),
        "m15_higher_low":           int(bool(data.get("m15_higher_low"))),
        "m15_confirmation":         int(data.get("m15_confirmation") or 0),
        "rs_score":                 data.get("rs_score"),
        "rs_vs_qqq":                data.get("rs_vs_qqq"),
        "sector_etf":               data.get("sector_etf"),
        "triggered_at":             triggered_at,
        "last_updated":             data.get("last_updated") or _et_now().strftime("%Y-%m-%d %I:%M %p"),
    })
    conn.commit()
    conn.close()
    data["triggered_at"] = triggered_at


# ---------------------------------------------------------------------------
# Company profile (name / sector / industry / description)
# ---------------------------------------------------------------------------

def save_company_profile(ticker: str, profile: dict) -> None:
    """
    Save/refresh the company profile blurb for a ticker (name, sector,
    industry, description, logo). Upserts a minimal row if stock_data
    doesn't have this ticker yet; otherwise updates only profile columns.
    """
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return

    now = datetime.now().isoformat()
    conn = get_db()
    existing = conn.execute(
        "SELECT 1 FROM stock_data WHERE ticker = ?", (ticker,)
    ).fetchone()

    params = {
        "ticker":               ticker,
        "company_name":         profile.get("company_name"),
        "company_sector":       profile.get("sector"),
        "company_industry":     profile.get("industry"),
        "company_description": profile.get("description"),
        "company_logo_url":     profile.get("logo_url"),
        "profile_fetched_at":   now,
    }

    if existing:
        conn.execute("""
            UPDATE stock_data SET
                company_name        = :company_name,
                company_sector      = :company_sector,
                company_industry    = :company_industry,
                company_description = :company_description,
                company_logo_url    = :company_logo_url,
                profile_fetched_at  = :profile_fetched_at
            WHERE ticker = :ticker
        """, params)
    else:
        conn.execute("""
            INSERT INTO stock_data
                (ticker, company_name, company_sector, company_industry,
                 company_description, company_logo_url, profile_fetched_at,
                 ticker_state, last_updated)
            VALUES
                (:ticker, :company_name, :company_sector, :company_industry,
                 :company_description, :company_logo_url, :profile_fetched_at,
                 'ready', :profile_fetched_at)
        """, params)

    conn.commit()
    conn.close()


def get_company_profile(ticker: str):
    """Return {company_name, sector, industry, description, logo_url, fetched_at} or None."""
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return None
    conn = get_db()
    row = conn.execute(
        """SELECT company_name, company_sector, company_industry,
                  company_description, company_logo_url, profile_fetched_at
           FROM stock_data WHERE ticker = ?""",
        (ticker,),
    ).fetchone()
    conn.close()
    if row is None or not row["company_name"]:
        return None
    return {
        "company_name": row["company_name"],
        "sector":       row["company_sector"],
        "industry":     row["company_industry"],
        "description":  row["company_description"],
        "logo_url":     row["company_logo_url"],
        "fetched_at":   row["profile_fetched_at"],
    }


# ---------------------------------------------------------------------------
# Notes helpers
# ---------------------------------------------------------------------------

def get_note(ticker: str, user_id: int = 1):
    """Return note text for a ticker for this user, or empty string."""
    conn = get_db()
    row = conn.execute(
        "SELECT note_text FROM notes WHERE user_id = ? AND ticker = ?",
        (user_id, ticker.upper())
    ).fetchone()
    conn.close()
    return row["note_text"] if row else ""


def get_all_notes(user_id: int = 1) -> dict:
    """Return a dict of {ticker: note_text} for all notes belonging to this user."""
    conn = get_db()
    rows = conn.execute(
        "SELECT ticker, note_text FROM notes "
        "WHERE user_id = ? AND note_text != '' AND note_text IS NOT NULL",
        (user_id,)
    ).fetchall()
    conn.close()
    return {row["ticker"]: row["note_text"] for row in rows}


def save_note(ticker: str, text: str, user_id: int = 1):
    """Insert or update trade plan note for a ticker."""
    conn = get_db()
    t = ticker.upper()
    now = datetime.now().isoformat()
    existing = conn.execute(
        "SELECT id FROM notes WHERE user_id = ? AND ticker = ?", (user_id, t)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE notes SET note_text = ?, updated_at = ? WHERE id = ?",
            (text, now, existing["id"])
        )
    else:
        conn.execute(
            "INSERT INTO notes (user_id, ticker, note_text, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, t, text, now)
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Pre-market trade plan helpers
# ---------------------------------------------------------------------------

def get_trade_plan(ticker: str, user_id: int = 1) -> dict:
    """Return the structured pre-market plan for a ticker for this user, or empty defaults."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM trade_plans WHERE user_id = ? AND ticker = ?",
        (user_id, ticker.upper())
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return {
        "ticker": ticker.upper(),
        "plan_bias": "",
        "entry_level": None,
        "stop_loss": None,
        "target_price": None,
        "updated_at": None,
    }


def save_trade_plan(ticker: str, plan_bias: str, entry_level, stop_loss, target_price,
                    user_id: int = 1):
    """Insert or update the pre-market plan for a ticker."""
    def _float(v):
        try:
            return float(v) if v not in (None, "") else None
        except (ValueError, TypeError):
            return None

    conn = get_db()
    t = ticker.upper()
    now = datetime.now().isoformat()
    existing = conn.execute(
        "SELECT id FROM trade_plans WHERE user_id = ? AND ticker = ?", (user_id, t)
    ).fetchone()
    vals = (plan_bias or "", _float(entry_level), _float(stop_loss), _float(target_price), now)
    if existing:
        conn.execute(
            "UPDATE trade_plans SET plan_bias=?, entry_level=?, stop_loss=?, "
            "target_price=?, updated_at=? WHERE id=?",
            (*vals, existing["id"])
        )
    else:
        conn.execute(
            "INSERT INTO trade_plans (user_id, ticker, plan_bias, entry_level, stop_loss, "
            "target_price, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, t, *vals)
        )
    conn.commit()
    conn.close()


def get_all_trade_plans(user_id: int = 1) -> dict:
    """Return {ticker: plan_dict} for all tickers with a saved plan for this user."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM trade_plans WHERE user_id = ? AND entry_level IS NOT NULL",
        (user_id,)
    ).fetchall()
    conn.close()
    return {row["ticker"]: dict(row) for row in rows}


# ---------------------------------------------------------------------------
# Trade journal helpers
# ---------------------------------------------------------------------------

def add_journal_entry(ticker, trade_date, direction, entry_price, exit_price,
                      shares, setup_type, momentum_score, pnl_pct, result, notes,
                      trade_mode=None, option_side=None, option_premium=None,
                      contracts=None, stop_price=None, is_aplus_setup=0, user_id: int = 1):
    """Insert a new trade journal entry. Returns the new row id."""
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO journal
            (user_id, ticker, trade_date, direction, entry_price, exit_price,
             shares, setup_type, momentum_score, pnl_pct, result, notes, created_at,
             trade_mode, option_side, option_premium, contracts, stop_price, is_aplus_setup)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id, ticker.upper(), trade_date, direction,
        entry_price, exit_price, shares,
        setup_type, momentum_score, pnl_pct, result, notes,
        datetime.now().isoformat(),
        trade_mode, option_side, option_premium, contracts, stop_price,
        1 if is_aplus_setup else 0,
    ), returning_id=True)
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id


def update_journal_entry(entry_id, ticker, trade_date, direction, entry_price, exit_price,
                         shares, setup_type, momentum_score, pnl_pct, result, notes,
                         trade_mode=None, option_side=None, option_premium=None,
                         contracts=None, stop_price=None, is_aplus_setup=0, user_id: int = 1):
    """Update an existing journal entry by id (only if it belongs to user_id)."""
    conn = get_db()
    conn.execute("""
        UPDATE journal SET
            ticker=?, trade_date=?, direction=?, entry_price=?, exit_price=?,
            shares=?, setup_type=?, momentum_score=?, pnl_pct=?, result=?, notes=?,
            trade_mode=?, option_side=?, option_premium=?, contracts=?, stop_price=?,
            is_aplus_setup=?
        WHERE id=? AND user_id=?
    """, (
        ticker.upper(), trade_date, direction,
        entry_price, exit_price, shares,
        setup_type, momentum_score, pnl_pct, result, notes,
        trade_mode, option_side, option_premium, contracts, stop_price,
        1 if is_aplus_setup else 0,
        entry_id, user_id,
    ))
    conn.commit()
    conn.close()


def delete_journal_entry(entry_id, user_id: int = 1):
    conn = get_db()
    conn.execute("DELETE FROM journal WHERE id = ? AND user_id = ?", (entry_id, user_id))
    conn.commit()
    conn.close()


def get_journal_entry(entry_id, user_id: int = 1):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM journal WHERE id = ? AND user_id = ?", (entry_id, user_id)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_journal_entries(user_id: int = 1) -> list:
    """Return all journal entries for a user, ordered newest first."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM journal WHERE user_id = ? ORDER BY trade_date DESC, created_at DESC",
        (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_journal_entries_for_date(date_str: str, user_id: int = 1) -> list:
    """Return journal entries for a specific date (YYYY-MM-DD) for a user."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM journal WHERE user_id = ? AND trade_date = ? ORDER BY created_at DESC",
        (user_id, date_str)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Daily session helpers (risk engine)
# ---------------------------------------------------------------------------

def get_daily_session(date_str: str | None = None, user_id: int = 1) -> dict:
    """Return today's (or a specific date's) trading session row, or defaults if none."""
    if date_str is None:
        date_str = _et_now().strftime("%Y-%m-%d")
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM daily_sessions WHERE user_id = ? AND session_date = ?",
        (user_id, date_str)
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return {
        "session_date": date_str,
        "locked":       0,
        "lock_reason":  None,
        "updated_at":   None,
    }


def upsert_daily_session(date_str: str, locked: int = 0, lock_reason: str | None = None,
                         user_id: int = 1):
    """Insert or update a daily session record for a user."""
    conn = get_db()
    now = _et_now().strftime("%Y-%m-%d %I:%M %p")
    existing = conn.execute(
        "SELECT id FROM daily_sessions WHERE user_id = ? AND session_date = ?",
        (user_id, date_str)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE daily_sessions SET locked=?, lock_reason=?, updated_at=? WHERE id=?",
            (locked, lock_reason, now, existing["id"])
        )
    else:
        conn.execute(
            "INSERT INTO daily_sessions (user_id, session_date, locked, lock_reason, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, date_str, locked, lock_reason, now)
        )
    conn.commit()
    conn.close()


def lock_daily_session(reason: str, date_str: str | None = None, user_id: int = 1):
    """Lock trading for a given date (default: today)."""
    if date_str is None:
        date_str = _et_now().strftime("%Y-%m-%d")
    upsert_daily_session(date_str, locked=1, lock_reason=reason, user_id=user_id)


def unlock_daily_session(date_str: str | None = None, user_id: int = 1):
    """Manually unlock trading for a given date (default: today)."""
    if date_str is None:
        date_str = _et_now().strftime("%Y-%m-%d")
    upsert_daily_session(date_str, locked=0, lock_reason=None, user_id=user_id)


# ---------------------------------------------------------------------------
# Scanner alert helpers (Phase 1 live opportunity finder)
# ---------------------------------------------------------------------------

_SCANNER_ALERTS_KEEP = 200   # max rows retained to avoid unbounded growth


def add_scanner_alert(
    ticker: str,
    alert_type: str,
    message: str,
    severity: str = "medium",
) -> int:
    """
    Persist a scanner-detected alert.  Returns the new row id.
    Automatically prunes the table to _SCANNER_ALERTS_KEEP rows so it
    never grows unbounded.
    """
    now = _et_now().strftime("%Y-%m-%d %I:%M %p")
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO scanner_alerts (ticker, alert_type, message, severity, seen, created_at) "
        "VALUES (?, ?, ?, ?, 0, ?)",
        (ticker.upper().strip(), alert_type, message, severity, now),
        returning_id=True,
    )
    new_id = cur.lastrowid

    # Prune oldest rows beyond the keep limit
    conn.execute(
        "DELETE FROM scanner_alerts WHERE id NOT IN ("
        "  SELECT id FROM scanner_alerts ORDER BY id DESC LIMIT ?"
        ")",
        (_SCANNER_ALERTS_KEEP,),
    )
    conn.commit()
    conn.close()
    return new_id or 0


def get_scanner_alerts(limit: int = 50) -> list:
    """Return the most-recent scanner alerts, newest first."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, ticker, alert_type, message, severity, seen, created_at "
        "FROM scanner_alerts ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_scanner_alerts_seen() -> None:
    """Mark all scanner alerts as seen (clears the unseen badge)."""
    conn = get_db()
    conn.execute("UPDATE scanner_alerts SET seen = 1 WHERE seen = 0")
    conn.commit()
    conn.close()


def get_unseen_scanner_alert_count() -> int:
    """Return the count of unread scanner alerts."""
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM scanner_alerts WHERE seen = 0"
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


def clear_scanner_alerts() -> None:
    """Delete all scanner alert rows."""
    conn = get_db()
    conn.execute("DELETE FROM scanner_alerts")
    conn.commit()
    conn.close()


# ── Setup outcome tracking (adaptive AI learning) ─────────────────────────────

def save_setup_outcome(
    ticker: str,
    setup_type: str,
    outcome: str,
    regime: str = "",
    pattern: str = "",
    prob_score: int = 0,
    notes: str = "",
    user_id: int = 1,
) -> None:
    """Persist a trade outcome for adaptive learning win-rate tracking."""
    from data_fetcher import _et_now as _db_et_now
    now = _db_et_now().strftime("%Y-%m-%d %I:%M %p")
    conn = get_db()
    conn.execute(
        "INSERT INTO setup_outcomes "
        "(user_id, ticker, setup_type, pattern, outcome, regime, prob_score, notes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id, ticker.upper().strip(), setup_type, pattern, outcome,
            regime, prob_score, notes, now,
        ),
    )
    conn.commit()
    conn.close()


def get_setup_outcomes(limit: int = 200, user_id: int = 1) -> list[dict]:
    """Return recent setup outcomes for a user, ordered newest-first."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM setup_outcomes WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_setup_outcome_stats(user_id: int = 1) -> list[dict]:
    """
    Aggregate setup outcomes by setup_type for a user.
    Returns list of {setup_type, total, wins, losses, win_rate}.
    """
    conn = get_db()
    rows = conn.execute("""
        SELECT
            setup_type,
            COUNT(*)                                                AS total,
            SUM(CASE WHEN outcome = 'win'  THEN 1 ELSE 0 END)     AS wins,
            SUM(CASE WHEN outcome = 'loss' THEN 1 ELSE 0 END)      AS losses,
            ROUND(
                100.0 * SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END)
                / NULLIF(COUNT(*), 0), 1
            )                                                       AS win_rate
        FROM setup_outcomes
        WHERE user_id = ?
        GROUP BY setup_type
        ORDER BY win_rate DESC
    """, (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Schwab import tracking ────────────────────────────────────────────────────

def schwab_import_exists(import_key: str, user_id: int = 1) -> bool:
    """Return True if this trade pair was already imported for this user."""
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM schwab_imports WHERE user_id = ? AND import_key = ?",
        (user_id, import_key)
    ).fetchone()
    conn.close()
    return row is not None


def record_schwab_import(import_key: str, journal_id: int,
                         ticker: str, trade_date: str, user_id: int = 1) -> None:
    """Mark a Schwab trade pair as imported for this user so it won't be re-imported."""
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM schwab_imports WHERE user_id = ? AND import_key = ?",
        (user_id, import_key)
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO schwab_imports "
            "(user_id, import_key, journal_id, ticker, trade_date, imported_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, import_key, journal_id, ticker, trade_date, datetime.now().isoformat()),
        )
    conn.commit()
    conn.close()


def get_schwab_import_keys(user_id: int = 1) -> set:
    """Return the set of all already-imported Schwab trade pair keys for this user."""
    conn = get_db()
    rows = conn.execute(
        "SELECT import_key FROM schwab_imports WHERE user_id = ?", (user_id,)
    ).fetchall()
    conn.close()
    return {r["import_key"] for r in rows}


# ── Nasdaq-100 constituent tracking ──────────────────────────────────────────

def get_latest_ndx_snapshot() -> tuple:
    """Return (snapshot_date, set_of_tickers) for the most recent NDX snapshot."""
    conn = get_db()
    row = conn.execute(
        "SELECT snapshot_date FROM ndx_constituents ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()
    if not row:
        conn.close()
        return None, set()
    snap_date = row["snapshot_date"]
    rows = conn.execute(
        "SELECT ticker FROM ndx_constituents WHERE snapshot_date = ?", (snap_date,)
    ).fetchall()
    conn.close()
    return snap_date, {r["ticker"] for r in rows}


def save_ndx_snapshot(tickers_data: list, snapshot_date: str) -> None:
    """Persist a full NDX constituent snapshot. tickers_data: [{ticker, weight_pct, company_name}]"""
    conn = get_db()
    for item in tickers_data:
        conn.execute(
            "INSERT OR IGNORE INTO ndx_constituents "
            "(snapshot_date, ticker, weight_pct, company_name) VALUES (?, ?, ?, ?)",
            (snapshot_date, item["ticker"], item.get("weight_pct"), item.get("company_name", "")),
        )
    # Retain only the 30 most recent snapshot dates to bound table growth
    conn.execute(
        "DELETE FROM ndx_constituents WHERE snapshot_date NOT IN ("
        "  SELECT DISTINCT snapshot_date FROM ndx_constituents "
        "  ORDER BY snapshot_date DESC LIMIT 30"
        ")"
    )
    conn.commit()
    conn.close()


def save_ndx_change(ticker: str, change_type: str, detected_date: str,
                    company_name: str = "") -> None:
    """Record an NDX constituent add or removal event."""
    conn = get_db()
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO ndx_changes "
        "(ticker, change_type, detected_date, company_name, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (ticker.upper(), change_type, detected_date, company_name or "", now),
    )
    # Keep the 200 most recent changes
    conn.execute(
        "DELETE FROM ndx_changes WHERE id NOT IN ("
        "  SELECT id FROM ndx_changes ORDER BY id DESC LIMIT 200"
        ")"
    )
    conn.commit()
    conn.close()


def get_ndx_changes(limit: int = 20) -> list:
    """Return recent NDX constituent changes, newest first."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM ndx_changes ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_ndx_latest_constituents(limit: int = 15) -> list:
    """Return top-N constituents from the most recent snapshot, ordered by weight."""
    conn = get_db()
    row = conn.execute(
        "SELECT snapshot_date FROM ndx_constituents ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()
    if not row:
        conn.close()
        return []
    snap_date = row["snapshot_date"]
    rows = conn.execute(
        "SELECT ticker, weight_pct, company_name FROM ndx_constituents "
        "WHERE snapshot_date = ? ORDER BY weight_pct DESC LIMIT ?",
        (snap_date, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Study log helpers
# ---------------------------------------------------------------------------

def save_study_log_entry(user_id: int, question: str, answer: str) -> int:
    """Save a Q&A pair to the study log. Returns the new entry id."""
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO study_log (user_id, question, answer, created_at) VALUES (?, ?, ?, ?)",
        (user_id, question, answer, datetime.now().isoformat()),
        returning_id=True,
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id


def get_study_log(user_id: int) -> list:
    """Return all study log entries for a user, newest first."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, question, answer, created_at FROM study_log "
        "WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_study_log_entry(user_id: int, entry_id: int) -> bool:
    """Delete a study log entry (scoped to user). Returns True if a row was deleted."""
    conn = get_db()
    conn.execute(
        "DELETE FROM study_log WHERE id = ? AND user_id = ?",
        (entry_id, user_id),
    )
    conn.commit()
    conn.close()
    return True


# ---------------------------------------------------------------------------
# Fundamentals cache helpers
# ---------------------------------------------------------------------------

def get_fundamentals_cache(ticker: str) -> dict | None:
    """Return cached fundamentals data if fresher than 24 hours, else None."""
    conn = get_db()
    row = conn.execute(
        "SELECT data_json, fetched_at FROM fundamentals_cache WHERE ticker = ?",
        (ticker.upper(),),
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        fetched = datetime.fromisoformat(row["fetched_at"])
        age_hours = (datetime.now() - fetched).total_seconds() / 3600
        if age_hours > 24:
            return None
        import json as _json
        return _json.loads(row["data_json"])
    except Exception:
        return None


def save_fundamentals_cache(ticker: str, data: dict) -> None:
    """Upsert fundamentals data into cache with current timestamp."""
    import json as _json
    conn = get_db()
    conn.execute(
        "INSERT INTO fundamentals_cache (ticker, data_json, fetched_at) VALUES (?, ?, ?) "
        "ON CONFLICT(ticker) DO UPDATE SET data_json = excluded.data_json, fetched_at = excluded.fetched_at",
        (ticker.upper(), _json.dumps(data), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# AI Briefings cache
# ---------------------------------------------------------------------------

def get_ai_briefing(date: str):
    """Return today's cached briefing dict, or None if not found."""
    import json as _json
    conn = get_db()
    row = conn.execute(
        "SELECT json_response FROM ai_briefings WHERE date = ?", (date,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        return _json.loads(row["json_response"])
    except Exception:
        return None


def save_ai_briefing(date: str, data: dict) -> None:
    """Upsert an AI briefing for the given date."""
    import json as _json
    conn = get_db()
    conn.execute(
        "INSERT INTO ai_briefings (date, json_response, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(date) DO UPDATE SET json_response = excluded.json_response, created_at = excluded.created_at",
        (date, _json.dumps(data), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# AI Score Narrations
# ---------------------------------------------------------------------------

def get_score_narration(ticker: str, date: str, score_key: str):
    """Return cached narration dict for (ticker, date, score_key), or None."""
    import json as _json
    conn = get_db()
    row = conn.execute(
        "SELECT json_response FROM score_narrations WHERE ticker = ? AND date = ? AND score_key = ?",
        (ticker.upper(), date, score_key),
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        return _json.loads(row["json_response"])
    except Exception:
        return None


def save_score_narration(ticker: str, date: str, score_key: str, data: dict) -> None:
    """Upsert a score narration row."""
    import json as _json
    conn = get_db()
    conn.execute(
        "INSERT INTO score_narrations (ticker, date, score_key, json_response, created_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(ticker, date, score_key) DO UPDATE SET "
        "json_response = excluded.json_response, created_at = excluded.created_at",
        (ticker.upper(), date, score_key, _json.dumps(data), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# AI Journal Summaries
# ---------------------------------------------------------------------------

def get_journal_summary(week_key: str):
    """Return cached journal summary dict for a week_key (e.g. '2026-W27'), or None."""
    import json as _json
    conn = get_db()
    row = conn.execute(
        "SELECT json_response FROM journal_summaries WHERE week_key = ?",
        (week_key,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        return _json.loads(row["json_response"])
    except Exception:
        return None


def save_journal_summary(week_key: str, data: dict) -> None:
    """Upsert a journal summary for a week."""
    import json as _json
    conn = get_db()
    conn.execute(
        "INSERT INTO journal_summaries (week_key, json_response, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(week_key) DO UPDATE SET json_response = excluded.json_response, created_at = excluded.created_at",
        (week_key, _json.dumps(data), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# AI Earnings Digests
# ---------------------------------------------------------------------------

def get_earnings_digest(date: str):
    """Return cached earnings digest dict for a date (YYYY-MM-DD), or None."""
    import json as _json
    conn = get_db()
    row = conn.execute(
        "SELECT json_response FROM earnings_digests WHERE date = ?",
        (date,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        return _json.loads(row["json_response"])
    except Exception:
        return None


def save_earnings_digest(date: str, data: dict) -> None:
    """Upsert an earnings digest for the given date."""
    import json as _json
    conn = get_db()
    conn.execute(
        "INSERT INTO earnings_digests (date, json_response, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(date) DO UPDATE SET json_response = excluded.json_response, created_at = excluded.created_at",
        (date, _json.dumps(data), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
