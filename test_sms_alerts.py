from datetime import datetime, timezone
import os
import sqlite3
from unittest.mock import patch

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-sms-secret")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import sms_alerts
from migrations import (m0001_live_research_feed, m0002_live_research_triage,
                        m0005_research_memory, m0006_sms_alerts)


NOW = datetime(2026, 9, 14, 18, 0, tzinfo=timezone.utc)


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
    m0006_sms_alerts.upgrade(db)
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
    conn.execute(
        "INSERT INTO sms_alert_preferences (user_id,phone_ciphertext,phone_last4,verified_at,enabled,quiet_start,quiet_end,timezone,daily_cap,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (1, sms_alerts._encrypt("+15551234567"), "4567", NOW.isoformat(), 1, 21, 7,
         "America/New_York", 3, NOW.isoformat(), NOW.isoformat()),
    )
    conn.commit()


def test_phone_normalization_is_strict():
    assert sms_alerts.normalize_phone("(555) 123-4567") == "+15551234567"
    assert sms_alerts.normalize_phone("+44 20 7946 0958") == "+442079460958"
    with pytest.raises(sms_alerts.SmsAlertError):
        sms_alerts.normalize_phone("555")


def test_verification_does_not_store_plaintext_phone(conn, monkeypatch):
    monkeypatch.setattr(sms_alerts, "provider_ready", lambda: True)
    sent = []
    monkeypatch.setattr(sms_alerts, "send_message", lambda phone, body: sent.append((phone, body)) or "SM1")
    sms_alerts.begin_verification(1, "555-123-4567", conn=conn)
    row = conn.execute("SELECT * FROM sms_alert_preferences WHERE user_id=1").fetchone()
    assert "+15551234567" not in row["phone_ciphertext"]
    assert row["phone_last4"] == "4567" and not row["enabled"]
    assert sent[0][0] == "+15551234567" and "verification code" in sent[0][1]


def test_wrong_verification_code_is_rate_limited_in_state(conn, monkeypatch):
    monkeypatch.setattr(sms_alerts, "provider_ready", lambda: True)
    monkeypatch.setattr(sms_alerts, "send_message", lambda *args: "SM1")
    sms_alerts.begin_verification(1, "+15551234567", conn=conn)
    with pytest.raises(sms_alerts.SmsAlertError):
        sms_alerts.verify_code(1, "000000", conn=conn)
    assert conn.execute("SELECT attempts FROM sms_alert_verifications WHERE user_id=1").fetchone()["attempts"] == 1


def test_delivery_is_thesis_only_and_idempotent(conn, monkeypatch):
    seed_alert(conn)
    monkeypatch.setenv("SMS_ALERTS_ENABLED", "1")
    monkeypatch.setattr(sms_alerts, "provider_ready", lambda: True)
    messages = []
    monkeypatch.setattr(sms_alerts, "send_message", lambda phone, body: messages.append((phone, body)) or "SM123")
    first = sms_alerts.deliver_pending(conn, now=NOW)
    second = sms_alerts.deliver_pending(conn, now=NOW)
    assert first["sms_sent"] == 1 and second["sms_sent"] == 0
    assert len(messages) == 1 and "AMD" in messages[0][1] and "STOP" in messages[0][1]
    row = conn.execute("SELECT status,provider_message_id FROM sms_alert_deliveries").fetchone()
    assert tuple(row) == ("sent", "SM123")


def test_quiet_hours_suppress_delivery(conn, monkeypatch):
    seed_alert(conn)
    monkeypatch.setenv("SMS_ALERTS_ENABLED", "1")
    monkeypatch.setattr(sms_alerts, "provider_ready", lambda: True)
    conn.execute("UPDATE sms_alert_preferences SET timezone='UTC',quiet_start=17,quiet_end=7")
    conn.commit()
    with patch.object(sms_alerts, "send_message") as sender:
        result = sms_alerts.deliver_pending(conn, now=NOW)
    assert result["sms_sent"] == 0 and result["sms_skipped"] == 1
    sender.assert_not_called()


def test_opt_out_provider_error_pauses_future_texts(conn, monkeypatch):
    seed_alert(conn)
    monkeypatch.setenv("SMS_ALERTS_ENABLED", "1")
    monkeypatch.setattr(sms_alerts, "provider_ready", lambda: True)
    def blocked(*args):
        raise sms_alerts.SmsProviderError("recipient opted out", "21610")
    monkeypatch.setattr(sms_alerts, "send_message", blocked)
    result = sms_alerts.deliver_pending(conn, now=NOW)
    assert result["sms_sent"] == 0
    assert not conn.execute("SELECT enabled FROM sms_alert_preferences WHERE user_id=1").fetchone()["enabled"]


def test_provider_request_uses_twilio_message_resource(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15557654321")
    class Response:
        status_code = 201
        content = b"yes"
        def json(self): return {"sid": "SM123"}
    captured = {}
    def post(url, **kwargs): captured.update(url=url, **kwargs); return Response()
    monkeypatch.setattr(sms_alerts.requests, "post", post)
    assert sms_alerts.send_message("+15551234567", "hello") == "SM123"
    assert captured["url"].endswith("/Accounts/AC123/Messages.json")
    assert captured["auth"] == ("AC123", "secret")
    assert captured["data"] == {"To": "+15551234567", "Body": "hello", "From": "+15557654321"}


def test_settings_page_explains_the_material_only_boundary():
    import web_app
    client = web_app.app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 1
    preference = {"configured": False, "exists": False, "verified": False, "enabled": False,
                  "phone_masked": "", "quiet_start": 21, "quiet_end": 7,
                  "timezone": "America/New_York", "daily_cap": 3}
    with patch.object(sms_alerts, "get_preference", return_value=preference):
        response = client.get("/sms-alerts")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Only material changes that match a thesis" in html
    assert "Reply STOP to opt out" in html
    assert "Provider setup required" in html
