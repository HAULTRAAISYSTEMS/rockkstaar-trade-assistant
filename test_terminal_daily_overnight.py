"""Daily chart keeps today's candle after the bell; Terminal price keeps moving overnight."""
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-daily-overnight")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")


def test_schwab_quote_carries_regular_close_and_time():
    import schwab
    q = schwab.parse_quote_entry({
        "quote": {"lastPrice": 752.69, "tradeTime": 1762736400000, "closePrice": 735.29,
                  "openPrice": 739.1, "highPrice": 754.2, "lowPrice": 738.0, "totalVolume": 81000000},
        "regular": {"regularMarketLastPrice": 751.94, "regularMarketTradeTime": 1762722000000}})
    assert q["day_close"] == 751.94 and q["day_epoch"] == 1762722000
    assert q["price"] == 752.69                       # the after-hours print stays the live price
    no_open = schwab.parse_quote_entry({"quote": {"lastPrice": 1, "tradeTime": 1},
                                        "regular": {"regularMarketLastPrice": 1, "regularMarketTradeTime": 1000}})
    assert "day_close" not in no_open                 # no session yet → no candle data


def test_alpaca_snapshot_carries_regular_close():
    from datetime import date
    import market_engine as me
    snap = {"latestTrade": {"p": 752.7, "t": "2026-11-10T01:05:00Z"},
            "dailyBar": {"o": 739.1, "h": 754.2, "l": 738.0, "c": 751.94, "v": 8.1e7, "t": "2026-11-09T05:00:00Z"},
            "prevDailyBar": {"c": 735.29}}
    q = me._quote_from_alpaca_snapshot(snap, date(2026, 11, 9))
    assert q["day_close"] == 751.94 and q["day_epoch"] and q["ext"] == "overnight"


@pytest.fixture
def client():
    import web_app
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    return c


def test_terminal_live_uses_newer_overnight_print(client, monkeypatch):
    import app as legacy
    stale = {"price": 752.69, "epoch": 1000, "prev_close": 735.29, "ext": "post", "source": "Schwab real-time",
             "day_open": 739.1, "day_close": 751.94, "day_epoch": 900}
    monkeypatch.setattr(legacy._mkt, "live_quotes", lambda syms: {"SPY": dict(stale)})
    monkeypatch.setattr(legacy._mkt, "overnight_window_start", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(legacy, "_terminal_overnight_bar", lambda t: {"time": 2000, "close": 753.4})
    q = client.get("/api/terminal/live/SPY").get_json()["quote"]
    assert q["price"] == 753.4 and q["ext"] == "overnight" and q["source"] == "Alpaca overnight"
    assert q["day_close"] == 751.94 and q["prev_close"] == 735.29          # today's candle data kept
    monkeypatch.setattr(legacy, "_terminal_overnight_bar", lambda t: {"time": 500, "close": 1.0})
    assert client.get("/api/terminal/live/SPY").get_json()["quote"]["price"] == 752.69   # older bar ignored
    monkeypatch.setattr(legacy._mkt, "overnight_window_start", lambda: None)
    monkeypatch.setattr(legacy, "_terminal_overnight_bar", lambda t: pytest.fail("not overnight"))
    assert client.get("/api/terminal/live/SPY").get_json()["quote"]["price"] == 752.69


def test_overnight_label(monkeypatch):
    import app as legacy
    monkeypatch.setattr(legacy._mkt, "overnight_window_start", lambda: datetime.now(timezone.utc))
    assert legacy._session_label("closed") == "Overnight 24H"
    assert legacy._session_label("regular") == "Live"
    monkeypatch.setattr(legacy._mkt, "overnight_window_start", lambda: None)
    assert legacy._session_label("closed") == "At the close"


JS_HARNESS = r"""
const fs=require('fs');const s=fs.readFileSync('templates/terminal.html','utf8');
const i=s.indexOf('function twPatchActiveCandle');const j=s.indexOf('\nfunction ',i+10);
const updates=[];
global.twChartState={ticker:'SPY',interval:'1d',extended:false,barsKey:'SPY|1d',barMap:{},
  bars:[{time:Date.parse('2026-11-06T05:00:00Z')/1000,open:740,high:742,low:734,close:735.29,volume:5e7}],
  area:{update:()=>{}},candle:{update:d=>updates.push(d)},vol:{update:()=>{}}};
global.twTimeKey=t=>String(t);global.twCandleDatum=d=>d;global.twVolumeColor=()=>'#fff';
global.twRenderIndicators=()=>{};global.twRenderEmas=()=>{};global.twOhlcv=()=>{};
eval(s.slice(i,j));
const q=JSON.parse(process.argv[2]);
twPatchActiveCandle(q,q.px);
const last=twChartState.bars[twChartState.bars.length-1];
console.log(JSON.stringify({n:twChartState.bars.length,last}));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_daily_candle_appears_after_hours_with_regular_close(tmp_path):
    harness = tmp_path / "h.js"
    harness.write_text(JS_HARNESS)
    q = {"ticker": "SPY", "market_session": "after_hours", "as_of_epoch": 1794272400, "px": 752.69,
         "day_open": 739.1, "day_high": 754.2, "day_low": 738.0, "day_volume": 8.1e7,
         "day_close": 751.94, "day_epoch": 1794258000}
    out = json.loads(subprocess.run(["node", str(harness), json.dumps(q)], capture_output=True, text=True,
                                    check=True, cwd=os.getcwd()).stdout)
    assert out["n"] == 2
    assert out["last"]["close"] == 751.94 and out["last"]["open"] == 739.1 and out["last"]["high"] == 754.2
    # No regular-session data (e.g. before the open) → nothing is invented.
    q2 = dict(q, day_close=None, day_epoch=None)
    out2 = json.loads(subprocess.run(["node", str(harness), json.dumps(q2)], capture_output=True, text=True,
                                     check=True, cwd=os.getcwd()).stdout)
    assert out2["n"] == 1
