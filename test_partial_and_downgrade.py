"""Partial credit, and showing the integrity check even when it passes.

Two criteria are not honestly binary. A company two years into a three-year
revenue streak has not met the test, but it is not in the same position as one
whose revenue is falling. Free cash flow at 0.78x of reported profit is the
same shape of answer. Scoring both zero throws the distinction away.

The downgrade panel exists because silence is ambiguous: a card that says
nothing when everything is clean looks identical to one where the check never
ran, and those are very different reasons to see no warning.
"""
from unittest.mock import patch

import pytest

import fundamentals_engine as fe
from test_edgar_stale_concepts import kla_shaped, duration
from test_edgar_pipeline import _Resp


def card(**over):
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(kla_shaped(**over))):
        return fe.score_fundamentals(fe.fetch_fundamentals_edgar("KLAC"))


def row(c, key):
    return next(r for sec in c["sections"] for r in sec["rows"] if r["key"] == key)


# ── The scoring rule ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("cond,points,expected", [
    (True,  2, (2, 2, True)),
    (False, 2, (0, 2, False)),
    (None,  2, (0, 0, None)),
    (0.5,   2, (1, 2, "partial")),
    (1 / 3, 3, (1, 3, "partial")),
    (0.5,   3, (2, 3, "partial")),
    (1.5,   2, (2, 2, True)),          # clamped, never over-awards
    (0.0,   2, (0, 2, False)),
    (0.01,  2, (1, 2, "partial")),     # always at least one point
    (0.99,  2, (1, 2, "partial")),     # never equal to a full pass
])
def test_the_scoring_rule(cond, points, expected):
    assert fe._score_check(cond, points) == expected


def test_partial_is_never_mistaken_for_either_neighbour():
    for points in (1, 2, 3):
        for frac in (0.05, 0.33, 0.5, 0.8, 0.95):
            earned, avail, passed = fe._score_check(frac, points)
            assert passed == "partial"
            assert 0 < earned <= max(points - 1, 0) or points == 1


# ── The two criteria that use it ─────────────────────────────────────────────

def test_a_two_year_streak_earns_half_not_nothing():
    """KLA: FY24 fell, so the run is two years."""
    r = row(card(), "revenue_growth")
    assert r["passed"] == "partial" and (r["earned"], r["avail"]) == (1, 2)


def test_a_real_three_year_streak_still_passes_outright():
    r = row(card(Revenues=duration([140e8, 130e8, 120e8, 110e8, 100e8])),
            "revenue_growth")
    assert r["passed"] is True and r["earned"] == 2


def test_falling_revenue_still_earns_nothing():
    r = row(card(Revenues=duration([90e8, 100e8, 110e8, 120e8, 130e8])),
            "revenue_growth")
    assert r["passed"] is False and r["earned"] == 0


def test_cash_at_three_quarters_of_profit_earns_one_of_three():
    """0.78x is the one line that fails on an otherwise strong card. Scaling
    with the ratio would award two of three and read as a near pass."""
    r = row(card(), "fcf_vs_net_income")
    assert r["passed"] == "partial" and (r["earned"], r["avail"]) == (1, 3)


def test_cash_well_below_profit_still_earns_nothing():
    r = row(card(NetCashProvidedByUsedInOperatingActivities=duration(
        [1200e6, 4114e6, 3308e6, 3675e6, 3312e6])), "fcf_vs_net_income")
    assert r["passed"] is False and r["earned"] == 0


def test_sections_report_how_many_rows_were_partial():
    c = card()
    by_name = {s["name"]: s for s in c["sections"]}
    assert by_name["Income Statement"]["partials"] == 1
    assert by_name["Cash Flow"]["partials"] == 1
    assert by_name["Balance Sheet"]["partials"] == 0


# ── The downgrade panel ──────────────────────────────────────────────────────

def test_the_check_is_reported_even_when_nothing_fires():
    d = card()["downgrade_check"]
    assert d["any_fired"] is False
    assert len(d["checks"]) == len(fe.DOWNGRADE_TRIGGERS)
    assert all(not c["fired"] for c in d["checks"])


def test_every_trigger_is_listed_with_a_readable_label():
    for c in card()["downgrade_check"]["checks"]:
        assert c["label"] and c["label"] != c["key"], c["key"]


def test_a_fired_trigger_is_marked_and_the_band_moves():
    import fundamentals_engine as engine
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(kla_shaped())):
        raw = fe.fetch_fundamentals_edgar("KLAC")
    raw["_filing_signals"] = {"available": True, "signals": [
        {"item": "4.02", "form": "8-K", "date": "2026-05-01", "label": "x",
         "why": "y", "severity": "critical"}]}
    d = fe.score_fundamentals(raw)["downgrade_check"]
    assert d["any_fired"] is True
    fired = [c["key"] for c in d["checks"] if c["fired"]]
    assert "restated_financials" in fired
    bands = engine.VERDICT_BANDS
    assert bands.index(d["verdict_after"]) < bands.index(d["verdict_before"])


# ── The template ─────────────────────────────────────────────────────────────

def test_partial_does_not_render_as_a_pass():
    """"partial" is a truthy string and fell straight into the pass branch."""
    tpl = open("templates/fundamentals.html").read()
    assert "row.passed == 'partial'" in tpl
    assert tpl.index("row.passed == 'partial'") < tpl.index("{% elif row.passed %}")
