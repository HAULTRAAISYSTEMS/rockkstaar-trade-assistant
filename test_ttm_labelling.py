"""A trailing-twelve-month margin is not the fiscal year's margin.

Position 0 of the gross, operating and net margin series is replaced with a
TTM figure from the metric feed - correct for scoring, because a fiscal year
end can be eleven months stale. Nothing said so. The history table printed
that number in a row labelled with the fiscal year end, beside that year's
revenue and net income, and the chart drew it above an "FY26" tick.
"""
from unittest.mock import patch

import pytest

import fundamentals_engine as fe
import fundamentals_charts as fc
from test_edgar_pipeline import YEARS, _Resp, facts


TTM = {"gross_margin_ttm": 0.615, "operating_margin_ttm": 0.415,
       "net_margin_ttm": 0.355, "period_end": "2026-03-31"}


def scored(ttm=None):
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts())):
        raw = fe.fetch_fundamentals_edgar("KLAC")
    if ttm:
        raw["_ttm_metrics"] = dict(ttm)
    return fe.score_fundamentals(raw)


class TestTheHistoryTable:
    def test_the_newest_row_is_marked_when_the_margins_are_ttm(self):
        rows = scored(TTM)["history"]
        assert rows[0]["margins_are_ttm"] is True

    def test_no_other_row_is_marked(self):
        rows = scored(TTM)["history"]
        assert [r["margins_are_ttm"] for r in rows[1:]] == [False] * (len(rows) - 1)

    def test_the_mark_names_the_period_it_actually_covers(self):
        rows = scored(TTM)["history"]
        assert rows[0]["margin_period"] == "TTM through 2026-03-31"

    def test_without_a_ttm_feed_nothing_is_marked(self):
        rows = scored()["history"]
        assert all(r["margins_are_ttm"] is False for r in rows)

    def test_the_fiscal_year_label_is_still_the_fiscal_year(self):
        """Revenue and net income in that row really are FY figures."""
        rows = scored(TTM)["history"]
        assert rows[0]["period_end"] == YEARS[0]


class TestTheChart:
    @staticmethod
    def _margins(scorecard):
        return next(c for c in scorecard["charts"] if c["key"] == "margins")

    @staticmethod
    def _revenue(scorecard):
        return next(c for c in scorecard["charts"] if c["key"] == "revenue_income")

    def test_the_newest_margin_tick_reads_ttm(self):
        chart = self._margins(scored(TTM))
        assert chart["x_labels"][-1]["text"] == "TTM"

    def test_the_older_margin_ticks_are_still_fiscal_years(self):
        chart = self._margins(scored(TTM))
        assert all(label["text"].startswith("FY")
                   for label in chart["x_labels"][:-1])

    def test_the_revenue_chart_keeps_its_fiscal_year(self):
        """Only the margins are TTM. The bars beside them are not."""
        chart = self._revenue(scored(TTM))
        assert chart["x_labels"][-1]["text"].startswith("FY")

    def test_a_hovered_point_says_which_period_it_is(self):
        chart = self._margins(scored(TTM))
        assert any("TTM" in point["title"] for point in chart["points"])

    def test_without_a_ttm_feed_every_tick_is_a_fiscal_year(self):
        chart = self._margins(scored())
        assert all(label["text"].startswith("FY") for label in chart["x_labels"])


def test_the_label_helper_ignores_the_flag_unless_asked():
    rows = [{"period_end": "2026-06-30", "margins_are_ttm": True}]
    assert fc._fiscal_labels(rows) == ["FY26"]
    assert fc._fiscal_labels(rows, "margins_are_ttm") == ["TTM"]


class TestRoicIsComputedOnce:
    """The headline and the chart's newest point were two calculations.

    They used different lookups and different guards, so the card could show
    a headline ROIC that did not match the first point of its own trail.
    """

    def test_the_headline_is_the_newest_year_of_the_trail(self):
        with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
             patch.object(fe._req_module, "get", return_value=_Resp(facts())):
            raw = fe.fetch_fundamentals_edgar("KLAC")
        assert raw["roic"] == raw["roic_series"][0]

    def test_it_is_none_rather_than_stale_when_the_newest_year_cannot_be_computed(self):
        payload = facts()
        payload["facts"]["us-gaap"].pop("StockholdersEquity")
        with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
             patch.object(fe._req_module, "get", return_value=_Resp(payload)):
            raw = fe.fetch_fundamentals_edgar("KLAC")
        assert raw["roic"] is None
