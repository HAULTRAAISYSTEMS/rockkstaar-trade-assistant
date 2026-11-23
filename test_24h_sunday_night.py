"""Sunday night (and any time the bell is shut) the 24H move must survive a
source that only knows the regular close, a restart, and Yahoo's 1-day chart
rolling over to the next, empty session."""
import time as _time

import market_engine as me

# The regular close two days ago (tests only need it recent, not a Friday).
FRI_CLOSE_EPOCH = int(_time.time()) - 2 * 86400
FRI_AH_EPOCH = FRI_CLOSE_EPOCH + 3 * 3600 + 59 * 60   # 7:59 PM ET after hours


def _reset(monkeypatch, session="overnight"):
    me._LQ_WANTED.clear(); me._LQ_CACHE.clear(); me._LIVE_CACHE.clear()
    me._LQ_STATE["overnight_bars"] = {}
    me._LQ_STATE["overnight_at"] = _time.time()        # no Blue Ocean call
    me._LQ_WANTED["NVDA"] = _time.time()
    monkeypatch.setattr(me, "pulse_session", lambda now=None: session)
    monkeypatch.setattr(me, "_lq_share_write", lambda: None)
    monkeypatch.setattr(me, "_alpaca_snapshots", lambda syms: {})


def _regular_close_quote():
    return {"price": 100.0, "epoch": FRI_CLOSE_EPOCH, "prev_close": 116.3,
            "change_pct": -14.01, "ext": None, "source": "Schwab real-time"}


def test_a_regular_close_print_does_not_replace_the_held_after_hours_print(monkeypatch):
    _reset(monkeypatch)
    me._LQ_CACHE["NVDA"] = {"at": _time.time() - 500, "quote": {
        "price": 102.0, "epoch": FRI_AH_EPOCH, "prev_close": 100.0, "day_close": 100.0,
        "change_pct": 2.0, "ext": "post"}}
    monkeypatch.setattr(me, "_schwab_live", lambda syms: {"NVDA": _regular_close_quote()})
    monkeypatch.setattr(me, "_fetch_live_quote", lambda s: None)
    me._refresh_live_quotes()
    q = me._LQ_CACHE["NVDA"]["quote"]
    assert q["ext"] == "post" and q["price"] == 102.0
    assert _time.time() - me._LQ_CACHE["NVDA"]["at"] < 5     # still served as live


def test_after_a_restart_yahoo_supplies_the_after_hours_print(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(me, "_schwab_live", lambda syms: {"NVDA": _regular_close_quote()})

    def fake_yahoo(sym):
        me._LIVE_CACHE[sym] = {"at": _time.time(), "quote": {
            "price": 102.0, "epoch": FRI_AH_EPOCH, "prev_close": 100.0,
            "regular_close": 100.0, "day_close": 100.0, "ext": "post"}}
    monkeypatch.setattr(me, "_fetch_live_quote", fake_yahoo)
    me._refresh_live_quotes()
    q = me._LQ_CACHE["NVDA"]["quote"]
    assert q["ext"] == "post" and q["day_close"] == 100.0
    item = {"symbol": "NVDA", "price": 100.0, "change_pct": -14.01}
    monkeypatch.setattr(me, "live_quotes", lambda syms: {"NVDA": dict(q)})
    monkeypatch.setattr(me, "fast_pulse_quotes", lambda: {})
    out = me.overlay_live_tape([item])[0]
    assert out["ext"] == "overnight" and out["ext_pct"] == 2.0 and out["change_pct"] == -14.01


def test_without_an_extended_print_the_regular_close_still_shows(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(me, "_schwab_live", lambda syms: {"NVDA": _regular_close_quote()})
    monkeypatch.setattr(me, "_fetch_live_quote", lambda s: None)
    me._refresh_live_quotes()
    assert me._LQ_CACHE["NVDA"]["quote"]["price"] == 100.0


def test_in_regular_hours_nothing_is_set_aside(monkeypatch):
    _reset(monkeypatch, session="regular")
    calls = []
    monkeypatch.setattr(me, "_schwab_live", lambda syms: {"NVDA": _regular_close_quote()})
    monkeypatch.setattr(me, "_fetch_live_quote", lambda s: calls.append(s))
    me._refresh_live_quotes()
    assert calls == [] and me._LQ_CACHE["NVDA"]["quote"]["price"] == 100.0


def test_yahoo_empty_one_day_chart_falls_back_to_five_days(monkeypatch):
    close, ah = 1763758800, 1763758800 + 239 * 60     # Fri 4:00 PM and 7:59 PM ET
    one_day = {"meta": {"regularMarketPrice": 100.0, "regularMarketTime": close,
                        "chartPreviousClose": 116.3, "previousClose": 116.3},
               "timestamp": [], "indicators": {"quote": [{"close": []}]}}
    five_day = {"meta": {"regularMarketPrice": 100.0, "regularMarketTime": close,
                         "chartPreviousClose": 90.0},
                "timestamp": [close - 60, ah],
                "indicators": {"quote": [{"close": [100.0, 102.0]}]}}

    class R:
        def __init__(self, res): self.res = res
        def json(self): return {"chart": {"result": [self.res]}}
    import requests
    monkeypatch.setattr(requests, "get", lambda url, params=None, **k:
                        R(five_day if params.get("range") == "5d" else one_day))
    monkeypatch.setattr(me, "pulse_session", lambda now=None: "closed")
    me._LIVE_CACHE.clear()
    me._fetch_live_quote("NVDA")
    q = me._LIVE_CACHE["NVDA"]["quote"]
    assert q["ext"] == "post" and q["price"] == 102.0
    assert q["day_close"] == 100.0 and q["prev_close"] == 100.0   # not the 5-day-ago close


def teardown_function(_fn):
    me._LQ_WANTED.clear(); me._LQ_CACHE.clear(); me._LIVE_CACHE.clear()


def test_overnight_every_ticker_reads_24h(monkeypatch):
    monkeypatch.setattr(me, "pulse_session", lambda now=None: "overnight")
    monkeypatch.setattr(me, "fast_pulse_quotes", lambda: {})
    monkeypatch.setattr(me, "live_quotes", lambda syms: {
        "AAPL": {"price": 230.0, "epoch": 1, "prev_close": 228.0, "change_pct": 0.88, "ext": None},
        "MSFT": {"price": 410.0, "epoch": 2, "prev_close": 400.0, "day_close": 405.0, "ext": "post"}})
    a, m = me.overlay_live_tape([{"symbol": "AAPL", "price": 230.0, "change_pct": 0.88},
                                 {"symbol": "MSFT", "price": 405.0, "change_pct": 1.25}])
    assert a["ext"] == "overnight" and a["ext_pct"] == 0.0 and a["change_pct"] == 0.88
    assert m["ext"] == "overnight" and m["ext_pct"] == round((410 / 405 - 1) * 100, 2)


def test_after_hours_keeps_its_own_label_before_8pm(monkeypatch):
    monkeypatch.setattr(me, "pulse_session", lambda now=None: "post")
    monkeypatch.setattr(me, "fast_pulse_quotes", lambda: {})
    monkeypatch.setattr(me, "live_quotes", lambda syms: {
        "MSFT": {"price": 410.0, "epoch": 2, "day_close": 405.0, "ext": "post"}})
    m = me.overlay_live_tape([{"symbol": "MSFT", "price": 405.0, "change_pct": 1.25}])[0]
    assert m["ext"] == "post"
