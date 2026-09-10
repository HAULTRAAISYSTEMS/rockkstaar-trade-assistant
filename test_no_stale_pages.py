"""Nothing signed-in gets cached.

Pages and API responses went out with no cache headers at all. Not "cache
this for a while" — nothing: no Cache-Control, no ETag, no Expires, no
Last-Modified. A browser given nothing to go on does not refetch, it applies
heuristic caching, and Safari on iOS is the most willing of the lot.

What that looked like: a phone serving a page whose JavaScript was weeks old
against JSON just as stale. A saved quarter came back carrying a revenue
figure the database had not held since it was corrected, and the drill graded
nothing, because the page doing the grading was not the page that had been
deployed. The same account on a freshly loaded desktop graded all seven. That
is exactly how a caching fault presents, and exactly why it reads as a broken
server when the server is fine.

It is a privacy fault too: balances, positions and saved work were sitting in
whatever cache the device or an intermediary kept.
"""
import re

import pytest

import database as db


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "c.db"))
    db.init_db()
    import migration_runner
    migration_runner.run_migrations()
    return db


@pytest.fixture
def client(store):
    from unittest.mock import patch
    import web_app, app as _app
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
        sess["logged_in"] = True
    with patch.object(_app, "_auth_required", lambda *a, **k: False):
        yield c


def _no_store(resp):
    control = resp.headers.get("Cache-Control") or ""
    return "no-store" in control


class TestSignedInResponsesAreNeverCached:
    @pytest.mark.parametrize("path", [
        "/api/quarter/saved",
        "/fundamentals?tab=quarter",
    ])
    def test_it_says_no_store(self, client, path):
        resp = client.get(path)
        assert resp.status_code == 200
        assert _no_store(resp), resp.headers.get("Cache-Control")

    def test_the_old_caches_are_told_too(self, client):
        resp = client.get("/fundamentals")
        assert resp.headers.get("Pragma") == "no-cache"
        assert resp.headers.get("Expires") == "0"

    def test_a_saved_quarter_is_never_served_from_a_stale_copy(self, client):
        """The one that cost a whole evening: a phone reopened a quarter and
        got a revenue figure the database had not held for days."""
        token = re.search(
            r'name="csrf-token" content="([^"]+)"',
            client.get("/fundamentals?tab=quarter").get_data(as_text=True)).group(1)
        saved = client.post("/api/quarter/saved",
                            json={"ticker": "TSLA", "period": "Q2 2026",
                                  "figures": {"rev": "28236000000"}},
                            headers={"X-CSRFToken": token}).get_json()
        resp = client.get("/api/quarter/saved/" + str(saved["id"]))
        assert _no_store(resp)
        assert resp.get_json()["figures"]["rev"] == "28236000000"

    def test_static_files_keep_revalidating_normally(self, client):
        """Flask gives them a Last-Modified and an ETag, so they refresh
        rather than going stale, and they carry nothing about anybody."""
        resp = client.get("/static/css/fundamentals.css")
        if resp.status_code == 200:
            assert not _no_store(resp)

    def test_the_security_headers_still_go_out(self, client):
        resp = client.get("/fundamentals")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
