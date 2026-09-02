"""What KLA's filing actually looks like, verified against SEC.

Every fixture in this file was checked against data.sec.gov rather than
assumed, because three separate rows on the card failed for reasons no amount
of reading the code would have revealed:

  * OperatingIncomeLoss and CostsAndExpenses exist for KLA and carry a decade
    of facts, but the newest is 2015-03-31. The lookup found them, returned
    2011-2015 values, and every one aligned to None against a 2022-2026
    timeline. The operating margin trail read N/A with no error anywhere.
  * WeightedAverageNumberOfDilutedSharesOutstanding is denominated in "shares".
    The lookup read only the USD buckets, so every share-count concept looked
    absent and the dilution row could never resolve.
  * LongTermDebtCurrent is $0 at 2025-06-30 and is not tagged at all for
    2026-06-30. Reading index 0 on each side of the coverage test produced
    None on one side only, so a company with no current maturities kept
    failing a test it passes trivially.
"""
from unittest.mock import patch

import pytest

import fundamentals_engine as fe
from test_edgar_pipeline import _Resp

MODERN = ["2026-06-30", "2025-06-30", "2024-06-30", "2023-06-30", "2022-06-30"]
MODERN_STARTS = ["2025-07-01", "2024-07-01", "2023-07-01", "2022-07-01", "2021-07-01"]
STALE = ["2015-06-30", "2014-06-30", "2013-06-30", "2012-06-30", "2011-06-30"]
STALE_STARTS = ["2014-07-01", "2013-07-01", "2012-07-01", "2011-07-01", "2010-07-01"]


def duration(values, ends=None, starts=None):
    ends, starts = ends or MODERN, starts or MODERN_STARTS
    return {"units": {"USD": [
        {"start": s, "end": e, "val": v, "form": "10-K", "accn": "0000319201-26-000001"}
        for i, (s, e, v) in enumerate(zip(starts, ends, values))]}}


def instants(values, ends=None, unit="USD"):
    return {"units": {unit: [
        {"end": e, "val": v, "form": "10-K", "accn": "0000319201-26-000001"}
        for i, (e, v) in enumerate(zip(ends or MODERN, values))]}}


def shares(values, ends=None, starts=None):
    """Share counts live in a "shares" unit bucket, not USD."""
    ends, starts = ends or MODERN, starts or MODERN_STARTS
    return {"units": {"shares": [
        {"start": s, "end": e, "val": v, "form": "10-K", "accn": "0000319201-26-000001"}
        for i, (s, e, v) in enumerate(zip(starts, ends, values))]}}


def kla_shaped(**over):
    gaap = {
        # Current lines.
        "Revenues": duration([13579e6, 12160e6, 9812e6, 10496e6, 9212e6]),
        "CostOfRevenue": duration([5215e6, 4753e6, 3928e6, 4215e6, 3593e6]),
        "ResearchAndDevelopmentExpense":
            duration([1450e6, 1360e6, 1330e6, 1200e6, 1030e6]),
        "SellingGeneralAndAdministrativeExpense":
            duration([1105e6, 1030e6, 985e6, 940e6, 830e6]),
        "NetIncomeLoss": duration([4831e6, 4062e6, 2762e6, 3387e6, 3322e6]),
        "WeightedAverageNumberOfDilutedSharesOutstanding":
            shares([132.0e6, 133.75e6, 136.19e6, 140.24e6, 151.56e6]),
        "Assets": instants([17952e6, 15600e6, 14100e6, 14700e6, 12500e6]),
        "StockholdersEquity": instants([6350e6, 3400e6, 2900e6, 2200e6, 1600e6]),
        "AssetsCurrent": instants([12382e6, 11000e6, 10200e6, 10500e6, 8900e6]),
        "LiabilitiesCurrent": instants([4305e6, 3900e6, 3500e6, 3600e6, 3100e6]),
        "CashAndCashEquivalentsAtCarryingValue": instants([1650e6] * 5),
        "LongTermDebt": instants([5890e6, 5880e6, 6660e6, 6700e6, 3400e6]),
        # Zero, and only for the four years the filer tagged it.
        "LongTermDebtCurrent": instants([0.0, 0.0, 0.0, 0.0], ends=MODERN[1:]),
        "NetCashProvidedByUsedInOperatingActivities":
            duration([4146e6, 4114e6, 3308e6, 3675e6, 3312e6]),
        "PaymentsToAcquirePropertyPlantAndEquipment":
            duration([376e6, 401e6, 311e6, 349e6, 264e6]),
        # Retired in 2015 but still full of facts.
        "OperatingIncomeLoss": duration(
            [772e6, 730e6, 1016e6, 1160e6, 900e6], ends=STALE, starts=STALE_STARTS),
        "CostsAndExpenses": duration(
            [2157e6, 2113e6, 2156e6, 2015e6, 1507e6], ends=STALE, starts=STALE_STARTS),
    }
    gaap.update(over)
    return {"facts": {"us-gaap": gaap}}


def fetched(**over):
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(kla_shaped(**over))):
        return fe.fetch_fundamentals_edgar("KLAC")


def card(**over):
    return fe.score_fundamentals(fetched(**over))


def row(c, key):
    return next(r for sec in c["sections"] for r in sec["rows"] if r["key"] == key)


# ── Retired concepts ─────────────────────────────────────────────────────────

def test_a_decade_stale_concept_is_not_used():
    """The 2015 values must not appear anywhere in the modern series."""
    raw = fetched()
    assert 772e6 not in [x for x in raw["operating_income"] if x is not None]


def test_operating_income_is_rebuilt_from_the_expense_lines():
    raw = fetched()
    assert all(x is not None for x in raw["operating_income"])
    # FY26: 13579 revenue - 5215 cost of revenue - 1450 R&D - 1105 SG&A
    assert raw["operating_income"][0] == pytest.approx(5809e6)


def test_the_operating_margin_trail_is_no_longer_blank():
    """The visible symptom: 41.8% and then four N/As."""
    trail = [r["operating_margin"] for r in card()["history"]]
    assert "N/A" not in trail and len(trail) == 5


def test_the_rebuild_from_expense_lines_is_disclosed():
    assert any("R&D" in line for line in card()["derived_lines"])


def test_operating_income_needs_both_major_expense_lines():
    """Missing SG&A would understate costs and overstate operating income —
    the direction that flatters a company."""
    facts = kla_shaped()
    del facts["facts"]["us-gaap"]["SellingGeneralAndAdministrativeExpense"]
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts)):
        raw = fe.fetch_fundamentals_edgar("KLAC")
    assert all(x is None for x in raw["operating_income"])


def test_a_concept_renamed_recently_still_supplies_its_history():
    """The guard must not throw away a line the filer renamed three years ago;
    that is history the card reports, not a retired concept."""
    facts = kla_shaped()
    gaap = facts["facts"]["us-gaap"]
    gaap["RevenueFromContractWithCustomerExcludingAssessedTax"] = duration(
        [13579e6, 12160e6, 9812e6], ends=MODERN[:3], starts=MODERN_STARTS[:3])
    gaap["Revenues"] = duration(
        [10496e6, 9212e6], ends=MODERN[3:], starts=MODERN_STARTS[3:])
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts)):
        raw = fe.fetch_fundamentals_edgar("KLAC")
    assert raw["revenue"] == [13579e6, 12160e6, 9812e6, 10496e6, 9212e6]


# ── Share-denominated concepts ───────────────────────────────────────────────

def test_share_counts_are_read_from_the_shares_unit():
    raw = fetched()
    assert raw["diluted_shares"][0] == pytest.approx(132.0e6)
    assert all(x is not None for x in raw["diluted_shares"])


def test_the_dilution_row_resolves_and_passes():
    dilution = row(card(), "share_dilution")
    assert dilution["passed"] is True
    # 151.56M down to 132.0M is a 12.9% reduction.
    assert dilution["value"] == "-12.9%"


# ── Coverage read at a period both sides cover ───────────────────────────────

def test_zero_current_maturities_passes_the_coverage_test():
    """Not tagged for the newest year, zero for the four before it. Reading
    index 0 on each side produced None on one side and fell back to comparing
    cash against the whole debt stack."""
    coverage = row(card(), "cash_covers_debt")
    assert coverage["passed"] is True
    assert "current portion unavailable" not in coverage["working"]


def test_coverage_still_falls_back_when_nothing_is_disclosed():
    facts = kla_shaped()
    del facts["facts"]["us-gaap"]["LongTermDebtCurrent"]
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts)):
        c = fe.score_fundamentals(fe.fetch_fundamentals_edgar("KLAC"))
    assert "current portion unavailable" in row(c, "cash_covers_debt")["working"]


# ── The whole card ───────────────────────────────────────────────────────────

def test_every_quality_row_resolves():
    quality = next(sec for sec in card()["sections"]
                   if sec["name"] == "Quality Metrics")
    assert quality["possible"] == 10
    assert all(r["value"] != "N/A" for r in quality["rows"])


# ── Stock splits ─────────────────────────────────────────────────────────────

SPLIT_SHARES = "WeightedAverageNumberOfDilutedSharesOutstanding"


def test_a_split_is_not_reported_as_dilution():
    """The live card read "152M shares -> 1,320M = +770.7%" and failed the row.
    A ten-for-one split multiplies the count and leaves earnings alone; the
    filer restates prior years inside the new report but older reports keep
    their pre-split figures, so splicing the two bases invents 770% issuance.
    """
    facts = kla_shaped(**{SPLIT_SHARES: shares(
        [1320.0e6, 133.75e6, 136.19e6, 140.24e6, 151.56e6])})
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts)):
        c = fe.score_fundamentals(fe.fetch_fundamentals_edgar("KLAC"))
    dilution = row(c, "share_dilution")
    assert dilution["passed"] is not False, dilution["working"]
    assert "+770" not in (dilution["value"] or "")


def test_a_split_that_leaves_one_year_is_unscored_and_says_why():
    facts = kla_shaped(**{SPLIT_SHARES: shares(
        [1320.0e6, 133.75e6, 136.19e6, 140.24e6, 151.56e6])})
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts)):
        c = fe.score_fundamentals(fe.fetch_fundamentals_edgar("KLAC"))
    dilution = row(c, "share_dilution")
    assert dilution["passed"] is None
    assert "split" in dilution["working"]


def test_a_real_decline_is_still_reported_after_the_guard():
    """The guard must not swallow ordinary buybacks."""
    dilution = row(card(), "share_dilution")
    assert dilution["passed"] is True and dilution["value"] == "-12.9%"


def test_the_coverage_row_names_the_period_it_read():
    """It falls back to an earlier year when the newest lacks a current-debt
    tag, so the balance sheet date has to be on the page."""
    working = row(card(), "cash_covers_debt")["working"]
    assert "2025-06-30" in working


def filed_shares(entries):
    """entries: (end, start, value, filed, accn) — one bucket, several filings."""
    return {"units": {"shares": [
        {"start": st, "end": e, "val": v, "form": "10-K", "filed": f, "accn": a}
        for e, st, v, f, a in entries]}}


def test_the_newest_filing_wins_by_date_not_accession_number():
    """Accession prefixes belong to whoever transmitted the filing. A company
    that changes filing agent gets numbers that no longer sort chronologically,
    and picking the wrong one here is the difference between a share series
    that sits on one side of a stock split and one that straddles it.

    Here the newer FY26 report carries the lower accession. Ordering by
    accession string would take the older report's pre-split figures, splice
    them against the post-split year, and read a 10-for-1 split as issuance.
    """
    facts = kla_shaped(**{SPLIT_SHARES: filed_shares([
        # FY26 report: post-split, restated, filed most recently, LOWER accn.
        ("2026-06-30", "2025-07-01", 1320.0e6, "2026-08-07", "0000319201-26-000010"),
        ("2025-06-30", "2024-07-01", 1337.5e6, "2026-08-07", "0000319201-26-000010"),
        ("2024-06-30", "2023-07-01", 1361.9e6, "2026-08-07", "0000319201-26-000010"),
        # FY25 report: pre-split, filed a year earlier, HIGHER accn.
        ("2025-06-30", "2024-07-01", 133.75e6, "2025-08-08", "0001564590-25-000099"),
        ("2024-06-30", "2023-07-01", 136.19e6, "2025-08-08", "0001564590-25-000099"),
        ("2023-06-30", "2022-07-01", 140.24e6, "2025-08-08", "0001564590-25-000099"),
    ])})
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts)):
        raw = fe.fetch_fundamentals_edgar("KLAC")
        c = fe.score_fundamentals(raw)
    # One basis, three years, no split break to trim.
    assert raw["diluted_shares"][:3] == [1320.0e6, 1337.5e6, 1361.9e6]
    dilution = row(c, "share_dilution")
    assert dilution["passed"] is True
    assert dilution["value"] == "-3.1%"
