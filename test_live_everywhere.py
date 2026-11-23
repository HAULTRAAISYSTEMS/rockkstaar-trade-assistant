"""Every ticker on screen updates from one shared live-quote feed, every 3 s.

The banner tape refreshed every 45 s; the Terminal rows, Quick mode and the
dashboard read a database refreshed about as often. They now lay a shared,
seconds-old quote over those values. One background refresher serves every
reader (a single batched Alpaca snapshot call, Yahoo per symbol as a capped
fallback), so the provider sees the same traffic however many tabs poll.
"""
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import app as legacy
import market_engine
import web_app

ET = ZoneInfo("America/New_York")
TODAY = date(2026, 11, 9)


def _snap(trade_at, price, daily_day, daily_close, prev_close):
    return {
        "latestTrade": {"t": trade_at, "p": price},
        "dailyBar": {"t": f"{daily_day}T05:00:00Z", "c": daily_close},
        "prevDailyBar": {"t": "2026-11-05T05:00:00Z", "c": prev_close},
    }


def test_premarket_snapshot_measures_against_fridays_close():
    # Before Monday's open the daily bar is still Friday's.
    q = market_engine._quote_from_alpaca_snapshot(
        _snap("2026-11-09T13:02:11.123456789Z", 682.0, "2026-11-06", 680.0, 675.0), TODAY)
    assert q["prev_close"] == 680.0 and q["change_pct"] == 0.29 and q["ext"] == "pre"


def test_regular_hours_snapshot_uses_the_previous_daily_bar():
    q = market_engine._quote_from_alpaca_snapshot(
        _snap("2026-11-09T15:30:00Z", 686.8, "2026-11-09", 686.8, 680.0), TODAY)
    assert q["prev_close"] == 680.0 and q["change_pct"] == 1.0 and q["ext"] is None


def test_after_hours_and_overnight_trades_are_marked():
    post = market_engine._quote_from_alpaca_snapshot(
        _snap("2026-11-09T22:15:00Z", 681.0, "2026-11-09", 686.8, 680.0), TODAY)
    night = market_engine._quote_from_alpaca_snapshot(
        _snap("2026-11-10T02:15:00Z", 681.0, "2026-11-09", 686.8, 680.0), TODAY)
    assert post["ext"] == "post" and night["ext"] == "overnight"


def test_a_snapshot_without_a_trade_is_no_quote():
    assert market_engine._quote_from_alpaca_snapshot({"dailyBar": {"c": 1}}, TODAY) is None


def test_the_feed_is_off_in_tests():
    assert market_engine.live_quotes(["SPY"]) == {}


def test_overlay_uses_live_quotes_for_stocks_and_the_fast_feed_for_futures(monkeypatch):
    monkeypatch.setattr(market_engine, "live_quotes", lambda syms: {
        "SPY": {"price": 681.234, "change_pct": 0.31}})
    monkeypatch.setattr(market_engine, "fast_pulse_quotes", lambda: {
        "ES": {"price": 6810.25, "pct": 0.22}})
    out = market_engine.overlay_live_tape([
        {"symbol": "SPY", "price": 670.0, "change_pct": -1.0},
        {"symbol": "ES", "price": 6700.0, "change_pct": -1.0},
        {"symbol": "NVDA", "price": 186.0, "change_pct": 2.0},
    ])
    assert out[0]["price"] == 681.23 and out[0]["change_pct"] == 0.31 and out[0]["live"]
    assert out[1]["price"] == 6810.25 and out[1]["change_pct"] == 0.22
    assert out[2]["price"] == 186.0 and "live" not in out[2]   # no fresh quote → unchanged


@pytest.fixture
def client():
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
    return c


def test_the_tape_endpoint_serves_the_overlay(client, monkeypatch):
    monkeypatch.setattr(legacy._mkt, "get_market_tape", lambda: [
        {"symbol": "SPY", "price": 670.0, "change_pct": -1.0}])
    monkeypatch.setattr(legacy, "_tape_watchlist_items", lambda: [])
    monkeypatch.setattr(legacy._mkt, "live_quotes", lambda syms: {
        "SPY": {"price": 681.2, "change_pct": 0.31}})
    r = client.get("/api/market-tape")
    assert "no-store" in r.headers["Cache-Control"]
    assert r.get_json()["items"][0]["price"] == 681.2


def test_payload_overlay_moves_price_and_change_only():
    stocks = [{"ticker": "NVDA", "current_price": 180.0, "gap_pct": 1.0,
               "gap_display": "+1.00%", "swing_score": 77}]
    legacy_live = {"NVDA": {"price": 186.64, "change_pct": 2.78}}
    orig = legacy._mkt.live_quotes
    legacy._mkt.live_quotes = lambda syms: legacy_live
    try:
        out = legacy._overlay_live_prices({"stocks": stocks})
    finally:
        legacy._mkt.live_quotes = orig
    s = out["stocks"][0]
    assert s["current_price"] == 186.64 and s["gap_display"] == "+2.78%" and s["swing_score"] == 77


class TestThePagesPollEveryThreeSeconds:
    def test_the_banner(self):
        tape = open("templates/_market_tape.html").read()
        assert "TSLive.on('tape', handleTape);" in tape and "setInterval(fetchTape, 3000);" in tape

    def test_the_terminal_tape_and_rows(self):
        page = open("templates/terminal.html").read()
        assert "twTapeRefresh(); }, 3000);" in page and "window._arInterval = 3000;" in page

    def test_the_shared_poller_runs_24_5(self):
        js = open("static/js/main.js").read()
        assert "window._arInterval      = 3000;" in js
        assert "if (day === 0) return mins >= 1200;" in js


def test_the_chart_retries_instead_of_staying_broken():
    page = open("templates/terminal.html").read()
    assert "Reconnecting chart…" in page and "application/json" in page


def test_after_hours_print_rides_alongside_the_regular_session(monkeypatch):
    """Fri Nov 20 2026: SPY closed 689.81 (-3.64%); an after-hours print of
    697.48 is +1.11% vs that close. The tape must keep -3.64% as the day's
    move and carry the after-hours move as a labelled extra."""
    monkeypatch.setattr(market_engine, "live_quotes", lambda syms: {
        "SPY": {"price": 697.48, "change_pct": 1.11, "ext": "post", "day_close": 689.81}})
    monkeypatch.setattr(market_engine, "fast_pulse_quotes", lambda: {})
    out = market_engine.overlay_live_tape([{"symbol": "SPY", "price": 689.81, "change_pct": -3.64}])
    assert out[0]["price"] == 689.81 and out[0]["change_pct"] == -3.64
    assert out[0]["ext"] == "post" and out[0]["ext_price"] == 697.48 and out[0]["ext_pct"] == 1.11


def test_tape_shows_an_index_once_even_if_it_is_on_the_watchlist(client, monkeypatch):
    monkeypatch.setattr(legacy._mkt, "get_market_tape", lambda: [
        {"symbol": "QQQ", "price": 608.45, "change_pct": -5.67}])
    monkeypatch.setattr(legacy, "_tape_watchlist_items", lambda: [
        {"symbol": "QQQ", "price": 608.45, "change_pct": -5.67}, {"symbol": "NVDA", "price": 160.0, "change_pct": -14.0}])
    monkeypatch.setattr(legacy._mkt, "live_quotes", lambda syms: {})
    syms = [i["symbol"] for i in client.get("/api/market-tape").get_json()["items"]]
    assert syms == ["QQQ", "NVDA"]


def test_an_extended_print_never_poses_as_the_days_move(monkeypatch):
    """Sun 8 PM: the slower feed had no numbers for NVDA, and its live quote
    is a 24H print. The chip must label it 24H, not show it as the day's
    change; with both closes known the day's move is filled in too."""
    monkeypatch.setattr(market_engine, "live_quotes", lambda syms: {
        "NVDA": {"price": 168.9, "change_pct": 5.62, "ext": "overnight", "prev_close": 159.92},
        "LMT": {"price": 455.0, "change_pct": 0.4, "ext": "post", "prev_close": 460.0, "day_close": 453.18}})
    monkeypatch.setattr(market_engine, "fast_pulse_quotes", lambda: {})
    nvda, lmt = market_engine.overlay_live_tape([{"symbol": "NVDA"}, {"symbol": "LMT", "price": None, "change_pct": None}])
    assert nvda["ext"] == "overnight" and nvda["ext_pct"] == 5.62 and nvda.get("change_pct") is None
    assert nvda["price"] == 159.92
    assert lmt["ext"] == "post" and lmt["ext_pct"] == 0.4 and lmt["change_pct"] == -1.48


def test_the_globe_chips_and_banner_label_extended_moves():
    js = open("static/js/command_center_picture.js").read()
    assert "{ pre: 'PRE', post: 'AH', overnight: '24H' }" in js and "show24h()" in js
    tape = open("templates/_market_tape.html").read()
    assert "overnight: '24H'" in tape and "'ON'" not in tape


def test_watchlist_tape_keeps_the_days_move_beside_extended_hours(monkeypatch):
    legacy._WL_TAPE_CACHE.update(at=0.0, user=None, items=[])
    monkeypatch.setattr(legacy, "get_all_watchlists", lambda uid: [{"id": 1, "name": "Core"}])
    monkeypatch.setattr(legacy, "get_watchlist_stocks", lambda wid: ["CRWD"])
    monkeypatch.setattr(legacy, "_MKT_AVAILABLE", True)
    monkeypatch.setattr(legacy._mkt, "_fetch_prices_chart", lambda t, period="5d": {"CRWD": [243.4, 229.44]})
    monkeypatch.setattr(legacy._mkt, "extended_quotes", lambda t: {"CRWD": {"price": 231.0, "pct": 0.68}})
    monkeypatch.setattr(legacy._mkt, "market_session", lambda: "post")
    legacy._rebuild_tape_watchlist(1)
    item = legacy._WL_TAPE_CACHE["items"][0]
    assert item["change_pct"] == -5.74 and item["price"] == 229.44
    assert item["ext"] == "post" and item["ext_pct"] == 0.68 and item["ext_price"] == 231.0


def test_24h_prices_is_a_saved_setting(client, monkeypatch):
    monkeypatch.setitem(web_app.app.config, "WTF_CSRF_ENABLED", False)
    assert client.get("/api/account/prefs").get_json()["prefs"]["show_24h"] is False
    assert client.post("/api/account/prefs", json={"show_24h": True}).get_json()["prefs"]["show_24h"] is True
    assert client.get("/api/account/prefs").get_json()["prefs"]["show_24h"] is True
    html = client.get("/").get_data(as_text=True)
    assert 'data-show-24h="1"' in html and 'data-pref="show_24h"' in html
    client.post("/api/account/prefs", json={"show_24h": False})
    assert 'data-show-24h="0"' in client.get("/").get_data(as_text=True)


def test_overnight_keeps_the_after_hours_print_however_old():
    """Sun 9:50 PM: LMT's last trade was Friday 7:59 PM (after hours). With no
    overnight trade it is still the latest print, so it must stay live — not
    age out and leave the chip with no extended move."""
    import time
    now = time.time()
    old_post = {"price": 455.0, "epoch": int(now - 3 * 86400), "ext": "post", "prev_close": 453.18}
    assert market_engine._lq_is_fresh(old_post, "overnight", now)
    assert not market_engine._lq_is_fresh({"price": 1, "epoch": int(now - 3600)}, "pre", now)


def test_extended_move_falls_back_to_the_tape_close(monkeypatch):
    """An overnight bar carries no close of its own; the tape item's regular
    price is the base for the 24H move."""
    monkeypatch.setattr(market_engine, "live_quotes", lambda syms: {
        "VOO": {"price": 648.36, "ext": "overnight"}})
    monkeypatch.setattr(market_engine, "fast_pulse_quotes", lambda: {})
    (voo,) = market_engine.overlay_live_tape([{"symbol": "VOO", "price": 635.54, "change_pct": -3.41}])
    assert voo["change_pct"] == -3.41 and voo["ext"] == "overnight"
    assert voo["ext_pct"] == round((648.36 / 635.54 - 1) * 100, 2)


def test_one_24h_rule_for_banner_terminal_and_watchlists(client):
    prefs = open("static/js/prefs.js").read()
    assert "window.TS24" in prefs and "pick: function (t)" in prefs
    tape = open("templates/_market_tape.html").read()
    assert "window.TS24.pick(t)" in tape and "data-mtape-ext hidden" in tape
    term = open("templates/terminal.html").read()
    assert "window.TS24.pick(t)" in term and "overnight:'24H '" in term
    wl = open("templates/watchlists.html").read()
    assert "/api/board-quotes" in wl and "data-show-24h" in wl
    # Long chart views label the year on the indicator time axis.
    assert "function twAxisLabel(t,span)" in term and "tSpan" in term


def test_board_quotes_overlay_extended_moves(client, monkeypatch):
    monkeypatch.setattr(legacy, "_MKT_AVAILABLE", True)
    monkeypatch.setattr(legacy, "get_stock_data", lambda t: {"current_price": 125.53, "prev_close": 145.98})
    monkeypatch.setattr(legacy._mkt, "live_quotes", lambda syms: {
        "NVDA": {"price": 132.52, "ext": "overnight", "prev_close": 125.53}})
    monkeypatch.setattr(legacy._mkt, "fast_pulse_quotes", lambda: {})
    d = client.get("/api/board-quotes?tickers=nvda,bad$,NVDA").get_json()
    q = d["quotes"]["NVDA"]
    assert list(d["quotes"]) == ["NVDA"]
    assert q["change_pct"] == -14.01 and q["ext"] == "overnight" and q["ext_pct"] == 5.57 and q["ext_price"] == 132.52
