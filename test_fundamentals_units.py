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


# --- EDGAR period selection -------------------------------------------------
# Prior-year gross and operating margins showed single digits where the filing
# gives ~60% and ~37%. _annual() filtered XBRL facts by FORM but not by period
# duration, and broke ties on accession number. A 10-K carries both the fiscal
# year and its fourth quarter for the same period end, so a quarterly GrossProfit
# could win and then be divided into annual revenue.

def fact(start=None, end="2026-06-30", val=1.0, accn="a-1", form="10-K"):
    row = {"end": end, "val": val, "accn": accn, "form": form}
    if start:
        row["start"] = start
    return row


def test_full_year_duration_is_annual():
    assert fe._is_annual_period(fact(start="2025-07-01")) is True


@pytest.mark.parametrize("start", ["2026-04-01", "2026-01-01", "2026-06-01"])
def test_sub_annual_durations_rejected(start):
    assert fe._is_annual_period(fact(start=start)) is False


def test_balance_sheet_instants_are_kept():
    """Instantaneous facts have no start date and must not be filtered out."""
    assert fe._is_annual_period(fact()) is True


def test_53_week_fiscal_year_accepted():
    """Retailers and some tech filers run 52/53-week years."""
    assert fe._is_annual_period(fact(start="2025-06-24", end="2026-06-30")) is True


def test_multi_year_duration_rejected():
    assert fe._is_annual_period(fact(start="2024-07-01", end="2026-06-30")) is False


def test_period_days_is_malformed_safe():
    assert fe._period_days({"start": "nonsense", "end": "2026-06-30"}) == 0
    assert fe._period_days({"end": "2026-06-30"}) == 0
    assert fe._period_days({}) == 0


def test_quarterly_fact_would_have_produced_a_single_digit_margin():
    """Guards the arithmetic that made this visible in the first place."""
    annual_gp, quarterly_gp, revenue = 8_324_000_000, 1_220_000_000, 13_579_000_000
    assert round(quarterly_gp / revenue * 100) == 9
    assert round(annual_gp / revenue * 100) == 61


# --- revenue streak ---------------------------------------------------------
# The label and the rubric both say "3+ consecutive years"; the check required
# only two. KLA passed on a two-year run while FY24 revenue had fallen 6.5%.

def _rev_raw(revs):
    return _raw(revenue=list(revs), gross_profit=[], operating_income=[])


def _streak_row(revs):
    return _row(fe.score_fundamentals(_rev_raw(revs)), "revenue_growth")


def test_broken_streak_no_longer_passes():
    """KLA: FY24 declined, so the run is two years, not three."""
    assert _streak_row([13579., 12160., 9812., 10496., 9212.])["passed"] is False


def test_three_consecutive_years_passes():
    assert _streak_row([140., 130., 120., 110., 100.])["passed"] is True


def test_latest_year_declining_fails():
    assert _streak_row([120., 130., 110., 100.])["passed"] is False


def test_working_line_states_the_streak():
    assert "2 consecutive years of growth" in _streak_row([13579., 12160., 9812., 10496.])["working"]
    assert "most recent year declined" in _streak_row([120., 130., 110., 100.])["working"]


def test_short_history_judged_on_what_exists():
    """Two data points cannot prove three years; judge what is available."""
    assert _streak_row([120., 100.])["passed"] is True
    assert _streak_row([100., 120.])["passed"] is False


# --- arithmetic layer -------------------------------------------------------

def test_every_computable_row_shows_its_working():
    result = fe.score_fundamentals(_raw())
    need = {"current_ratio", "debt_to_equity", "cash_covers_debt", "goodwill_ratio",
            "fcf_positive", "fcf_vs_net_income", "capex_ratio"}
    for section in result["sections"]:
        for row in section["rows"]:
            if row["key"] in need:
                assert row.get("working"), f"{row['key']} has no working shown"
                need.discard(row["key"])
    assert not need, f"rows never scored: {need}"


def test_working_shows_the_actual_division():
    row = _row(fe.score_fundamentals(_raw()), "current_ratio")
    assert "/" in row["working"] and "=" in row["working"]


def test_working_is_blank_not_broken_when_data_missing():
    result = fe.score_fundamentals(_raw(current_assets=[None], current_liabilities=[None]))
    assert _row(result, "current_ratio")["working"] == ""


# --- working lines must be internally consistent ----------------------------
# The ROE row read "$4.83B net income / $6.35B equity = 85.4%". That division is
# 76.1%. The inputs were computed from the balance sheet while the result came
# from the TTM provider, which uses average equity and trailing income.
# Arithmetic on screen that does not produce its own stated result is worse than
# no arithmetic at all.

def test_roe_working_line_arithmetic_is_self_consistent():
    row = _row(fe.score_fundamentals(_raw(net_income=[4831.0], total_equity=[6350.0], roe=85.4)), "roe")
    working = row["working"]
    assert "76.1%" in working, "the stated division must show its own true result"
    assert "provider" in working, "the TTM figure must be labelled as the provider's"


def test_roe_without_provider_shows_plain_division():
    raw = _raw(net_income=[4831.0], total_equity=[6350.0])
    raw.pop("roe", None)
    row = _row(fe.score_fundamentals(raw), "roe")
    assert "provider" not in row["working"]
    assert "76.1%" in row["working"]


def test_roic_is_labelled_as_provider_ttm():
    row = _row(fe.score_fundamentals(_raw(roic=41.9)), "roic")
    assert "41.9%" in row["working"] and "provider" in row["working"]


def test_no_working_line_claims_a_division_it_cannot_support():
    """Any line containing '=' must not pair inputs with an unrelated result."""
    import re
    result = fe.score_fundamentals(_raw(net_income=[4831.0], total_equity=[6350.0], roe=85.4))
    for section in result["sections"]:
        for row in section["rows"]:
            w = row.get("working") or ""
            for chunk in w.split(". "):
                m = re.search(r"\$?([\d,.]+)([BM])?\s*/\s*\$?([\d,.]+)([BM])?\s*=\s*([\d.]+)%", chunk)
                if not m:
                    continue
                num, nu, den, du, stated = m.groups()
                scale = {"B": 1e9, "M": 1e6, None: 1.0}
                got = (float(num.replace(",", "")) * scale[nu]) / (float(den.replace(",", "")) * scale[du]) * 100
                assert abs(got - float(stated)) < 0.5, f"{chunk!r} does not compute to {stated}%"


# --- yfinance income-statement row matching ---------------------------------
# Two defects in one helper. Callers chained alternatives with
# `_row(a) or _row(b)`, but `or` on a pandas Series raises ValueError, and the
# whole block sits under a bare `except Exception` - so any successful match
# silently discarded the entire income statement. Separately, a bare substring
# test let "ebit" match "ebitda".

import pandas as pd


def _inc():
    return pd.DataFrame(
        [[8324e6, 7407e6], [5661e6, 5016e6], [6800e6, 6100e6], [4831e6, 4062e6], [13579e6, 12160e6]],
        index=["Gross Profit", "Operating Income", "EBITDA", "Net Income", "Total Revenue"],
        columns=["2026", "2025"])


def _norm(text):
    return str(text).lower().replace(" ", "").replace("_", "")


def _match(inc, *labels):
    """Mirrors the fixed helper in fetch_fundamentals_raw."""
    keys = {_norm(k): k for k in inc.index}
    for label in labels:
        key = keys.get(_norm(label))
        if key is not None:
            return inc.loc[key]
    for label in labels:
        norm = _norm(label)
        if len(norm) < 5:
            continue
        for key_norm, original in keys.items():
            if key_norm.startswith(norm):
                return inc.loc[original]
    return None


def test_series_or_chaining_raises():
    """The pattern the old code used, which the bare except then swallowed."""
    inc = _inc()
    with pytest.raises(ValueError):
        _ = inc.loc["Total Revenue"] or inc.loc["Net Income"]


def test_short_label_no_longer_matches_a_longer_metric():
    """'ebit' must not resolve to EBITDA."""
    assert _match(_inc(), "EBIT") is None or _match(_inc(), "EBIT").name != "EBITDA"


def test_operating_income_resolves_before_falling_back_to_ebit():
    assert _match(_inc(), "Operating Income", "OperatingIncome", "EBIT").name == "Operating Income"


@pytest.mark.parametrize("labels,expected", [
    (("Total Revenue", "Revenue"), "Total Revenue"),
    (("Gross Profit", "GrossProfit"), "Gross Profit"),
    (("Net Income", "NetIncome"), "Net Income"),
])
def test_each_line_item_resolves(labels, expected):
    assert _match(_inc(), *labels).name == expected


def test_alternatives_are_tried_without_boolean_coercion():
    """A missing first label falls through to the second and returns a Series."""
    inc = _inc().drop(index=["Total Revenue"])
    row = _match(inc, "Total Revenue", "Gross Profit")
    assert row is not None and row.name == "Gross Profit"


def test_absent_metric_returns_none():
    assert _match(_inc(), "Deferred Revenue") is None
