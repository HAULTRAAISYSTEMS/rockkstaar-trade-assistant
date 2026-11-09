"""The Today pulse strip keeps itself live without a reload.

The strip used to be server-rendered once, so a reader watching the open saw
the numbers from whenever the page loaded. It now polls /api/today/pulse. These
check that the endpoint applies the same rules as the server render (session
labels, `known`), that it never hands back a regime for an unread market, and
that the page carries the hooks the poller writes into.
"""
import os

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import app as legacy
import web_app


LIVE = {
    "regime": "RISK_ON", "vix_level": 17.4, "vix_as_of_label": "10:31 AM ET",
    "spy_1d_pct": 0.62, "qqq_1d_pct": 0.91, "es_1d_chg": 0.55, "nq_1d_chg": 0.88,
}


@pytest.fixture
def client():
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
    return c


@pytest.fixture
def live_market(monkeypatch):
    monkeypatch.setattr(legacy, "_get_mkt_ctx", lambda: dict(LIVE))
    monkeypatch.setattr(legacy._mkt, "context_is_real", lambda m: True)
    monkeypatch.setattr(legacy._mkt, "snapshot_age_minutes", lambda m: 1)
    monkeypatch.setattr(legacy._mkt, "market_session", lambda: "regular")
    monkeypatch.setattr(legacy._mkt, "get_market_tape", lambda: [])
    monkeypatch.setattr(legacy._mkt, "fast_pulse_quotes", lambda: {})


def test_the_endpoint_returns_the_live_strip(client, live_market):
    r = client.get("/api/today/pulse")
    assert r.status_code == 200
    assert "no-store" in r.headers["Cache-Control"]
    data = r.get_json()
    assert data["ok"] is True
    p = data["pulse"]
    assert p["regime"] == "RISK_ON"
    assert p["vix"] == 17.4 and p["vix_as_of"] == "10:31 AM ET"
    assert p["spy"] == 0.62 and p["qqq"] == 0.91
    assert p["es"] == 0.55 and p["nq"] == 0.88
    assert p["spy_label"] == "SPY" and p["session"] == "regular"


def test_it_labels_a_closed_session_like_the_page_does(client, live_market, monkeypatch):
    monkeypatch.setattr(legacy._mkt, "market_session", lambda: "closed")
    monkeypatch.setattr(legacy._mkt, "_day_label", lambda: "Friday")
    p = client.get("/api/today/pulse").get_json()["pulse"]
    assert p["spy_label"] == "SPY FRI" and p["qqq_label"] == "QQQ FRI"


def test_an_unread_market_gets_no_regime(client, monkeypatch):
    monkeypatch.setattr(legacy, "_get_mkt_ctx", lambda: {"regime": "NEUTRAL"})
    monkeypatch.setattr(legacy._mkt, "context_is_real", lambda m: False)
    data = client.get("/api/today/pulse").get_json()
    assert data["ok"] is False
    assert data["pulse"]["known"] is False
    assert data["pulse"]["regime"] is None


def test_a_failing_market_context_is_an_answer_not_a_500(client, monkeypatch):
    monkeypatch.setattr(legacy, "_build_today_pulse",
                        lambda m: (_ for _ in ()).throw(RuntimeError("x")))
    r = client.get("/api/today/pulse")
    assert r.status_code == 200
    assert r.get_json()["pulse"]["known"] is False


def test_it_needs_a_signed_in_user():
    r = web_app.app.test_client().get("/api/today/pulse")
    assert r.status_code in (302, 401)


def test_the_page_carries_the_hooks_the_poller_writes_into(client, live_market):
    html = client.get("/").get_data(as_text=True)
    assert 'id="cc-pulse"' in html and 'data-pulse-url="/api/today/pulse"' in html
    for hook in ("cc-pulse-live", "cc-regime-meta", "cc-spy-k", "cc-qqq-k",
                 "cc-es-cell", "cc-nq-cell", "cc-liq"):
        assert f'id="{hook}"' in html
    assert "startLivePulse()" in html


def test_futures_cells_wait_hidden_when_there_is_no_reading(client, live_market, monkeypatch):
    monkeypatch.setattr(legacy, "_get_mkt_ctx",
                        lambda: {**LIVE, "es_1d_chg": None, "nq_1d_chg": None})
    html = client.get("/").get_data(as_text=True)
    assert 'id="cc-es-cell" hidden' in html and 'id="cc-nq-cell" hidden' in html


TAPE = [
    {"symbol": "SPY", "price": 681.2, "change_pct": 0.42, "ext_session": "pre"},
    {"symbol": "QQQ", "price": 612.0, "change_pct": -0.18, "ext_session": "pre"},
]


def test_premarket_shows_the_premarket_move(client, live_market, monkeypatch):
    monkeypatch.setattr(legacy._mkt, "market_session", lambda: "pre")
    monkeypatch.setattr(legacy._mkt, "_day_label", lambda: "Friday")
    monkeypatch.setattr(legacy._mkt, "get_market_tape", lambda: [dict(i) for i in TAPE])
    p = client.get("/api/today/pulse").get_json()["pulse"]
    assert p["spy"] == 0.42 and p["spy_label"] == "SPY PRE" and p["spy_ext"] is True
    assert p["qqq"] == -0.18 and p["qqq_label"] == "QQQ PRE"


def test_after_hours_shows_the_after_hours_move(client, live_market, monkeypatch):
    monkeypatch.setattr(legacy._mkt, "market_session", lambda: "post")
    monkeypatch.setattr(legacy._mkt, "_day_label", lambda: "today")
    monkeypatch.setattr(legacy._mkt, "get_market_tape", lambda: [
        {**i, "ext_session": "post"} for i in TAPE])
    p = client.get("/api/today/pulse").get_json()["pulse"]
    assert p["spy_label"] == "SPY AH" and p["spy"] == 0.42


def test_no_extended_print_yet_keeps_the_close_label(client, live_market, monkeypatch):
    """Before the first premarket trade, a closed session's move stays named as one."""
    monkeypatch.setattr(legacy._mkt, "market_session", lambda: "pre")
    monkeypatch.setattr(legacy._mkt, "_day_label", lambda: "Friday")
    monkeypatch.setattr(legacy._mkt, "get_market_tape", lambda: [
        {"symbol": "SPY", "price": 680.0, "change_pct": 0.62}])
    p = client.get("/api/today/pulse").get_json()["pulse"]
    assert p["spy_label"] == "SPY FRI" and p["spy"] == 0.62 and p["spy_ext"] is False


def test_a_print_from_the_other_session_is_not_used(client, live_market, monkeypatch):
    monkeypatch.setattr(legacy._mkt, "market_session", lambda: "pre")
    monkeypatch.setattr(legacy._mkt, "_day_label", lambda: "Friday")
    monkeypatch.setattr(legacy._mkt, "get_market_tape", lambda: [
        {**i, "ext_session": "post"} for i in TAPE])
    p = client.get("/api/today/pulse").get_json()["pulse"]
    assert p["spy_label"] == "SPY FRI"


def test_the_page_renders_premarket_numbers_on_load(client, live_market, monkeypatch):
    monkeypatch.setattr(legacy._mkt, "market_session", lambda: "pre")
    monkeypatch.setattr(legacy._mkt, "_day_label", lambda: "Friday")
    monkeypatch.setattr(legacy._mkt, "get_market_tape", lambda: [dict(i) for i in TAPE])
    html = client.get("/").get_data(as_text=True)
    assert ">SPY PRE<" in html and "+0.42%" in html
    assert 'data-session="pre"' in html


# ── The fast feed ───────────────────────────────────────────────────────────
from datetime import date, datetime as _dt
from zoneinfo import ZoneInfo
import market_engine

ET = ZoneInfo("America/New_York")
DAY = date(2026, 11, 9)


def _chart(prev, reg, bars):
    stamps = [int(_dt(2026, 11, 9, h, m, tzinfo=ET).timestamp()) for h, m, _ in bars]
    return {"meta": {"chartPreviousClose": prev, "regularMarketPrice": reg},
            "timestamp": stamps,
            "indicators": {"quote": [{"close": [px for _, _, px in bars]}]}}


def test_fast_quote_regular_hours_is_last_print_vs_prior_close():
    q = market_engine._fast_quote_from_chart(
        _chart(100.0, 101.0, [(10, 0, 100.5), (10, 1, 101.0)]), "SPY", "regular", DAY)
    assert q["pct"] == 1.0 and q["ext"] is None and q["at"] == "10:01:00 AM ET"


def test_fast_quote_premarket_uses_the_pre_print():
    # Before the open, regularMarketPrice is Friday's close.
    q = market_engine._fast_quote_from_chart(
        _chart(99.0, 100.0, [(7, 58, 100.4)]), "SPY", "pre", DAY)
    assert q["pct"] == 0.4 and q["ext"] == "pre"


def test_fast_quote_premarket_without_a_print_today_says_nothing():
    res = _chart(99.0, 100.0, [])
    res["meta"]["regularMarketTime"] = int(_dt(2026, 11, 6, 16, 0, tzinfo=ET).timestamp())
    assert market_engine._fast_quote_from_chart(res, "SPY", "pre", DAY) is None


def test_fast_quote_after_hours_is_against_todays_close():
    q = market_engine._fast_quote_from_chart(
        _chart(98.0, 100.0, [(16, 30, 99.5)]), "QQQ", "post", DAY)
    assert q["pct"] == -0.5 and q["ext"] == "post"


def test_fast_quote_futures_trade_around_the_clock():
    q = market_engine._fast_quote_from_chart(
        _chart(6000.0, 6030.0, [(3, 15, 6030.0)]), "ES", "pre", DAY)
    assert q["pct"] == 0.5


def test_the_fast_feed_is_off_in_tests():
    assert market_engine.fast_pulse_quotes() == {}


FAST = {
    "SPY": {"price": 681.0, "pct": 0.37, "ext": "pre", "at": "7:58:41 AM ET"},
    "QQQ": {"price": 612.0, "pct": 0.12, "ext": "pre", "at": "7:58:41 AM ET"},
    "ES": {"price": 6810.0, "pct": 0.29, "ext": None, "at": "7:58:40 AM ET"},
    "NQ": {"price": 25210.0, "pct": 0.44, "ext": None, "at": "7:58:40 AM ET"},
    "VIX": {"price": 16.2, "pct": -2.1, "ext": None, "at": "7:58:39 AM ET"},
}


def test_the_fast_feed_drives_the_strip(client, live_market, monkeypatch):
    monkeypatch.setattr(legacy._mkt, "market_session", lambda: "pre")
    monkeypatch.setattr(legacy._mkt, "_day_label", lambda: "Friday")
    monkeypatch.setattr(legacy._mkt, "fast_pulse_quotes", lambda: dict(FAST))
    p = client.get("/api/today/pulse").get_json()["pulse"]
    assert p["spy"] == 0.37 and p["spy_label"] == "SPY PRE"
    assert p["es"] == 0.29 and p["nq"] == 0.44
    assert p["vix"] == 16.2 and p["vix_as_of"] == "7:58:39 AM ET"


def test_a_missing_fast_reading_keeps_the_snapshot_value(client, live_market, monkeypatch):
    monkeypatch.setattr(legacy._mkt, "fast_pulse_quotes", lambda: {"VIX": FAST["VIX"]})
    p = client.get("/api/today/pulse").get_json()["pulse"]
    assert p["spy"] == 0.62 and p["es"] == 0.55 and p["vix"] == 16.2
