"""A US listing's price against a home listing's range.

Finnhub's /quote returns the ADR in dollars while /stock/metric states the
52-week range and the market cap for the company's primary listing in its own
currency, and nothing in either payload says so. TSMC rendered as a $428.91
share against a "52-week high" of 2,535 and a "52-week low" of 1,145: a price
below its own annual low, "-83.1% off the high", "still -63% above it", and a
market capitalisation of $61.8T. Every one of those figures is a New Taiwan
dollar wearing a dollar sign.

Ratios survive the mismatch — a P/E is the same number whichever currency both
halves are stated in. Absolute prices and comparisons across the two do not.
"""
import pytest

from valuation import build_valuation, _on_one_basis


TSM_METRIC = {
    "52WeekHigh": 2535.0, "52WeekLow": 1145.0, "52WeekHighDate": "2026-06-23",
    "peTTM": 27.3, "pfcfShareTTM": 54.3, "psTTM": 13.8,
    "marketCapitalization": 61_800_000.0, "currentDividendYieldTTM": 0.86,
}
TSM_QUOTE = {"c": 428.91, "pc": 417.02}

US_METRIC = {
    "52WeekHigh": 1150.0, "52WeekLow": 550.0, "52WeekHighDate": "2026-06-23",
    "peTTM": 47.9, "marketCapitalization": 120_000.0,
}
US_QUOTE = {"c": 640.0, "pc": 656.0}


def rows(valuation):
    return {r["key"]: r for r in valuation["rows"]}


class TestTheDetector:
    def test_a_price_inside_the_range_is_one_basis(self):
        assert _on_one_basis(640.0, 1150.0, 550.0) is True

    def test_a_genuine_new_high_is_still_one_basis(self):
        assert _on_one_basis(1160.0, 1150.0, 550.0) is True

    def test_a_genuine_new_low_is_still_one_basis(self):
        assert _on_one_basis(540.0, 1150.0, 550.0) is True

    def test_a_price_a_third_of_the_annual_low_is_not(self):
        assert _on_one_basis(428.91, 2535.0, 1145.0) is False

    @pytest.mark.parametrize("price,high,low", [
        (None, 100.0, 50.0), (75.0, None, 50.0), (75.0, 100.0, None),
    ])
    def test_it_cannot_decide_without_all_three(self, price, high, low):
        """Absent data is not evidence of a mismatch."""
        assert _on_one_basis(price, high, low) is True


class TestAMismatchedCard:
    @pytest.fixture
    def tsm(self):
        return build_valuation(TSM_METRIC, TSM_QUOTE, home_symbol="NT$")

    def test_it_is_flagged(self, tsm):
        assert tsm["one_basis"] is False

    def test_the_price_is_still_in_dollars(self, tsm):
        assert rows(tsm)["price"]["value"] == "$428.91"

    def test_the_range_is_labelled_in_the_home_currency(self, tsm):
        assert rows(tsm)["high"]["value"] == "NT$2,535.00"
        assert rows(tsm)["low"]["value"] == "NT$1,145.00"

    def test_the_market_cap_is_not_sixty_one_trillion_dollars(self, tsm):
        assert rows(tsm)["cap"]["value"] == "NT$61.8T"

    def test_no_comparison_is_drawn_across_the_two(self, tsm):
        assert "off_high" not in rows(tsm)
        assert rows(tsm)["low"]["note"] == ""
        assert tsm["off_high_pct"] is None

    def test_the_ratios_are_kept(self, tsm):
        """Currency-neutral, so they are still true."""
        assert rows(tsm)["pe"]["value"] == "27.3x"
        assert rows(tsm)["ps"]["value"] == "13.8x"
        assert rows(tsm)["div"]["value"] == "0.86%"

    def test_the_reader_is_told(self, tsm):
        assert "not comparable" in tsm["notice"]
        assert "NT$" in tsm["notice"]

    def test_it_still_works_without_knowing_the_home_currency(self):
        val = build_valuation(TSM_METRIC, TSM_QUOTE)
        assert val["one_basis"] is False
        assert rows(val)["high"]["value"] == "2,535.00"
        assert val["notice"]


class TestAnOrdinaryCard:
    @pytest.fixture
    def usd(self):
        return build_valuation(US_METRIC, US_QUOTE, home_symbol="$")

    def test_nothing_is_flagged(self, usd):
        assert usd["one_basis"] is True
        assert usd["notice"] == ""

    def test_every_figure_is_in_dollars(self, usd):
        assert rows(usd)["high"]["value"] == "$1,150.00"
        assert rows(usd)["cap"]["value"] == "$120.0B"

    def test_the_comparisons_are_drawn(self, usd):
        assert "off_high" in rows(usd)
        assert usd["off_high_pct"] == pytest.approx(-44.3, abs=0.1)

    def test_above_the_low_reads_as_a_sentence(self, usd):
        assert rows(usd)["low"]["note"] == "still +16% above it"


def test_a_price_at_a_new_low_says_so_rather_than_negative_above_it():
    """"still -63% above it" is not a sentence."""
    val = build_valuation({"52WeekHigh": 1150.0, "52WeekLow": 550.0},
                          {"c": 545.0, "pc": 560.0})
    assert rows(val)["low"]["note"] == "a new 52-week low"
