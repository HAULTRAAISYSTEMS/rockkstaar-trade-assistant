"""One connection per request, not twenty-three.

get_db() opened a brand new connection every time it was called, and a page
calls it many times. Measured, before:

    /                      23 connections
    /terminal              11
    /watchlists            15
    /api/terminal/quotes   18     ← every four seconds, per open tab

Against SQLite that is cheap. Against Render's Postgres each one is a TCP
handshake, a TLS negotiation and an auth round trip before the first query
runs, so most of a page's latency was spent opening connections — and one
open Terminal tab could work through the connection cap by itself. The
session-hardening commit made it worse by adding a user lookup to every
request, including the polls.
"""
import database
import web_app


class TestTheRequestSharesOneConnection:
    def test_repeated_calls_inside_a_request_return_the_same_handle(self):
        with web_app.app.test_request_context("/"):
            a = database.get_db()
            b = database.get_db()
            c = database.get_db()
            assert a is b is c

    def test_closing_it_does_not_close_it(self):
        """Call sites close eagerly. Honouring that would mean reopening for
        the next query, which is the cost this exists to avoid."""
        with web_app.app.test_request_context("/"):
            conn = database.get_db()
            conn.close()
            assert conn.execute("SELECT 1").fetchone() is not None
            assert database.get_db() is conn

    def test_teardown_really_closes_it(self):
        ctx = web_app.app.test_request_context("/")
        ctx.push()
        conn = database.get_db()
        conn.execute("SELECT 1").fetchone()
        ctx.pop()
        import pytest, sqlite3
        with pytest.raises(Exception):
            conn.execute("SELECT 1")

    def test_two_requests_do_not_share(self):
        with web_app.app.test_request_context("/"):
            first = database.get_db()
        with web_app.app.test_request_context("/"):
            second = database.get_db()
        assert first is not second

    def test_outside_a_request_nothing_changes(self):
        """Background refresh threads have no app context and must keep
        getting their own connection — psycopg2 connections are not safe to
        share across threads."""
        a = database.get_db()
        b = database.get_db()
        assert a is not b
        a.close(); b.close()

    def test_a_standalone_connection_still_closes_on_close(self):
        conn = database.get_db()
        conn.close()
        import pytest
        with pytest.raises(Exception):
            conn.execute("SELECT 1")

    def test_the_teardown_is_registered(self):
        funcs = web_app.app.teardown_appcontext_funcs
        assert any(getattr(f, "__name__", "") == "close_request_db" for f in funcs)


class TestAFailedQueryDoesNotPoisonTheRest:
    def test_the_rollback_guard_is_there_for_postgres(self):
        """On PostgreSQL a failed statement aborts the transaction and every
        later statement fails until it is rolled back. That died with the
        connection when each caller had its own; on a shared one it would
        take down the rest of the page."""
        from pathlib import Path
        src = Path("database.py").read_text()
        body = src[src.index("def execute"):]
        body = body[:body.index("\n    def ")]
        assert "self._shared and _USE_POSTGRES" in body
        assert "rollback()" in body

    def test_a_bad_query_leaves_the_connection_usable(self):
        with web_app.app.test_request_context("/"):
            conn = database.get_db()
            try:
                conn.execute("SELECT * FROM a_table_that_is_not_there")
            except Exception:
                pass
            assert conn.execute("SELECT 1").fetchone() is not None
