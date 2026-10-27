"""How much of a grade is about the stock.

A swing score is seven weighted categories normalised to one number, and that
number becomes a letter on a card — the most authoritative thing on the page
and the least examinable. Every category degrades silently: compute_swing_score
reads its inputs with data.get(...) and falls through to a default when they
are absent, so a stock whose daily bars failed scores no pullback, no
extension penalty and no EMA confluence, and comes out a D.

The app's own logs say this happens:

    fetch_swing_data: insufficient daily bars for AAPL
    (yfinance=failed, chart_api tried) — EMA/fib skipped

A D meaning "no edge" and a D meaning "the price feed was down" are different
claims, and the card made them in identical type.
"""
import pytest

import score_provenance as sp
import scoring


FULL = {
    "trade_bias": "Long Bias",
    "daily_trend": "Bullish", "h4_trend": "Bullish",
    "daily_hh_hl": True, "h4_hh_hl": True, "daily_lh_ll": False,
    "in_demand_zone": True, "in_supply_zone": False,
    "fib_50": 100.0, "fib_618": 98.0,
    "pct_from_ema20": 1.2, "pct_from_ema50": 4.0,
    "distance_to_demand_pct": 0.5,
    "m15_confirmation": 2,
    "risk_reward": 3.1,
    "rel_volume": 1.8,
    "catalyst_score": 5,
}

# What the data actually looks like when yfinance fails: the daily bars are
# gone, so every level and the extension go with them.
FEED_DOWN = {k: v for k, v in FULL.items() if k not in (
    "fib_50", "fib_618", "pct_from_ema20", "pct_from_ema50",
    "distance_to_demand_pct", "in_demand_zone", "in_supply_zone")}


class TestAFullyFedScoreSaysNothing:
    def test_complete_data_produces_no_caveat(self):
        assert sp.coverage(FULL)["complete"] is True
        assert sp.caveat(FULL) == ""
        assert sp.trustworthy(FULL) is True

    def test_no_category_is_blind(self):
        assert sp.coverage(FULL)["blind"] == []


class TestTheFeedGoingDownIsNotTheStocksFault:
    def test_the_pullback_category_goes_completely_blind(self):
        """Zones, fib levels and both EMAs all come from the daily bars, so
        they vanish together — and scoring reads that as 'no pullback'."""
        cov = sp.coverage(FEED_DOWN)
        assert "pullback" in cov["blind"]
        assert "extension" in cov["blind"]

    def test_it_says_so_rather_than_letting_the_grade_speak(self):
        note = sp.caveat(FEED_DOWN)
        assert note
        assert "pullback quality" in note
        assert "extension penalty" in note
        assert "may be the data rather than the setup" in note

    def test_the_grade_is_marked_untrustworthy(self):
        assert sp.trustworthy(FEED_DOWN) is False

    def test_the_score_really_does_collapse_on_this_input(self):
        """Not a hypothetical. The same setup, minus the daily bars, drops
        far enough to change the letter — which is the whole argument for
        saying something."""
        full = scoring.compute_swing_score(FULL)
        starved = scoring.compute_swing_score(FEED_DOWN)
        assert starved.score < full.score
        assert scoring.compute_swing_grade(starved.score) != \
            scoring.compute_swing_grade(full.score)


class TestZeroIsAReadingNotAGap:
    """The same mistake that once dropped a breakeven EPS consensus of 0.00
    from the earnings calendar — exactly the companies where it mattered."""

    @pytest.mark.parametrize("key, value", [
        ("rel_volume", 0.0),
        ("pct_from_ema20", 0.0),
        ("m15_confirmation", 0),
        ("catalyst_score", 0),
        ("daily_hh_hl", False),
        ("in_demand_zone", False),
    ])
    def test_a_zero_counts_as_present(self, key, value):
        data = dict(FULL, **{key: value})
        assert sp._present(data, key) is True
        assert sp.coverage(data)["complete"] is True

    @pytest.mark.parametrize("missing", [None, "absent"])
    def test_none_and_absence_both_count_as_missing(self, missing):
        data = dict(FULL)
        if missing is None:
            data["rel_volume"] = None
        else:
            del data["rel_volume"]
        assert sp._present(data, "rel_volume") is False
        assert "volume" in sp.coverage(data)["blind"]


class TestPartialIsNotTheSameAsBlind:
    def test_losing_one_of_several_signals_is_still_an_assessment(self):
        """Pullback reads seven interchangeable levels. Losing the 50 EMA
        does not stop it assessing a pullback; losing all seven does."""
        data = dict(FULL)
        del data["pct_from_ema50"]
        cov = sp.coverage(data)
        assert "pullback" in cov["partial"]
        assert "pullback" not in cov["blind"]
        assert sp.trustworthy(data) is True

    def test_but_it_is_still_reported_as_incomplete(self):
        data = dict(FULL)
        del data["pct_from_ema50"]
        assert sp.coverage(data)["complete"] is False
        assert "Scored on" in sp.caveat(data)


class TestTheWordingClaimsOnlyWhatItKnows:
    def test_it_never_guesses_what_the_score_would_have_been(self):
        note = sp.caveat(FEED_DOWN)
        for invented in ("would have scored", "would be", "likely a", "probably"):
            assert invented not in note.lower()

    def test_one_blind_category_reads_as_singular(self):
        data = dict(FULL)
        data["rel_volume"] = None
        note = sp.caveat(data)
        assert "that category scored" in note
        assert "those categories" not in note

    def test_several_read_as_plural_and_are_listed(self):
        note = sp.caveat(FEED_DOWN)
        assert "those categories" in note
        assert " and " in note          # the list is joined readably


class TestItSurvivesWhateverTheCallerPasses:
    @pytest.mark.parametrize("junk", [None, {}, [], "string", 0])
    def test_nothing_raises(self, junk):
        cov = sp.coverage(junk)
        assert cov["complete"] is False
        assert sp.trustworthy(junk) is False
        assert sp.caveat(junk)

    def test_an_empty_dict_is_blind_everywhere(self):
        cov = sp.coverage({})
        assert len(cov["blind"]) == len(sp.CATEGORIES)
        assert cov["seen"] == 0

    def test_every_category_names_real_inputs_the_scorer_reads(self):
        """A map that drifts from the code it describes is worse than none."""
        from pathlib import Path
        src = Path("scoring.py").read_text()
        body = src[src.index("def compute_swing_score"):
                   src.index("def compute_swing_grade")]
        for cat in sp.CATEGORIES:
            for key in cat["inputs"]:
                assert f'"{key}"' in body, (cat["key"], key)
