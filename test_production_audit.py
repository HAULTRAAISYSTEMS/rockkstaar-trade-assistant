"""Regression tests for the October production-readiness audit."""
from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo
import re

import pytest
import app as server
import web_app  # Register the production blueprints before serving requests.
import browser_push
import exchange_schedule as schedule


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "_is_production", True)
    monkeypatch.setitem(server.app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setattr(server, "_MKT_AVAILABLE", False)
    return server.app.test_client()


def sign_in(client, user):
    with client.session_transaction() as session:
        session.update(user_id=user["id"], username=user["username"], is_admin=1,
                       credential_stamp=server._credential_stamp(user))


USER = {"id": 42, "username": "audit", "is_admin": 0, "password_hash": "test-hash"}


def test_empty_production_database_never_opens_auth(client, monkeypatch):
    monkeypatch.setattr(server, "get_all_users", lambda: [])
    monkeypatch.delenv("ALLOW_REGISTRATION", raising=False)
    assert client.get("/api/session-status").status_code == 401
    assert server._registration_enabled() is False


def test_every_private_get_route_requires_production_login(client):
    public = {"/login", "/logout", "/register", "/favicon.ico", "/health", "/service-worker.js", "/schwab/callback"}
    checked = 0
    for rule in server.app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.rule in public or rule.rule.startswith(("/static/", "/ws/")):
            continue
        path = re.sub(r"<int:[^>]+>", "42", rule.rule)
        path = re.sub(r"<[^>]+>", "AMD", path)
        response = client.get(path)
        assert response.status_code in {301, 302, 308, 401}, (path, response.status_code)
        checked += 1
    assert checked > 50


@pytest.mark.parametrize("changed", [None, dict(USER, password_hash="changed")])
def test_deleted_user_or_changed_password_revokes_cookie(client, monkeypatch, changed):
    sign_in(client, USER)
    monkeypatch.setattr(server, "get_user_by_id", lambda _: changed)
    assert client.get("/api/session-status").status_code == 401


def test_stale_admin_role_is_not_authority(client, monkeypatch):
    sign_in(client, USER)
    monkeypatch.setattr(server, "get_user_by_id", lambda _: USER)
    assert client.get("/admin/users").status_code == 403
    with client.session_transaction() as session:
        assert session["is_admin"] == 0


def test_identity_database_outage_fails_closed(client, monkeypatch):
    sign_in(client, USER)
    monkeypatch.setattr(server, "get_user_by_id", Mock(side_effect=RuntimeError("password-secret")))
    response = client.get("/api/session-status")
    assert response.status_code == 503
    assert b"password-secret" not in response.data


def test_old_cookie_requires_new_sign_in(client):
    with client.session_transaction() as session:
        session.update(user_id=42, is_admin=1)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(server, "get_user_by_id", lambda _: USER)
        assert client.get("/api/session-status").status_code == 401


@pytest.mark.parametrize("value", ["https://evil.test", "//evil.test", "/\\evil.test", "/%5cevil.test", "/%2fevil.test", "/%252fevil.test", "/\nevil.test"])
def test_login_rejects_external_redirects(value):
    with server.app.test_request_context():
        assert server._safe_login_next(value) == "/opportunity"


def test_login_preserves_relative_destination():
    with server.app.test_request_context():
        assert server._safe_login_next("/research?ticker=AMD") == "/research?ticker=AMD"


@pytest.mark.parametrize("hops,expected", [("0", "127.0.0.1"), ("1", "192.0.2.1"), ("broken", "127.0.0.1")])
def test_login_does_not_trust_spoofable_leftmost_ip(monkeypatch, hops, expected):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", hops)
    with server.app.test_request_context("/login", headers={"X-Forwarded-For": "203.0.113.9, 192.0.2.1"}, environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert server._login_client_address() == expected


def test_login_preserves_password_spaces(client, monkeypatch):
    checker = Mock(return_value=USER)
    monkeypatch.setattr(server, "check_user_password", checker)
    monkeypatch.setattr(server, "_refresh_on_signin", lambda _: None)
    import database
    monkeypatch.setattr(database, "get_db", Mock(side_effect=RuntimeError("isolated throttle")))
    response = client.post("/login", data={"username": "audit", "password": " spaced password ", "next": "/research"})
    assert response.status_code == 302
    checker.assert_called_once_with("audit", " spaced password ")
    with client.session_transaction() as session:
        assert session["credential_stamp"] == server._credential_stamp(USER)


@pytest.mark.parametrize("keys", [["bad"], "bad", 12])
def test_push_rejects_non_object_keys(keys):
    with pytest.raises(browser_push.BrowserPushError):
        browser_push._validate_subscription({"endpoint": "https://fcm.googleapis.com/send/x", "keys": keys})


@pytest.mark.parametrize("endpoint", ["https://[broken", "https://fcm.googleapis.com:bad/a", "https://fcm.googleapis.com:8443/a", "https://user:secret@fcm.googleapis.com/a", "https://fcm.googleapis.com/a#fragment", "https://fcm.googleapis.com.evil.test/a"])
def test_push_rejects_unsafe_urls(endpoint):
    with pytest.raises(browser_push.BrowserPushError):
        browser_push._validate_subscription({"endpoint": endpoint, "keys": {"auth": "a" * 22, "p256dh": "b" * 87}})


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_push_malformed_json_is_400(client, monkeypatch, method):
    sign_in(client, USER)
    monkeypatch.setattr(server, "get_user_by_id", lambda _: USER)
    response = client.open("/api/browser-push/subscriptions", method=method, json=["bad"])
    assert response.status_code == 400


def test_anonymous_push_cannot_be_saved():
    with pytest.raises(browser_push.BrowserPushError):
        browser_push.save_subscription(0, {})


@pytest.mark.parametrize("stamp,expected", [
    ("2026-10-26T09:29:59-04:00", "pre_market"),
    ("2026-10-26T09:30:00-04:00", "regular"),
    ("2026-10-26T16:00:00-04:00", "after_hours"),
    ("2026-10-26T20:00:00-04:00", "closed"),
    ("2026-10-25T11:00:00-04:00", "closed"),
    ("2026-11-27T12:59:00-05:00", "regular"),
    ("2026-11-27T13:00:00-05:00", "after_hours"),
    ("2026-11-27T17:00:00-05:00", "closed"),
    ("2026-11-02T14:15:00+00:00", "pre_market"),
    ("2026-10-26T14:15:00+00:00", "regular"),
    ("2029-01-08T11:00:00-05:00", "unknown"),
    ("2027-12-31T11:00:00-05:00", "regular"),
])
def test_session_boundaries_holidays_and_dst(stamp, expected):
    assert schedule.session_status(datetime.fromisoformat(stamp))["session"] == expected


@pytest.mark.parametrize("year,days", schedule.HOLIDAYS.items())
def test_every_published_holiday_is_closed(year, days):
    for day in days:
        now = datetime.fromisoformat(f"{year}-{day}T11:00:00").replace(tzinfo=ZoneInfo("America/New_York"))
        assert schedule.session_status(now)["session"] == "closed"


@pytest.mark.parametrize("stamp,day", [("2026-11-27T08:00:00-05:00", "Wednesday"), ("2026-09-07T12:00:00-04:00", "Friday"), ("2026-09-08T08:00:00-04:00", "Friday")])
def test_previous_candle_label_skips_holidays(stamp, day):
    import market_engine
    assert market_engine._day_label(datetime.fromisoformat(stamp)) == day


def test_company_acronym_never_used_as_wikipedia_entity(monkeypatch):
    import intel_engine as engine
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setattr(engine, "_yf_is_rate_limited", lambda: True)
    monkeypatch.setattr(engine, "_company_description", lambda _: None)
    monkeypatch.setattr(engine, "_company_name", lambda _: "ZZZX")
    wiki = Mock(return_value="ZZZX is an unrelated club")
    monkeypatch.setattr(engine, "_wikipedia_description", wiki)
    assert not engine.fetch_company_profile("ZZZX", force=True).get("description")
    wiki.assert_not_called()


def test_caught_api_errors_do_not_expose_upstream_secrets():
    with server.app.test_request_context("/api/test"):
        response = server.jsonify(error="token=secret", errors=["SQL secret"], ok=False)
        response.status_code = 500
        clean = server._add_security_headers(response)
        assert b"secret" not in clean.data
        assert "frame-ancestors 'self'" in clean.headers["Content-Security-Policy"]


def test_error_page_is_actionable_and_safe(client, monkeypatch):
    sign_in(client, USER)
    monkeypatch.setattr(server, "get_user_by_id", lambda _: USER)
    response = client.get("/not-a-real-page")
    assert response.status_code == 404
    assert b"Back to Today" in response.data


def test_workspace_links_and_accessible_search_render(client, monkeypatch):
    sign_in(client, USER)
    monkeypatch.setattr(server, "get_user_by_id", lambda _: USER)
    with server.app.test_request_context("/opportunity"):
        html = server.render_template("_workspace_search.html")
    assert 'id="workspace-search"' in html and 'aria-labelledby="workspace-search-title"' in html
    assert '/fundamentals?tab=quarter' in html and '/notifications' in html
