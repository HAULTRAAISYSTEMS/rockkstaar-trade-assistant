"""Pre-market / after-hours prices in the Terminal watchlist."""
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _ts(y, m, d, hh, mm):
    return int(datetime(y, m, d, hh, mm, tzinfo=ET).timestamp())


def test_yahoo_premarket_measures_from_last_nights_close():
    import market_engine as me
    res = {"meta": {"regularMarketPrice": 166.19, "regularMarketTime": _ts(2026, 11, 12, 16, 0),
                    "previousClose": 173.73, "chartPreviousClose": 173.73},
           "timestamp": [_ts(2026, 11, 13, 5, 52)], "indicators": {"quote": [{"close": [170.86]}]}}
    q = me._live_quote_from_chart(res, "pre")
    assert q["ext"] == "pre" and q["prev_close"] == 166.19       # not the day before yesterday


def test_yahoo_after_hours_carries_the_bells_close():
    import market_engine as me
    res = {"meta": {"regularMarketPrice": 166.19, "regularMarketTime": _ts(2026, 11, 12, 16, 0),
                    "chartPreviousClose": 173.73},
           "timestamp": [_ts(2026, 11, 12, 19, 40)], "indicators": {"quote": [{"close": [167.84]}]}}
    q = me._live_quote_from_chart(res, "post")
    assert q["ext"] == "post" and q["day_close"] == 166.19 and q["prev_close"] == 173.73


def test_token_key_added_later_still_reads_old_tokens(monkeypatch):
    import secret_box
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("SECRET_KEY", "the-flask-secret-key-123")
    old = secret_box.encrypt("refresh-abc")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "a-new-dedicated-key-456")
    assert secret_box.decrypt(old) == "refresh-abc"              # still readable
    new = secret_box.encrypt("refresh-abc")
    monkeypatch.delenv("SECRET_KEY")
    assert secret_box.decrypt(new) == "refresh-abc"              # new writes use the dedicated key


def test_premarket_daily_prev_close_is_yesterday(monkeypatch):
    import data_fetcher as df
    monkeypatch.setattr(df, "market_session_now", lambda: "pre_market")
    monkeypatch.setattr(df, "_et_now", lambda: datetime(2026, 11, 13, 5, 53, tzinfo=ET))
    bars = {"chart": {"result": [{"meta": {"regularMarketPrice": 166.19},
             "timestamp": [_ts(2026, 11, 11, 9, 30), _ts(2026, 11, 12, 9, 30)],
             "indicators": {"quote": [{"close": [173.73, 166.19], "high": [175, 174], "low": [170, 165]}]}}]}}

    class R:
        status_code = 200
        def json(self):
            return bars
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: R())
    out = df._fetch_price_via_chart_api("NVDA")
    assert out["prev_close"] == 166.19 and out.get("prev_close_date") == "2026-11-12"
