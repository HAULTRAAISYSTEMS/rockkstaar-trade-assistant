"""End-to-end exercise of fetch_fundamentals_edgar against stubbed SEC facts.

This function had no test coverage. An alignment refactor renamed its local
series variables and left one reference behind, so every ticker returned
"name 'oi' is not defined" and the Fundamentals page rendered nothing. The unit
tests all passed because they call score_fundamentals() directly and never
execute this function.
"""
import json
from unittest.mock import patch

import pytest

import fundamentals_engine as fe

YEARS = ["2026-06-30", "2025-06-30", "2024-06-30", "2023-06-30", "2022-06-30"]
STARTS = ["2025-07-01", "2024-07-01", "2023-07-01", "2022-07-01", "2021-07-01"]


def duration(values):
    return {"units": {"USD": [
        {"start": s, "end": e, "val": v, "form": "10-K", "accn": f"a-{i}"}
        for i, (s, e, v) in enumerate(zip(STARTS, YEARS, values))]}}


def instant(value):
    return {"units": {"USD": [
        {"end": YEARS[0], "val": value, "form": "10-K", "accn": "a-0"}]}}


def facts():
    return {"facts": {"us-gaap": {
        "Revenues": duration([13579e6, 12160e6, 9812e6, 10496e6, 9212e6]),
        "GrossProfit": duration([8324e6, 7407e6, 5884e6, 6281e6, 5619e6]),
        "OperatingIncomeLoss": duration([5661e6, 5016e6, 3636e6, 3995e6, 3652e6]),
        "NetIncomeLoss": duration([4831e6, 4062e6, 2762e6, 3387e6, 3322e6]),
        "Assets": instant(17952e6),
        "Liabilities": instant(11602e6),
        "StockholdersEquity": instant(6350e6),
        "AssetsCurrent": instant(12382e6),
        "LiabilitiesCurrent": instant(4305e6),
        "CashAndCashEquivalentsAtCarryingValue": instant(1650e6),
        "LongTermDebt": instant(5890e6),
        "Goodwill": instant(1789e6),
        "RetainedEarningsAccumulatedDeficit": instant(3684e6),
    }}}


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def edgar():
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA Corporation")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts())):
        yield


def test_edgar_fetch_returns_a_result(edgar):
    """The regression: this raised NameError and the page showed an error box."""
    result = fe.fetch_fundamentals_edgar("KLAC")
    assert result is not None and not result.get("error")


def test_series_are_populated_and_aligned(edgar):
    result = fe.fetch_fundamentals_edgar("KLAC")
    for field in ("revenue", "gross_profit", "operating_income", "net_income"):
        assert len(result[field]) == 5, f"{field} is not aligned to the timeline"
    assert result["fiscal_period_ends"][0] == "2026-06-30"


def test_operating_margins_match_the_filing(edgar):
    result = fe.fetch_fundamentals_edgar("KLAC")
    margins = [round(o / r * 100, 1)
               for o, r in zip(result["operating_income"], result["revenue"])]
    assert margins == [41.7, 41.2, 37.1, 38.1, 39.6]


def test_gross_margins_match_the_filing(edgar):
    result = fe.fetch_fundamentals_edgar("KLAC")
    margins = [round(g / r * 100, 1)
               for g, r in zip(result["gross_profit"], result["revenue"])]
    assert margins == [61.3, 60.9, 60.0, 59.8, 61.0]


def test_derived_roe_uses_the_aligned_series(edgar):
    """oi0/ni0 are read after alignment; this is the line that broke."""
    result = fe.fetch_fundamentals_edgar("KLAC")
    assert result.get("roe") == pytest.approx(76.1, abs=0.2)
    assert result.get("roic") is not None


def test_scoring_the_edgar_output_does_not_raise(edgar):
    """The whole path a page load takes."""
    scored = fe.score_fundamentals(fe.fetch_fundamentals_edgar("KLAC"))
    assert scored["total_possible"] > 0
    assert scored["verdict"] in fe.VERDICT_BANDS
