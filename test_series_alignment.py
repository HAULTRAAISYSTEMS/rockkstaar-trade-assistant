"""Every series sits on one set of fiscal year ends.

Only the income statement was date-aligned. The balance sheet and the cash
flow statement were bare lists ordered by whatever period ends each concept
happened to cover, and then read by index against the aligned ones. A concept
a filer starts tagging a year late, or stops tagging, shifts the whole series
by a slot: free cash flow is drawn under the wrong fiscal year on the chart
and in the history table, and the debt-versus-revenue and goodwill-impairment
downgrade triggers compare two different years and fire on nothing.
"""
from unittest.mock import patch

import pytest

import fundamentals_engine as fe
from test_edgar_pipeline import YEARS, STARTS, _Resp, duration


def instants(values_by_year):
    """Instant facts at the fiscal year ends named, and no others."""
    return {"units": {"USD": [
        {"end": end, "val": val, "form": "10-K", "accn": f"a-{end}"}
        for end, val in values_by_year.items()]}}


def durations(values_by_year):
    """Annual duration facts for a subset of the years."""
    starts = dict(zip(YEARS, STARTS))
    return {"units": {"USD": [
        {"start": starts[end], "end": end, "val": val,
         "form": "10-K", "accn": f"a-{end}"}
        for end, val in values_by_year.items()]}}


REVENUE = [13579e6, 12160e6, 9812e6, 10496e6, 9212e6]
GOODWILL = {YEARS[2]: 2400e6, YEARS[3]: 2350e6, YEARS[4]: 2300e6}
DEBT = {YEARS[1]: 5890e6, YEARS[2]: 6100e6}
OCF = {YEARS[1]: 4114e6, YEARS[2]: 3308e6, YEARS[3]: 3675e6, YEARS[4]: 3312e6}
CAPEX = dict(zip(YEARS, [376e6, 401e6, 311e6, 349e6, 264e6]))


def ragged_facts():
    """A filer whose concepts start and stop at different years.

    Revenue and net income cover all five years. Goodwill was only tagged for
    the three oldest, long-term debt for two in the middle, and operating cash
    flow is missing the newest year.
    """
    return {"facts": {"us-gaap": {
        "Revenues": duration(REVENUE),
        "NetIncomeLoss": duration([4831e6, 4062e6, 2762e6, 3387e6, 3322e6]),
        "Assets": instants(dict(zip(YEARS, [17952e6, 16800e6, 15100e6,
                                            14400e6, 13900e6]))),
        "StockholdersEquity": instants(dict(zip(YEARS, [6350e6, 6000e6, 5700e6,
                                                        5500e6, 5300e6]))),
        "Goodwill": instants(GOODWILL),
        "LongTermDebt": instants(DEBT),
        "NetCashProvidedByUsedInOperatingActivities": durations(OCF),
        "PaymentsToAcquirePropertyPlantAndEquipment": durations(CAPEX),
    }}}


@pytest.fixture
def ragged():
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA Corporation")), \
         patch.object(fe._req_module, "get", return_value=_Resp(ragged_facts())):
        yield fe.fetch_fundamentals_edgar("KLAC")


def test_the_timeline_is_the_income_statements(ragged):
    assert ragged["fiscal_period_ends"] == YEARS
    assert ragged["balance_period_ends"] == YEARS


@pytest.mark.parametrize("key", [
    "total_assets", "total_equity", "goodwill", "total_debt", "cash",
    "operating_cash_flow", "capex", "free_cash_flow", "financing_cash_flow",
])
def test_every_series_has_one_slot_per_year(ragged, key):
    assert len(ragged[key]) == len(YEARS)


def test_a_concept_that_stops_early_leaves_the_recent_years_empty(ragged):
    """Goodwill was tagged for the three oldest years only."""
    assert ragged["goodwill"] == [None, None,
                                  GOODWILL[YEARS[2]],
                                  GOODWILL[YEARS[3]],
                                  GOODWILL[YEARS[4]]]


def test_a_concept_tagged_for_middle_years_lands_on_those_years(ragged):
    """Unaligned, 5890 sat at index 0 and read as the latest year's debt."""
    assert ragged["total_debt"] == [None, DEBT[YEARS[1]], DEBT[YEARS[2]],
                                    None, None]


def test_free_cash_flow_is_drawn_under_the_year_it_belongs_to(ragged):
    """Capex covers five years, operating cash flow four.

    Subtracting index-for-index across two ragged lists took the newest
    capex off the second-newest operating cash flow, and every later year
    was wrong by one as well.
    """
    fcf = ragged["free_cash_flow"]
    assert fcf[0] is None
    for i, end in enumerate(YEARS[1:], start=1):
        assert fcf[i] == pytest.approx(OCF[end] - CAPEX[end])


def test_a_filer_with_no_balance_sheet_at_all_is_reported_missing():
    """All-None is not the same as present.

    Timeline-shaped series are never empty, so the missing-section checks
    could no longer be plain truthiness tests.
    """
    facts = ragged_facts()
    for name in ("Assets", "StockholdersEquity", "Goodwill", "LongTermDebt"):
        facts["facts"]["us-gaap"].pop(name)
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts)):
        result = fe.fetch_fundamentals_edgar("KLAC")
    assert result is not None
    assert all(v is None for v in result["total_assets"])
    assert result.get("roe") is None
