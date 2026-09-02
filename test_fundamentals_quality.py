"""The two Quality rows that never scored.

"Economic Moat" carried zero points and read "Needs manual review", and
"Insider ownership data available" awarded two points for a field merely being
present — from yfinance, which this host's IP range is blocked from, so in
production it was permanently N/A. Between them, four of forty points were
unreachable and the card showed 29/36 with no way to move the denominator.

Moat is now scored on the trace a moat leaves in the arithmetic: returns on
capital that competition has not dragged down, and a gross margin that has not
eroded. The insider row is replaced by share dilution, which measures what
happens to the reader's own slice.
"""
from unittest.mock import patch

import pytest

import fundamentals_engine as fe
from test_edgar_pipeline import YEARS, STARTS, _Resp

SHARES = "WeightedAverageNumberOfDilutedSharesOutstanding"


def duration(values):
    return {"units": {"USD": [
        {"start": s, "end": e, "val": v, "form": "10-K", "accn": f"d-{i}"}
        for i, (s, e, v) in enumerate(zip(STARTS, YEARS, values))]}}


def instants(values):
    """One balance-sheet instant per fiscal year end, newest first."""
    return {"units": {"USD": [
        {"end": e, "val": v, "form": "10-K", "accn": f"i-{i}"}
        for i, (e, v) in enumerate(zip(YEARS, values))]}}


def facts(**over):
    gaap = {
        "Revenues": duration([13579e6, 12160e6, 9812e6, 10496e6, 9212e6]),
        "GrossProfit": duration([8324e6, 7407e6, 5884e6, 6281e6, 5619e6]),
        "OperatingIncomeLoss": duration([5661e6, 5016e6, 3636e6, 3995e6, 3652e6]),
        "NetIncomeLoss": duration([4831e6, 4062e6, 2762e6, 3387e6, 3322e6]),
        SHARES: duration([132.0e6, 134.0e6, 137.0e6, 141.0e6, 148.0e6]),
        "Assets": instants([17952e6, 15600e6, 14100e6, 14700e6, 12500e6]),
        "StockholdersEquity": instants([6350e6, 3400e6, 2900e6, 2200e6, 1600e6]),
        "LongTermDebt": instants([5890e6, 5880e6, 6660e6, 6700e6, 3400e6]),
        "AssetsCurrent": instants([12382e6, 11000e6, 10200e6, 10500e6, 8900e6]),
        "LiabilitiesCurrent": instants([4305e6, 3900e6, 3500e6, 3600e6, 3100e6]),
        "CashAndCashEquivalentsAtCarryingValue": instants([1650e6] * 5),
        "NetCashProvidedByUsedInOperatingActivities":
            duration([4146e6, 4114e6, 3308e6, 3675e6, 3312e6]),
        "PaymentsToAcquirePropertyPlantAndEquipment":
            duration([376e6, 401e6, 311e6, 349e6, 264e6]),
    }
    gaap.update(over)
    return {"facts": {"us-gaap": gaap}}


def scored(**over):
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts(**over))):
        return fe.score_fundamentals(fe.fetch_fundamentals_edgar("KLAC"))


def row(card, key):
    return next(r for sec in card["sections"] for r in sec["rows"] if r["key"] == key)


# ── ROIC series ──────────────────────────────────────────────────────────────

def test_roic_is_computed_for_every_year_not_just_the_latest():
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts())):
        raw = fe.fetch_fundamentals_edgar("KLAC")
    series = raw["roic_series"]
    assert len(series) == 5 and all(x is not None for x in series)


def test_roic_matches_the_hand_calculation():
    """NOPAT over equity plus debt, with the tax rate implied by the filing."""
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts())):
        raw = fe.fetch_fundamentals_edgar("KLAC")
    oi, ni, eq, td = 5661e6, 4831e6, 6350e6, 5890e6
    tax = 1.0 - (ni / oi)
    assert raw["roic_series"][0] == pytest.approx(
        oi * (1 - tax) / (eq + td) * 100, rel=1e-6)


# ── Moat ─────────────────────────────────────────────────────────────────────

def test_the_moat_row_now_carries_points():
    """It was points: 0, which quietly shrank the denominator."""
    assert row(scored(), "moat")["avail"] == 2


def test_durable_returns_earn_the_moat_points():
    card = scored()
    assert row(card, "moat")["passed"] is True


def test_one_year_below_the_bar_loses_them():
    """A moat is what survives a bad year; this company's returns did not."""
    card = scored(OperatingIncomeLoss=duration(
        [5661e6, 5016e6, 400e6, 3995e6, 3652e6]))
    assert row(card, "moat")["passed"] is False


def test_eroding_gross_margin_loses_them_even_with_high_returns():
    """Competition shows up in price before it shows up in returns."""
    card = scored(GrossProfit=duration(
        [6100e6, 6300e6, 5884e6, 6281e6, 6450e6]))
    moat = row(card, "moat")
    assert moat["passed"] is False
    assert "fell" in moat["working"]


def test_too_little_history_is_unscored_rather_than_failed():
    short = duration([5661e6, 5016e6])
    short["units"]["USD"] = short["units"]["USD"][:2]
    card = scored(OperatingIncomeLoss=short)
    assert row(card, "moat")["passed"] is None


def test_the_moat_working_line_shows_every_year():
    working = row(scored(), "moat")["working"]
    assert working.count("%") >= 5


# ── Share dilution ───────────────────────────────────────────────────────────

def test_the_insider_availability_row_is_gone():
    """It paid two points for a field existing, from a blocked provider."""
    keys = [r["key"] for sec in scored()["sections"] for r in sec["rows"]]
    assert "insider_ownership" not in keys
    assert "share_dilution" in keys


def test_a_shrinking_share_count_passes():
    card = scored()
    dilution = row(card, "share_dilution")
    assert dilution["passed"] is True
    # 148M shares down to 132M is a 10.8% reduction.
    assert dilution["value"] == "-10.8%"


def test_heavy_issuance_fails():
    card = scored(**{SHARES: duration(
        [180.0e6, 168.0e6, 155.0e6, 150.0e6, 148.0e6])})
    assert row(card, "share_dilution")["passed"] is False


def test_a_flat_count_still_passes():
    """Not every good company buys back stock."""
    card = scored(**{SHARES: duration([148.0e6] * 5)})
    assert row(card, "share_dilution")["passed"] is True


def test_the_working_line_names_both_endpoints():
    working = row(scored(), "share_dilution")["working"]
    assert "148M" in working and "132M" in working and "-10.8%" in working


def test_a_missing_share_count_is_unscored_not_failed():
    facts_no_shares = facts()
    del facts_no_shares["facts"]["us-gaap"][SHARES]
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts_no_shares)):
        card = fe.score_fundamentals(fe.fetch_fundamentals_edgar("KLAC"))
    assert row(card, "share_dilution")["passed"] is None


# ── The whole card ───────────────────────────────────────────────────────────

def test_the_quality_section_is_scored_out_of_ten():
    """Both rows used to drop out — moat carried no points and the insider row
    could never resolve — so Quality maxed at 6 and the card read 29/36 with no
    way to move the denominator. Rows elsewhere still drop out when the filing
    genuinely lacks the data; that is the honest behaviour and is unchanged."""
    quality = next(sec for sec in scored()["sections"]
                   if sec["name"] == "Quality Metrics")
    assert quality["possible"] == 10
    assert quality["earned"] == 10
