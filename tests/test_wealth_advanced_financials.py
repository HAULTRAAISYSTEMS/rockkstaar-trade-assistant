from wealth_advanced_financials import derive_metrics


def fact(value, start="2025-01-01", end="2025-12-31", form="10-K"):
    return {"status": "reported", "value": value, "period_start": start,
            "period_end": end, "form": form}


def test_free_cash_flow_and_margins_same_period():
    data = {"metrics": {"operating_cash_flow": fact(100), "capital_expenditures": fact(30),
                        "gross_profit": fact(60), "revenue": fact(200),
                        "operating_income": fact(40), "net_income": fact(20)}}
    result = derive_metrics(data)
    assert result["free_cash_flow"]["value"] == 70
    assert result["gross_margin_pct"]["value"] == 30
    assert result["operating_margin_pct"]["value"] == 20
    assert result["net_margin_pct"]["value"] == 10


def test_period_mismatch_blocks_derived_metric():
    data = {"metrics": {"operating_cash_flow": fact(100),
                        "capital_expenditures": fact(30, start="2025-10-01")}}
    assert derive_metrics(data)["free_cash_flow"]["status"] == "unavailable"


def test_zero_denominator_blocks_margin():
    data = {"metrics": {"gross_profit": fact(20), "revenue": fact(0)}}
    assert derive_metrics(data)["gross_margin_pct"]["value"] is None


def test_noncurrent_debt_ratio_is_not_total_debt():
    data = {"metrics": {"long_term_debt": fact(90), "stockholders_equity": fact(30)}}
    result = derive_metrics(data)["debt_to_equity"]
    assert result["value"] == 3
    assert "Noncurrent" in result["formula"]
