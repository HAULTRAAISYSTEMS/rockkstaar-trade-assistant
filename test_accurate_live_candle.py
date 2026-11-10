"""Today's daily candle from Schwab's official session; one latest print everywhere overnight."""
import os
import time
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-accurate")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

FRI = int(datetime(2026, 11, 6, 5, 0, tzinfo=timezone.utc).timestamp())       # Alpaca daily stamp
MON_CLOSE = int(datetime(2026, 11, 9, 21, 0, tzinfo=timezone.utc).timestamp())


def bars():
    return [{"time": FRI - 86400, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
            {"time": FRI, "open": 734.82, "high": 742.21, "low": 734.58, "close": 735.29, "volume": 58850000}]


SESSION = {"price": 752.69, "epoch": MON_CLOSE + 3600, "day_open": 739.1, "day_high": 754.2, "day_low": 738.0,
           "day_volume": 81000000, "day_close": 752.50, "day_epoch": MON_CLOSE}


def test_schwab_requests_regular_and_extended_fields():
    src = open("schwab.py", encoding="utf-8").read()
    assert '"fields": "quote,regular,extended"' in src


def test_extended_block_newer_trade_wins():
    import schwab
    q = schwab.parse_quote_entry({
        "quote": {"lastPrice": 752.50, "tradeTime": MON_CLOSE * 1000, "closePrice": 735.29, "openPrice": 739.1},
        "regular": {"regularMarketLastPrice": 752.50, "regularMarketTradeTime": MON_CLOSE * 1000},
        "extended": {"lastPrice": 752.69, "tradeTime": (MON_CLOSE + 3000) * 1000}})
    assert q["price"] == 752.69 and q["epoch"] == MON_CLOSE + 3000 and q["day_close"] == 752.50
    assert q["change_pct"] == round((752.69 / 735.29 - 1) * 100, 2)


def test_todays_daily_bar_appended_from_official_session():
    import app as legacy
    out = legacy._with_todays_daily_bar("SPY", bars(), quote=SESSION)
    assert len(out) == 3
    today = out[-1]
    assert today["time"] == FRI + 3 * 86400                        # Fri → Mon, same stamp time
    assert (today["open"], today["high"], today["low"], today["close"]) == (739.1, 754.2, 738.0, 752.50)
    assert today["volume"] == 81000000 and today["source"] == "schwab_session"


def test_no_bar_invented_when_provider_already_has_today_or_no_session():
    import app as legacy
    have = bars() + [{"time": FRI + 3 * 86400, "open": 739, "high": 754, "low": 738, "close": 752.5, "volume": 1}]
    assert legacy._with_todays_daily_bar("SPY", have, quote=SESSION) == have
    assert legacy._with_todays_daily_bar("SPY", bars(), quote=dict(SESSION, day_epoch=FRI + 72000)) == bars()


def test_candles_endpoint_uses_it(monkeypatch):
    import web_app
    import app as legacy
    legacy._TERMINAL_CANDLE_CACHE.clear()
    import market_data, terminal_intelligence
    monkeypatch.setattr(market_data, "fetch_chart_bars", lambda *a, **k: ({"x": 1}, {}))
    monkeypatch.setattr(terminal_intelligence, "normalize_ohlcv_data", lambda d: bars())
    monkeypatch.setattr(legacy, "_todays_session_quote", lambda t: SESSION)
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    d = c.get("/api/terminal/candles/SPY?interval=1d&range=1y").get_json()
    assert d["ok"] and d["bars"][-1]["close"] == 752.50
    legacy._TERMINAL_CANDLE_CACHE.clear()


def test_overnight_prints_go_into_the_shared_feed(monkeypatch):
    import market_engine as me
    import overnight_data
    saved = dict(me._LQ_CACHE)
    me._LQ_CACHE.clear()
    me._LQ_STATE.pop("overnight_at", None)
    me._LQ_WANTED.clear()
    me._LQ_WANTED.update({"SPY": time.time(), "AAPL": time.time()})
    monkeypatch.setattr(me, "_LQ_SHARED_PATH", "/tmp/test-lq-accurate.json")
    monkeypatch.setattr(me, "pulse_session", lambda: "overnight")
    monkeypatch.setattr(me, "_schwab_live", lambda syms: {
        "SPY": {"price": 752.69, "epoch": 1000, "prev_close": 735.29, "ext": "post", "source": "Schwab real-time"},
        "AAPL": {"price": 350.49, "epoch": 5000, "prev_close": 342.4, "source": "Schwab real-time"}})
    monkeypatch.setattr(me, "_alpaca_snapshots", lambda syms: {})
    monkeypatch.setattr(overnight_data, "latest_overnight_bars", lambda syms: {
        "SPY": {"time": 2000, "close": 753.10, "source": "alpaca_overnight"},
        "AAPL": {"time": 4000, "close": 1.0, "source": "alpaca_boats"}})
    try:
        me._refresh_live_quotes()
        spy, aapl = me._LQ_CACHE["SPY"]["quote"], me._LQ_CACHE["AAPL"]["quote"]
        assert spy["price"] == 753.10 and spy["ext"] == "overnight" and "may lag" in spy["source"]
        assert spy["change_pct"] == round((753.10 / 735.29 - 1) * 100, 2)
        assert aapl["price"] == 350.49                                 # older overnight bar ignored
    finally:
        me._LQ_CACHE.clear()
        me._LQ_CACHE.update(saved)
        me._LQ_WANTED.clear()
