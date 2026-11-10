"""The Today strip must not flip between live prints and the slow snapshot."""
import json
import os
import shutil
import subprocess
import time

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-pulse-flip")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import market_engine as me


@pytest.fixture
def shared(tmp_path, monkeypatch):
    monkeypatch.setattr(me, "_FAST_SHARED_PATH", str(tmp_path / "fast.json"))
    old = dict(me._FAST_CACHE)
    me._FAST_CACHE.update(at=0.0, quotes={})
    yield tmp_path / "fast.json"
    me._FAST_CACHE.clear()
    me._FAST_CACHE.update(old)


def test_a_worker_adopts_the_other_workers_newer_reading(shared):
    me._FAST_CACHE.update(at=time.time(), quotes={"SPY": {"pct": 0.08}, "_session": "overnight"})
    me._fast_share_write()
    me._FAST_CACHE.update(at=0.0, quotes={})          # the "other" worker: empty
    me._fast_share_read()
    assert me._FAST_CACHE["quotes"]["SPY"]["pct"] == 0.08
    me._FAST_CACHE.update(at=time.time() + 5, quotes={"SPY": {"pct": 0.2}})
    me._fast_share_read()                             # its own newer reading wins
    assert me._FAST_CACHE["quotes"]["SPY"]["pct"] == 0.2


def test_partial_fetch_keeps_the_missing_symbols(shared, monkeypatch):
    monkeypatch.setattr(me, "pulse_session", lambda: "overnight")
    monkeypatch.setattr(me, "overnight_window_start", lambda *a, **k: None)
    me._FAST_CACHE.update(at=time.time(), quotes={"SPY": {"pct": 0.08}, "QQQ": {"pct": -0.01}, "_session": "overnight"})

    class R:
        def __init__(self, sym):
            self.sym = sym

        def json(self):
            return {"chart": {"result": [{"meta": {"symbol": self.sym}}]}}

    import requests
    monkeypatch.setattr(requests, "get", lambda url, **k: R(url.rsplit("/", 1)[-1]))
    monkeypatch.setattr(me, "_fast_quote_from_chart",
                        lambda res, key, session, today: {"pct": 0.05} if key == "ES" else None)
    me._fetch_fast_quotes()
    q = me._FAST_CACHE["quotes"]
    assert q["ES"]["pct"] == 0.05 and q["SPY"]["pct"] == 0.08 and q["QQQ"]["pct"] == -0.01
    assert json.loads(shared.read_text())["quotes"]["SPY"]["pct"] == 0.08


def test_pulse_says_whether_it_is_live():
    import app as legacy
    src = open("app.py", encoding="utf-8").read()
    assert 'pulse["fast_live"]' in src and '"fast_live",' in src
    assert callable(legacy._build_today_pulse)


HARNESS = r"""
const fs=require('fs');const s=fs.readFileSync('templates/_liq_scripts.html','utf8');
const i=s.indexOf('let _lastLivePulse');const j=s.indexOf('function _applyPulse');
eval(s.slice(i,j).replace('let _lastLivePulse','var _lastLivePulse'));
const live={fast_live:true,session:'overnight',spy:0.08,qqq:-0.01,es:0.05,nq:0.03,spy_label:'SPY 24H',spy_ext:true,regime:'Caution'};
const slow={fast_live:false,session:'overnight',spy:0.31,qqq:1.40,es:2.40,nq:3.48,spy_label:'SPY CLOSE',spy_ext:false,regime:'Caution',vix:19.9};
const a=_keepLivePulse(live), b=_keepLivePulse(slow);
const c=_keepLivePulse(Object.assign({},slow,{session:'pre'}));
console.log(JSON.stringify({a:a.spy,b:[b.spy,b.es,b.spy_label,b.vix],c:c.spy}));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_page_keeps_the_live_reading_over_a_slow_poll(tmp_path):
    h = tmp_path / "h.js"
    h.write_text(HARNESS)
    out = json.loads(subprocess.run(["node", str(h)], capture_output=True, text=True, check=True, cwd=os.getcwd()).stdout)
    assert out["a"] == 0.08
    assert out["b"] == [0.08, 0.05, "SPY 24H", 19.9]     # slow numbers ignored, other fields still update
    assert out["c"] == 0.31                               # a new session takes the new reading


@pytest.fixture
def lq(tmp_path, monkeypatch):
    monkeypatch.setattr(me, "_LQ_SHARED_PATH", str(tmp_path / "lq.json"))
    saved = dict(me._LQ_CACHE)
    me._LQ_CACHE.clear()
    me._LQ_SHARE_STATE["read_at"] = 0.0
    yield
    me._LQ_CACHE.clear()
    me._LQ_CACHE.update(saved)


def test_live_quotes_shared_across_workers_newest_print_wins(lq):
    now = time.time()
    me._LQ_CACHE["SPY"] = {"at": now, "quote": {"price": 752.9, "epoch": 2000, "source": "Schwab real-time"}}
    me._lq_share_write()
    # The other worker only has an older Yahoo print.
    me._LQ_CACHE.clear()
    me._LQ_CACHE["SPY"] = {"at": now, "quote": {"price": 752.1, "epoch": 1500, "source": "Yahoo"}}
    me._lq_share_read(now + 2)
    assert me._LQ_CACHE["SPY"]["quote"]["price"] == 752.9
    # And it never writes its older print over the newer shared one.
    me._LQ_CACHE["SPY"] = {"at": now, "quote": {"price": 752.1, "epoch": 1500}}
    me._lq_share_write()
    assert json.load(open(me._LQ_SHARED_PATH))["SPY"]["quote"]["price"] == 752.9


def test_refresh_never_steps_back_to_an_older_print(lq, monkeypatch):
    now = time.time()
    me._LQ_CACHE["SPY"] = {"at": now, "quote": {"price": 752.9, "epoch": 2000}}
    me._LQ_WANTED["SPY"] = now
    monkeypatch.setattr(me, "_schwab_live", lambda syms: {"SPY": {"price": 751.0, "epoch": 1000}})
    me._refresh_live_quotes()
    assert me._LQ_CACHE["SPY"]["quote"]["price"] == 752.9
    monkeypatch.setattr(me, "_schwab_live", lambda syms: {"SPY": {"price": 753.2, "epoch": 2100}})
    me._refresh_live_quotes()
    assert me._LQ_CACHE["SPY"]["quote"]["price"] == 753.2


def test_terminal_ignores_older_prints():
    src = open("templates/terminal.html", encoding="utf-8").read()
    assert "if(twLastEpoch[t] && ep < twLastEpoch[t]) return;" in src
