"""A split after the last filing must not shrink past multiples, and a
near-zero profit must not turn into a 10,000% peer premium."""
import pytest

import thesis as th
from valuation_history import build_history, split_since_filing


@pytest.mark.parametrize("filed,now,want", [
    (250e6, 1048e6, 4.0),      # CRWD 4-for-1
    (100e6, 1005e6, 10.0),
    (300e6, 297e6, 1.0),       # buybacks
    (100e6, 130e6, 1.0),       # heavy issuance is not a split
    (100e6, 260e6, 1.0),       # 2.6x is no usual ratio
    (1000e6, 100e6, 0.1),      # 1-for-10 reverse split
    (None, 100e6, 1.0), (100e6, None, 1.0),
])
def test_split_since_filing(filed, now, want):
    assert split_since_filing(filed, now) == pytest.approx(want)


def _bars(prices):
    from datetime import datetime, timezone
    ts = [int(datetime(2026 - i, 1, 31, tzinfo=timezone.utc).timestamp()) for i in range(len(prices))]
    return {"timestamps": ts[::-1], "closes": prices[::-1]}


def test_history_is_restated_on_todays_share_basis():
    # Split-adjusted prices; filed FCF / shares on the old (pre 4:1) basis.
    bars = _bars([100.0, 80.0, 60.0])
    kw = dict(period_ends=["2026-01-31", "2025-01-31", "2024-01-31"],
              eps=[0.4, 0.3, 0.2], fcf=[1.0e9, 0.8e9, 0.6e9],
              shares=[250e6, 245e6, 240e6], bars=bars, today_price=211.0,
              today_pe=None, today_pfcf=140.0)
    raw = build_history(**kw)
    fixed = build_history(**kw, current_shares=1048e6)
    assert fixed["split_adjusted"] == 4.0 and raw["split_adjusted"] is None
    r0, f0 = raw["rows"][0], fixed["rows"][0]
    assert f0["pfcf"] == pytest.approx(r0["pfcf"] * 4)
    assert f0["pe"] == pytest.approx(r0["pe"] * 4)
    assert f0["pfcf"] == pytest.approx(100.0 / (1.0e9 / 1000e6))


def _m():
    return {"CRM": {"peTTM": 15.4, "pfcfShareTTM": 14.0, "psTTM": 5.0, "revenueGrowthTTMYoy": 9},
            "MSFT": {"peTTM": 25.2, "pfcfShareTTM": 35.0, "psTTM": 9.0, "revenueGrowthTTMYoy": 15},
            "NOW": {"peTTM": 62.9, "pfcfShareTTM": 50.0, "psTTM": 13.0, "revenueGrowthTTMYoy": 20},
            "PLTR": {"peTTM": 94.2, "pfcfShareTTM": 120.0, "psTTM": 60.0, "revenueGrowthTTMYoy": 40},
            "CRWD": {"peTTM": 3695.7, "pfcfShareTTM": 141.9, "psTTM": 38.0}}


def test_near_zero_profit_compares_on_cash_flow():
    m = _m()
    out = th.peer_comparison("CRWD", 211.28, 3695.7, 0.30, peers=["CRM", "MSFT", "NOW", "PLTR"],
                             metrics_fn=lambda t: m[t])
    assert out["available"] and out["metric"] == "pfcf" and out["metric_label"] == "P/FCF"
    assert out["median_pe"] == pytest.approx(42.5)
    assert out["today_pe_fmt"] == "141.9x" and "P/E to mean anything" in out["read"]
    assert out["price_at_peer"] == pytest.approx(211.28 * 42.5 / 141.9)


def test_no_pe_from_the_page_also_falls_back():
    m = _m()
    out = th.peer_comparison("CRWD", 211.28, None, 0.30, peers=["CRM", "MSFT", "NOW", "PLTR"],
                             metrics_fn=lambda t: m[t])
    assert out["metric"] == "pfcf"


def test_a_normal_pe_stays_on_pe():
    m = _m()
    out = th.peer_comparison("X", 100.0, 40.0, 0.2, peers=["CRM", "MSFT", "NOW", "PLTR"],
                             metrics_fn=lambda t: m[t])
    assert out["metric"] == "pe" and out["metric_label"] == "P/E"


def test_page_shows_the_metric_it_compared():
    js = open("static/js/thesis.js", encoding="utf-8").read()
    assert "'Vs. its peers · ' + lbl" in js and "M.pe > 0 ? M.pe : ''" in js
