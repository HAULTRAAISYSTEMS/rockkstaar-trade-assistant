"""The data inspector. Read-only, and must never raise into a route."""
from unittest.mock import patch

import fundamentals_debug as fd
import fundamentals_engine as fe


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


def _facts():
    """One annual fact and one quarterly fact sharing a period end."""
    return {"facts": {"us-gaap": {"GrossProfit": {"units": {"USD": [
        {"start": "2025-07-01", "end": "2026-06-30", "val": 8324e6, "form": "10-K", "accn": "a-1"},
        {"start": "2026-04-01", "end": "2026-06-30", "val": 2180e6, "form": "10-K", "accn": "z-9"},
        {"start": "2024-07-01", "end": "2025-06-30", "val": 7407e6, "form": "10-K", "accn": "a-0"},
    ]}}}}}


def test_reports_which_facts_the_filter_keeps():
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(_facts())):
        out = fd.inspect_edgar("KLAC")
    gp = out["concepts"]["gross_profit"]
    assert gp["concept"] == "GrossProfit"
    assert gp["total_annual_form_facts"] == 3
    assert gp["kept_by_filter"] == 2, "the 90-day fact must be excluded"
    assert gp["distinct_periods_selected"] == 2


def test_the_quarterly_fact_is_visibly_rejected():
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(_facts())):
        out = fd.inspect_edgar("KLAC")
    quarterly = [f for f in out["concepts"]["gross_profit"]["facts"] if f["days"] == 90]
    assert quarterly and quarterly[0]["kept_by_filter"] is False


def test_missing_concept_is_reported_not_crashed():
    with patch.object(fe, "_edgar_cik", return_value=("1", "X")), \
         patch.object(fe._req_module, "get", return_value=_Resp({"facts": {"us-gaap": {}}})):
        out = fd.inspect_edgar("XYZ")
    assert out["concepts"]["gross_profit"]["concept"] is None


def test_unknown_ticker_returns_an_error_not_an_exception():
    with patch.object(fe, "_edgar_cik", return_value=(None, None)):
        assert "not found" in fd.inspect_edgar("NOPE")["error"]


def test_http_failure_is_reported():
    with patch.object(fe, "_edgar_cik", return_value=("1", "X")), \
         patch.object(fe._req_module, "get", return_value=_Resp({}, status=403)):
        assert "403" in fd.inspect_edgar("XYZ")["error"]


def test_gated_finnhub_is_stated_plainly():
    with patch("finnhub_ttm.fetch_finnhub_annual", return_value=[]):
        out = fd.inspect_finnhub("KLAC")
    assert out["available"] is False and "premium-gated" in out["note"]


def test_finnhub_failure_never_raises():
    with patch("finnhub_ttm.fetch_finnhub_annual", side_effect=RuntimeError("boom")):
        assert fd.inspect_finnhub("KLAC")["available"] is False
