"""A dividend yield is a percentage on both surfaces.

info["dividendYield"] cannot be trusted on its own: Yahoo returned it as a
fraction (0.0152) for years and switched to a percentage (1.52) during 2025,
and which one arrives depends on the yfinance version and on what the endpoint
is serving that day. The intel card multiplied by 100 unconditionally - a 1.5%
yield printed as 152.0% the moment the convention flipped - while the
opportunity engine passed the same field through raw, so the two surfaces
disagreed by a factor of a hundred.
"""
import pytest

from intel_engine import normalize_dividend_yield
import opportunity_engine


class TestComputedFromRateAndPrice:
    """Unambiguous, so it is preferred over any yield field."""

    def test_a_normal_yield(self):
        info = {"dividendRate": 2.40, "currentPrice": 160.0}
        assert normalize_dividend_yield(info) == pytest.approx(1.50)

    def test_it_wins_over_a_disagreeing_yield_field(self):
        """A stale or differently-scaled yield field does not get a vote."""
        info = {"dividendRate": 2.40, "currentPrice": 160.0,
                "dividendYield": 0.0153}
        assert normalize_dividend_yield(info) == pytest.approx(1.50)

    @pytest.mark.parametrize("price_key",
                             ["currentPrice", "regularMarketPrice", "previousClose"])
    def test_any_price_field_will_do(self, price_key):
        info = {"dividendRate": 4.0, price_key: 100.0}
        assert normalize_dividend_yield(info) == pytest.approx(4.0)


class TestTheAmbiguousField:
    def test_the_old_fraction_convention(self):
        assert normalize_dividend_yield({"dividendYield": 0.0152}) == pytest.approx(1.52)

    def test_the_new_percentage_convention(self):
        """This is what used to come out as 152%."""
        assert normalize_dividend_yield({"dividendYield": 1.52}) == pytest.approx(1.52)

    def test_a_high_yielder_under_the_new_convention(self):
        assert normalize_dividend_yield({"dividendYield": 11.4}) == pytest.approx(11.4)

    def test_the_fraction_field_is_preferred_over_the_ambiguous_one(self):
        info = {"trailingAnnualDividendYield": 0.0201, "dividendYield": 2.01}
        assert normalize_dividend_yield(info) == pytest.approx(2.01)


class TestNothingToReport:
    @pytest.mark.parametrize("info", [
        {}, {"dividendYield": None}, {"dividendYield": 0},
        {"dividendRate": 0, "currentPrice": 100.0},
        {"dividendYield": "n/a"},
    ])
    def test_no_dividend_is_none_not_zero_or_a_crash(self, info):
        assert normalize_dividend_yield(info) is None

    def test_a_rate_with_no_price_falls_through_rather_than_dividing_by_zero(self):
        assert normalize_dividend_yield({"dividendRate": 2.0, "currentPrice": 0}) is None


def test_both_surfaces_agree():
    """The whole point: one number, one unit."""
    info = {"dividendRate": 2.44, "currentPrice": 160.0, "dividendYield": 1.53}
    assert opportunity_engine._normalize_yield(info) == normalize_dividend_yield(info)
