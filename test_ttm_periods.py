"""A trailing twelve months must actually be twelve months.

The feed mixes annual rows in with the quarterly ones and nothing filtered
them, so a 10-K row was summed as though it were a quarter. Three quarters of
100 plus an annual 400 produced a "TTM" of 700 against a true 400 — a 75%
overstatement, displayed with a TTM period label and fed straight into the
scored free cash flow and earnings quality rows.
"""
import pytest

from finnhub_ttm import _is_quarter, compute_ttm


def q(rev=100.0, ocf=90.0, cap=10.0, ni=50.0, start="2026-01-01",
      end="2026-03-31", form="10-Q", **over):
    report = {"ic": [], "cf": []}
    if rev is not None:
        report["ic"].append({"concept": "Revenues", "value": rev})
    if ni is not None:
        report["ic"].append({"concept": "NetIncomeLoss", "value": ni})
    if ocf is not None:
        report["cf"].append(
            {"concept": "NetCashProvidedByUsedInOperatingActivities",
             "value": ocf})
    if cap is not None:
        report["cf"].append(
            {"concept": "PaymentsToAcquirePropertyPlantAndEquipment",
             "value": -cap})
    out = {"startDate": start, "endDate": end, "form": form, "report": report}
    out.update(over)
    return out


def four_quarters(**over):
    spans = [("2026-01-01", "2026-03-31"), ("2025-10-01", "2025-12-31"),
             ("2025-07-01", "2025-09-30"), ("2025-04-01", "2025-06-30")]
    return [q(start=s, end=e, **over) for s, e in spans]


# ── Telling a quarter from a year ────────────────────────────────────────────

def test_a_three_month_report_is_a_quarter():
    assert _is_quarter(q()) is True


def test_a_full_year_report_is_not():
    assert _is_quarter(q(start="2025-04-01", end="2026-03-31", form="10-K")) is False


def test_an_annual_form_is_rejected_even_if_the_dates_look_short():
    assert _is_quarter(q(form="10-K")) is False
    assert _is_quarter(q(form="20-F")) is False


def test_a_row_with_no_period_falls_back_to_the_form():
    assert _is_quarter(q(start=None, end=None, form="10-Q")) is True
    assert _is_quarter(q(start=None, end=None, form="")) is False


def test_a_malformed_date_is_not_assumed_to_be_a_quarter():
    assert _is_quarter(q(start="not-a-date", end="2026-03-31")) is False


# ── The sum ──────────────────────────────────────────────────────────────────

def test_four_real_quarters_sum_correctly():
    r = compute_ttm(four_quarters())
    assert r["ttm_revenue"] == 400.0
    assert r["ttm_ocf"] == 360.0 and r["ttm_capex"] == 40.0
    assert r["ttm_fcf"] == 320.0
    assert r["quarters_used"] == 4 and r["computed"] is True


def test_an_annual_row_is_not_counted_as_a_quarter():
    """The headline bug: 3 quarters + a 10-K read as 700 instead of 400."""
    reports = four_quarters()[:3] + [
        q(rev=400.0, ocf=360.0, cap=40.0, ni=200.0,
          start="2025-04-01", end="2026-03-31", form="10-K")]
    r = compute_ttm(reports)
    assert r["ttm_revenue"] != 700.0
    assert r["computed"] is False          # only three real quarters remain
    assert "3 quarterly reports" in r["incomplete_reason"]


def test_three_quarters_is_not_a_trailing_year():
    r = compute_ttm(four_quarters()[:3])
    assert r["ttm_revenue"] is None and r["computed"] is False
    assert r["quarters_used"] == 3


def test_a_line_missing_from_one_quarter_is_not_reported_as_a_full_year():
    """Revenue in four quarters and cash flow in two produced a full-year
    revenue beside a half-year cash flow, both labelled TTM."""
    reports = four_quarters()
    for r_ in reports[2:]:
        r_["report"]["cf"] = []
    out = compute_ttm(reports)
    assert out["ttm_revenue"] == 400.0     # revenue is complete
    assert out["ttm_ocf"] is None          # cash flow is not
    assert out["ttm_capex"] is None and out["ttm_fcf"] is None


def test_no_reports_at_all():
    r = compute_ttm([])
    assert r["computed"] is False and r["ttm_revenue"] is None


def test_more_than_four_quarters_takes_the_newest_four():
    r = compute_ttm(four_quarters() + [q(rev=999.0)])
    assert r["ttm_revenue"] == 400.0
