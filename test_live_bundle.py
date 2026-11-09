"""One live poll per page instead of three or four.

Each open page ran its own 3-second polls (banner tape, Today pulse,
Terminal rows, chart price). With gunicorn recycling a worker every 500
requests, that restarted each worker every few minutes — ~12 s of no answer
each time, which looked like crashes. These pin the bundle endpoint, the
cheaper login check for polls, and the recycling limit.
"""
import os

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import app as legacy
import web_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(legacy, "_get_mkt_ctx", lambda: {})
    monkeypatch.setattr(legacy._mkt, "get_market_tape", lambda: [
        {"symbol": "SPY", "price": 681.2, "change_pct": 0.3}])
    monkeypatch.setattr(legacy, "_tape_watchlist_items", lambda: [])
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
    return c


def test_the_bundle_returns_each_part_its_endpoint_would(client):
    r = client.get("/api/live/bundle?want=tape,pulse,quotes,live&ticker=SPY")
    assert r.status_code == 200 and "no-store" in r.headers["Cache-Control"]
    parts = r.get_json()["parts"]
    assert parts["tape"] == client.get("/api/market-tape").get_json()
    assert "pulse" in parts["pulse"] and "quotes" in parts["quotes"]
    assert parts["live"]["ticker"] == "SPY"


def test_only_what_the_page_asks_for(client):
    parts = client.get("/api/live/bundle?want=tape").get_json()["parts"]
    assert set(parts) == {"tape"}


def test_one_broken_part_does_not_take_the_rest_down(client, monkeypatch):
    monkeypatch.setattr(legacy, "api_today_pulse", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    parts = client.get("/api/live/bundle?want=tape,pulse").get_json()["parts"]
    assert parts["pulse"] is None and parts["tape"]["ok"] is True


def test_polls_reuse_a_recent_login_check_but_pages_do_not(monkeypatch):
    calls = []
    monkeypatch.setattr(legacy, "get_user_by_id", lambda uid: calls.append(uid) or {"id": uid})
    legacy._POLL_VERIFY_CACHE.clear()
    with web_app.app.test_request_context("/api/live/bundle"):
        legacy._verified_user_for_request(7); legacy._verified_user_for_request(7)
    with web_app.app.test_request_context("/api/session-status"):
        legacy._verified_user_for_request(7)
    with web_app.app.test_request_context("/terminal"):
        legacy._verified_user_for_request(7)
    assert calls == [7, 7, 7]     # bundle once, then session-status and the page each check
    legacy._POLL_VERIFY_CACHE.clear()


def test_workers_are_not_recycled_every_few_minutes():
    assert "--max-requests 20000" in open("Procfile").read()
    assert "max_requests = 20000" in open("gunicorn.conf.py").read()


def test_every_live_view_subscribes_to_the_shared_poll():
    assert "TSLive.on('tape', handleTape);" in open("templates/_market_tape.html").read()
    term = open("templates/terminal.html").read()
    for part in ("TSLive.on('tape', twTapeApply)", "TSLive.on('quotes', twQuotesApply)", "TSLive.on('live', twLiveApply)"):
        assert part in term
    assert "TSLive.on('pulse', _onPulsePart)" in open("templates/_liq_scripts.html").read()
    assert "js/live_hub.js" in open("templates/base.html").read()
