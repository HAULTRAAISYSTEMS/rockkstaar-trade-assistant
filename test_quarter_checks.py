"""Seven checks on one quarter, from figures the reader typed.

The annual scorecard reads EDGAR and asks whether the business is worth
owning. This asks what the latest quarter said, and four of its checks exist
nowhere else in the app: operating leverage, days sales outstanding, whether
EPS growth is the business or the buyback, and cash conversion year-to-date.

The figures are typed rather than fetched because reading a filing is the
skill. These tests pin the arithmetic and the bands against a worked example
— Meta's September quarter, the same inputs the original HTML drill was
built on — so a change to a threshold has to be deliberate.
"""
import pytest

import quarter_checks as qc


# The worked example. Every expected verdict below was produced by hand from
# the filing first, not read back out of this module.
META_Q3 = dict(
    rev=60801, revP=47516, cogs=11330, cogsP=8491,
    opex=30696, opexP=18584, opinc=18775, opincP=20441,
    ni=15848, niP=18337, sh=2566, shP=2570, eps=6.18, epsP=7.14,
    ca=449956, caP=366021, cl=188735, clP=148778,
    ar=21752, arP=19769, niy=42621, cfo=64088, capex=49113,
)


def by_key(result):
    return {c["key"]: c for c in result["checks"]}


@pytest.fixture
def meta():
    return by_key(qc.run(META_Q3))


class TestTheWorkedExample:
    @pytest.mark.parametrize("key,value,verdict", [
        ("cash_conversion",    "1.50",            "clean"),
        ("gross_margin",       "81.4%",           "watch"),
        ("operating_leverage", "+28.0% / +65.2%", "flag"),
        ("current_ratio",      "2.38",            "clean"),
        ("dso",                "32 days",         "clean"),
        ("share_count",        "-0.2%",           "clean"),
        ("free_cash_flow",     "14,975",          "clean"),
    ])
    def test_each_check(self, meta, key, value, verdict):
        assert meta[key]["value"] == value
        assert meta[key]["verdict"] == verdict

    def test_the_tally(self):
        assert qc.run(META_Q3)["counts"] == {"clean": 5, "watch": 1, "flag": 1}

    def test_costs_outrunning_sales_is_what_gets_flagged(self, meta):
        """Revenue +28%, operating expenses +65%. The quarter's real story."""
        assert "Costs outran sales" in meta["operating_leverage"]["says"]
        assert "Operating income -8.2%" in meta["operating_leverage"]["says"]


class TestReadingWhatSomebodyTyped:
    @pytest.mark.parametrize("text,expected", [
        ("1,234", 1234.0),
        ("$1,234", 1234.0),
        ("(1,234)", -1234.0),          # filings print negatives in parentheses
        (" 60801 ", 60801.0),
        ("6.18", 6.18),
        ("", None),
        ("   ", None),
        ("n/a", None),
        (None, None),
    ])
    def test_it(self, text, expected):
        assert qc._num(text) == expected

    def test_a_field_that_cannot_be_read_skips_its_check_rather_than_zeroing(self):
        """A check that grades a zero it invented is worse than one that skips."""
        result = by_key(qc.run(dict(META_Q3, cfo="not a number")))
        assert result["cash_conversion"]["skipped"]
        assert result["cash_conversion"]["verdict"] is None


class TestChecksThatCannotRunSayWhatTheyWanted:
    def test_an_empty_form_grades_nothing_and_explains_each_gap(self):
        result = qc.run({})
        assert result["graded"] == 0
        assert result["summary"] is None
        assert all(c["skipped"] for c in result["checks"])

    def test_zero_prior_operating_expenses_is_a_stated_skip_not_a_silence(self):
        """The original tested truthiness and dropped the check without a word.

        Silence on a results page reads as a pass.
        """
        result = by_key(qc.run(dict(META_Q3, opexP=0)))
        assert result["operating_leverage"]["skipped"]
        assert "operating expenses" in result["operating_leverage"]["skipped"]

    def test_every_check_carries_a_concept_to_explain_itself(self):
        import concepts
        for check in qc.run(META_Q3)["checks"]:
            assert check["concept"] in concepts.BY_SLUG


class TestTheBands:
    @pytest.mark.parametrize("cfo,niy,verdict", [
        (150, 100, "clean"),      # cash above profit
        (100, 100, "clean"),      # exactly one
        (90, 100, "watch"),
        (79, 100, "flag"),
    ])
    def test_cash_conversion(self, cfo, niy, verdict):
        assert by_key(qc.run({"cfo": cfo, "niy": niy}))["cash_conversion"]["verdict"] == verdict

    @pytest.mark.parametrize("ca,cl,verdict", [
        (150, 100, "clean"), (120, 100, "watch"), (90, 100, "flag"),
    ])
    def test_current_ratio(self, ca, cl, verdict):
        assert by_key(qc.run({"ca": ca, "cl": cl}))["current_ratio"]["verdict"] == verdict

    @pytest.mark.parametrize("ar,rev,verdict", [
        (10, 100, "clean"),       # 9 days
        (60, 100, "watch"),       # 54 days
        (90, 100, "flag"),        # 81 days
    ])
    def test_days_sales_outstanding(self, ar, rev, verdict):
        assert by_key(qc.run({"ar": ar, "rev": rev}))["dso"]["verdict"] == verdict

    def test_a_margin_holding_flat_is_not_a_warning(self):
        flat = {"rev": 100, "cogs": 20, "revP": 100, "cogsP": 20}
        assert by_key(qc.run(flat))["gross_margin"]["verdict"] == "clean"

    def test_negative_free_cash_flow_is_flagged_whatever_the_capex(self):
        result = by_key(qc.run({"cfo": 100, "capex": 150}))["free_cash_flow"]
        assert result["verdict"] == "flag"
        assert result["value"] == "-50"


class TestBuybacksVersusTheBusiness:
    def test_eps_rising_while_profit_falls_is_flagged(self):
        """The buyback is doing the work — that is worth saying out loud."""
        result = by_key(qc.run({
            "sh": 90, "shP": 100, "ni": 95, "niP": 100, "eps": 1.06, "epsP": 1.00,
        }))["share_count"]
        assert result["verdict"] == "flag"
        assert "outrunning profit" in result["says"]

    def test_matching_growth_says_the_business_did_it(self, meta):
        assert "the growth is the business" in meta["share_count"]["says"]

    def test_a_growing_share_count_is_dilution(self):
        result = by_key(qc.run({"sh": 110, "shP": 100}))["share_count"]
        assert result["verdict"] == "watch"
        assert "diluted" in result["says"]


class TestTheInferredDenominatorIsGone:
    """The original approximated year-to-date revenue.

    It scaled quarterly revenue by (year-to-date net income ÷ quarterly net
    income), which holds only while net margin is flat across the quarters —
    exactly when it is least likely to. Meta's Q3 came out as "capex 30.0% of
    revenue" from a denominator no filing contains.
    """

    def test_capex_intensity_is_omitted_when_ytd_revenue_is_not_given(self, meta):
        assert "of year-to-date revenue" not in (meta["free_cash_flow"]["basis"] or "")

    def test_and_stated_against_a_real_figure_when_it_is(self):
        result = by_key(qc.run(dict(META_Q3, revYtd=168000)))["free_cash_flow"]
        assert "29.2% of year-to-date revenue" in result["basis"]


class TestTheWorkedExampleIsOnTheFiling:
    """Every figure in Load a worked example, against Meta's 10-Q.

    The example shipped with two wrong numbers: Meta's TOTAL assets and TOTAL
    liabilities (449,956 and 188,735) where the current subtotals belong
    (125,475 and 56,379). That is the exact mistake the balance-sheet hint
    warns about, and it put the current ratio at 2.38 when the filing says
    2.23 — which the app's own annual scorecard had right all along.

    Source: META 10-Q for the period ended 2026-06-30.
      Three months ended June 30      2026        2025
        Revenue                     60,801      47,516
        Cost of revenue             11,330       8,491
        Total costs and expenses    42,026      27,075
        Income from operations      18,775      20,441
        Net income                  15,848      18,337
        Diluted EPS                   6.18        7.14
        Diluted shares               2,566       2,570
      Balance sheet            30 Jun 2026  31 Dec 2025
        Total current assets       125,475     108,722
        Total current liabilities   56,379      41,836
        Accounts receivable         21,752      19,769
      Six months ended June 30 2026
        Revenue                    117,111
        Net income                  42,621
        Operating cash flow         64,088
        Capex                       49,113
    """
    FILING = {
        "rev": 60801, "revP": 47516, "cogs": 11330, "cogsP": 8491,
        "opex": 30696, "opexP": 18584, "opinc": 18775, "opincP": 20441,
        "ni": 15848, "niP": 18337, "sh": 2566, "shP": 2570,
        "eps": 6.18, "epsP": 7.14,
        "ca": 125475, "caP": 108722, "cl": 56379, "clP": 41836,
        "ar": 21752, "arP": 19769,
        "niy": 42621, "cfo": 64088, "capex": 49113, "revYtd": 117111,
    }

    @staticmethod
    def example():
        """The figures the page actually loads."""
        import re
        from pathlib import Path
        page = Path("templates/fundamentals.html").read_text()
        block = page[page.index("const QD_EXAMPLE = {"):]
        block = block[:block.index("};")]
        out = {}
        for key, value in re.findall(r"(\w+):\s*([\d.]+)", block):
            out[key] = float(value) if "." in value else int(value)
        return out

    def test_every_figure_matches_the_filing(self):
        assert self.example() == self.FILING

    def test_operating_expenses_are_the_combined_line_less_cost_of_revenue(self):
        """Meta prints only "Total costs and expenses"; 42,026 includes COGS."""
        assert 42026 - 11330 == self.FILING["opex"]
        assert 27075 - 8491 == self.FILING["opexP"]

    def test_the_totals_are_not_mistaken_for_the_current_subtotals(self):
        loaded = self.example()
        assert loaded["ca"] != 449956, "that is Total assets"
        assert loaded["cl"] != 188735, "that is Total liabilities"

    def test_the_current_ratio_agrees_with_the_annual_scorecard(self):
        """The Analyze tab reads 2.23 latest and 2.60 at the last year end."""
        result = by_key(qc.run(self.FILING))["current_ratio"]
        assert result["value"] == "2.23"
        assert "prior 2.60" in result["basis"]

    def test_capex_intensity_uses_the_real_year_to_date_revenue(self):
        result = by_key(qc.run(self.FILING))["free_cash_flow"]
        assert "41.9% of year-to-date revenue" in result["basis"]

    def test_the_quarter_is_labelled_correctly(self):
        """Three months ended 30 June is Q2 for a calendar-year filer."""
        from pathlib import Path
        page = Path("templates/fundamentals.html").read_text()
        block = page[page.index("const QD_EXAMPLE = {"):]
        assert "per: 'Q2 2026'" in block[:block.index("};")]
