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


# ── Concurrency, daily candle, status ───────────────────────────────────────
import threading
import time as _time


def test_the_refresher_survives_symbols_being_added_while_it_runs(monkeypatch):
    """Request threads add symbols while the refresher reads them. It used to
    die with 'dictionary changed size during iteration' every cycle."""
    market_engine._LQ_WANTED.clear()
    now = _time.time()
    for i in range(400):
        market_engine._LQ_WANTED[f"S{i}"] = now
    seen = []
    monkeypatch.setattr(market_engine, "_schwab_live", lambda syms: seen.append(len(syms)) or {})
    monkeypatch.setattr(market_engine, "_alpaca_snapshots", lambda syms: {})
    monkeypatch.setattr(market_engine, "_fetch_live_quote", lambda s: None)
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            with market_engine._LQ_LOCK:
                market_engine._LQ_WANTED[f"W{i % 500}"] = _time.time()
            i += 1
            _time.sleep(0.0005)
    t = threading.Thread(target=writer); t.start()
    try:
        for _ in range(50):
            market_engine._refresh_live_quotes()
    finally:
        stop.set(); t.join()
        market_engine._LQ_WANTED.clear()
    assert len(seen) == 50


def test_schwab_quotes_carry_todays_session_for_the_daily_candle():
    import schwab
    q = schwab.parse_quote_entry({"quote": {"lastPrice": 743.3, "closePrice": 735.29,
        "tradeTime": 1794238200000, "openPrice": 740.1, "highPrice": 744.0,
        "lowPrice": 739.5, "totalVolume": 12500000}})
    assert (q["day_open"], q["day_high"], q["day_low"], q["day_volume"]) == (740.1, 744.0, 739.5, 12500000)


def test_alpaca_quotes_carry_todays_bar_only_when_it_is_today():
    from datetime import date
    snap = {"latestTrade": {"t": "2026-11-09T14:33:00Z", "p": 743.3},
            "dailyBar": {"t": "2026-11-09T05:00:00Z", "o": 740.1, "h": 744.0, "l": 739.5, "c": 743.3, "v": 9e6},
            "prevDailyBar": {"t": "2026-11-06T05:00:00Z", "c": 735.29}}
    q = market_engine._quote_from_alpaca_snapshot(snap, date(2026, 11, 9))
    assert q["day_open"] == 740.1 and q["prev_close"] == 735.29
    snap["dailyBar"]["t"] = "2026-11-06T05:00:00Z"
    assert "day_open" not in market_engine._quote_from_alpaca_snapshot(snap, date(2026, 11, 9))


def test_the_daily_chart_opens_todays_candle():
    page = open("templates/terminal.html").read()
    assert "day={time:lastTime+days*86400,open:open," in page
    assert "day_open:q.day_open" in page


def test_the_live_status_page(client):
    r = client.get("/api/live/status")
    assert r.status_code == 200 and "no-store" in r.headers["Cache-Control"]
    data = r.get_json()
    for key in ("quotes_by_source", "schwab_market_data", "last_error", "session"):
        assert key in data


class TestThePhoneChartBar:
    """Chart type, overlays, indicators and full screen used to sit past the
    right edge of a sideways-scrolling strip on phones. Two rows now."""
    CSS = open("static/css/terminal_workspace.css").read()
    PAGE = open("templates/terminal.html").read()

    def test_the_bar_wraps_instead_of_scrolling_sideways(self):
        assert "flex-wrap: wrap !important;" in self.CSS
        assert ".tw-chart-toolbar::after { content: \"\"; order: 3; flex: 0 0 100%;" in self.CSS

    def test_full_screen_sits_on_the_first_row(self):
        assert ".tw-chart-toolbar .tw-fs-btn {\n    order: 2;" in self.CSS

    def test_drawing_tools_open_from_a_button_and_are_remembered(self):
        assert 'id="tw-draw-toggle"' in self.PAGE and "tradestaar.drawbar" in self.PAGE
        assert ".tw-workspace:not(.show-draw) > .tw-drawbar { display: none !important; }" in self.CSS

    def test_the_range_picker_uses_short_labels_on_phones(self):
        assert '"10y":"10Y"' in self.PAGE


class TestImmersiveFullScreen:
    """iPhone full screen pins the panel over the page; the theme made it
    see-through, so the Terminal header collided with the chart controls."""
    CSS = open("static/css/terminal_workspace.css").read()

    def test_the_backdrop_is_solid_and_beats_the_floating_panel_theme(self):
        assert "html.cc27 body .tw .tw-center > .tw-panel.tw-is-fullscreen," in self.CSS
        assert "#04070c !important;" in self.CSS

    def test_exit_is_pinned_top_right_and_the_dock_does_not_trap_it(self):
        assert "position: fixed !important; z-index: 3;" in self.CSS
        dock = self.CSS.split("/* Floating glass dock */")[1].split("/* Exit:")[0]
        assert "backdrop-filter:" not in dock.replace("No backdrop-filter", "")

    def test_landscape_gives_the_chart_the_room(self):
        assert "(orientation: landscape)" in self.CSS
