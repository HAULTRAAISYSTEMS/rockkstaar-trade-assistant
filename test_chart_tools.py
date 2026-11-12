"""Terminal chart tools: drawings sync per symbol, and the drawing maths.

The drawing engine lives in static/js/chart_tools.js. Drawings belong to a
symbol (not a timeframe), are stored on the device and on the account, and
extended-hours candles use the same green/red as the regular session.
"""
import json
import os
import shutil
import subprocess

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-chart-tools")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

JS = open("static/js/chart_tools.js", encoding="utf-8").read()
TEMPLATE = open("templates/terminal.html", encoding="utf-8").read()


@pytest.fixture
def client(monkeypatch):
    import app as legacy
    import web_app
    store = {}
    monkeypatch.setattr(legacy, "get_user_setting", lambda uid, key, default=None: store.get((uid, key), default))
    monkeypatch.setattr(legacy, "set_user_setting", lambda uid, key, value: store.__setitem__((uid, key), value))
    web_app.app.config["WTF_CSRF_ENABLED"] = False
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    yield c, store
    web_app.app.config["WTF_CSRF_ENABLED"] = True


def test_drawings_round_trip_per_symbol(client):
    c, store = client
    assert c.get("/api/chart/drawings/SPY").get_json() == {"ok": True, "ticker": "SPY", "updated": 0, "drawings": []}
    d = [{"id": "a", "type": "hline", "points": [{"t": 1794240000, "p": 756.11}], "style": {"color": "#38d6c5"}}]
    r = c.put("/api/chart/drawings/spy", json={"updated": 1794240000123, "drawings": d})
    assert r.status_code == 200 and r.get_json()["count"] == 1
    got = c.get("/api/chart/drawings/SPY").get_json()
    assert got["updated"] == 1794240000123 and got["drawings"] == d
    assert c.get("/api/chart/drawings/QQQ").get_json()["drawings"] == []     # other symbols untouched
    assert (1, "chart_drawings:SPY") in store


def test_drawings_reject_bad_input(client):
    c, _ = client
    assert c.get("/api/chart/drawings/<script>").status_code in (400, 404)
    assert c.put("/api/chart/drawings/SPY", json={"drawings": "nope"}).status_code == 400
    assert c.put("/api/chart/drawings/SPY", json={"drawings": [{"type": "hline"}]}).status_code == 400
    too_many = [{"type": "hline", "points": []}] * 301
    assert c.put("/api/chart/drawings/SPY", json={"drawings": too_many}).status_code == 400
    huge = [{"type": "text", "points": [], "style": {"text": "x" * 3000}}] * 100
    assert c.put("/api/chart/drawings/SPY", json={"drawings": huge}).status_code == 413


def test_chart_prefs_round_trip(client):
    c, _ = client
    assert c.get("/api/chart/prefs").get_json() == {"ok": True, "prefs": None}
    prefs = {"overlays": {"vwap": {"on": False}}, "tools": {"hline": {"color": "#ff00aa"}}, "updated": 5}
    assert c.put("/api/chart/prefs", json={"prefs": prefs}).get_json()["ok"]
    assert c.get("/api/chart/prefs").get_json()["prefs"] == prefs
    assert c.put("/api/chart/prefs", json={"prefs": [1]}).status_code == 400


def test_template_uses_chart_tools():
    assert "js/chart_tools.js" in TEMPLATE and "css/chart_tools.css" in TEMPLATE
    assert 'id="ct-rail"' in TEMPLATE
    # The old per-interval engine is gone; candles colour through chart_tools.
    assert "function twDrawFib" not in TEMPLATE and "tradestaar.drawings.'+" not in TEMPLATE
    assert "function twCandleDatum(bar){ return ctCandleDatum(bar); }" in TEMPLATE
    assert "ctInitStudies(chart);" in TEMPLATE
    # Settings for candles, overlays and the price scale are on the panel.
    for key in ("candles.up", "candles.down", "candles.session", "vwap.on", "bb.on", "sma.on",
                "levels.on", "labels.bid", "labels.ask", "labels.indicators", "labels.drawings",
                "levels.pdh", "levels.pdl", "levels.pdc", "levels.pmh", "levels.pml", "candles.style",
                "scale.mode", "scale.lastLine", "view.grid", "view.crosshair", "view.watermark",
                "view.countdown", "view.hilo", "view.ohlc", "view.tooltip", "draw.stay", "draw.hidden"):
        assert 'data-ct-set="%s"' % key in TEMPLATE, key


def test_all_tools_are_on_the_rail():
    for tool in ("trend", "ray", "extended", "arrow", "hline", "hray", "vline", "channel", "fib",
                 "fibext", "rect", "text", "long", "short", "measure", "avwap"):
        assert "  %s:" % tool in JS or "  %s: " % tool in JS, tool


HARNESS = r"""
const fs=require('fs');
global.localStorage={_d:{},getItem(k){return this._d[k]===undefined?null:this._d[k];},setItem(k,v){this._d[k]=String(v);},removeItem(k){delete this._d[k];},key(i){return Object.keys(this._d)[i];},get length(){return Object.keys(this._d).length;}};
global.document={addEventListener(){},getElementById(){return null;},querySelectorAll(){return [];},querySelector(){return null;}};
global.window={};global.fetch=()=>Promise.resolve({ok:false,json:()=>({})});
const bars=[];for(let i=0;i<10;i++)bars.push({time:1000+i*900,open:10,high:11,low:9,close:10.5,volume:100,session:i<2?'premarket':'regular'});
global.twChartState={bars:bars,interval:'15m',type:'candles',ticker:'SPY',selectedDrawing:null,chart:null};
eval(fs.readFileSync('static/js/chart_tools.js','utf8')+'\n;global.T={ctTimeToLogical,ctLogicalToTime,ctNewDrawing,ctConvertLegacy,ctCandleDatum,ctVolumeColor,ctPrefs,ctReadLocal};');
const out={};
out.l_mid=T.ctTimeToLogical(1000+3*900+450);
out.l_after=T.ctTimeToLogical(1000+9*900+1800);
out.t_back=T.ctLogicalToTime(4.5);
out.t_before=T.ctLogicalToTime(-1);
const lg=T.ctNewDrawing('long',[{t:1000,p:100},{t:2000,p:104}]);out.stop=lg.stop;
const fib=T.ctNewDrawing('fib',[{t:1000,p:100},{t:2000,p:110}]);out.fibLevels=fib.style.levels.filter(l=>l.on).map(l=>l.v);
out.legacy=T.ctConvertLegacy({id:'x',type:'line',points:[{time:1000,price:5},{time:1900,price:6}],color:'#123456',width:3});
out.ah=T.ctCandleDatum({time:1,open:1,high:2,low:0.5,close:1.5,session:'after_hours'});
T.ctPrefs.candles.session=true;out.ahSession=T.ctCandleDatum({time:1,open:1,high:2,low:0.5,close:1.5,session:'after_hours'});
localStorage.setItem('tradestaar.drawings.SPY.15m.5d.extended',JSON.stringify([{id:'old1',type:'fib',points:[{time:1000,price:5},{time:1900,price:6}],color:'#abcdef'}]));
localStorage.setItem('tradestaar.drawings.SPY.1h.1mo.regular',JSON.stringify([{id:'old2',type:'line',points:[{time:1000,price:5},{time:1900,price:6}]}]));
const merged=T.ctReadLocal('SPY');out.migrated=merged.drawings.map(d=>d.type+':'+d.id).sort();
out.oldKeysLeft=Object.keys(localStorage._d).filter(k=>k.indexOf('tradestaar.drawings.')===0).length;
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_drawing_maths_and_migration(tmp_path):
    h = tmp_path / "h.js"
    h.write_text(HARNESS)
    out = json.loads(subprocess.run(["node", str(h)], capture_output=True, text=True, check=True,
                                    cwd=os.getcwd()).stdout)
    # Time ⇄ bar index: between bars interpolates, past the last bar extrapolates
    # by the interval, so a drawing keeps its place on any timeframe.
    assert out["l_mid"] == pytest.approx(3.5)
    assert out["l_after"] == pytest.approx(11)
    assert out["t_back"] == pytest.approx(1000 + 4.5 * 900)
    assert out["t_before"] == pytest.approx(100)
    # Long position starts at 1:1 — stop mirrors the target around the entry.
    assert out["stop"] == pytest.approx(96)
    assert out["fibLevels"] == [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618]
    leg = out["legacy"]
    assert leg["type"] == "trend" and leg["style"]["color"] == "#123456" and leg["style"]["width"] == 3
    assert leg["points"] == [{"t": 1000, "p": 5}, {"t": 1900, "p": 6}]
    # After-hours candles: series green/red by default, session palette is opt-in.
    assert "color" not in out["ah"]
    assert out["ahSession"]["color"] == "#c77dff"
    # Old per-interval drawings fold into the one per-symbol list.
    assert out["migrated"] == ["fib:old1", "trend:old2"] and out["oldKeysLeft"] == 0


def test_drawings_follow_price_axis_moves():
    # Zoom, auto-scale and Reset view move the price axis without a time-axis
    # event; a per-frame check of where prices sit on screen redraws then.
    assert "function ctViewSignature()" in JS and "requestAnimationFrame(ctWatchView)" in JS
    sig = JS[JS.index("function ctViewSignature()"):JS.index("function ctWatchView()")]
    assert "priceToCoordinate(ref)" in sig and "getVisibleLogicalRange" in sig


LOGX_HARNESS = r"""
const fs=require('fs');
global.localStorage={_d:{},getItem(k){return this._d[k]===undefined?null:this._d[k];},setItem(k,v){this._d[k]=String(v);},removeItem(k){delete this._d[k];},key(i){return Object.keys(this._d)[i];},get length(){return Object.keys(this._d).length;}};
global.document={addEventListener(){},getElementById(){return null;},querySelectorAll(){return [];},querySelector(){return null;}};
global.window={};global.fetch=()=>Promise.resolve({ok:false,json:()=>({})});
const bars=[];for(let i=0;i<10;i++)bars.push({time:1000+i*3600,open:10,high:11,low:9,close:10.5,volume:100});
// Like lightweight-charts 4.1: whole indexes map to pixels, fractions answer 0.
const ts={logicalToCoordinate:l=>Number.isInteger(l)?100+l*20:0};
global.twChartState={bars:bars,interval:'1h',type:'candles',ticker:'SPY',selectedDrawing:null,chart:{timeScale:()=>ts}};
eval(fs.readFileSync('static/js/chart_tools.js','utf8')+'\n;global.T={ctX};');
console.log(JSON.stringify([T.ctX(1000+3*3600), T.ctX(1000+3*3600+900), T.ctX(1000+9*3600+1800)]));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_points_between_bars_are_placed_between_bars(tmp_path):
    # A 15m point on the 1H chart sits between two bars; it must land between
    # them on screen, not at x = 0 (which made a fib impossible to grab).
    h = tmp_path / "h.js"
    h.write_text(LOGX_HARNESS)
    out = json.loads(subprocess.run(["node", str(h)], capture_output=True, text=True, check=True,
                                    cwd=os.getcwd()).stdout)
    assert out == [pytest.approx(160), pytest.approx(165), pytest.approx(290)]


def test_clear_all_does_not_rely_on_browser_dialogs():
    assert "window.confirm" not in JS and "ctClearArmed" in JS


def test_weekly_monthly_and_full_history_are_offered():
    import app as legacy
    assert "max" in legacy._TERMINAL_INTERVALS["1d"]["ranges"]
    assert legacy._TERMINAL_INTERVALS["1w"]["fetch"] == "1d"   # built from daily bars
    assert legacy._TERMINAL_INTERVALS["1mo"]["default"] == "max"
    assert "('1w','1W'),('1mo','1M')" in TEMPLATE and '"max":"All (full history)"' in TEMPLATE


def test_full_history_prefers_yahoo_and_keeps_weekly_bars(monkeypatch):
    import data_fetcher
    import market_data
    calls = []

    def fake_yahoo(ticker, interval="1d", range_str="1y", include_prepost=False):
        calls.append((interval, range_str))
        return {"timestamps": [-252322200, 0, 86400], "opens": [0.25, 1, 2], "closes": [0.3, 1.1, 2.1],
                "highs": [0.31, 1.2, 2.2], "lows": [0.24, 0.9, 1.9], "volumes": [1, 2, 3],
                "data_granularity": interval}
    monkeypatch.setattr(data_fetcher, "_fetch_ohlcv_via_chart_api", fake_yahoo)
    monkeypatch.setattr(market_data, "_alpaca_bars", lambda *a, **k: (None, None))
    data, meta = market_data.fetch_chart_bars("KO", "1d", "max")
    assert calls[0] == ("1d", "max") and meta["feed_label"] == "FULL HISTORY"
    assert meta["history_start"] == "1962-01-02" and data["timestamps"][0] == -252322200
    # Weekly bars are stamped at midnight; they are not intraday bars to filter.
    rows = [{"t": "2026-11-09T05:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}]
    assert market_data._parse_bars(rows, False, "1wk")["timestamps"] == [1794200400]


def test_candles_route_builds_weekly_from_daily(client, monkeypatch):
    c, _ = client
    import app as legacy
    import market_data
    legacy._TERMINAL_CANDLE_CACHE.clear()
    # Mon 2026-11-02 .. Fri 2026-11-13, 14:30 UTC daily stamps (two weeks).
    from datetime import datetime, timezone
    monday = int(datetime(2026, 11, 2, 14, 30, tzinfo=timezone.utc).timestamp())
    days = [monday + 86400 * i for i in range(12) if i % 7 not in (5, 6)]
    seen = {}

    def fake(ticker, interval, range_str, include_extended=False):
        seen["interval"] = interval
        return ({"timestamps": days, "opens": [10.0 + i for i in range(len(days))],
                 "closes": [10.5 + i for i in range(len(days))], "highs": [11.0 + i for i in range(len(days))],
                 "lows": [9.5 + i for i in range(len(days))], "volumes": [100] * len(days),
                 "data_granularity": "1d"}, {"provider": "Alpaca"})
    monkeypatch.setattr(market_data, "fetch_chart_bars", fake)
    monkeypatch.setattr(legacy, "_with_todays_daily_bar", lambda t, bars, quote=None: bars)
    d = c.get("/api/terminal/candles/SPY?interval=1w&range=max").get_json()
    assert seen["interval"] == "1d"
    assert d["ok"] and d["source_interval"] == "1w" and len(d["bars"]) == 2
    wk1, wk2 = d["bars"]
    assert wk1["open"] == 10.0 and wk1["close"] == 14.5 and wk1["high"] == 15.0 and wk1["volume"] == 500
    assert wk2["open"] == 15.0 and wk2["time"] == days[5]
    m = c.get("/api/terminal/candles/SPY?interval=1mo&range=max").get_json()
    assert m["ok"] and len(m["bars"]) == 1 and m["bars"][0]["volume"] == 1000
    assert c.get("/api/terminal/candles/SPY?interval=1w&range=1d").status_code == 400


def test_chart_retries_server_errors_instead_of_sticking():
    assert "if(r.status>=500) return r.json()" in TEMPLATE and "e5.retryable=true" in TEMPLATE


def test_terminal_has_watchlist_switcher(client, monkeypatch):
    c, _ = client
    import app as legacy
    monkeypatch.setattr(legacy, "get_all_watchlists", lambda uid=None: [
        {"id": 1, "name": "A+ Ready", "user_id": 1}, {"id": 2, "name": "Swing Ideas", "user_id": 1}])
    html = c.get("/terminal").get_data(as_text=True)
    assert 'id="tw-wl-select"' in html and "Swing Ideas" in html and "twSwitchWatchlist" in html


def test_header_and_watchlist_agree_overnight(client, monkeypatch):
    c, _ = client
    import app as legacy
    shared = {"SPY": {"price": 757.67, "epoch": 1000, "prev_close": 750.0, "day_close": 759.81, "ext": "overnight"}}
    monkeypatch.setattr(legacy._mkt, "live_quotes", lambda syms: {k: dict(v) for k, v in shared.items() if k in syms})
    monkeypatch.setattr(legacy._mkt, "overnight_window_start", lambda *a: object())
    monkeypatch.setattr(legacy, "_terminal_overnight_bar", lambda t: {"time": 2000, "close": 757.27})
    monkeypatch.setattr(legacy, "get_active_wl_id", lambda: 1)
    monkeypatch.setattr(legacy, "get_watchlist_stocks", lambda wl: ["SPY"])
    monkeypatch.setattr(legacy, "get_all_stock_data", lambda: [{"ticker": "SPY", "current_price": 759.81}])
    head = c.get("/api/terminal/live/SPY").get_json()["quote"]["price"]
    row = next(r for r in c.get("/api/terminal/quotes").get_json()["quotes"] if r["ticker"] == "SPY")
    assert head == row["price"] == 757.27


def test_volume_goes_one_color_when_zoomed_far_out():
    assert "var ctVolDense = false;" in JS and "function ctCheckVolumeDensity()" in JS
    assert "if (ctVolDense) return 'rgba(120,150,190,0.42)';" in JS


def test_switching_to_an_empty_list_does_not_seed_it(client, monkeypatch):
    c, _ = client
    import app as legacy
    added = []
    monkeypatch.setattr(legacy, "get_all_watchlists", lambda uid=None: [
        {"id": 1, "name": "A+ Ready", "user_id": 1}, {"id": 2, "name": "AVOID", "user_id": 1}])
    monkeypatch.setattr(legacy, "get_active_wl_id", lambda: 2)
    monkeypatch.setattr(legacy, "get_watchlist_stocks", lambda wl: [])
    monkeypatch.setattr(legacy, "add_ticker_to_watchlist", lambda wl, t: added.append(t))
    assert c.get("/terminal").status_code == 200
    assert added == []


def test_full_history_is_fetched_in_ten_year_windows(monkeypatch):
    # Yahoo truncates / downsamples range=max for daily candles, so full
    # history is asked for as explicit 10-year windows and joined.
    import requests
    import data_fetcher
    calls = []

    def fake_get(url, params=None, **k):
        calls.append(dict(params))
        p1 = params["period1"]

        class R:
            status_code = 200
            def json(self):
                if p1 < 315532800:          # before 1980: not listed yet
                    return {"chart": {"result": [{"timestamp": [], "meta": {}, "indicators": {"quote": [{}]}}]}}
                ts = [p1 + 86400, p1 + 2 * 86400]
                return {"chart": {"result": [{"timestamp": ts, "meta": {"dataGranularity": "1d"},
                                              "indicators": {"quote": [{"open": [1, 2], "close": [1, 2], "high": [1, 2],
                                                                        "low": [1, 2], "volume": [1, 1]}]}}]}}
        return R()
    monkeypatch.setattr(requests, "get", fake_get)
    out = data_fetcher._fetch_ohlcv_via_chart_api("KLAC", interval="1d", range_str="max")
    assert all("range" not in c for c in calls)
    assert min(c["period1"] for c in calls) == data_fetcher._FULL_HISTORY_START
    assert out["data_granularity"] == "1d" and out["timestamps"] == sorted(out["timestamps"])
    listed = [c for c in calls if c["period1"] >= 315532800]
    assert len(out["timestamps"]) == 2 * len(listed)
    assert out["timestamps"][0] >= 315532800


def test_full_history_with_a_missing_window_falls_back(monkeypatch):
    import data_fetcher
    def fake(ticker, interval="1d", range_str="1y", include_prepost=False, period=None):
        if period is None:
            return data_fetcher._fetch_full_history_chunked(ticker, interval)
        if 1000000000 < period[0] < 1400000000:   # a hole in the middle
            return None
        return {"timestamps": [period[0] + 1], "opens": [1], "highs": [1], "lows": [1], "closes": [1],
                "volumes": [1], "data_granularity": "1d"}
    monkeypatch.setattr(data_fetcher, "_fetch_ohlcv_via_chart_api", fake)
    assert data_fetcher._fetch_ohlcv_via_chart_api("KLAC", "1d", "max") is None


def test_alpaca_start_is_clamped_to_its_history(monkeypatch):
    import market_data
    seen = {}
    monkeypatch.setattr(market_data, "_credentials", lambda: ("k", "s"))

    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"bars": [{"t": "2016-01-04T05:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 1}]}
    monkeypatch.setattr(market_data.requests, "get", lambda url, params=None, **k: (seen.update(params), R())[1])
    data, meta = market_data._alpaca_bars("KLAC", "1d", "max", False)
    assert data and seen["start"].startswith("2015-01-01")


def test_interval_mismatch_is_shown_not_retried(client, monkeypatch):
    c, _ = client
    import app as legacy
    import market_data
    legacy._TERMINAL_CANDLE_CACHE.clear()
    monkeypatch.setattr(market_data, "fetch_chart_bars", lambda *a, **k: (
        {"timestamps": [1794200400], "opens": [1.0], "closes": [1.5], "highs": [2.0], "lows": [0.5],
         "volumes": [10], "data_granularity": "3mo"}, {"provider": "Yahoo"}))
    r = c.get("/api/terminal/candles/KLAC?interval=1d&range=max")
    d = r.get_json()
    assert r.status_code == 200 and d["ok"] is False and "3mo" in d["error"]


def test_empty_list_still_charts_a_symbol(client, monkeypatch):
    # An empty list is no longer auto-seeded once the user has several lists;
    # the chart must still load something instead of sitting on "No symbol".
    c, _ = client
    import app as legacy
    monkeypatch.setattr(legacy, "get_all_watchlists", lambda uid=None: [
        {"id": 1, "name": "A+ Ready", "user_id": 1}, {"id": 2, "name": "AVOID", "user_id": 1}])
    monkeypatch.setattr(legacy, "get_active_wl_id", lambda: 2)
    monkeypatch.setattr(legacy, "get_watchlist_stocks", lambda wl: [])
    html = c.get("/terminal").get_data(as_text=True)
    assert "starter watchlist is seeding" not in html and "No symbol" not in html
    assert 'id="tw-c-ticker">SPY<' in html and 'var bootSym=(first && first.dataset.ticker) || "SPY";' in html
    assert "This list is empty" in html


def test_list_whose_rows_have_not_loaded_charts_its_first_ticker(client, monkeypatch):
    c, _ = client
    import app as legacy
    monkeypatch.setattr(legacy, "get_active_wl_id", lambda: 1)
    monkeypatch.setattr(legacy, "get_watchlist_stocks", lambda wl: ["NVDA", "AMD"])
    monkeypatch.setattr(legacy, "get_all_stock_data", lambda: [])
    html = c.get("/terminal").get_data(as_text=True)
    assert '|| "NVDA";' in html and 'id="tw-c-ticker">NVDA<' in html
