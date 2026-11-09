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
        assert "}, 3000);" in open("templates/_market_tape.html").read()

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
