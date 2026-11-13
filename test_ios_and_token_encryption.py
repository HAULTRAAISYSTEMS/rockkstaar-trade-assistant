"""Broker tokens encrypted at rest; the iPhone App Store build has no live
order entry (Apple 3.2.1(viii)) while the web keeps it."""
import os

import pytest

os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")
os.environ.setdefault("SECRET_KEY", "test-only-ios")


@pytest.fixture
def db(tmp_path, monkeypatch):
    import database
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    database.init_db()
    return database


def test_secret_box_round_trip_and_legacy(monkeypatch):
    import secret_box
    monkeypatch.setenv("SECRET_KEY", "k1-abcdefabcdefabcdef")
    enc = secret_box.encrypt("tok-123")
    assert enc.startswith("enc:v1:") and "tok-123" not in enc
    assert secret_box.decrypt(enc) == "tok-123"
    assert secret_box.decrypt("plain-old-token") == "plain-old-token"   # saved before encryption
    assert secret_box.encrypt("") == "" and secret_box.decrypt("") == ""
    monkeypatch.setenv("SECRET_KEY", "a-different-key-xxxxxxx")
    assert secret_box.decrypt(enc) == ""                                  # key changed → reconnect


def test_schwab_tokens_stored_encrypted_and_legacy_upgraded(db):
    import schwab
    schwab.save_tokens({"access_token": "access-xyz-789", "refresh_token": "RRR", "expires_in": 1800}, user_id=1)
    raw = db.get_user_setting(1, "schwab_access_token")
    assert raw.startswith("enc:v1:") and "access-xyz-789" not in raw
    assert schwab._read_stored_tokens(1)["access_token"] == "access-xyz-789"
    db.set_user_setting(1, "schwab_refresh_token", "LEGACY")              # plaintext from before
    assert schwab._read_stored_tokens(1)["refresh_token"] == "LEGACY"
    assert db.get_user_setting(1, "schwab_refresh_token").startswith("enc:v1:")


def _client(ua):
    import web_app
    import app as legacy
    import database
    web_app.app.config["WTF_CSRF_ENABLED"] = False
    c = web_app.app.test_client()
    c.environ_base["HTTP_USER_AGENT"] = ua
    u = database.get_user_by_id(1)
    with c.session_transaction() as s:
        s["user_id"] = 1; s["credential_stamp"] = legacy._credential_stamp(dict(u)); s["is_admin"] = 1
    return c


def test_iphone_app_has_no_live_trading_web_does(db):
    import web_app
    try:
        ios = _client("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 TradestaarIOS/1.0")
        r = ios.post("/api/trade/preview", json={"symbol": "NVDA"})
        assert r.status_code == 403 and "iPhone app" in r.get_json()["error"]
        assert ios.get("/api/trade/status").get_json()["enabled"] is False
        assert 'id="lt-mode"' not in ios.get("/terminal").get_data(as_text=True)
        web = _client("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Version/18.0 Mobile Safari/604.1")
        assert web.post("/api/trade/preview", json={"symbol": "NVDA"}).status_code != 403
        assert 'id="lt-mode"' in web.get("/terminal").get_data(as_text=True)
    finally:
        web_app.app.config["WTF_CSRF_ENABLED"] = True
