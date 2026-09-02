"""EDGAR/Finnhub reconciliation for the annual series.

EDGAR is the source of record. Finnhub fills years EDGAR has no fact for, and a
material disagreement is reported rather than silently resolved - a wrong figure
should reach the page as a discrepancy, not as a confident number.
"""
import pytest

import finnhub_annual as fa

TIMELINE = ["2026-06-30", "2025-06-30", "2024-06-30"]


def report(end, **concepts):
    return {"endDate": end, "form": "10-K",
            "report": {"ic": [{"concept": c, "value": v} for c, v in concepts.items()]}}


def test_concepts_are_extracted_by_name():
    parsed = fa.parse_annual_reports([
        report("2026-06-30", Revenues=13579e6, GrossProfit=8324e6,
               OperatingIncomeLoss=5661e6, NetIncomeLoss=4831e6)])
    row = parsed["2026-06-30"]
    assert row["revenue"] == 13579e6
    assert row["operating_income"] == 5661e6


def test_reports_without_a_period_end_are_skipped():
    assert fa.parse_annual_reports([{"report": {"ic": [{"concept": "Revenues", "value": 1}]}}]) == {}


def test_agreement_leaves_edgar_untouched_and_silent():
    finnhub = {"2026-06-30": {"operating_income": 5661e6}}
    values, notes = fa.reconcile("operating_income", TIMELINE, [5661e6, None, None], finnhub)
    assert values[0] == 5661e6 and notes == []


def test_gap_is_filled_and_recorded():
    finnhub = {"2025-06-30": {"operating_income": 5016e6}}
    values, notes = fa.reconcile("operating_income", TIMELINE, [5661e6, None, None], finnhub)
    assert values[1] == 5016e6
    assert notes and notes[0]["kind"] == "filled" and notes[0]["period"] == "2025-06-30"


def test_material_disagreement_keeps_the_filing_and_reports_it():
    """The observed failure: EDGAR yielding a quarterly-sized operating income."""
    finnhub = {"2026-06-30": {"operating_income": 5661e6}}
    values, notes = fa.reconcile("operating_income", TIMELINE, [1400e6, None, None], finnhub)
    assert values[0] == 1400e6, "EDGAR is the record; the disagreement is reported, not overwritten"
    assert notes[0]["kind"] == "disagreement"
    assert notes[0]["edgar"] == 1400e6 and notes[0]["finnhub"] == 5661e6
    assert notes[0]["drift"] > 0.5


def test_small_differences_are_not_flagged():
    """Rounding and restatement noise must not produce alarm fatigue."""
    finnhub = {"2026-06-30": {"revenue": 13579_000_000}}
    _, notes = fa.reconcile("revenue", TIMELINE, [13580_000_000, None, None], finnhub)
    assert notes == []


def test_series_shorter_than_the_timeline_is_padded():
    finnhub = {"2024-06-30": {"revenue": 9812e6}}
    values, _ = fa.reconcile("revenue", TIMELINE, [13579e6], finnhub)
    assert len(values) == 3 and values[2] == 9812e6


def test_cross_check_is_a_no_op_without_a_timeline():
    raw = {"ticker": "T", "revenue": [1.0]}
    assert fa.cross_check(dict(raw), [report("2026-06-30", Revenues=2.0)])["revenue"] == [1.0]


def test_cross_check_never_raises_on_bad_input():
    raw = {"ticker": "T", "fiscal_period_ends": TIMELINE, "revenue": [1.0]}
    for junk in (None, [], ["nonsense"], [{"endDate": None}], [{"report": None}]):
        assert fa.cross_check(dict(raw), junk) is not None


def test_disagreements_reach_the_result_as_notes():
    raw = {"ticker": "KLAC", "fiscal_period_ends": TIMELINE,
           "operating_income": [1400e6, None, None], "revenue": [13579e6, None, None]}
    out = fa.cross_check(raw, [report("2026-06-30", OperatingIncomeLoss=5661e6, Revenues=13579e6)])
    kinds = {n["kind"] for n in out.get("_source_notes", [])}
    assert "disagreement" in kinds
