"""App Store / Google Play requirements: public legal pages, self-service
account deletion that removes everything, Android asset links."""
import os

import pytest

os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")
os.environ.setdefault("SECRET_KEY", "test-only-store")


@pytest.fixture
def db(tmp_path, monkeypatch):
    import database
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    database.init_db()
    return database


def test_legal_pages_are_public(db):
    import web_app
    c = web_app.app.test_client()
    for path, needle in (("/privacy", "Privacy Policy"), ("/terms", "Not financial advice"), ("/support", "delete my account")):
        r = c.get(path)
        assert r.status_code == 200, path
        assert needle.lower() in r.get_data(as_text=True).lower(), path
    login = c.get("/login").get_data(as_text=True)
    assert 'href="/privacy"' in login and 'href="/terms"' in login


def test_assetlinks(db, monkeypatch):
    import web_app
    c = web_app.app.test_client()
    assert c.get("/.well-known/assetlinks.json").get_json() == []
    monkeypatch.setenv("ANDROID_PACKAGE", "com.rockkstaar.tradestaar")
    monkeypatch.setenv("ANDROID_SHA256_FINGERPRINTS", "aa:bb, cc:dd")
    d = c.get("/.well-known/assetlinks.json").get_json()
    assert d[0]["target"]["package_name"] == "com.rockkstaar.tradestaar"
    assert d[0]["target"]["sha256_cert_fingerprints"] == ["AA:BB", "CC:DD"]


def test_self_service_delete_removes_everything(db):
    import app as legacy
    import web_app
    import notify_inbox
    uid = db.create_user("deleteme", "a-long-password-123", is_admin=False)
    wl = db.create_watchlist("Mine", uid)
    db.add_ticker_to_watchlist(wl, "NVDA")
    db.create_price_alert(uid, "NVDA", "above", 200.0)
    db.set_user_setting(uid, "chart_drawings:SPY", "[]")
    notify_inbox.add("system", "hello", user_id=uid)
    web_app.app.config["WTF_CSRF_ENABLED"] = False
    try:
        c = web_app.app.test_client()
        u = db.get_user_by_id(uid)
        with c.session_transaction() as s:
            s["user_id"] = uid; s["credential_stamp"] = legacy._credential_stamp(dict(u))
        assert c.get("/account/delete").status_code == 200
        r = c.post("/account/delete", data={"password": "wrong", "confirm": "DELETE"})
        assert b"isn" in r.data and db.get_user_by_id(uid)
        r = c.post("/account/delete", data={"password": "a-long-password-123", "confirm": "nope"})
        assert db.get_user_by_id(uid)
        r = c.post("/account/delete", data={"password": "a-long-password-123", "confirm": "delete"})
        assert r.status_code == 302 and db.get_user_by_id(uid) is None
    finally:
        web_app.app.config["WTF_CSRF_ENABLED"] = True
    conn = db.get_db()
    for t in db._tables_with_user_id(conn):
        n = conn.execute(f'SELECT COUNT(*) AS n FROM "{t}" WHERE user_id = ?', (uid,)).fetchone()
        assert (n["n"] if hasattr(n, "keys") else n[0]) == 0, t
    conn.close()


def test_owner_account_cannot_self_delete(db):
    with pytest.raises(ValueError):
        db.delete_user(1)


def test_percent_in_sql_is_escaped_for_postgres(monkeypatch):
    import database
    monkeypatch.setattr(database, "_USE_POSTGRES", True)
    sql, _ = database._adapt_sql("SELECT 1 FROM t WHERE user_id=? AND key LIKE 'research_lab:%'", (1,))
    assert sql == "SELECT 1 FROM t WHERE user_id=%s AND key LIKE 'research_lab:%%'"
    sql, _ = database._adapt_sql("SELECT 1 FROM t WHERE key LIKE 'x%'")
    assert sql.endswith("'x%'")          # no params: psycopg2 doesn't interpolate
