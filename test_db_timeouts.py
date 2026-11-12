"""One stuck database session must not be able to freeze the app."""
import json
import time

import database
import live_research_runner


def test_postgres_sessions_carry_lock_and_idle_limits(monkeypatch):
    seen = {}

    class FakePg:
        @staticmethod
        def connect(url, **kw):
            seen.update(kw)
            raise RuntimeError("stop here")
    monkeypatch.setattr(database, "_USE_POSTGRES", True)
    monkeypatch.setattr(database, "psycopg2", FakePg)
    try:
        database._new_conn()
    except RuntimeError:
        pass
    opts = seen["options"]
    assert "lock_timeout=" in opts and "statement_timeout=" in opts
    assert "idle_in_transaction_session_timeout=" in opts and seen["connect_timeout"] == 10


def test_cron_run_is_cut_off_at_its_deadline(monkeypatch, capsys):
    monkeypatch.setattr(live_research_runner, "main", lambda: time.sleep(5))
    t0 = time.time()
    assert live_research_runner._run_with_deadline(1) == 1
    assert time.time() - t0 < 3
    assert "run_timeout" in json.loads(capsys.readouterr().out.strip().splitlines()[-1])
