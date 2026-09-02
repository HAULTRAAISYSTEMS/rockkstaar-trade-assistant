"""Foreign filers report under IFRS, and the element names differ.

TSM files a 20-F under ifrs-full. Its capital expenditure is tagged
PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities; the lookup
only listed AcquisitionOf..., which does not exist. One wrong name cost three
rows: no capex means no free cash flow, and no free cash flow means the
FCF-positive test, the earnings-quality test and the capex-to-revenue test all
read N/A — while operating cash flow sat right there on the same card, found
and correct, which is what made it look like a display bug rather than a
missing concept.
"""
from unittest.mock import patch

import pytest

import fundamentals_engine as fe
from test_edgar_pipeline import _Resp

YEARS = ["2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31", "2020-12-31"]
STARTS = ["2024-01-01", "2023-01-01", "2022-01-01", "2021-01-01", "2020-01-01"]
ACCN = "0001046179-25-000012"


def duration(values, unit="USD"):
    return {"units": {unit: [
        {"start": st, "end": e, "val": v, "form": "20-F", "filed": "2025-04-17",
         "accn": ACCN}
        for st, e, v in zip(STARTS, YEARS, values)]}}


def instants(values, unit="USD"):
    return {"units": {unit: [
        {"end": e, "val": v, "form": "20-F", "filed": "2025-04-17", "accn": ACCN}
        for e, v in zip(YEARS, values)]}}


def tsm_shaped(**over):
    ifrs = {
        "Revenue": duration([90e9, 69e9, 76e9, 57e9, 45e9]),
        "ProfitLoss": duration([36e9, 27e9, 34e9, 21e9, 17e9]),
        "GrossProfit": duration([50e9, 37e9, 45e9, 30e9, 24e9]),
        "ProfitLossFromOperatingActivities": duration([40e9, 30e9, 37e9, 24e9, 19e9]),
        "Assets": instants([208e9, 190e9, 178e9, 148e9, 114e9]),
        "Equity": instants([130e9, 111e9, 96e9, 71e9, 55e9]),
        "CurrentAssets": instants([90e9, 78e9, 71e9, 58e9, 44e9]),
        "CurrentLiabilities": instants([35e9, 30e9, 28e9, 24e9, 18e9]),
        "CashAndCashEquivalents": instants([73e9, 55e9, 44e9, 39e9, 23e9]),
        "CashFlowsFromUsedInOperatingActivities":
            duration([55.69e9, 40.56e9, 52.41e9, 40.09e9, 29.30e9]),
        # The name that was missing.
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities":
            duration([29.15e9, 30.45e9, 36.29e9, 30.04e9, 17.24e9]),
    }
    ifrs.update(over)
    return {"facts": {"ifrs-full": ifrs}}


def fetched(**over):
    with patch.object(fe, "_edgar_cik", return_value=("0001046179", "TSMC")), \
         patch.object(fe._req_module, "get", return_value=_Resp(tsm_shaped(**over))):
        return fe.fetch_fundamentals_edgar("TSM")


def row(card, key):
    return next(r for sec in card["sections"] for r in sec["rows"] if r["key"] == key)


def test_capital_expenditure_is_found_under_its_ifrs_name():
    raw = fetched()
    assert raw["capex"] and raw["capex"][0] == pytest.approx(29.15e9)


def test_free_cash_flow_can_then_be_computed():
    raw = fetched()
    assert raw["free_cash_flow"][0] == pytest.approx(55.69e9 - 29.15e9)


def test_the_three_rows_that_went_dark_all_resolve():
    """Operating cash flow was found and correct on the same card, which is
    why this read as a display problem rather than a missing concept."""
    card = fe.score_fundamentals(fetched())
    for key in ("fcf_positive", "fcf_vs_net_income", "capex_ratio"):
        r = row(card, key)
        assert r["passed"] is not None, f"{key} still unscored"
        assert r["value"] != "N/A", key


def test_operating_cash_flow_was_never_the_problem():
    card = fe.score_fundamentals(fetched())
    assert row(card, "ocf_trend")["passed"] is not None


def test_a_filer_that_genuinely_lacks_capex_is_still_unscored():
    """The fix must not invent a number where the filing has none."""
    facts = tsm_shaped()
    del facts["facts"]["ifrs-full"][
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"]
    with patch.object(fe, "_edgar_cik", return_value=("0001046179", "TSMC")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts)):
        card = fe.score_fundamentals(fe.fetch_fundamentals_edgar("TSM"))
    assert row(card, "capex_ratio")["passed"] is None


def test_currency_buckets_are_never_mixed():
    """TSM reports in both TWD and USD. Taking revenue in one and cash flow in
    the other would be wrong by a factor of about thirty."""
    facts = tsm_shaped()
    facts["facts"]["ifrs-full"]["Revenue"] = {"units": {
        "USD": duration([90e9, 69e9, 76e9, 57e9, 45e9])["units"]["USD"],
        "TWD": duration([2900e9, 2160e9, 2260e9, 1590e9, 1339e9])["units"]["USD"],
    }}
    with patch.object(fe, "_edgar_cik", return_value=("0001046179", "TSMC")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts)):
        raw = fe.fetch_fundamentals_edgar("TSM")
    assert raw["revenue"][0] == pytest.approx(90e9)
