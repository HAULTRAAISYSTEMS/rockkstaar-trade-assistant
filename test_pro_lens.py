"""Pro lens: owner cash flow, the ten-year model and what you have to believe."""
import json
import subprocess

import pytest

import pro_lens as pl

CRWD = dict(fcf=1.52e9, sbc=1.10e9, shares=1.048e9, price=211.28, cash_ps=4.0, g=0.30)


def test_growth_path_fades_in_a_straight_line():
    p = pl.growth_path(0.30, 10, 0.03)
    assert p[0] == pytest.approx(0.30) and p[-1] == pytest.approx(0.03) and len(p) == 10
    assert all(a > b for a, b in zip(p, p[1:]))


def test_dcf_matches_a_hand_calculation():
    # Two years, flat growth: cash 100 -> 110 -> 121, terminal at 3%, r 10%.
    v = pl.dcf_value(100, 1, 0.10, r=0.10, years=2, gt=0.10 - 0.07)
    path = pl.growth_path(0.10, 2, 0.03)
    cf1 = 100 * (1 + path[0]); cf2 = cf1 * (1 + path[1])
    want = cf1 / 1.1 + cf2 / 1.1 ** 2 + cf2 * 1.03 / 0.07 / 1.1 ** 2
    assert v == pytest.approx(want)
    assert pl.dcf_value(-5, 1, 0.1) is None and pl.dcf_value(100, 1, 0.1, r=0.03) is None


def test_implied_growth_round_trips():
    c = CRWD
    g = pl.implied_start_growth(c["price"], c["fcf"], c["shares"], cash_ps=c["cash_ps"])
    assert pl.dcf_value(c["fcf"], c["shares"], g, cash_ps=c["cash_ps"]) == pytest.approx(c["price"], rel=1e-6)
    # Owner cash is smaller, so the price needs more growth from it.
    g_owner = pl.implied_start_growth(c["price"], c["fcf"] - c["sbc"], c["shares"], cash_ps=c["cash_ps"])
    assert g_owner is None or g_owner > g


def test_belief_tone():
    assert pl.belief_tone(0.20, 0.30) == ("load", "Below its record")
    assert pl.belief_tone(0.35, 0.30) == ("fair", "Demanding")
    assert pl.belief_tone(0.80, 0.30) == ("pricey", "Heroic")
    assert pl.belief_tone(None, 0.30)[1] == "Out of reach"


def test_build_pro_lens_crwd_shape():
    c = CRWD
    hist = [{"fcf": 1.3e9, "sbc": c["sbc"]}, {"fcf": 1.0e9, "sbc": 0.9e9}]
    out = pl.build_pro_lens(hist, {"fcf": c["fcf"]}, c["price"], c["shares"], c["cash_ps"], 0.30)
    assert out["available"] and out["fcf_is_ttm"] and out["sbc_heavy"]
    assert out["owner"] == pytest.approx(c["fcf"] - c["sbc"]) and out["default"] == "owner"
    assert [s["key"] for s in out["fcf_lens"]["scenarios"]] == ["bear", "base", "bull"]
    b = out["fcf_lens"]["scenarios"]
    assert b[0]["value"] < b[1]["value"] < b[2]["value"]
    assert out["model"]["owner"] == pytest.approx(c["fcf"] - c["sbc"])


def test_owner_cash_negative_falls_back_to_fcf():
    out = pl.build_pro_lens([{"fcf": 1e9, "sbc": 2e9}], {}, 50.0, 1e8, 0.0, 0.2)
    assert out["available"] and out["default"] == "fcf" and out["owner_lens"] is None
    assert out["model"]["owner"] is None


def test_no_cash_flow_is_unavailable():
    assert pl.build_pro_lens([{"fcf": -1e9, "sbc": None}], {}, 50.0, 1e8)["available"] is False


def test_js_mirrors_python():
    """Run the browser math in node and hold it to the Python numbers."""
    js = open("static/js/pro_lens.js", encoding="utf-8").read()
    start = js.index("  function path(")
    end = js.index("  function tone(")
    harness = js[start:end] + """
    var c = %s, out = [];
    [-0.05, 0.1, 0.3, 0.55].forEach(function (g) {
      [0.08, 0.1, 0.13].forEach(function (r) {
        [5, 10, 15].forEach(function (n) { out.push(dcf(c.fcf, c.shares, g, r, n, 0.03, c.cash_ps)); });
      });
    });
    out.push(implied(c.price, c.fcf, c.shares, 0.1, 10, 0.03, c.cash_ps));
    console.log(JSON.stringify(out));
    """ % json.dumps(CRWD)
    res = json.loads(subprocess.run(["node", "-e", harness], capture_output=True, text=True, check=True).stdout)
    want = []
    for g in (-0.05, 0.1, 0.3, 0.55):
        for r in (0.08, 0.1, 0.13):
            for n in (5, 10, 15):
                want.append(pl.dcf_value(CRWD["fcf"], CRWD["shares"], g, r, n, 0.03, CRWD["cash_ps"]))
    want.append(pl.implied_start_growth(CRWD["price"], CRWD["fcf"], CRWD["shares"], 0.1, 10, 0.03, CRWD["cash_ps"]))
    assert res == pytest.approx(want, rel=1e-9)


def test_thesis_page_shows_the_pro_lens(monkeypatch):
    from unittest.mock import patch
    import thesis as th
    import test_value_zones as tv
    monkeypatch.setattr(th, "_shares_outstanding", lambda t, p: 860e6)
    monkeypatch.setattr(th, "_ttm_values", lambda t: {})
    legacy, client = tv._client()
    data = tv._data()
    for i, row in enumerate(data["history"]):
        row["sbc_num"] = 0.6e9
    with (patch.object(legacy, "_auth_required", return_value=False),
          patch.object(legacy, "get_user_tracked_tickers", return_value=[]),
          patch("fundamentals_engine.get_fundamentals", return_value=dict(data, company_name="Marvell")),
          patch.object(legacy, "_thesis_daily_bars", return_value=tv._bars()),
          patch.object(legacy, "_thesis_live_price", return_value=None)):
        html = client.get("/thesis?ticker=MRVL").get_data(as_text=True)
    for s in ('id="pro-lens"', "Owner cash flow", "Ten-year cash-flow model", "What you have to believe",
              'id="pl-model"', "js/pro_lens.js", "Minus stock pay", 'id="pl-r"'):
        assert s in html, s
