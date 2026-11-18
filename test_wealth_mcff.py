"""M.C.F.F. gates, Next Dollar ranking, Seasonal Planner."""
from datetime import date

import pytest

import wealth_mcff as mc
import wealth_nextdollar as nd
import wealth_planner as wp

FUND = {"ticker": "KLAC", "company_name": "KLA Corp", "sector": "Technology", "score_percent": 82.0,
        "scored_metrics": 18, "total_metrics": 20, "verdict": "Strong", "red_flags": [],
        "fiscal_period_ends": ["2026-06-30"],
        "sections": [{"name": "Profitability", "rows": [{"label": "Operating margin > 20%", "value": "41%", "passed": True},
                                                        {"label": "Not scored", "value": "N/A", "passed": None}]}],
        "valuation": {"pe": 30.0},
        "history": [{"year_offset": 0, "revenue_num": 12.0, "net_income_num": 4.0, "fcf_num": 4.0},
                    {"year_offset": 4, "revenue_num": 6.0, "net_income_num": 2.0, "fcf_num": 2.0}]}


def research(score=8, buy_high=None, **kw):
    g = {"score": score, "origin": "investor", "evidence": "x"}
    return {"moat": dict(g), "ceo": dict(g), "future": dict(g), "buy_high": buy_high, **kw}


def test_financials_gate_is_verified_and_scored_from_the_scorecard():
    f = mc.financials_gate(FUND, fetched_at="2026-11-18T10:00:00")
    assert f["origin"] == "verified" and f["score"] == 8.2 and f["passed"]
    assert f["confidence"] == "high" and f["verified_on"] == "2026-11-18"
    assert [e["label"] for e in f["evidence"]] == ["Operating margin > 20%"]      # unscored rows dropped
    flagged = mc.financials_gate(dict(FUND, red_flags=["Going-concern language"]))
    assert flagged["passed"] is False and flagged["risks"] == ["Going-concern language"]
    assert mc.financials_gate(None)["passed"] is None


def test_buy_zone_needs_all_gates_and_the_price_inside_the_zone():
    fin = mc.financials_gate(FUND)
    assert mc.evaluate(research(buy_high=150), fin, price=140, value_zone=None)["status"] == "BUY ZONE"
    hold = mc.evaluate(research(buy_high=150), fin, price=160, value_zone=None)
    assert hold["status"] == "HOLD" and "do not chase" in hold["reasons"][0]
    fail = mc.evaluate(research(score=5, buy_high=150), fin, price=100, value_zone=None)
    assert fail["status"] == "DO NOT BUY" and "Moat" in fail["reasons"][0]


def test_a_low_price_never_overrides_a_failed_or_missing_gate():
    fin = mc.financials_gate(FUND)
    r = research(buy_high=150)
    r["ceo"] = {}
    ev = mc.evaluate(r, fin, price=10, value_zone={"zone": "load"})
    assert ev["status"] == "DO NOT BUY" and "Research incomplete" in ev["reasons"][0]


def test_ai_draft_scores_do_not_count():
    fin = mc.financials_gate(FUND)
    r = research(buy_high=150)
    r["moat"]["origin"] = "ai_draft"
    ev = mc.evaluate(r, fin, price=140, value_zone=None)
    assert ev["gates"]["moat"]["counted"] is False and ev["status"] == "DO NOT BUY"


def test_value_zone_is_used_only_without_an_investor_zone():
    fin = mc.financials_gate(FUND)
    assert mc.evaluate(research(), fin, price=140, value_zone={"zone": "accumulate", "accum_top": 150})["status"] == "BUY ZONE"
    assert mc.evaluate(research(), fin, price=140, value_zone={"zone": "pricey"})["status"] == "HOLD"
    # The investor's zone wins even when the app's zone is favourable.
    assert mc.evaluate(research(buy_high=120), fin, price=140, value_zone={"zone": "load"})["status"] == "HOLD"
    below = mc.evaluate(research(buy_high=150, buy_low=130), fin, price=120, value_zone=None)
    assert below["status"] == "BUY ZONE" and "re-check" in below["reasons"][1]


def test_scenarios_and_defaults():
    d = mc.default_assumptions(FUND, 30.0)
    assert d["growth"]["base"] == pytest.approx(2 ** 0.25 - 1, abs=1e-4)       # 6→12 over 4 years
    assert d["exit_pe"] == {"conservative": 22.5, "base": 30.0, "optimistic": 33.0}
    sc = {s["case"]: s for s in mc.scenarios(150.0, 30.0, {"years": 5, "growth": {"base": 0.10}, "exit_pe": {"base": 30.0}})}
    assert sc["base"]["annual_return_pct"] == pytest.approx(10.0)              # no multiple change → growth
    assert "growth" in sc["conservative"]["missing"]
    loss = dict(FUND, history=[{"year_offset": 0, "net_income_num": -1.0, "revenue_num": 5.0},
                               {"year_offset": 2, "net_income_num": 2.0, "revenue_num": 4.0}])
    assert mc.history_growth(loss)["net_income"]["cagr"] is None


def test_clean_research_clamps_and_strips():
    c = mc.clean_research({"moat": {"score": 14, "sources": ["javascript:alert(1)", {"label": "10-K", "url": "https://sec.gov/x"}]},
                           "buy_high": -5, "assumptions": {"years": 99}})
    assert c["moat"]["score"] == 10 and c["moat"]["sources"][0]["url"] is None
    assert c["moat"]["sources"][1]["url"] == "https://sec.gov/x" and c["buy_high"] is None
    assert c["assumptions"]["years"] == 20 and c["ceo"]["score"] is None


def cand(t, status, base, cons=None, sector="Technology", price=100.0):
    return {"ticker": t, "price": price, "sector": sector,
            "evaluation": {"status": status, "reasons": ["r"], "valuation": {"zone_text": "zone"}},
            "scenarios": [{"case": "base", "annual_return_pct": base, "years": 5, "growth": 0.1, "exit_pe": 20},
                          {"case": "conservative", "annual_return_pct": cons}, {"case": "optimistic", "annual_return_pct": None}]}


def test_next_dollar_ranks_eligible_by_base_return_then_cash():
    r = nd.rank(500, [cand("A", "BUY ZONE", 9, 4), cand("B", "BUY ZONE", 12, 2), cand("C", "HOLD", 30)],
                total_value=10000, holdings={"A": 1000}, sectors={"Technology": 3000})
    assert [x["ticker"] for x in r["rows"]] == ["B", "A", "CASH", "C"]
    assert r["rows"][0]["shares"] == 5 and r["rows"][1]["weight_after_pct"] == pytest.approx(15)
    assert "B ranks first" in r["verdict"]


def test_next_dollar_holds_cash_when_nothing_qualifies():
    r = nd.rank(500, [cand("A", "HOLD", 20), cand("B", "BUY ZONE", 3)], total_value=10000, holdings={}, sectors={},
                hurdle_pct=8)
    assert r["rows"][0]["ticker"] == "CASH" and r["eligible_count"] == 0
    assert any("hurdle" in b for b in r["rows"][1]["blocks"] + r["rows"][2]["blocks"])


def test_next_dollar_respects_position_limit():
    r = nd.rank(1000, [cand("A", "BUY ZONE", 15)], total_value=10000, holdings={"A": 1800}, sectors={},
                max_position_pct=25)
    assert r["rows"][0]["ticker"] == "CASH" and "over your 25% limit" in r["rows"][1]["blocks"][0]


def test_planner_schedule_ira_limits_and_tax_years():
    pf = [{"id": 1, "name": "Roth IRA", "kind": "roth_ira"}, {"id": 2, "name": "Aggressive", "kind": "taxable"}]
    plan = wp.clean_plan({"entries": [
        {"portfolio_id": 1, "kind": "monthly", "months": [1, 2, 3], "amount": 2000},
        {"portfolio_id": 1, "kind": "one_time", "months": [3, 4], "amount": 1500, "label": "refund"},
        {"portfolio_id": 2, "kind": "monthly", "months": [4, 5], "amount": 500},
        {"portfolio_id": 9, "months": [1], "amount": 100}], "goals": {"1": 7500}}, 2027, {1, 2})
    assert len(plan["entries"]) == 3 and plan["entries"][1]["months"] == [3]
    actual = [{"portfolio_id": 1, "date": "2027-01-10", "amount": 2000, "tax_year": None},
              {"portfolio_id": 1, "date": "2027-02-10", "amount": 2000, "tax_year": 2026}]
    v = wp.schedule(plan, 2027, pf, actual, today=date(2027, 3, 5))
    roth = v["rows"][0]
    assert roth["planned"][:4] == [2000, 2000, 3500, 0] and roth["planned_total"] == 7500
    assert roth["due_to_date"] == 7500 and roth["behind_by"] == 3500 and roth["goal_pct"] == pytest.approx(4000 / 7500 * 100)
    ira = {r["tax_year"]: r for r in v["ira"]}
    assert ira[2027]["planned"] == 7500 and ira[2027]["actual"] == 2000 and ira[2027]["limit"]["limit"] == 7500
    assert ira[2026]["actual"] == 2000 and not ira[2027]["warnings"]
    assert v["seasons"]["Winter"] == 7500 and v["seasons"]["Spring"] == 1000 and v["prior_year_window_open"]


def test_planner_warns_over_the_limit_and_for_unknown_years():
    pf = [{"id": 1, "name": "Roth", "kind": "roth_ira"}]
    plan = wp.clean_plan({"entries": [{"portfolio_id": 1, "months": list(range(1, 13)), "amount": 700}]}, 2027, {1})
    v = wp.schedule(plan, 2027, pf, [])
    assert "over the $7,500 IRA limit" in v["ira"][-1]["warnings"][0]
    assert wp.ira_limit(2027, True)["limit"] == 8600 and wp.ira_limit(2031) is None
    assert wp.prior_year_window(date(2027, 4, 15)) and not wp.prior_year_window(date(2027, 4, 16))
