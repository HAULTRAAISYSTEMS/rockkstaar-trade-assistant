"""One account must not be able to read or destroy another's data.

Three separate places had no user scoping. The watchlist write deleted by
ticker across the whole table, so posting an empty form on one stock's page
removed it from every other account. The journal summary selected the week's
trades with no user filter and cached the result under the week alone, so the
first person to ask got a digest of everyone's trades and everyone after got
that same digest. current_user_id() defaulted to the bootstrap admin for any
caller without a session.
"""
import ast
import sqlite3

import pytest

import database


SCHEMA = """
CREATE TABLE watchlists(id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT);
CREATE TABLE watchlist_stocks(watchlist_id INTEGER, ticker TEXT, added_date TEXT,
                              UNIQUE(watchlist_id, ticker));
"""


class _KeepOpen:
    """sqlite3.Connection has no settable attributes, and the code under test
    closes what get_db() hands it."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        pass


@pytest.fixture
def db(monkeypatch):
    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    conn = _KeepOpen(real)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO watchlists(id,user_id,name) VALUES(?,?,?)",
                     [(1, 1, "alice-core"), (2, 1, "alice-swing"), (3, 2, "bob-core")])
    conn.executemany(
        "INSERT INTO watchlist_stocks(watchlist_id,ticker,added_date) VALUES(?,?,?)",
        [(1, "NVDA", "x"), (3, "NVDA", "x")])
    conn.commit()
    monkeypatch.setattr(database, "get_db", lambda: conn)
    yield conn


def members(conn, ticker="NVDA"):
    return sorted(r["watchlist_id"] for r in conn.execute(
        "SELECT watchlist_id FROM watchlist_stocks WHERE ticker=?", (ticker,)))


def test_clearing_your_own_lists_leaves_other_accounts_alone(db):
    """The DELETE was global by ticker. An empty form on the NVDA page wiped
    NVDA out of every user's watchlists."""
    database.set_ticker_watchlists("NVDA", [], user_id=1)
    assert members(db) == [3]          # Bob's row survives


def test_you_cannot_add_a_ticker_to_someone_elses_list(db):
    database.set_ticker_watchlists("NVDA", [2, 3], user_id=1)
    assert members(db) == [2, 3]       # 3 was already Bob's, not newly added
    database.set_ticker_watchlists("AMD", [3], user_id=1)
    assert members(db, "AMD") == []    # nothing written into Bob's list


def test_your_own_assignment_still_works(db):
    database.set_ticker_watchlists("NVDA", [1, 2], user_id=1)
    assert members(db) == [1, 2, 3]


def test_junk_ids_are_skipped_not_crashed(db):
    database.set_ticker_watchlists("NVDA", ["", None, "abc", 1], user_id=1)
    assert members(db) == [1, 3]


# ── The scoping that lives in app.py ─────────────────────────────────────────

APP = open("app.py").read()


def test_the_journal_summary_query_filters_by_user():
    start = APP.index("def api_journal_summary")
    body = APP[start:start + 4000]
    assert "FROM journal WHERE user_id = ?" in body


def test_the_journal_summary_cache_key_carries_the_user():
    start = APP.index("def api_journal_summary")
    body = APP[start:start + 12000]
    assert 'f"u{current_user_id()}:{week_key}"' in body
    assert "get_journal_summary(cache_key)" in body
    assert "save_journal_summary(cache_key" in body


def test_an_anonymous_caller_is_not_the_admin():
    start = APP.index("def current_user_id")
    body = APP[start:APP.index("\ndef ", start + 10)]
    assert 'session.get("user_id", 1)' not in body
    assert "or 0" in body


def test_debug_endpoints_require_admin():
    for route in ('@app.route("/debug/status")', '@app.route("/api/intel/debug")'):
        i = APP.index(route)
        assert "@require_admin" in APP[i:i + 120], route


def test_require_admin_returns_a_status_not_a_tuple():
    """The conditional bound inside the tuple, so a non-API path returned
    (Response, (html, 403)). Flask read the inner tuple as headers and raised —
    every 403 became a 500 and the login page never rendered."""
    tree = ast.parse(APP)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "require_admin")
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
            assert len(node.value.elts) == 2
            second = node.value.elts[1]
            assert not isinstance(second, (ast.Tuple, ast.IfExp)), ast.dump(second)
