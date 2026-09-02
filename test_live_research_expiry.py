"""Retiring Live Research items nobody reviewed.

The queue only ever grew: discovery adds catalysts every five minutes, review
is manual, and nothing removed an item that was never looked at. It reached 823
items, which is not a queue anyone works through — and a catalyst that has sat
unreviewed for a week has already told you whatever it was going to.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import live_research_expiry as expiry

SCHEMA = """CREATE TABLE research_posts(
  id TEXT PRIMARY KEY, ticker TEXT, headline TEXT, status TEXT,
  created_at TEXT, updated_at TEXT, reviewed_at TEXT,
  reviewed_by_user_id INTEGER)"""

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    yield c
    c.close()


def add(conn, post_id, days_old, status="incoming"):
    created = (NOW - timedelta(days=days_old)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO research_posts(id,ticker,headline,status,created_at)"
                 " VALUES(?,?,?,?,?)", (post_id, "KLAC", "h", status, created))


def status_of(conn, post_id):
    return conn.execute("SELECT status FROM research_posts WHERE id=?",
                        (post_id,)).fetchone()["status"]


# ── The switch ───────────────────────────────────────────────────────────────

def test_it_is_off_by_default(conn):
    """Draining a queue should be someone's decision, not a deploy's side
    effect."""
    add(conn, "old", 90)
    summary = expiry.run(conn, days=expiry.expiry_days({}), now=NOW)
    assert summary["expired"] == 0
    assert status_of(conn, "old") == "incoming"


@pytest.mark.parametrize("raw,expected", [
    ("7", 7), ("30", 30), ("", 0), ("0", 0), ("-5", 0), ("abc", 0), (None, 0)])
def test_the_window_is_read_strictly(raw, expected):
    env = {} if raw is None else {"LIVE_RESEARCH_EXPIRE_DAYS": raw}
    assert expiry.expiry_days(env) == expected


# ── What it touches ──────────────────────────────────────────────────────────

def test_stale_items_are_retired(conn):
    add(conn, "stale", 30)
    assert expiry.run(conn, days=7, now=NOW)["expired"] == 1
    assert status_of(conn, "stale") == "rejected"


def test_recent_items_are_left_alone(conn):
    add(conn, "fresh", 2)
    assert expiry.run(conn, days=7, now=NOW)["expired"] == 0
    assert status_of(conn, "fresh") == "incoming"


def test_the_boundary_is_not_off_by_a_day(conn):
    add(conn, "just_inside", 6)
    add(conn, "just_outside", 8)
    expiry.run(conn, days=7, now=NOW)
    assert status_of(conn, "just_inside") == "incoming"
    assert status_of(conn, "just_outside") == "rejected"


def test_drafts_and_published_work_is_never_touched(conn):
    """Only items still awaiting a first look expire. A draft is work someone
    started, and a published post is live."""
    for i, state in enumerate(("draft", "published", "rejected")):
        add(conn, f"p{i}", 90, status=state)
    assert expiry.run(conn, days=7, now=NOW)["expired"] == 0
    for i, state in enumerate(("draft", "published", "rejected")):
        assert status_of(conn, f"p{i}") == state


# ── How it retires them ──────────────────────────────────────────────────────

def test_rows_are_kept_so_dedupe_still_recognises_the_story(conn):
    add(conn, "stale", 30)
    expiry.run(conn, days=7, now=NOW)
    row = conn.execute("SELECT * FROM research_posts WHERE id='stale'").fetchone()
    assert row is not None and row["ticker"] == "KLAC"


def test_an_expired_item_is_distinguishable_from_a_human_rejection(conn):
    add(conn, "stale", 30)
    expiry.run(conn, days=7, now=NOW)
    row = conn.execute("SELECT * FROM research_posts WHERE id='stale'").fetchone()
    assert row["reviewed_at"] is not None
    assert row["reviewed_by_user_id"] is None      # nobody looked at it


# ── Draining a real backlog ──────────────────────────────────────────────────

def test_a_long_backlog_is_taken_in_batches(conn):
    """823 items should not hold the database for one sweep; the cron returns
    in five minutes for the next batch."""
    for i in range(120):
        add(conn, f"p{i}", 30)
    summary = expiry.run(conn, days=7, now=NOW, batch=50)
    assert summary["expired"] == 50
    assert summary["expire_remaining"] == 70


def test_repeated_runs_drain_it_completely(conn):
    for i in range(120):
        add(conn, f"p{i}", 30)
    while expiry.run(conn, days=7, now=NOW, batch=50)["expired"]:
        pass
    remaining = conn.execute(
        "SELECT COUNT(*) AS n FROM research_posts WHERE status='incoming'").fetchone()
    assert remaining["n"] == 0


def test_a_database_error_is_reported_not_raised(conn):
    """A failure here must not take down the whole scheduled run."""
    conn.execute("DROP TABLE research_posts")
    summary = expiry.run(conn, days=7, now=NOW)
    assert summary["expired"] == 0 and "expire_error" in summary
