"""FRED does not publish every series in the same unit.

Every dollar series was declared as billions and then divided by 1000 to reach
trillions. That is right for the two that really are billions and wrong by a
factor of a thousand for the three that are millions — so the Fed balance sheet
rendered as "$6737.2T", which is about a thousand times world GDP, and the
Treasury General Account as "$967.93T" when it holds roughly $968 billion.
Both appeared on the command center and the liquidity page.

The units below were checked against each series' own page on
fred.stlouisfed.org and are asserted here so a future edit cannot quietly
reintroduce the same mistake.
"""
import pytest

import liquidity_engine as L


# series id -> the unit FRED publishes it in, per its series page.
PUBLISHED_UNITS = {
    "WALCL": "millions",      # Millions of U.S. Dollars, Not Seasonally Adjusted
    "WTREGEN": "millions",    # Millions of U.S. Dollars, Not Seasonally Adjusted
    "WRESBAL": "millions",    # Millions of U.S. Dollars, Not Seasonally Adjusted
    "M2SL": "billions",       # Billions of Dollars, Seasonally Adjusted
    "RRPONTSYD": "billions",  # Billions of US Dollars, Not Seasonally Adjusted
}


class TestTheManifestMatchesFred:
    @pytest.mark.parametrize("series,unit", PUBLISHED_UNITS.items())
    def test_each_series_declares_the_unit_fred_publishes(self, series, unit):
        assert L._FRED_SERIES[series]["source"] == unit, series

    def test_every_dollar_series_declares_one(self):
        """A missing declaration silently defaults, which is how this broke."""
        for series, spec in L._FRED_SERIES.items():
            if spec["unit"] == "%":
                continue
            assert spec.get("source"), series


class TestConversion:
    def test_a_millions_series_reaches_trillions(self):
        """6,737,204 million is $6.7 trillion, not $6,737.2 trillion."""
        assert L.to_trillions("WALCL", 6_737_204, 1) == 6.7

    def test_a_billions_series_reaches_trillions(self):
        assert L.to_trillions("M2SL", 23_200, 1) == 23.2

    def test_the_treasury_account_is_under_a_trillion(self):
        assert L.to_trillions("WTREGEN", 967_935) == 0.97

    def test_millions_and_billions_do_not_convert_alike(self):
        assert L.to_trillions("WALCL", 1_000_000) != L.to_trillions("M2SL", 1_000_000)

    def test_billions_conversion(self):
        assert L.to_billions("WALCL", 6_737_204) == pytest.approx(6737.204)
        assert L.to_billions("M2SL", 23_200) == 23_200

    @pytest.mark.parametrize("value", [None])
    def test_no_value_converts_to_nothing(self, value):
        assert L.to_trillions("WALCL", value) is None
        assert L.to_billions("WALCL", value) is None

    def test_zero_is_zero_not_missing(self):
        assert L.to_trillions("RRPONTSYD", 0) == 0.0

    def test_a_negative_change_keeps_its_sign(self):
        assert L.to_trillions("WALCL", -500_000, 1) == -0.5

    def test_an_unknown_series_is_assumed_to_be_billions(self):
        """The safer default: it under-converts rather than inflating by 1000."""
        assert L.to_trillions("NOTASERIES", 1000) == 1.0


class TestNothingIsPlausibleButWrong:
    """The failure mode here was a number that looked like a number."""

    def test_the_fed_balance_sheet_lands_in_a_believable_range(self):
        value = L.to_trillions("WALCL", 6_737_204, 1)
        assert 1 < value < 20, value

    def test_the_treasury_account_lands_in_a_believable_range(self):
        value = L.to_trillions("WTREGEN", 967_935)
        assert 0 < value < 5, value

    def test_bank_reserves_land_in_a_believable_range(self):
        assert 0 < L.to_trillions("WRESBAL", 3_100_000, 1) < 10


class TestTheThresholdsAreInTheRightUnit:
    def test_a_fifty_million_move_is_not_a_treasury_drain(self):
        """A $50m change is a raw value of 50 in a millions series. The old
        test compared that raw 50 against a threshold meant as $50B, so it
        fired on a move a thousand times too small."""
        raw_fifty_million = 50
        assert abs(L.to_billions("WTREGEN", raw_fifty_million)) == 0.05
        assert abs(L.to_billions("WTREGEN", raw_fifty_million)) < 50

    def test_a_fifty_billion_drain_is_one(self):
        assert L.to_billions("WTREGEN", -60_000) < -50

    def test_a_small_move_is_not(self):
        assert -50 < L.to_billions("WTREGEN", -40_000) < 0

    def test_the_source_says_so(self):
        source = open("liquidity_engine.py").read()
        assert "chg_b = to_billions(\"WTREGEN\", chg)" in source


class TestNoBareDivisionsRemain:
    def test_the_analyzers_convert_through_the_helper(self):
        """A bare / 1000 is the bug. Every dollar conversion names its series."""
        import re
        source = open("liquidity_engine.py").read()
        body = source[source.index("def _analyze_balance_sheet"):]
        bare = [line.strip() for line in body.splitlines()
                if re.search(r"/\s*1000\b", line) and "to_trillions" not in line
                and "to_billions" not in line and "_TO_BILLIONS" not in line]
        assert bare == [], bare
