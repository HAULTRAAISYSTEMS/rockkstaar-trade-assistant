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
                "levels.on", "labels.bidask", "labels.indicators", "labels.drawings"):
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
