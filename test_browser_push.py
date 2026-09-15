from datetime import datetime, timezone
import base64
import os
import sqlite3
from unittest.mock import patch

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-browser-push-secret")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import browser_push
from migrations import (m0001_live_research_feed, m0002_live_research_triage,
                        m0005_research_memory, m0007_browser_push)


NOW = datetime(2026, 9, 14, 18, 0, tzinfo=timezone.utc)
SUBSCRIPTION = {
    "endpoint": "https://fcm.googleapis.com/fcm/send/example-capability-token",
    "keys": {
        "auth": "abcdefghijk_1234567890",
        "p256dh": "B" + "a" * 86,
    },
}


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, is_admin INTEGER)")
    db.execute("CREATE TABLE watchlists (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT)")
    db.execute("CREATE TABLE watchlist_stocks (id INTEGER PRIMARY KEY, watchlist_id INTEGER, ticker TEXT)")
    m0001_live_research_feed.upgrade(db)
    m0002_live_research_triage.upgrade(db)
    m0005_research_memory.upgrade(db)
    m0007_browser_push.upgrade(db)
    db.execute("INSERT INTO users VALUES (1,'reader',0)")
    yield db
    db.close()


def seed_alert(conn):
    conn.execute("INSERT INTO watchlists VALUES (1,1,'Core')")
    conn.execute("INSERT INTO watchlist_stocks VALUES (1,1,'AMD')")
    conn.execute(
        "INSERT INTO research_posts (id,ticker,company_name,headline,research_notes,category,sentiment,source_name,source_url,tradestaar_take,take_origin,status,should_notify,notification_status,author_user_id,created_at,updated_at,published_at,priority,catalyst_type,source_published_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("p1", "AMD", "AMD", "AMD raises data-center guidance", "Quarterly results", "Earnings", "Bullish",
         "AMD Investor Relations", "https://ir.amd.com/release", "The outlook moved above the prior range.",
         "manual", "published", 0, "not_requested", 1, "2026-09-14T16:00:00+00:00",
         "2026-09-14T16:00:00+00:00", "2026-09-14T16:00:00+00:00", "High", "EARNINGS",
         "2026-09-14T16:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO research_memory_cards (id,user_id,ticker,headline,why_it_matters,thesis_impact,review_question,status,box,due_at,review_count,remembered_count,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("c1", 1, "AMD", "Data center thesis", "Accelerator demand must become durable revenue.",
         "strengthened", "What changed?", "active", 0, NOW.isoformat(), 0, 0, NOW.isoformat(), NOW.isoformat()),
    )
    browser_push.save_subscription(1, SUBSCRIPTION, user_agent="Test Browser", conn=conn)
    conn.commit()


def test_vapid_public_key_is_stable_uncompressed_p256(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "stable-test-secret")
    first = browser_push.public_key()
    assert first == browser_push.public_key()
    raw = base64.urlsafe_b64decode(first + "=" * ((4 - len(first) % 4) % 4))
    assert len(raw) == 65 and raw[0] == 4
    private_raw = base64.urlsafe_b64decode(browser_push._vapid_private_key() + "==")
    assert len(private_raw) == 32


def test_subscription_is_encrypted_and_unknown_hosts_are_rejected(conn):
    browser_push.save_subscription(1, SUBSCRIPTION, conn=conn)
    row = conn.execute("SELECT * FROM browser_push_subscriptions").fetchone()
    assert SUBSCRIPTION["endpoint"] not in row["subscription_ciphertext"]
    assert len(row["endpoint_hash"]) == 64
    bad = {**SUBSCRIPTION, "endpoint": "https://example.com/internal"}
    with pytest.raises(browser_push.BrowserPushError):
        browser_push.save_subscription(1, bad, conn=conn)


def test_subscription_upsert_does_not_duplicate_a_device(conn):
    browser_push.save_subscription(1, SUBSCRIPTION, conn=conn)
    browser_push.save_subscription(1, SUBSCRIPTION, conn=conn)
    assert conn.execute("SELECT COUNT(*) AS n FROM browser_push_subscriptions").fetchone()["n"] == 1


def test_delivery_is_thesis_only_and_idempotent(conn, monkeypatch):
    seed_alert(conn)
    sent = []
    monkeypatch.setattr(browser_push, "_send", lambda subscription, payload: sent.append((subscription, payload)))
    first = browser_push.deliver_pending(conn, now=NOW)
    second = browser_push.deliver_pending(conn, now=NOW)
    assert first["push_sent"] == 1 and second["push_sent"] == 0
    assert len(sent) == 1 and sent[0][1]["title"].startswith("AMD thesis change")
    assert sent[0][1]["url"] == "/research-memory?post_id=p1"


def test_quiet_hours_prevent_delivery(conn, monkeypatch):
    seed_alert(conn)
    conn.execute("UPDATE browser_push_preferences SET timezone='UTC',quiet_start=17,quiet_end=7")
    conn.commit()
    with patch.object(browser_push, "_send") as sender:
        result = browser_push.deliver_pending(conn, now=NOW)
    assert result["push_sent"] == 0 and result["push_skipped"] == 1
    sender.assert_not_called()


def test_expired_subscription_is_disabled_on_test(conn, monkeypatch):
    seed_alert(conn)
    response = type("Response", (), {"status_code": 410})()
    def expired(*args, **kwargs):
        error = browser_push.WebPushException("gone")
        error.response = response
        raise error
    monkeypatch.setattr(browser_push, "_send", expired)
    result = browser_push.send_test(1, conn=conn)
    assert result["expired"] == 1
    assert conn.execute("SELECT enabled FROM browser_push_subscriptions").fetchone()["enabled"] == 0


def test_settings_page_explains_free_and_iphone_installation():
    import web_app
    client = web_app.app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 1
    preference = {"enabled": True, "quiet_start": 21, "quiet_end": 7,
                  "timezone": "America/New_York", "daily_cap": 3, "devices": 0}
    with patch.object(browser_push, "get_settings", return_value=preference):
        response = client.get("/notifications")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "without an SMS subscription or another bill" in html
    assert "Add to Home Screen" in html
    assert "Enable free alerts" in html


def test_service_worker_handles_push_and_notification_clicks():
    script = open("static/service-worker.js", encoding="utf-8").read()
    assert "addEventListener('push'" in script
    assert "showNotification" in script
    assert "addEventListener('notificationclick'" in script
