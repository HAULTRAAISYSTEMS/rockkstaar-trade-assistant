"""Hot paths must not run DDL on Postgres.

CREATE INDEX IF NOT EXISTS takes a SHARE lock on its table even when the index
exists, so it waits behind any open transaction writing that table (the
5-minute cron writes notifications and insider trades). The notification bell
runs it on every page; with 8 gunicorn threads a few of those waits froze the
whole app every time the cron ran.
"""
import database
import live_research_runner
import notify_inbox


class FakeConn:
    def __init__(self, existing):
        self.sql, self.existing, self.commits, self.closed = [], set(existing), 0, False

    def execute(self, sql, params=None):
        self.sql.append(sql)
        conn = self

        class Cur:
            def fetchone(self_inner):
                return {"r": params[0] if params and params[0] in conn.existing else None}
        return Cur()

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def test_postgres_schema_runs_once_on_its_own_connection(monkeypatch):
    opened = []
    monkeypatch.setattr(database, "_USE_POSTGRES", True)
    monkeypatch.setattr(database, "_SCHEMA_READY", set())
    monkeypatch.setattr(database, "_new_conn", lambda shared=False: opened.append(FakeConn([])) or opened[-1])
    request_conn = FakeConn([])
    notify_inbox.ensure_tables(request_conn)
    notify_inbox.ensure_tables(request_conn)
    notify_inbox.ensure_tables(request_conn)
    assert request_conn.sql == []                      # the request's transaction never runs DDL
    assert len(opened) == 1 and opened[0].commits == 1 and opened[0].closed
    assert any("CREATE INDEX" in s for s in opened[0].sql)


def test_postgres_schema_skips_ddl_when_everything_exists(monkeypatch):
    opened = []
    monkeypatch.setattr(database, "_USE_POSTGRES", True)
    monkeypatch.setattr(database, "_SCHEMA_READY", set())
    monkeypatch.setattr(database, "_new_conn", lambda shared=False: opened.append(
        FakeConn(["notifications", "idx_notifications_user_time"])) or opened[-1])
    notify_inbox.ensure_tables(FakeConn([]))
    assert not any("CREATE" in s for s in opened[0].sql)   # catalog check only: no lock taken


def test_sqlite_still_creates_on_the_given_connection():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    notify_inbox.ensure_tables(conn)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='notifications'").fetchone()


def test_cron_commits_between_steps():
    src = open(live_research_runner.__file__).read()
    assert src.count("_checkpoint(conn)") >= 9
