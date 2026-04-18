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

if _USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
    logger.info("DB  backend=postgresql")
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

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def get_db() -> _Conn:
    """Return a wrapped database connection (PG or SQLite based on env)."""
    if _USE_POSTGRES:
        try:
            conn = psycopg2.connect(_DATABASE_URL)
            return _Conn(conn)
        except Exception as exc:
            logger.error("DB  PostgreSQL connection failed: %s", exc)
            raise
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return _Conn(conn)


DEFAULT_WATCHLISTS = ["A+ Swing Setups", "Secondary Swing Watch", "Extended", "Core Swing Plays"]


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

    # Legacy watchlist table — kept for migration reading only, no longer written
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT UNIQUE NOT NULL,
            added_date TEXT NOT NULL
        )
    """))

    # Named watchlists
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS watchlists (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
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

    # Notes: user's trade plan notes per ticker
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT UNIQUE NOT NULL,
            note_text TEXT,
            updated_at TEXT
        )
    """))

    # Trade journal: one row per executed trade
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS journal (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
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

    # Pre-market plans: structured trade plan fields per ticker
    cursor.execute(_adapt_ddl("""
        CREATE TABLE IF NOT EXISTS trade_plans (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker       TEXT UNIQUE NOT NULL,
            plan_bias    TEXT,
            entry_level  REAL,
            stop_loss    REAL,
            target_price REAL,
            updated_at   TEXT
        )
    """))

    # Seed default watchlists on first run (use cnt alias — works in both DBs)
    wl_count = cursor.execute(
        "SELECT COUNT(*) AS cnt FROM watchlists"
    ).fetchone()["cnt"]

    now_iso = datetime.now().isoformat()
    if wl_count == 0:
        for name in DEFAULT_WATCHLISTS:
            cursor.execute(
                "INSERT OR IGNORE INTO watchlists (name, created_at) VALUES (?, ?)",
                (name, now_iso)
            )
        # Migrate legacy watchlist table data into "Swing Ready"
        first_row = cursor.execute("SELECT id FROM watchlists LIMIT 1").fetchone()
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

    # ── Rename any legacy watchlist names to the swing-focused labels ──────
    _LEGACY_RENAMES = {
        # Original names → v2 names
        "A+ Momentum":          "A+ Swing Setups",
        "Secondary Watch":      "Secondary Swing Watch",
        "Swing Watchlist":      "Extended",
        "Core":                 "Core Swing Plays",
        # v2 names → v3 names
        "Swing Ready":          "A+ Swing Setups",
        "Pullback Zone":        "Secondary Swing Watch",
        "Core List":            "Core Swing Plays",
    }
    for old_name, new_name in _LEGACY_RENAMES.items():
        cursor.execute(
            "UPDATE watchlists SET name = ? WHERE name = ?",
            (new_name, old_name),
        )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Named watchlist helpers
# ---------------------------------------------------------------------------

def get_all_watchlists() -> list:
    """Return all watchlists ordered by creation."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM watchlists ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_watchlist_by_id(wl_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM watchlists WHERE id = ?", (wl_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_watchlist(name: str) -> int:
    """Create a new named watchlist. Returns its id."""
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO watchlists (name, created_at) VALUES (?, ?)",
        (name.strip(), datetime.now().isoformat()),
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


def get_watchlist_stock_counts() -> dict:
    """Return {watchlist_id: count} for all watchlists."""
    conn = get_db()
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


def remove_ticker_from_defaults(ticker: str):
    """
    Remove a ticker from ALL default watchlists in one operation.

    Called when the user explicitly deletes a ticker so it cannot be
    re-inserted by auto-classification.  The ticker stays in any user-created
    custom watchlists; stock_data is deleted only when no memberships remain.
    """
    conn = get_db()
    t = ticker.upper().strip()
    logger.info("DB REMOVE FROM DEFAULTS  ticker=%s", t)

    # Resolve IDs of the four built-in lists
    placeholders = ",".join(["?"] * len(DEFAULT_WATCHLISTS))
    rows = conn.execute(
        f"SELECT id FROM watchlists WHERE name IN ({placeholders})",
        DEFAULT_WATCHLISTS,
    ).fetchall()
    default_ids = [r["id"] for r in rows]

    for wl_id in default_ids:
        conn.execute(
            "DELETE FROM watchlist_stocks WHERE watchlist_id = ? AND ticker = ?",
            (wl_id, t),
        )

    # Remove stock_data if the ticker is now in no watchlist at all
    remaining = conn.execute(
        "SELECT COUNT(*) AS cnt FROM watchlist_stocks WHERE ticker = ?", (t,)
    ).fetchone()["cnt"]
    if remaining == 0:
        conn.execute("DELETE FROM stock_data WHERE ticker = ?", (t,))

    conn.commit()
    conn.close()


def get_ticker_watchlist_ids(ticker: str) -> list:
    """Return list of watchlist IDs that contain this ticker."""
    conn = get_db()
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
             ema_20_daily, ema_50_daily, ema_200_daily,
             pct_from_ema20, pct_from_ema50, daily_trend,
             daily_hh_hl, daily_lh_ll,
             fib_high, fib_low, fib_50, fib_618,
             swing_score, swing_reason, swing_confidence,
             swing_setup_type, swing_status,
             entry_zone_low, entry_zone_high, stop_level,
             target_1, target_2, risk_reward, swing_data_fetched_at,
             h4_trend, h4_ema20, h4_ema50, h4_hh_hl,
             m15_higher_low, m15_confirmation, ticker_state)
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
             :ema_20_daily, :ema_50_daily, :ema_200_daily,
             :pct_from_ema20, :pct_from_ema50, :daily_trend,
             :daily_hh_hl, :daily_lh_ll,
             :fib_high, :fib_low, :fib_50, :fib_618,
             :swing_score, :swing_reason, :swing_confidence,
             :swing_setup_type, :swing_status,
             :entry_zone_low, :entry_zone_high, :stop_level,
             :target_1, :target_2, :risk_reward, :swing_data_fetched_at,
             :h4_trend, :h4_ema20, :h4_ema50, :h4_hh_hl,
             :m15_higher_low, :m15_confirmation, :ticker_state)
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
            ticker_state             = excluded.ticker_state
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
        "triggered_at":             triggered_at,
        "last_updated":             data.get("last_updated") or _et_now().strftime("%Y-%m-%d %I:%M %p"),
    })
    conn.commit()
    conn.close()
    data["triggered_at"] = triggered_at


# ---------------------------------------------------------------------------
# Notes helpers
# ---------------------------------------------------------------------------

def get_note(ticker: str):
    """Return note text for a ticker, or empty string."""
    conn = get_db()
    row = conn.execute(
        "SELECT note_text FROM notes WHERE ticker = ?", (ticker.upper(),)
    ).fetchone()
    conn.close()
    return row["note_text"] if row else ""


def get_all_notes() -> dict:
    """Return a dict of {ticker: note_text} for all tickers that have notes."""
    conn = get_db()
    rows = conn.execute(
        "SELECT ticker, note_text FROM notes WHERE note_text != '' AND note_text IS NOT NULL"
    ).fetchall()
    conn.close()
    return {row["ticker"]: row["note_text"] for row in rows}


def save_note(ticker: str, text: str):
    """Insert or update trade plan note for a ticker."""
    conn = get_db()
    conn.execute("""
        INSERT INTO notes (ticker, note_text, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            note_text  = excluded.note_text,
            updated_at = excluded.updated_at
    """, (ticker.upper(), text, datetime.now().isoformat()))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Pre-market trade plan helpers
# ---------------------------------------------------------------------------

def get_trade_plan(ticker: str) -> dict:
    """Return the structured pre-market plan for a ticker, or empty defaults."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM trade_plans WHERE ticker = ?", (ticker.upper(),)
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


def save_trade_plan(ticker: str, plan_bias: str, entry_level, stop_loss, target_price):
    """Insert or update the pre-market plan for a ticker."""
    def _float(v):
        try:
            return float(v) if v not in (None, "") else None
        except (ValueError, TypeError):
            return None

    conn = get_db()
    conn.execute("""
        INSERT INTO trade_plans (ticker, plan_bias, entry_level, stop_loss, target_price, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            plan_bias    = excluded.plan_bias,
            entry_level  = excluded.entry_level,
            stop_loss    = excluded.stop_loss,
            target_price = excluded.target_price,
            updated_at   = excluded.updated_at
    """, (
        ticker.upper(),
        plan_bias or "",
        _float(entry_level),
        _float(stop_loss),
        _float(target_price),
        datetime.now().isoformat(),
    ))
    conn.commit()
    conn.close()


def get_all_trade_plans() -> dict:
    """Return {ticker: plan_dict} for all tickers that have a saved plan."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM trade_plans WHERE entry_level IS NOT NULL"
    ).fetchall()
    conn.close()
    return {row["ticker"]: dict(row) for row in rows}


# ---------------------------------------------------------------------------
# Trade journal helpers
# ---------------------------------------------------------------------------

def add_journal_entry(ticker, trade_date, direction, entry_price, exit_price,
                      shares, setup_type, momentum_score, pnl_pct, result, notes):
    """Insert a new trade journal entry. Returns the new row id."""
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO journal
            (ticker, trade_date, direction, entry_price, exit_price,
             shares, setup_type, momentum_score, pnl_pct, result, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ticker.upper(), trade_date, direction,
        entry_price, exit_price, shares,
        setup_type, momentum_score, pnl_pct, result, notes,
        datetime.now().isoformat()
    ), returning_id=True)
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id


def update_journal_entry(entry_id, ticker, trade_date, direction, entry_price, exit_price,
                         shares, setup_type, momentum_score, pnl_pct, result, notes):
    """Update an existing journal entry by id."""
    conn = get_db()
    conn.execute("""
        UPDATE journal SET
            ticker=?, trade_date=?, direction=?, entry_price=?, exit_price=?,
            shares=?, setup_type=?, momentum_score=?, pnl_pct=?, result=?, notes=?
        WHERE id=?
    """, (
        ticker.upper(), trade_date, direction,
        entry_price, exit_price, shares,
        setup_type, momentum_score, pnl_pct, result, notes,
        entry_id
    ))
    conn.commit()
    conn.close()


def delete_journal_entry(entry_id):
    conn = get_db()
    conn.execute("DELETE FROM journal WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()


def get_journal_entry(entry_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM journal WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_journal_entries() -> list:
    """Return all journal entries ordered newest first."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM journal ORDER BY trade_date DESC, created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
