"""Rebuilding income-statement subtotals a filer never tagged.

KLA's income statement runs "Total revenues" straight into "Costs and
expenses" with no gross profit subtotal, so EDGAR carries no GrossProfit fact
for any year. The page showed a TTM gross margin of 61.6% followed by four
N/As, and the same for operating margin. Both subtotals are identities over
lines the filer does tag, so they are rebuilt rather than reported as missing.
"""
from unittest.mock import patch

import pytest

import fundamentals_engine as fe
from test_edgar_pipeline import YEARS, STARTS, _Resp, duration, instant


def facts_without_subtotals():
    """A filer that tags components but presents no gross profit line."""
    return {"facts": {"us-gaap": {
        "Revenues": duration([13579e6, 12160e6, 9812e6, 10496e6, 9212e6]),
        "CostOfRevenue": duration([5255e6, 4753e6, 3928e6, 4215e6, 3593e6]),
        "CostsAndExpenses": duration([7918e6, 7144e6, 6176e6, 6501e6, 5560e6]),
        "NetIncomeLoss": duration([4831e6, 4062e6, 2762e6, 3387e6, 3322e6]),
        "Assets": instant(17952e6),
        "StockholdersEquity": instant(6350e6),
        "AssetsCurrent": instant(12382e6),
        "LiabilitiesCurrent": instant(4305e6),
        "CashAndCashEquivalentsAtCarryingValue": instant(1650e6),
        "ShortTermInvestments": instant(2870e6),
        "DebtCurrent": instant(20e6),
        "LongTermDebt": instant(5890e6),
        "NetCashProvidedByUsedInOperatingActivities":
            duration([4146e6, 4114e6, 3308e6, 3675e6, 3312e6]),
        "PaymentsToAcquirePropertyPlantAndEquipment":
            duration([376e6, 401e6, 311e6, 349e6, 264e6]),
    }}}


@pytest.fixture
def filer():
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA Corporation")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts_without_subtotals())):
        yield


def test_gross_profit_is_rebuilt_for_every_year(filer):
    result = fe.fetch_fundamentals_edgar("KLAC")
    assert all(v is not None for v in result["gross_profit"])
    assert result["gross_profit"][0] == pytest.approx(13579e6 - 5255e6)


def test_operating_income_is_rebuilt_for_every_year(filer):
    result = fe.fetch_fundamentals_edgar("KLAC")
    assert all(v is not None for v in result["operating_income"])
    assert result["operating_income"][0] == pytest.approx(13579e6 - 7918e6)


def test_the_margin_trail_is_no_longer_blank(filer):
    """The visible symptom: four N/As under a single TTM figure."""
    scored = fe.score_fundamentals(fe.fetch_fundamentals_edgar("KLAC"))
    trail = [row["gross_margin"] for row in scored["history"]]
    assert "N/A" not in trail and len(trail) == 5


def test_the_rebuild_is_disclosed(filer):
    scored = fe.score_fundamentals(fe.fetch_fundamentals_edgar("KLAC"))
    assert any("gross profit" in line for line in scored["derived_lines"])
    assert any("operating income" in line for line in scored["derived_lines"])


def test_a_tagged_subtotal_is_never_overwritten():
    """Where the filer does present the line, its own number wins."""
    payload = facts_without_subtotals()
    payload["facts"]["us-gaap"]["GrossProfit"] = duration(
        [8000e6, 7000e6, 5500e6, 6000e6, 5300e6])
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(payload)):
        result = fe.fetch_fundamentals_edgar("KLAC")
    assert result["gross_profit"][0] == 8000e6


def test_an_implausible_derivation_is_discarded():
    """Cost of revenue above revenue would imply a negative gross profit for a
    company that is plainly profitable; the guard drops it rather than charting
    a number the filing does not support."""
    payload = facts_without_subtotals()
    payload["facts"]["us-gaap"]["CostOfRevenue"] = duration(
        [99999e6, 99999e6, 99999e6, 99999e6, 99999e6])
    del payload["facts"]["us-gaap"]["CostsAndExpenses"]
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(payload)):
        result = fe.fetch_fundamentals_edgar("KLAC")
    assert all(v is None for v in result["gross_profit"])


def test_liquidity_and_near_term_debt_are_populated(filer):
    """Neither field was ever set on the EDGAR path, so cash coverage compared
    bare cash against the entire debt stack and could not pass."""
    result = fe.fetch_fundamentals_edgar("KLAC")
    assert result["cash_and_st_investments"][0] == pytest.approx(1650e6 + 2870e6)
    assert result["short_term_debt"][0] == pytest.approx(20e6)


def test_cash_coverage_now_scores_on_near_term_maturities(filer):
    scored = fe.score_fundamentals(fe.fetch_fundamentals_edgar("KLAC"))
    row = next(r for section in scored["sections"] for r in section["rows"]
               if r["key"] == "cash_covers_debt")
    assert row["passed"] is True
    assert "current portion unavailable" not in (row.get("working") or "")


def test_renamed_concepts_merge_their_coverage():
    """A filer that switched revenue tags mid-decade covered five years across
    two concepts; stopping at the first match returned only the newer three."""
    payload = facts_without_subtotals()
    gaap = payload["facts"]["us-gaap"]
    new_tag = {"units": {"USD": [
        {"start": STARTS[i], "end": YEARS[i], "val": v, "form": "10-K", "accn": f"n-{i}"}
        for i, v in enumerate([13579e6, 12160e6, 9812e6])]}}
    old_tag = {"units": {"USD": [
        {"start": STARTS[i], "end": YEARS[i], "val": v, "form": "10-K", "accn": f"o-{i}"}
        for i, v in [(3, 10496e6), (4, 9212e6)]]}}
    gaap["RevenueFromContractWithCustomerExcludingAssessedTax"] = new_tag
    gaap["Revenues"] = old_tag
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(payload)):
        result = fe.fetch_fundamentals_edgar("KLAC")
    assert result["revenue"] == [13579e6, 12160e6, 9812e6, 10496e6, 9212e6]


def test_the_page_gets_all_three_charts(filer):
    """The end of the pipeline: charts are built from the same rebuilt series
    the table shows, so a filled margin trail is a drawn margin line."""
    scored = fe.score_fundamentals(fe.fetch_fundamentals_edgar("KLAC"))
    assert [c["key"] for c in scored["charts"]] == [
        "revenue_income", "margins", "cash_quality"]
    margins = next(c for c in scored["charts"] if c["key"] == "margins")
    assert all(len(line["points"].split()) == 5 for line in margins["lines"])
