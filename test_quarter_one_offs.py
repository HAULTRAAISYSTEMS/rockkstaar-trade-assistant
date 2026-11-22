"""Below-the-line gains and losses: read from the filing, flagged on the page."""
from pathlib import Path

from quarter_facts import one_offs

B = 1e9


def f(start, end, val, filed="2026-10-29"):
    return {"start": start, "end": end, "val": val, "filed": filed}


def facts(nonop=True):
    other = [f("2025-07-01", "2025-09-30", 12.759 * B, "2025-10-30"),
             f("2025-01-01", "2025-09-30", 25.0 * B, "2025-10-30"),
             f("2025-01-01", "2025-12-31", 35.0 * B, "2026-02-04"),      # FY 2025 -> Q4 = 10.0
             f("2026-01-01", "2026-03-31", 40.0 * B, "2026-04-29"),
             f("2026-04-01", "2026-06-30", 97.983 * B, "2026-07-22"),
             f("2026-07-01", "2026-09-30", -8.221 * B),
             f("2026-01-01", "2026-09-30", 129.762 * B)]
    g = {"IncomeTaxExpenseBenefit": {"units": {"USD": [f("2026-01-01", "2026-09-30", 40.0 * B)]}},
         "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest":
             {"units": {"USD": [f("2026-01-01", "2026-09-30", 250.0 * B), f("2026-07-01", "2026-09-30", 32.355 * B)]}},
         "OperatingIncomeLoss": {"units": {"USD": [f("2026-01-01", "2026-09-30", 120.238 * B), f("2026-07-01", "2026-09-30", 40.576 * B)]}}}
    if nonop:
        g["NonoperatingIncomeExpense"] = {"units": {"USD": other}}
    return {"facts": {"us-gaap": g}}


ENDS = ["2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"]


def test_reads_quarter_ytd_and_ttm_with_a_derived_q4():
    o = one_offs(facts(), "2026-09-30", "2026-09-30", ENDS)
    assert round(o["quarter"] / B, 3) == -8.221
    assert round(o["ytd"] / B, 3) == 129.762
    assert round(o["ttm"] / B, 3) == round(10.0 + 40.0 + 97.983 - 8.221, 3)
    assert o["tax_rate"] == 0.16 and o["tax_rate_source"] == "effective"


def test_falls_back_to_pretax_less_operating_income():
    o = one_offs(facts(nonop=False), "2026-09-30", "2026-09-30", ENDS)
    assert o["source"] == "derived" and round(o["quarter"] / B, 3) == -8.221
    assert round(o["ytd"] / B, 3) == round(250.0 - 120.238, 3)
    assert o["ttm"] is None                       # earlier quarters not derivable here


def test_page_uses_it_for_pe_and_cash_checks():
    page = Path("templates/fundamentals.html").read_text()
    for needle in ("function qxOneOffs(", "P/E excluding one-offs", "qx-oneoff", "Cash ÷ profit without them"):
        assert needle in page, needle
