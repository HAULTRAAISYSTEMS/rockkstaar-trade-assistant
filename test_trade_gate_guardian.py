"""Pre-Trade Gate, Position Guardian and the Terminal size tool."""
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-gate")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import position_guardian as pg
import trade_gate as tg

GOOD = {"price": 100.0, "ema20": 97.0, "ema50": 92.0, "vwap": 99.0, "rel_volume": 2.1, "atr": 2.5,
        "earnings_days": 30, "earnings_date": "2026-12-09", "vix": 15.0, "spy_change_pct": 0.4}
PLAN = {"entry": 100.0, "stop": 96.0, "target": 110.0, "account_size": 50000, "risk_pct": 1}


def by(result, key):
    return next(c for c in result["checks"] if c["key"] == key)


# ── Gate rules ───────────────────────────────────────────────────────────────

def test_clean_long_is_go_with_size():
    r = tg.evaluate(GOOD, PLAN)
    assert r["verdict"] == "GO" and r["counts"]["fail"] == 0
    # $500 risk / $4 per share = 125 shares
    assert r["plan"]["shares"] == 125 and r["plan"]["position_value"] == 12500
    assert r["plan"]["reward_risk"] == 2.5
    assert r["score"] >= 90


def test_no_stop_is_always_no():
    r = tg.evaluate(GOOD, dict(PLAN, stop=None))
    assert r["verdict"] == "NO" and by(r, "stop")["status"] == "fail"


def test_earnings_within_three_days_is_no():
    r = tg.evaluate(dict(GOOD, earnings_days=2, earnings_date="2026-11-11"), PLAN)
    assert r["verdict"] == "NO" and "binary" in by(r, "earnings")["detail"]


def test_below_ema50_long_fails_trend():
    r = tg.evaluate(dict(GOOD, price=90.0, vwap=89.0), dict(PLAN, entry=90, stop=87, target=97))
    assert by(r, "trend")["status"] == "fail" and r["verdict"] == "NO"


def test_poor_reward_risk_and_wrong_side_stop():
    assert by(tg.evaluate(GOOD, dict(PLAN, target=103)), "rr")["status"] == "fail"
    r = tg.evaluate(GOOD, dict(PLAN, stop=101))
    assert by(r, "stop")["status"] == "fail" and r["verdict"] == "NO"


def test_two_warnings_is_wait():
    ind = dict(GOOD, rel_volume=1.0, vix=24.0)
    r = tg.evaluate(ind, PLAN)
    assert r["counts"]["fail"] == 0 and r["counts"]["warn"] >= 2 and r["verdict"] == "WAIT"


def test_short_mirrors_the_rules():
    ind = dict(GOOD, price=100.0, ema20=104.0, ema50=108.0, vwap=101.0)
    r = tg.evaluate(ind, {"entry": 100, "stop": 104, "target": 90, "account_size": 50000, "direction": "short"})
    assert by(r, "trend")["status"] == "pass" and by(r, "vwap")["status"] == "pass"
    assert r["plan"]["shares"] == 125 and r["direction"] == "short"


def test_position_cap_and_unaffordable():
    r = tg.evaluate(GOOD, dict(PLAN, stop=99.5, target=101.5))        # 1000 shares = $100k
    assert by(r, "size")["status"] == "fail"
    r = tg.evaluate(GOOD, dict(PLAN, stop=98.0, target=104))          # 250 sh = $25k = 50%
    assert by(r, "size")["status"] == "warn" and "Cap at 125 shares" in by(r, "size")["detail"]


def test_stop_inside_noise_warns():
    r = tg.evaluate(GOOD, dict(PLAN, stop=99.0, target=103))
    assert by(r, "stop")["status"] == "warn" and "ATR" in by(r, "stop")["detail"]


def test_high_vix_market_fails():
    assert by(tg.evaluate(dict(GOOD, vix=32), PLAN), "market")["status"] == "fail"


def test_indicator_math():
    assert tg.ema([1.0] * 30, 20) == pytest.approx(1.0)
    assert tg.ema([1.0] * 5, 20) is None
    bars = [{"high": 11, "low": 9, "close": 10, "volume": 100}] * 20
    assert tg.atr(bars, 14) == pytest.approx(2.0)
    assert tg.session_vwap([{"high": 11, "low": 9, "close": 10, "volume": 100},
                            {"high": 21, "low": 19, "close": 20, "volume": 300}]) == pytest.approx(17.5)
    assert tg.days_until("2026-11-12", today=datetime(2026, 11, 9).date()) == 3


def test_gather_computes_from_bars_when_no_snapshot(monkeypatch):
    tg._cache.clear()
    daily = [{"time": i, "open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100 + i, "volume": 1000} for i in range(60)]
    daily[-1]["volume"] = 3000
    intraday = [{"time": 1, "open": 159, "high": 160, "low": 158, "close": 159, "volume": 10}]
    monkeypatch.setattr(tg, "_bars", lambda t, i, r: daily if i == "1d" else intraday)
    with patch("database.get_stock_data", return_value=None):
        ind = tg.gather("ZZZ")
    assert ind["ema20"] and ind["ema50"] and ind["atr"] == pytest.approx(2.0)
    assert ind["rel_volume"] == 3.0 and ind["vwap"] == pytest.approx(159.0)
    assert ind["price"] == 159


# ── Guardian ─────────────────────────────────────────────────────────────────

POS = {"symbol": "NVDA", "quantity": 100, "avg_price": 150, "last_price": 170, "day_pnl_pct": -1.0,
       "unrealized": 2000, "unrealized_pct": 13.3}


def codes(row):
    return [s["code"] for s in row["signals"]]


def test_stop_hit_is_danger():
    row = pg.evaluate_position(POS, {"price": 159.5}, {"stop": 160})
    assert row["status"] == "danger" and codes(row) == ["STOP_HIT"]


def test_near_stop_vwap_loss_big_drop_earnings_news():
    now = datetime(2026, 11, 9, 18, 0, tzinfo=timezone.utc)
    news = [{"headline": "NVDA guidance cut", "published_at": (now - timedelta(minutes=20)).isoformat()}]
    row = pg.evaluate_position(dict(POS, day_pnl_pct=-5.0),
                               {"price": 161.0, "vwap": 165.0, "rel_volume": 2.0, "earnings_days": 2, "earnings_date": "2026-11-11"},
                               {"stop": 160}, news, now=now)
    assert set(codes(row)) == {"NEAR_STOP", "VWAP_LOSS", "BIG_DROP", "EARNINGS", "NEWS"}
    assert row["status"] == "watch"


def test_old_news_and_light_volume_vwap_are_quiet():
    now = datetime(2026, 11, 9, 18, 0, tzinfo=timezone.utc)
    news = [{"headline": "old", "published_at": (now - timedelta(hours=3)).isoformat()}]
    row = pg.evaluate_position(POS, {"price": 170, "vwap": 175, "rel_volume": 0.9}, {"stop": 150}, news, now=now)
    assert codes(row) == [] and row["status"] == "ok"


def test_no_stop_is_flagged_and_short_logic():
    assert codes(pg.evaluate_position(POS, {"price": 170}, None)) == ["NO_STOP"]
    short = dict(POS, quantity=-50)
    assert codes(pg.evaluate_position(short, {"price": 181}, {"stop": 180})) == ["STOP_HIT"]


def test_alerts_fire_once_per_symbol_per_day(monkeypatch):
    store = {}
    monkeypatch.setattr("database.get_setting", lambda k: store.get(k))
    monkeypatch.setattr("database.set_setting", lambda k, v: store.__setitem__(k, v))
    scan = {"positions": [pg.evaluate_position(POS, {"price": 159}, {"stop": 160})]}
    monkeypatch.setattr(pg, "scan", lambda uid: scan)
    sent, recorded = [], []
    rec = lambda sym, sig, text: recorded.append(text)
    first = pg.scan_and_alert(1, notify=sent.append, record=rec, today="2026-11-09")
    again = pg.scan_and_alert(1, notify=sent.append, record=rec, today="2026-11-09")
    assert len(first) == 1 and again == [] and len(sent) == 1 and "NVDA" in sent[0]
    pg.scan_and_alert(1, notify=sent.append, record=rec, today="2026-11-10")
    assert len(sent) == 2


def test_stops_round_trip(monkeypatch):
    store = {}
    monkeypatch.setattr("database.get_setting", lambda k: store.get(k))
    monkeypatch.setattr("database.set_setting", lambda k, v: store.__setitem__(k, v))
    pg.set_stop(1, "nvda", 160, 190)
    assert pg.get_stops(1) == {"NVDA": {"stop": 160.0, "target": 190.0}}
    pg.set_stop(1, "NVDA", None)
    assert pg.get_stops(1) == {}


# ── Routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    import web_app
    app = web_app.app
    old = app.config.get("WTF_CSRF_ENABLED", True)
    app.config["WTF_CSRF_ENABLED"] = False
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    yield c
    app.config["WTF_CSRF_ENABLED"] = old


def test_gate_page_and_evaluate_route(client, monkeypatch):
    monkeypatch.setattr(tg, "gather", lambda t: dict(GOOD, ticker=t))
    html = client.get("/trade-gate?ticker=nvda&entry=100").get_data(as_text=True)
    assert "Pre-Trade Gate" in html and 'value="NVDA"' in html
    d = client.post("/api/gate/evaluate", json=dict(PLAN, ticker="nvda")).get_json()
    assert d["ok"] and d["verdict"] == "GO" and d["ticker"] == "NVDA" and d["plan"]["shares"] == 125
    assert client.post("/api/gate/evaluate", json={"ticker": "!!"}).status_code == 400


def test_guardian_routes(client, monkeypatch):
    monkeypatch.setattr(pg, "scan", lambda uid: {"ok": True, "error": None, "positions": [], "counts": {}, "as_of": "x"})
    assert "Position Guardian" in client.get("/guardian").get_data(as_text=True)
    assert client.get("/api/guardian").get_json()["ok"]
    with patch.object(pg, "set_stop", return_value={"NVDA": {"stop": 1.0}}) as setter:
        assert client.post("/api/guardian/stop", json={"symbol": "NVDA", "stop": "160"}).get_json()["ok"]
    setter.assert_called_once_with(1, "NVDA", 160.0, None)


def test_terminal_has_size_tool_and_nav_links():
    term = open("templates/terminal.html", encoding="utf-8").read()
    assert 'id="tw-size-btn"' in term and "function twSizeClick" in term
    assert "if(twChartState.sizeMode&&param&&param.point)" in term
    nav = open("templates/_elite_navigation.html", encoding="utf-8").read()
    assert nav.count("trade_tools.gate_page") == 2 and nav.count("trade_tools.guardian_page") == 2
