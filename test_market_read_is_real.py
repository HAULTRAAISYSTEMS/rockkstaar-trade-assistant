"""The command centre's read, when there is nothing to read.

get_market_context never blocks: on a cold cache, or after a failed refresh,
it hands back _empty_context() immediately and refreshes behind the request.
That empty context is not empty-looking. It carries

    regime       NEUTRAL
    regime_label Neutral
    qqq_trend    Unknown
    longs_ok     True
    shorts_ok    True

and generate_market_story read it happily, composing "Mixed market signals —
patience and selectivity required" with a sentiment badge, a regime chip and
permission chips. That is a confident market read assembled from no market
data, at the top of the first page of the app, granting long permission.

It is the same defect as a D that means "the feed was down" — except this one
is the first thing on the screen.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

import market_engine as me


ET = ZoneInfo("America/New_York")


def et(y, m, d, hh, mm=0):
    return dt.datetime(y, m, d, hh, mm, tzinfo=ET)


REAL = {
    "regime": "RISK_OFF", "regime_label": "Risk-Off",
    "vix_level": 18.6, "qqq_trend": "Below 20 EMA", "spy_trend": "Below 20 EMA",
    "leading_sectors": ["XLU"], "weak_sectors": ["XLY"],
    "longs_ok": False, "shorts_ok": True,
    "signal": "Indices below EMAs", "qqq_1d_pct": -2.5, "spy_1d_pct": -1.11,
    "fallback": False, "fetched_at": dt.datetime.now().isoformat(),
}


class TestAnEmptyContextIsNotANeutralMarket:
    def test_the_empty_context_admits_it(self):
        assert me._empty_context()["fallback"] is True
        assert me.context_is_real(me._empty_context()) is False

    def test_the_empty_regime_block_admits_it_too(self):
        """_empty_context spreads this, and _compute_regime returns it when
        the index prices never arrived — which is the case that slipped
        through, because the context builder had still run."""
        assert me._empty_regime()["fallback"] is True

    def test_a_real_snapshot_is_real(self):
        assert me.context_is_real(REAL) is True

    def test_prices_missing_means_the_builder_output_is_still_not_a_read(self):
        """_compute_regime hands back _empty_regime() with no prices, and the
        surrounding context used to be marked real simply because the builder
        ran to completion."""
        regime = me._compute_regime([], [], [])
        assert regime["fallback"] is True

    @pytest.mark.parametrize("ctx", [None, {}, {"regime": "NEUTRAL"}])
    def test_nothing_without_an_index_move_counts_as_a_read(self, ctx):
        assert me.context_is_real(ctx) is False

    def test_a_hand_built_context_with_real_moves_still_counts(self):
        """Contexts predating the flag — and the test fixtures — carry index
        moves and no `fallback` key. Those are real."""
        legacy = {k: v for k, v in REAL.items() if k not in ("fallback", "fetched_at")}
        assert me.context_is_real(legacy) is True


class TestTheStoryRefusesRatherThanInvents:
    def test_no_story_from_an_empty_context(self):
        assert me.generate_market_story(me._empty_context()) is None

    def test_it_used_to_produce_exactly_this(self):
        """Pinning the sentence down so the regression is unmistakable: the
        inputs that produced it are regime NEUTRAL and qqq_1d of None, which
        is what an empty context is."""
        empty = me._empty_context()
        assert empty["regime"] == "NEUTRAL"
        assert empty["qqq_1d_pct"] is None
        assert empty["longs_ok"] is True       # the part that granted permission
        assert me.generate_market_story(empty) is None

    def test_a_real_context_still_gets_its_story(self):
        story = me.generate_market_story(REAL)
        assert story is not None
        assert story["sentiment"] == "bearish"
        assert story["headline"]

    @pytest.mark.parametrize("ctx", [None, {}])
    def test_junk_does_not_raise(self, ctx):
        assert me.generate_market_story(ctx) is None


class TestTheDayChangeNamesTheRightSession:
    """Index day-changes come off daily candles, so the strip has to say which
    session it is quoting."""

    def test_premarket_is_the_previous_session(self):
        assert me._day_label(et(2026, 10, 19, 8, 30)) == "Friday"   # Monday

    def test_in_session_is_today(self):
        assert me._day_label(et(2026, 10, 19, 10, 0)) == "today"

    def test_after_the_close_is_still_today(self):
        """The regression. Outside 09:30–16:00 the label walked back one
        weekday unconditionally, so from 4 PM Monday until Tuesday's open the
        page called Monday's move Friday's — every weekday evening, which is
        exactly when the day gets reviewed."""
        assert me._day_label(et(2026, 10, 19, 16, 30)) == "today"
        assert me._day_label(et(2026, 10, 19, 17, 0)) == "today"
        assert me._day_label(et(2026, 10, 19, 23, 59)) == "today"

    def test_overnight_before_the_premarket_is_the_previous_session(self):
        assert me._day_label(et(2026, 10, 20, 2, 0)) == "Monday"    # Tuesday

    def test_the_weekend_is_friday(self):
        assert me._day_label(et(2026, 10, 17, 12, 0)) == "Friday"   # Saturday
        assert me._day_label(et(2026, 10, 18, 12, 0)) == "Friday"   # Sunday


class TestTheSnapshotSaysHowOldItIs:
    def test_a_fresh_snapshot_is_near_zero(self):
        ctx = {"fetched_at": dt.datetime.now().isoformat()}
        assert me.snapshot_age_minutes(ctx) < 1

    def test_an_hour_old_snapshot_reports_about_sixty(self):
        then = dt.datetime.now() - dt.timedelta(minutes=63)
        assert 62 <= me.snapshot_age_minutes({"fetched_at": then.isoformat()}) <= 64

    @pytest.mark.parametrize("ctx", [None, {}, {"fetched_at": None},
                                     {"fetched_at": "not a time"}])
    def test_an_unknown_age_is_none_not_zero(self, ctx):
        """Zero would read as "just now", which is the opposite of unknown."""
        assert me.snapshot_age_minutes(ctx) is None

    def test_the_empty_context_has_no_age(self):
        assert me.snapshot_age_minutes(me._empty_context()) is None


class TestTheRefreshEndpointReportsWhatItDid:
    def test_an_empty_context_is_not_a_successful_refresh(self):
        """`ok` was `bool(market)`, and an empty context is a populated dict —
        so a refresh that read nothing said "Market pulse refreshed" and
        handed the strip a NEUTRAL regime to paint."""
        from pathlib import Path
        src = Path("app.py").read_text()
        body = src[src.index("def api_opportunity_refresh"):]
        body = body[:body.index("\n@app.route")]
        assert '"ok": bool(market)' not in body
        assert '"ok": known' in body
        assert '"known": known' in body

    def test_the_client_will_not_paint_a_regime_over_a_dash(self):
        from pathlib import Path
        js = Path("templates/_liq_scripts.html").read_text()
        assert "market.known === false" in js

    def test_the_page_shows_a_dash_rather_than_a_regime_it_lacks(self):
        from pathlib import Path
        html = Path("templates/liquidity.html").read_text()
        assert "pulse.known" in html
        assert "not read yet" in html
