from wealth_advanced_financials import derive_metrics
from wealth_intelligence_3 import assess


def fact(value, start="2025-01-01", end="2025-12-31"):
    return {"status": "reported", "value": value, "period_start": start,
            "period_end": end, "form": "10-K", "freshness": {"status": "recent"}}


def test_debt_components_are_not_total_debt():
    m = {"long_term_debt": fact(50), "long_term_debt_current": fact(20),
         "short_term_borrowings": fact(10)}
    result = derive_metrics({"metrics": m})["identified_debt_components"]
    assert result["value"] == 80
    assert "Not total debt" in result["warning"]


def test_roic_proxy_period_matched():
    m = {"operating_income": fact(30), "income_tax_expense": fact(5),
         "pretax_income": fact(25), "stockholders_equity": fact(100),
         "cash": fact(20), "long_term_debt": fact(40),
         "long_term_debt_current": fact(10), "short_term_borrowings": fact(0)}
    result = derive_metrics({"metrics": m})["roic_proxy_pct"]
    assert result["status"] == "calculated"
    assert result["value"] == 18.4615


def test_roic_missing_inputs_fails_closed():
    assert derive_metrics({"metrics": {"operating_income": fact(20)}})["roic_proxy_pct"]["status"] == "unavailable"


def test_intelligence_blocks_incomplete_evidence():
    result = assess({"ok": True, "metrics": {}, "derived_metrics": {}})
    assert not result["eligible"]
    assert result["integrity"] == "partial"
    assert not result["mcff"]["eligible"]
    assert result["scoring_eligible"] is False


def test_intelligence_never_emits_buy_signal():
    result = assess({"ok": True, "metrics": {"revenue": fact(10)}, "derived_metrics": {}})
    assert "buy" not in result
    assert result["scoring_eligible"] is False
