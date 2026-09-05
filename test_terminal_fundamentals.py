"""The Terminal's Fundamentals tab.

It used to render one hardcoded sentence and a link to another page, which is
the least useful thing a tab can be: the reader is already looking at the
company and the whole scorecard is already computed. These check that it now
carries the verdict, where the points went, the rows that decided it, and the
concept behind each one — and that it still degrades honestly when there is no
scorecard to show.
"""
from unittest.mock import patch

import pytest

import concepts as C
import fundamentals_engine as fe
import terminal_intelligence as TI
from test_edgar_pipeline import facts, _Resp


@pytest.fixture(scope="module")
def card():
    with patch.object(fe, "_edgar_cik", return_value=("1", "KLA Corporation")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts())):
        return fe.score_fundamentals(fe.fetch_fundamentals_edgar("KLAC"))


@pytest.fixture(scope="module")
def summary(card):
    return TI.summarize_scorecard(card)


class TestTheSummary:
    def test_it_reports_the_verdict(self, summary, card):
        assert summary["available"] is True
        assert summary["verdict"] == card["verdict"]

    def test_it_reports_the_score(self, summary, card):
        assert summary["earned"] == card["total_earned"]
        assert summary["possible"] == card["total_possible"]

    def test_it_reports_every_section(self, summary, card):
        assert len(summary["sections"]) == len(card["sections"])

    def test_it_carries_the_currency_so_the_tab_can_say_so(self, summary):
        assert summary["currency"]

    def test_it_carries_the_coverage_note_when_there_is_one(self, card):
        thin = fe.score_fundamentals({
            "ticker": "THIN", "source": "edgar",
            "revenue": [1000.0, 900.0], "net_income": [200.0, 170.0],
            "fiscal_period_ends": ["2026-06-30", "2025-06-30"]})
        assert TI.summarize_scorecard(thin)["coverage_note"]


class TestTheHighlightsExplainTheVerdict:
    def test_there_are_some(self, summary):
        assert summary["highlights"]

    def test_it_stays_a_summary_not_a_second_scorecard(self, summary):
        assert len(summary["highlights"]) <= TI.FUNDAMENTAL_HIGHLIGHTS

    def test_failures_come_first_because_they_cost_the_points(self, summary):
        marks = [h["passed"] for h in summary["highlights"]]
        order = {False: 0, "partial": 1, True: 2}
        assert [order[m] for m in marks] == sorted(order[m] for m in marks)

    def test_an_unscored_row_is_never_surfaced(self, summary):
        """An N/A explains nothing about why the verdict came out as it did."""
        assert all(h["passed"] is not None for h in summary["highlights"])

    def test_every_highlight_shows_its_arithmetic(self, summary):
        assert all(h.get("working") for h in summary["highlights"])

    def test_every_highlight_links_to_the_idea_behind_it(self, summary):
        """This is what makes it a teaching surface rather than a report."""
        for highlight in summary["highlights"]:
            assert highlight["concept"], highlight["label"]
            assert C.get(highlight["concept"]) is not None
            assert highlight["one_liner"]

    def test_a_heavier_row_outranks_a_lighter_one_of_the_same_result(self):
        light = {"passed": False, "points": 1, "label": "b"}
        heavy = {"passed": False, "points": 2, "label": "a"}
        assert TI._highlight_rank(heavy) < TI._highlight_rank(light)


class TestWhenThereIsNothingToShow:
    @pytest.mark.parametrize("scored", [
        None, {}, {"error": "not found"}, {"sections": []},
    ])
    def test_it_says_so_rather_than_inventing(self, scored):
        assert TI.summarize_scorecard(scored)["available"] is False

    def test_a_card_of_only_unscored_rows_still_summarises(self, card):
        blank = {**card, "sections": [
            {**s, "rows": [{**r, "passed": None} for r in s["rows"]]}
            for s in card["sections"]]}
        result = TI.summarize_scorecard(blank)
        assert result["available"] is True
        assert result["highlights"] == []


class TestThePayload:
    def _payload(self, scored, asset="EQUITY"):
        return TI.build_terminal_intelligence(
            "KLAC" if asset == "EQUITY" else "SPY", {}, {}, scored=scored)

    def test_the_scorecard_reaches_the_tab(self, card):
        payload = self._payload(card)
        assert payload["fundamentals"]["scorecard"]["available"] is True

    def test_the_stub_message_is_gone_when_there_is_real_data(self, card):
        assert self._payload(card)["fundamentals"]["message"] == ""

    def test_an_unanalysed_company_is_told_what_to_do(self):
        message = self._payload(None)["fundamentals"]["message"]
        assert "has not been scored yet" in message

    def test_an_etf_is_told_why_rather_than_prompted(self):
        message = self._payload(None, asset="ETF")["fundamentals"]["message"]
        assert "ETF" in message

    def test_the_old_fields_are_still_there(self, card):
        """The tab keeps sector, industry and trend beside the new content."""
        fundamentals = self._payload(card)["fundamentals"]
        for key in ("sector", "industry", "relative_strength", "trend"):
            assert key in fundamentals

    def test_nothing_in_the_builder_fetches(self, card):
        """A Terminal panel must never block on a provider call."""
        source = open("terminal_intelligence.py").read()
        assert "requests" not in source
        assert "urlopen" not in source


class TestTheTemplateRendersIt:
    PAGE = open("templates/terminal.html").read()

    def test_the_hardcoded_stub_is_no_longer_the_only_path(self):
        assert "sc.available" in self.PAGE

    def test_it_renders_the_verdict_and_the_score(self):
        assert "tw-fund-verdict" in self.PAGE and "tw-fund-score" in self.PAGE

    def test_it_renders_a_bar_per_section(self):
        assert "tw-fund-sec-bar" in self.PAGE

    def test_every_highlight_row_links_into_the_library(self):
        assert '"/learn/\'+twEsc(h.concept)' in self.PAGE

    def test_it_still_falls_back_when_there_is_no_scorecard(self):
        assert "f.message" in self.PAGE

    def test_every_class_it_uses_is_styled(self):
        css = open("static/css/terminal.css").read()
        for name in ("tw-fund-head", "tw-fund-verdict", "tw-fund-score",
                     "tw-fund-sec", "tw-fund-sec-bar", "tw-fund-sec-pts",
                     "tw-fund-rows", "tw-fund-row", "tw-fund-mark",
                     "tw-fund-val", "tw-fund-work", "tw-fund-learn",
                     "tw-fund-flags", "tw-fund-note"):
            assert f".{name}" in css, name
