"""A thin scorecard cannot produce a confident verdict.

The score renormalises to 40 points no matter how many rows resolved, so a
company whose filings only answered six points of tests could pass all six and
be reported a "Great Company" on that evidence. The page said "6 pts available
- some data missing" in nine-pixel type beside the headline, which is not the
same as not making the claim.
"""
import pytest

import fundamentals_engine as fe


class TestTheCapItself:
    def test_a_full_scorecard_is_never_capped(self):
        assert fe.cap_for_coverage("Great Company", 40) == ("Great Company", None)

    def test_almost_the_whole_scorecard_is_never_capped(self):
        verdict, note = fe.cap_for_coverage("Great Company", 30)
        assert (verdict, note) == ("Great Company", None)

    def test_under_seventy_percent_stops_at_good(self):
        verdict, note = fe.cap_for_coverage("Great Company", 24)
        assert verdict == "Good"
        assert "24 of 40" in note

    def test_under_half_stops_at_caution(self):
        verdict, note = fe.cap_for_coverage("Great Company", 12)
        assert verdict == "Caution"
        assert "Caution" in note

    @pytest.mark.parametrize("verdict", ["Avoid", "Caution"])
    def test_a_cap_never_lowers_a_verdict_that_is_already_lower(self, verdict):
        """The cap is a ceiling, not a penalty."""
        assert fe.cap_for_coverage(verdict, 6) == (verdict, None)

    def test_a_verdict_at_the_ceiling_is_left_alone_and_unexplained(self):
        """No note, because nothing was held back."""
        assert fe.cap_for_coverage("Good", 24) == ("Good", None)

    def test_an_unknown_verdict_passes_through(self):
        assert fe.cap_for_coverage("", 4) == ("", None)


class TestOnTheCard:
    """End to end, through score_fundamentals."""

    @staticmethod
    def _scored(raw):
        return fe.score_fundamentals(raw)

    def test_a_company_with_almost_no_data_is_not_called_great(self):
        # Revenue and net income only: a handful of rows resolve, all of them
        # favourably, and everything else reads N/A.
        raw = {
            "ticker": "THIN",
            "source": "edgar",
            "revenue": [1000.0, 900.0, 800.0, 700.0, 600.0],
            "net_income": [200.0, 170.0, 140.0, 120.0, 100.0],
            "fiscal_period_ends": ["2026-06-30", "2025-06-30", "2024-06-30",
                                   "2023-06-30", "2022-06-30"],
        }
        scored = self._scored(raw)
        assert scored["total_possible"] < 40
        assert scored["verdict"] in ("Caution", "Good", "Avoid")
        assert scored["verdict"] != "Great Company"

    def test_the_reader_is_told_why_it_was_held_back(self):
        raw = {
            "ticker": "THIN",
            "source": "edgar",
            "revenue": [1000.0, 900.0, 800.0, 700.0, 600.0],
            "net_income": [200.0, 170.0, 140.0, 120.0, 100.0],
            "fiscal_period_ends": ["2026-06-30", "2025-06-30", "2024-06-30",
                                   "2023-06-30", "2022-06-30"],
        }
        scored = self._scored(raw)
        if scored["total_possible"] < 28:
            assert scored["coverage_note"], "capped without saying so"
            assert "points could be scored" in scored["coverage_note"]

    def test_a_complete_scorecard_carries_no_note(self):
        from test_edgar_pipeline import facts, _Resp
        from unittest.mock import patch
        with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
             patch.object(fe._req_module, "get", return_value=_Resp(facts())):
            scored = self._scored(fe.fetch_fundamentals_edgar("KLAC"))
        if scored["total_possible"] >= 28:
            assert scored["coverage_note"] is None


def test_the_downgrade_panel_still_reports_the_triggers_not_the_cap():
    """verdict_after belongs to the triggers.

    Reusing the final verdict there would have shown a coverage cap as
    though an integrity trigger had fired.
    """
    raw = {
        "ticker": "THIN",
        "source": "edgar",
        "revenue": [1000.0, 900.0, 800.0, 700.0, 600.0],
        "net_income": [200.0, 170.0, 140.0, 120.0, 100.0],
        "fiscal_period_ends": ["2026-06-30", "2025-06-30", "2024-06-30",
                               "2023-06-30", "2022-06-30"],
    }
    scored = fe.score_fundamentals(raw)
    check = scored["downgrade_check"]
    if not check["any_fired"]:
        assert check["verdict_before"] == check["verdict_after"]


class TestNothingAtAll:
    """Zero resolved rows is not the same as a bad company."""

    EMPTY = {"ticker": "NONE", "source": "edgar"}

    def test_no_data_does_not_read_as_avoid(self):
        scored = fe.score_fundamentals(dict(self.EMPTY))
        assert scored["total_possible"] == 0
        assert scored["verdict"] == fe.NO_DATA_VERDICT

    def test_the_reason_does_not_talk_about_the_business(self):
        scored = fe.score_fundamentals(dict(self.EMPTY))
        reason = scored["verdict_reason"]
        assert "capital is better deployed elsewhere" not in reason
        assert "scorecard" in reason.lower()

    def test_it_says_the_gap_is_in_the_data(self):
        scored = fe.score_fundamentals(dict(self.EMPTY))
        assert "not a judgement about the business" in scored["coverage_note"]

    def test_it_gets_a_neutral_badge(self):
        scored = fe.score_fundamentals(dict(self.EMPTY))
        assert scored["verdict_class"] == "verdict-na"


def test_a_provider_with_no_cash_flow_statement_does_not_500():
    """_history_table subscripted four series directly.

    A provider that returns an income statement and nothing else raised
    KeyError on free_cash_flow and took the whole fundamentals page down.
    """
    raw = {"ticker": "IS", "source": "edgar",
           "revenue": [1000.0, 900.0], "net_income": [200.0, 170.0]}
    scored = fe.score_fundamentals(raw)
    assert len(scored["history"]) == 2
    assert scored["history"][0]["fcf"] == "N/A"
