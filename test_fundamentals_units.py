"""Unit and definition bugs found by reconciling KLAC against its FY26 10-K.

The app reported debt-to-equity 0.01 where the filing gives 0.97 ($6,152M debt
/ $6,350M equity), and failed "cash covers 1+ yr of debt" using $1.6B of bare
cash against $5.9B of total debt - when KLA holds $4,902M in cash and
short-term investments and has zero current portion of long-term debt.
"""
import pytest

import fundamentals_engine as fe
import finnhub_ttm


def test_finnhub_debt_to_equity_is_not_divided_by_100():
    """Finnhub returns this as a ratio, like currentRatioQuarterly."""
    parsed = finnhub_ttm._as_float(0.97)
    assert parsed == pytest.approx(0.97), "0.97 must not become 0.0097"


@pytest.mark.parametrize("raw,expected", [(0.97, 0.97), (4.84, 4.84), (0.0, 0.0)])
def test_de_ratio_passthrough(raw, expected):
    assert finnhub_ttm._as_float(raw) == pytest.approx(expected)


def test_as_float_is_none_safe():
    assert finnhub_ttm._as_float(None) is None
    assert finnhub_ttm._as_float("nonsense") is None


def test_current_ratio_conversion_untouched():
    """currentRatioQuarterly was always correct; do not regress it."""
    assert finnhub_ttm._pct_to_ratio(65.2) == pytest.approx(0.652)


# --- liquidity definition ---------------------------------------------------

def _raw(**kw):
    base = {
        "ticker": "TEST", "revenue": [1000.0], "gross_profit": [600.0],
        "operating_income": [400.0], "net_income": [300.0], "diluted_eps": [3.0],
        "total_assets": [5000.0], "total_liabilities": [2000.0], "total_equity": [3000.0],
        "current_assets": [2000.0], "current_liabilities": [700.0],
        "cash": [1600.0], "cash_and_st_investments": [4902.0], "short_term_debt": [0.0],
        "total_debt": [5900.0], "goodwill": [100.0], "intangible_assets": [50.0],
        "retained_earnings": [500.0], "operating_cash_flow": [400.0],
        "free_cash_flow": [350.0], "capex": [-30.0], "missing_fields": [],
    }
    base.update(kw)
    return base


def _row(result, key):
    for section in result["sections"]:
        for row in section["rows"]:
            if row["key"] == key:
                return row
    raise AssertionError(f"{key} not scored")


def test_termed_out_debt_no_longer_fails_liquidity():
    """KLA case: plenty of liquidity, nothing maturing inside 12 months."""
    row = _row(fe.score_fundamentals(_raw()), "cash_covers_debt")
    assert row["passed"] is True, "zero current maturities must not score as a fail"


def test_short_term_investments_count_as_liquidity():
    """Bare cash alone understated KLA's position by roughly 3x."""
    thin = fe.score_fundamentals(_raw(cash_and_st_investments=[1600.0], short_term_debt=[2000.0]))
    assert _row(thin, "cash_covers_debt")["passed"] is False
    rich = fe.score_fundamentals(_raw(cash_and_st_investments=[4902.0], short_term_debt=[2000.0]))
    assert _row(rich, "cash_covers_debt")["passed"] is True


def test_real_near_term_maturities_still_fail():
    """The check must not become a rubber stamp."""
    row = _row(fe.score_fundamentals(_raw(cash_and_st_investments=[500.0], short_term_debt=[3000.0])),
               "cash_covers_debt")
    assert row["passed"] is False


def test_missing_current_portion_falls_back_to_total_debt():
    """Absent disclosure must not silently pass; it reverts to the strict test."""
    row = _row(fe.score_fundamentals(_raw(short_term_debt=[None], cash_and_st_investments=[1600.0])),
               "cash_covers_debt")
    assert row["passed"] is False, "no disclosure should fall back to the stricter comparison"


def test_missing_st_investments_falls_back_to_cash():
    result = fe.score_fundamentals(_raw(cash_and_st_investments=[None], short_term_debt=[100.0]))
    assert _row(result, "cash_covers_debt")["passed"] is True
