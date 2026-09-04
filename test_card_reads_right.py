"""Three things the live card said that were not what they meant.

Found by reading TSM's page rather than a fixture: a passed integrity check
marked with a tick beside a sentence asserting bankruptcy; a liquidity row
scoring full marks off a balance sheet four years old; and a row whose
working, value and verdict were three unrelated numbers.
"""
from unittest.mock import patch

import pytest

import fundamentals_engine as fe
from test_edgar_pipeline import YEARS, STARTS, _Resp, facts, duration


PAGE = open("templates/fundamentals.html").read()


class TestTheDowngradePanelCanBeRead:
    """Each label states the bad thing, so a tick beside "the company has
    filed for bankruptcy" reads as "yes, it did"."""

    def test_the_ticks_are_gone(self):
        assert "'✗' if c.fired else '✓'" not in PAGE

    def test_the_outcome_is_a_word(self):
        assert "'FIRED' if c.fired else 'CLEAR'" in PAGE

    def test_both_words_are_styled(self):
        css = open("static/css/fundamentals.css").read()
        assert ".dg-status" in css
        assert ".dg-item.is-fired .dg-status" in css

    def test_the_engine_still_reports_which_fired(self):
        with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
             patch.object(fe._req_module, "get", return_value=_Resp(facts())):
            scored = fe.score_fundamentals(fe.fetch_fundamentals_edgar("KLAC"))
        checks = scored["downgrade_check"]["checks"]
        assert checks and all("fired" in c for c in checks)


def stale_coverage_facts():
    """Current maturities tagged only in the oldest year on the card."""
    payload = facts()
    us = payload["facts"]["us-gaap"]
    us["CashAndCashEquivalentsAtCarryingValue"] = {"units": {"USD": [
        {"end": end, "val": val, "form": "10-K", "accn": f"c-{i}"}
        for i, (end, val) in enumerate(zip(YEARS, [1650e6, 1500e6, 1400e6,
                                                   1300e6, 1200e6]))]}}
    us["DebtCurrent"] = {"units": {"USD": [
        {"end": YEARS[4], "val": 20e6, "form": "10-K", "accn": "d-4"}]}}
    us["LongTermDebt"] = {"units": {"USD": [
        {"end": end, "val": val, "form": "10-K", "accn": f"t-{i}"}
        for i, (end, val) in enumerate(zip(YEARS, [9000e6, 8000e6, 7000e6,
                                                   6000e6, 5890e6]))]}}
    return payload


class TestLiquidityIsAboutToday:
    @pytest.fixture
    def stale(self):
        with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
             patch.object(fe._req_module, "get", return_value=_Resp(stale_coverage_facts())):
            return fe.score_fundamentals(fe.fetch_fundamentals_edgar("KLAC"))

    @staticmethod
    def _row(scored, key):
        for section in scored["sections"]:
            for row in section["rows"]:
                if row["key"] == key:
                    return row
        return None

    def test_a_four_year_old_figure_is_not_used(self, stale):
        """TSMC passed this row in 2026 on its 2021 current maturities."""
        row = self._row(stale, "cash_covers_debt")
        assert row is not None
        assert YEARS[4] not in (row.get("working") or "")

    def test_it_falls_back_to_the_stricter_comparison(self, stale):
        row = self._row(stale, "cash_covers_debt")
        assert "current portion unavailable" in (row.get("working") or "")

    def test_a_current_disclosure_is_still_used(self):
        with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
             patch.object(fe._req_module, "get", return_value=_Resp(facts())):
            scored = fe.score_fundamentals(fe.fetch_fundamentals_edgar("KLAC"))
        row = self._row(scored, "cash_covers_debt")
        assert row is not None


class TestARowShowsItsOwnArithmetic:
    @pytest.fixture
    def scored(self):
        payload = facts()
        payload["facts"]["us-gaap"]["NetCashProvidedByUsedInOperatingActivities"] = \
            duration([4146e6, 4114e6, 3308e6, 3675e6, 3312e6])
        payload["facts"]["us-gaap"]["NetCashProvidedByUsedInFinancingActivities"] = \
            duration([-2500e6, -2100e6, -1800e6, -1500e6, -1200e6])
        with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
             patch.object(fe._req_module, "get", return_value=_Resp(payload)):
            return fe.score_fundamentals(fe.fetch_fundamentals_edgar("KLAC"))

    @staticmethod
    def _row(scored, key):
        for section in scored["sections"]:
            for row in section["rows"]:
                if row["key"] == key:
                    return row
        return None

    def test_the_working_is_about_cash_flow_not_the_debt_balance(self, scored):
        working = self._row(scored, "debt_financing")["working"]
        assert "financing cash flow" in working
        assert "from operations" in working

    def test_the_value_and_the_working_are_the_same_number(self, scored):
        """They were two different numbers on one row."""
        row = self._row(scored, "debt_financing")
        assert row["value"] == "-$2.5B"
        assert row["working"].startswith("-$2.50B financing cash flow")

    def test_negative_money_puts_the_sign_before_the_symbol(self, scored):
        """"$-2.5B" is not how anyone writes money."""
        row = self._row(scored, "debt_financing")
        assert "$-" not in row["value"]
        assert "$-" not in row["working"]

    def test_it_shows_the_proportion_the_test_actually_uses(self, scored):
        assert "% of operating cash flow" in self._row(scored, "debt_financing")["working"]
