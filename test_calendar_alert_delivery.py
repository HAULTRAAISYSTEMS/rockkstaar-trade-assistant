"""Once each, to each browser — proved against the database, not the plan.

build_alerts decides what to send; this is the half that decides it is sent
exactly once. The guarantee is a UNIQUE(subscription_id, alert_key) row
written before the push, which is what makes the runner's cadence
irrelevant: fire it every five minutes inside a window and the alert still
goes out once.

It needs its own table rather than reusing browser_push_deliveries, whose
post_id is a foreign key into research_posts and would reject a synthetic
key like "digest:morning_of:2026-11-03".
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")

import browser_push
import calendar_alerts as ca
import database


TODAY = datetime(2026, 11, 3, 13, 0, tzinfo=timezone.utc)   # 8am ET


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    database.init_db()
    from migration_runner import run_migrations
    run_migrations()
    c = database.get_db()
    yield c
    c.close()


def _subscribe(conn, user_id=1):
    sub_id = str(uuid4())
    conn.execute(
        "INSERT INTO browser_push_subscriptions "
        "(id,user_id,endpoint_hash,subscription_ciphertext,enabled,created_at,updated_at) "
        "VALUES (?,?,?,?,1,?,?)",
        (sub_id, user_id, str(uuid4()), "ciphertext", TODAY.isoformat(), TODAY.isoformat()))
    # One preferences row per user — a second device is another subscription,
    # not another set of settings.
    if not conn.execute("SELECT 1 FROM browser_push_preferences WHERE user_id=?",
                        (user_id,)).fetchone():
        conn.execute(
            "INSERT INTO browser_push_preferences "
            "(user_id,enabled,quiet_start,quiet_end,timezone,daily_cap,created_at,updated_at) "
            "VALUES (?,1,0,0,'America/New_York',10,?,?)",
            (user_id, TODAY.isoformat(), TODAY.isoformat()))
    conn.commit()
    return sub_id


def _calendar():
    return {"today": [{"ticker": "AMD", "date": "2026-11-03",
                       "time_label": "AMC", "eps_est": 1.68}],
            "tomorrow": [], "this_week": [], "coming_up": []}


def _run(conn, now=TODAY):
    with patch.object(browser_push, "_send") as send, \
         patch.object(browser_push, "_decrypt_subscription", lambda c: {"endpoint": "x"}), \
         patch.object(browser_push, "_tracked_tickers", lambda c, u, cards: ["AMD"]):
        result = browser_push.deliver_calendar_alerts(
            conn, now=now, calendar=_calendar(), macro_events=[])
    return result, send


class TestItSendsOnceAndOnlyOnce:
    def test_the_first_pass_sends(self, conn):
        _subscribe(conn)
        result, send = _run(conn)
        assert result["push_sent"] == 1
        assert send.call_count == 1

    def test_the_second_pass_sends_nothing(self, conn):
        _subscribe(conn)
        _run(conn)
        result, send = _run(conn)
        assert send.call_count == 0
        assert result["push_sent"] == 0

    def test_ten_passes_inside_the_window_still_send_once(self, conn):
        """The cadence of the runner is not the guarantee; the key is."""
        _subscribe(conn)
        total = 0
        for minute in range(0, 50, 5):
            _, send = _run(conn, now=TODAY + timedelta(minutes=minute))
            total += send.call_count
        assert total == 1

    def test_a_delivery_row_records_it(self, conn):
        _subscribe(conn)
        _run(conn)
        rows = conn.execute(
            "SELECT alert_key,status FROM calendar_alert_deliveries").fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "sent"
        assert rows[0]["alert_key"] == "digest:morning_of:2026-11-03"

    def test_each_browser_gets_its_own(self, conn):
        _subscribe(conn); _subscribe(conn)
        _, send = _run(conn)
        assert send.call_count == 2


class TestTheGuaranteeIsTheConstraintNotTheCode:
    def test_a_duplicate_key_cannot_be_inserted(self, conn):
        sub = _subscribe(conn)
        for _ in range(2):
            try:
                conn.execute(
                    "INSERT INTO calendar_alert_deliveries "
                    "(id,user_id,subscription_id,alert_key,status,attempted_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (str(uuid4()), 1, sub, "dup:key", "sent", TODAY.isoformat()))
                conn.commit()
            except Exception:
                pass
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM calendar_alert_deliveries WHERE alert_key='dup:key'"
        ).fetchone()["n"]
        assert n == 1


class TestTheUsersOwnSettingsAreRespected:
    def test_quiet_hours_skip_the_send(self, conn):
        _subscribe(conn)
        conn.execute("UPDATE browser_push_preferences SET quiet_start=6,quiet_end=12")
        conn.commit()
        result, send = _run(conn)
        assert send.call_count == 0 and result["push_skipped"] == 1

    def test_the_daily_cap_applies(self, conn):
        _subscribe(conn)
        conn.execute("UPDATE browser_push_preferences SET daily_cap=1")
        conn.commit()
        _run(conn)
        result, send = _run(conn, now=TODAY + timedelta(minutes=10))
        assert send.call_count == 0

    def test_push_disabled_entirely_sends_nothing(self, conn):
        _subscribe(conn)
        conn.execute("UPDATE browser_push_preferences SET enabled=0")
        conn.commit()
        _, send = _run(conn)
        assert send.call_count == 0

    def test_turning_the_trigger_off_sends_nothing(self, conn):
        _subscribe(conn)
        conn.execute("UPDATE browser_push_preferences SET calendar_triggers=''")
        conn.commit()
        _, send = _run(conn)
        assert send.call_count == 0

    def test_the_env_kill_switch_wins(self, conn, monkeypatch):
        _subscribe(conn)
        monkeypatch.setenv("BROWSER_PUSH_ENABLED", "0")
        result, send = _run(conn)
        assert send.call_count == 0 and result["push_enabled"] is False


class TestStateIsRecordedForTheNextPass:
    def test_the_calendar_state_is_written(self, conn):
        _subscribe(conn)
        _run(conn)
        row = conn.execute(
            "SELECT time_label FROM calendar_event_state WHERE event_key=?",
            (ca.event_key("earn", "AMD", "2026-11-03"),)).fetchone()
        assert row is not None and row["time_label"] == "AMC"

    def test_it_is_written_even_with_no_subscribers(self, conn):
        """Otherwise the first person to subscribe gets every already-known
        session announced as if it had just been confirmed."""
        _run(conn)
        n = conn.execute("SELECT COUNT(*) AS n FROM calendar_event_state").fetchone()["n"]
        assert n == 1
