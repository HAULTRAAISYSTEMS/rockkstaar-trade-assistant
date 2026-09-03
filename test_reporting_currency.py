"""A filer that does not report in dollars.

Every unit lookup read the "USD" bucket and nothing else. TSMC tags its 20-F
in TWD and supplies a USD convenience translation for some lines and some
years only - its USD revenue stopped a full year before its TWD revenue did -
so the card came out a year stale, with most rows N/A, under a dollar sign.

Getting the figures is only half of it. The price strip, the valuation history
and the trailing-twelve-month cash flow all come from dollar feeds, and each
one is divided into a figure from the filing somewhere on this page.
"""
from unittest.mock import patch

import pytest

import fundamentals_engine as fe
from test_edgar_pipeline import YEARS, STARTS, _Resp


def in_unit(unit, values, instant=False):
    facts = []
    for i, (start, end, val) in enumerate(zip(STARTS, YEARS, values)):
        fact = {"end": end, "val": val, "form": "20-F", "accn": f"a-{i}"}
        if not instant:
            fact["start"] = start
        facts.append(fact)
    return {"units": {unit: facts}}


def two_currencies():
    """TWD on every year, USD on the older three only — the real shape."""
    twd = [2894e9, 2161e9, 2263e9, 1587e9, 1339e9]
    usd_partial = [None, None, 70.5e9, 47.6e9, 35.7e9]

    def mixed(twd_vals, usd_vals):
        units = dict(in_unit("TWD", twd_vals)["units"])
        units["USD"] = [f for f in in_unit("USD", [v or 0 for v in usd_vals])["units"]["USD"]
                        if usd_vals[int(f["accn"].split("-")[1])] is not None]
        return {"units": units}

    return {"facts": {"ifrs-full": {
        "Revenue": mixed(twd, usd_partial),
        "ProfitLoss": in_unit("TWD", [1173e9, 838e9, 1016e9, 597e9, 517e9]),
        "Assets": in_unit("TWD", [6690e9, 5532e9, 4964e9, 3725e9, 2760e9], instant=True),
        "Equity": in_unit("TWD", [4470e9, 3694e9, 2934e9, 2263e9, 1849e9], instant=True),
        "CashAndCashEquivalents": in_unit("TWD", [2127e9, 1465e9, 1343e9, 1065e9, 660e9],
                                          instant=True),
    }}}


@pytest.fixture
def tsm():
    with patch.object(fe, "_edgar_cik", return_value=("0001046179", "TSMC")), \
         patch.object(fe._req_module, "get", return_value=_Resp(two_currencies())):
        yield fe.fetch_fundamentals_edgar("TSM")


class TestTheCurrencyIsChosen:
    def test_the_filers_own_currency_wins_on_coverage(self, tsm):
        assert tsm["currency"] == "TWD"

    def test_the_newest_year_is_no_longer_lost(self, tsm):
        """USD revenue stopped a year early; TWD did not."""
        assert tsm["revenue"][0] == pytest.approx(2894e9)
        assert len([x for x in tsm["revenue"] if x is not None]) == 5

    def test_the_symbol_matches(self, tsm):
        assert tsm["currency_symbol"] == "NT$"

    def test_a_us_filer_is_untouched(self):
        from test_edgar_pipeline import facts
        with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
             patch.object(fe._req_module, "get", return_value=_Resp(facts())):
            raw = fe.fetch_fundamentals_edgar("KLAC")
        assert raw["currency"] == "USD"
        assert raw["currency_symbol"] == "$"


class TestNothingIsMixed:
    def test_no_series_carries_the_other_currency(self, tsm):
        """The USD facts are a subset. Filling gaps from them would put
        70,500,000,000 dollars beside 2,894,000,000,000 New Taiwan dollars."""
        for value in tsm["revenue"]:
            assert value is None or value > 1e11

    def test_the_trailing_cash_flow_figure_is_not_borrowed(self, tsm):
        raw = dict(tsm)
        raw["_ttm_metrics"] = {"fcf_ttm_usd": 40e9}
        scored = fe.score_fundamentals(raw)
        rows = {r["key"]: r for section in scored["sections"] for r in section["rows"]}
        fcf_margin = rows.get("fcf_margin") or rows.get("fcf_positive")
        assert fcf_margin is None or "40" not in str(fcf_margin.get("value", ""))

    def test_a_us_filer_still_gets_the_trailing_figure(self):
        from test_edgar_pipeline import facts
        with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
             patch.object(fe._req_module, "get", return_value=_Resp(facts())):
            raw = fe.fetch_fundamentals_edgar("KLAC")
        raw["_ttm_metrics"] = {"fcf_ttm_usd": 4000e6}
        scored = fe.score_fundamentals(raw)
        assert scored is not None


class TestTheCardSaysSo:
    def test_the_figures_are_labelled_in_the_right_currency(self, tsm):
        scored = fe.score_fundamentals(tsm)
        values = [r["value"] for section in scored["sections"] for r in section["rows"]]
        assert any("NT$" in str(v) for v in values)
        assert not any(str(v).startswith("$") for v in values)

    def test_the_scorecard_carries_the_code(self, tsm):
        assert fe.score_fundamentals(tsm)["currency"] == "TWD"

    def test_the_chart_axis_uses_it(self, tsm):
        scored = fe.score_fundamentals(tsm)
        chart = next((c for c in scored["charts"] if c["key"] == "revenue_income"), None)
        if chart:
            assert any("NT$" in line["text"] for line in chart["gridlines"])


class TestTheSymbolTable:
    @pytest.mark.parametrize("code,symbol", [
        ("USD", "$"), ("TWD", "NT$"), ("EUR", "€"), ("GBP", "£"),
        ("JPY", "¥"),
    ])
    def test_known_codes(self, code, symbol):
        assert fe.currency_symbol(code) == symbol

    def test_an_unknown_code_prints_itself(self):
        """Better an ugly label than a number under the wrong currency mark."""
        assert fe.currency_symbol("XYZ") == "XYZ "

    def test_no_currency_falls_back_to_dollars(self):
        assert fe.currency_symbol(None) == "$"
