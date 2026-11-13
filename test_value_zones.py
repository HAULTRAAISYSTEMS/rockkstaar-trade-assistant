"""Value zones: what price to buy at, and when.

Covers the Thesis model additions (zones, implied growth, earnings basis,
own-history multiple, trend check, signal, buy plan), the zones API, the
chart/watchlist hooks and the daily scan.
"""
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import thesis as th


def _data(price=156.93, ni=2.5e9, fcf=None, vh=None):
    return {"valuation": {"price": price, "pe": 52.0, "pfcf": 45.0},
            "history": [{"revenue_num": 8.2e9 * (0.86 ** i), "net_income_num": ni if i == 0 else -5e8,
                         "fcf_num": (fcf if fcf is not None else ni * 1.1)} for i in range(4)],
            "valuation_history": vh or {}, "sections": []}


@pytest.fixture(autouse=True)
def _shares(monkeypatch):
    monkeypatch.setattr(th, "_shares_outstanding", lambda t, p: 860e6)


def test_zone_boundaries_use_margin_of_safety():
    assert th.zone_for(156.93, 73.89, 104.04) == "pricey"
    assert th.zone_for(90, 73.89, 104.04) == "fair"
    assert th.zone_for(60, 73.89, 104.04) == "accumulate"
    assert th.zone_for(73.89 * 0.75, 73.89, 104.04) == "load"
    z = th.build_zones(156.93, 73.89, 104.04)
    assert [r["key"] for r in z["rows"]] == ["pricey", "fair", "accumulate", "load"]
    assert z["rows"][0]["current"] and round(z["load_top"], 2) == round(73.89 * 0.75, 2)
    assert z["to_buy_zone"]["pct_fmt"] == "-53%"
    assert th.build_zones(60, 73.89, 104.04)["to_buy_zone"] is None


def test_implied_growth_reproduces_todays_price():
    power, shares = 2.5e9, 860e6
    g = th.implied_growth(156.93, power, shares)
    assert power * (1 + g) * th._base_multiple(g) / shares == pytest.approx(156.93, rel=1e-6)
    t = th.build_targets(_data(), "MRVL")
    assert th.implied_growth(t["base"], t["power"], t["shares"]) == pytest.approx(t["growth"], abs=1e-6)


def test_buy_plan_is_25_25_50_through_the_buy_zones():
    plan = th.buy_plan(80.0)
    assert [p["weight"] for p in plan] == [0.25, 0.25, 0.5]
    assert [round(p["price"], 2) for p in plan] == [80.0, 70.0, 60.0]


def test_basis_auto_switches_to_cash_when_profit_is_held_down():
    # Reported profit far under free cash flow (acquisition amortization):
    # value it on cash earned unless the user picks reported profit.
    d = _data(ni=1.0e9, fcf=2.0e9)
    auto = th.build_targets(d, "MRVL")
    assert auto["basis_key"] == "fcf" and auto["basis"] == "free cash flow"
    earn = th.build_targets(d, "MRVL", basis="earnings")
    assert earn["basis_key"] == "earnings" and earn["base"] < auto["base"]
    assert set(auto["basis_options"]) == {"earnings", "fcf"}
    close = th.build_targets(_data(ni=2.0e9, fcf=2.2e9), "X")
    assert close["basis_key"] == "earnings"


def _bars(n=1260, start=datetime(2021, 11, 15, tzinfo=timezone.utc), last=None, trend=0.0):
    ts, cl = [], []
    for i in range(n):
        ts.append(int((start + timedelta(days=i * 1.45)).timestamp()))
        cl.append(100 + 20 * math.sin(i / 90) + trend * i)
    if last:
        cl[-1] = last
    return {"timestamps": ts, "closes": cl, "lows": [c - 1 for c in cl], "highs": [c + 1 for c in cl]}


def test_multiple_history_ranks_today_against_daily_pe():
    vh = {"rows": [{"period_end": "2022-01-29", "price": 100, "pe": 25},
                   {"period_end": "2024-02-03", "price": 100, "pe": 20},
                   {"is_today": True, "price": 150, "pe": 52}]}
    h = th.multiple_history(_data(vh=vh), _bars())
    assert h["available"] and h["label"] == "P/E"
    assert h["percentile"] == 1.0 and "Pricier than" in h["read"]
    assert h["price_at_median"] < 156.93 and len(h["spark"]) <= 130
    assert th.multiple_history(_data(vh=vh), None) == {"available": False}
    assert th.multiple_history(_data(vh={}), _bars()) == {"available": False}


def test_trend_check_and_signal_combine_value_and_timing():
    up = th.trend_check({"closes": [50 + i * 0.5 for i in range(80)], "lows": [49 + i * 0.5 for i in range(80)]})
    down = th.trend_check({"closes": [90 - i * 0.5 for i in range(80)], "lows": [89 - i * 0.5 for i in range(80)]})
    assert up["confirmed"] and up["score"] >= 3
    assert not down["confirmed"] and down["score"] <= 1
    assert th.trend_check({"closes": [1, 2, 3]})["available"] is False
    assert th.load_signal("load", up)["key"] == "load"
    assert th.load_signal("load", down)["key"] == "load-wait"
    assert th.load_signal("accumulate", up)["key"] == "buy"
    assert th.load_signal("accumulate", down)["key"] == "accum-wait"
    assert th.load_signal("pricey", up)["key"] == "wait"


def test_build_thesis_value_block_and_zone_summary():
    out = th.build_thesis(_data(), "MRVL", bars=_bars(last=156.93))
    v = out["value"]
    assert v["available"] and v["zones"]["current"] == "pricey"
    assert v["implied_growth"] > v["model"]["growth"] and v["growth_gap"] > 0.1
    assert v["signal"]["key"] == "wait" and len(v["plan"]) == 3
    s = th.zone_summary(_data(), "MRVL", _bars())
    assert s["available"] and s["zone"] == "pricey" and s["load_top"] < s["accum_top"] < s["bull"]
    assert th.build_thesis({}, "X")["value"] == {"available": False}


def _client():
    pytest.importorskip("flask")
    import web_app  # noqa: F401
    import app as legacy
    legacy.app.config["TESTING"] = True
    return legacy, legacy.app.test_client()


def test_zones_api_builds_in_background_then_serves_cache(monkeypatch):
    import time
    legacy, client = _client()
    monkeypatch.setattr(legacy, "_auth_required", lambda: False)
    legacy._VALUE_ZONES.clear()
    calls = []

    def fake_build(t):
        calls.append(t)
        out = {"available": True, "ticker": t, "zone": "accumulate", "zone_label": "Accumulate",
               "price": 60.0, "accum_top": 74.0, "load_top": 55.0, "bull": 104.0}
        legacy._VALUE_ZONES[t] = (time.time(), out)
        return out
    monkeypatch.setattr(legacy, "build_value_zones", fake_build)
    d = client.get("/api/thesis/zones?tickers=mrvl,NVDA,bad$").get_json()
    assert d["ok"] and set(d["pending"]) == {"MRVL", "NVDA"}
    for _ in range(50):
        d = client.get("/api/thesis/zones?tickers=MRVL,NVDA").get_json()
        if not d["pending"]:
            break
        time.sleep(0.05)
    assert d["zones"]["MRVL"]["zone"] == "accumulate" and sorted(calls) == ["MRVL", "NVDA"]


def test_thesis_page_renders_ladder_timing_plan_and_whatif(monkeypatch):
    from unittest.mock import patch
    legacy, client = _client()
    vh = {"rows": [{"period_end": "2022-01-29", "price": 100, "pe": 25}]}
    with (patch.object(legacy, "_auth_required", return_value=False),
          patch.object(legacy, "get_user_tracked_tickers", return_value=[]),
          patch("fundamentals_engine.get_fundamentals", return_value=dict(_data(vh=vh), company_name="Marvell")),
          patch.object(legacy, "_thesis_daily_bars", return_value=_bars())):
        html = client.get("/thesis?ticker=MRVL").get_data(as_text=True)
        cash = client.get("/thesis?ticker=MRVL&basis=fcf").get_data(as_text=True)
    for s in ("Too pricey — wait", "What price do I buy at?", "Load the boat", "data-alert-price=",
              "What today's price assumes", "Vs. its own past", "Is the chart turning up?",
              "Scale in, don't guess the bottom", "Move the assumptions", 'id="vz-model"', "js/thesis.js"):
        assert s in html, s
    assert 'class="is-on" href="/thesis?ticker=MRVL&amp;basis=fcf"' in cash


def test_chart_and_watchlist_hooks():
    js = open("static/js/chart_tools.js", encoding="utf-8").read()
    tpl = open("templates/terminal.html", encoding="utf-8").read()
    wl = open("templates/watchlists.html", encoding="utf-8").read()
    assert "function ctDrawValueZones(ctx, dim)" in js and "try { ctDrawValueZones(ctx, dim); }" in js
    assert "events: true, zones: true}" in js
    assert 'data-ct-set="view.zones"' in tpl and "function twLoadValueZones(ticker, tries)" in tpl
    assert 'id="tw-zone-chip"' in tpl and 'data-zone-badge="{{ s.ticker }}"' in tpl
    assert wl.count('data-zone-badge="{{ t }}"') == 2 and "js/value_badges.js" in wl and "js/value_badges.js" in tpl
    tjs = open("static/js/thesis.js", encoding="utf-8").read()
    assert "/api/alerts/price" in tjs and "fairMultiple" in tjs and "tradestaar.thesis.planSize" in tjs


def test_daily_scan_sends_one_digest_on_entry(monkeypatch):
    import value_zone_scan as vzs
    store = {}
    monkeypatch.setattr(vzs, "get_user_setting", lambda u, k, d=None: store.get((u, k), d))
    monkeypatch.setattr(vzs, "set_user_setting", lambda u, k, v: store.__setitem__((u, k), v))
    monkeypatch.setattr(vzs, "_watch_by_user", lambda conn: {7: {"AMD", "MRVL", "NVDA"}})

    class _C:
        def close(self): pass
    monkeypatch.setattr(vzs, "get_db", lambda: _C())
    zones = {
        "AMD": {"available": True, "ticker": "AMD", "zone": "load", "zone_label": "Load the boat", "price": 50.0,
                "accum_top": 80.0, "load_top": 60.0, "signal": {"key": "load", "title": "Load the boat"}},
        "MRVL": {"available": True, "ticker": "MRVL", "zone": "pricey", "zone_label": "Pricey", "price": 156.0,
                 "accum_top": 74.0, "load_top": 55.0, "signal": {"key": "wait"}},
        "NVDA": {"available": False}}
    sent = []
    after_close = datetime(2026, 11, 13, 16, 30, tzinfo=ZoneInfo("America/New_York"))
    r = vzs.run(after_close, zone_fn=lambda t: zones[t], send_fn=lambda u, title, body: sent.append((u, title, body)))
    assert r["digests"] == 1 and sent[0][0] == 7 and "1 name in a buy zone" in sent[0][1]
    assert "⚓ AMD → Load the boat" in sent[0][2] and "MRVL" not in sent[0][2]
    # Once a day, and not again for a name already in its zone.
    assert vzs.run(after_close, zone_fn=lambda t: zones[t], send_fn=lambda *a: sent.append(a))["skipped"] == "done today"
    assert vzs.run(after_close.replace(hour=10), zone_fn=lambda t: zones[t])["skipped"] == "not due"
    assert vzs.changes("load|load", zones["AMD"]) is None
    assert vzs.changes("fair|hold", zones["AMD"]) == "entered"
    assert vzs.changes("load|load-wait", zones["AMD"]) == "signal"
    assert vzs.changes("accumulate|", dict(zones["AMD"], zone="accumulate", signal={})) is None
