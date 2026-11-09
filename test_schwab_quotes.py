"""Schwab real-time quotes feed the 3-second live layer.

The free Alpaca IEX feed sees one exchange; before the open its "latest
trade" for SPY was an hour old, so every 3-second poll re-served the same
price. Schwab's market data is consolidated and real-time for an account
holder. These pin the parsing, the back-off that keeps a missing
entitlement from becoming a token refresh every three seconds, and the
staleness rule that drops hour-old prints.
"""
import os
import time

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import market_engine
import schwab


def test_a_quote_entry_parses():
    q = schwab.parse_quote_entry({"quote": {"lastPrice": 741.75, "closePrice": 735.29,
                                            "tradeTime": 1794236940123}})
    assert q == {"price": 741.75, "epoch": 1794236940, "prev_close": 735.29, "change_pct": 0.88}


def test_junk_entries_are_no_quote():
    assert schwab.parse_quote_entry({}) is None
    assert schwab.parse_quote_entry({"quote": {"lastPrice": 0, "tradeTime": 1}}) is None
    assert schwab.parse_quote_entry({"quote": {"lastPrice": "x", "tradeTime": 1}}) is None


def test_not_connected_backs_off_instead_of_retrying(monkeypatch):
    schwab._MD_STATE.update(blocked_until=0.0, last_error="")
    calls = []
    monkeypatch.setattr(schwab, "load_tokens", lambda uid: calls.append(uid) or None)
    assert schwab.fetch_quotes(["SPY"]) == {}
    assert schwab.fetch_quotes(["SPY"]) == {}
    assert calls == [1]                      # second call never touched the tokens
    assert schwab.market_data_status()["blocked"]
    schwab._MD_STATE.update(blocked_until=0.0, last_error="")


def test_an_http_error_backs_off_without_a_token_refresh(monkeypatch):
    import urllib.error
    import urllib.request
    schwab._MD_STATE.update(blocked_until=0.0, last_error="")
    monkeypatch.setattr(schwab, "load_tokens", lambda uid: {"access_token": "t"})
    refreshed = []
    monkeypatch.setattr(schwab, "refresh_access_token", lambda rt: refreshed.append(rt))

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 401, "no", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert schwab.fetch_quotes(["SPY"]) == {}
    assert refreshed == [] and schwab.market_data_status()["last_error"] == "HTTP 401"
    schwab._MD_STATE.update(blocked_until=0.0, last_error="")


def test_an_hour_old_print_is_not_live_while_the_market_trades():
    now = time.time()
    assert not market_engine._lq_is_fresh({"epoch": now - 3600}, "pre", now)
    assert market_engine._lq_is_fresh({"epoch": now - 30}, "pre", now)
    assert market_engine._lq_is_fresh({"epoch": now - 400}, "overnight", now)
    assert market_engine._lq_is_fresh({"epoch": now - 86400 * 2}, "closed", now)


def test_schwab_answers_first_and_stale_alpaca_is_dropped(monkeypatch):
    now = time.time()
    market_engine._LQ_CACHE.clear(); market_engine._LQ_WANTED.clear()
    market_engine._LQ_WANTED.update({"SPY": now, "NVDA": now, "AAPL": now})
    monkeypatch.setattr(market_engine, "pulse_session", lambda now=None: "pre")
    monkeypatch.setattr(market_engine, "_schwab_live", lambda syms: {
        "SPY": {"price": 741.9, "epoch": int(now) - 2, "prev_close": 735.29,
                "change_pct": 0.9, "ext": "pre", "source": "Schwab real-time"}})
    monkeypatch.setattr(market_engine, "_alpaca_snapshots", lambda syms: {
        "NVDA": {"price": 175.5, "epoch": int(now) - 3600, "change_pct": -6.0},   # an hour old
        "AAPL": {"price": 343.1, "epoch": int(now) - 20, "change_pct": 0.2}})
    monkeypatch.setattr(market_engine, "_fetch_live_quote", lambda s: None)
    market_engine._refresh_live_quotes()
    cache = market_engine._LQ_CACHE
    assert cache["SPY"]["quote"]["source"] == "Schwab real-time"
    assert "AAPL" in cache and "NVDA" not in cache
    market_engine._LQ_CACHE.clear(); market_engine._LQ_WANTED.clear()
