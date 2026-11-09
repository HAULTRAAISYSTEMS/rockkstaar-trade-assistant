"""The Terminal chart ticks live from /api/terminal/live.

Before this the active candle only moved off the watchlist's database poll —
refreshed at most every ~45 s, and never for a charted symbol that was not on
the watchlist. These pin the endpoint contract, the print parsing, and the
hooks the chart relies on.
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import app as legacy
import market_engine
import web_app

ET = ZoneInfo("America/New_York")


@pytest.fixture
def client():
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
    return c


def _chart(bars, prev=100.0, reg=101.0):
    stamps = [int(datetime(2026, 11, 9, h, m, tzinfo=ET).timestamp()) for h, m, _ in bars]
    return {"meta": {"chartPreviousClose": prev, "regularMarketPrice": reg},
            "timestamp": stamps,
            "indicators": {"quote": [{"close": [px for _, _, px in bars]}]}}


def test_a_regular_hours_print():
    q = market_engine._live_quote_from_chart(_chart([(10, 14, 101.5), (10, 15, None)]), "regular")
    assert q["price"] == 101.5 and q["ext"] is None
    assert q["epoch"] == int(datetime(2026, 11, 9, 10, 14, tzinfo=ET).timestamp())
    assert q["prev_close"] == 100.0


def test_pre_and_post_prints_are_marked_by_their_own_clock():
    assert market_engine._live_quote_from_chart(_chart([(8, 2, 100.7)]), "pre")["ext"] == "pre"
    assert market_engine._live_quote_from_chart(_chart([(17, 30, 99.9)]), "post")["ext"] == "post"


def test_no_print_at_all_is_none():
    assert market_engine._live_quote_from_chart({"meta": {}}, "regular") is None


def test_the_feed_is_off_in_tests():
    assert market_engine.live_ticker_quote("SPY") is None


def test_the_endpoint_serves_the_cached_print(client, monkeypatch):
    quote = {"price": 681.42, "epoch": 1794236100, "prev_close": 679.0,
             "regular_close": 679.0, "ext": "pre", "session": "pre", "age_seconds": 1.2}
    monkeypatch.setattr(legacy._mkt, "live_ticker_quote", lambda t: dict(quote) if t == "SPY" else None)
    r = client.get("/api/terminal/live/spy")
    assert r.status_code == 200 and "no-store" in r.headers["Cache-Control"]
    data = r.get_json()
    assert data["ticker"] == "SPY" and data["quote"]["price"] == 681.42
    assert client.get("/api/terminal/live/NVDA").get_json()["quote"] is None


def test_the_endpoint_rejects_junk(client):
    assert client.get("/api/terminal/live/" + "X" * 13).status_code == 400
    assert client.get("/api/terminal/live/<script>").status_code in (400, 404)


def test_it_needs_a_signed_in_user():
    r = web_app.app.test_client().get("/api/terminal/live/SPY")
    assert r.status_code in (302, 401)


class TestTheChartHooks:
    PAGE = open("templates/terminal.html").read()

    def test_the_chart_polls_the_live_feed_every_three_seconds(self):
        assert "/api/terminal/live/" in self.PAGE and "3000" in self.PAGE
        assert "twLiveSchedule(1500)" in self.PAGE

    def test_stale_bars_from_another_ticker_never_take_the_price(self):
        assert "twChartState.barsKey!==twChartState.ticker+'|'+twChartState.interval" in self.PAGE

    def test_the_slow_database_poll_yields_to_the_live_feed(self):
        assert "if(!liveOwns) twPatchActiveCandle(q,px);" in self.PAGE

    def test_overnight_prints_tick_the_extended_chart(self):
        assert "sess==='overnight'" in self.PAGE
