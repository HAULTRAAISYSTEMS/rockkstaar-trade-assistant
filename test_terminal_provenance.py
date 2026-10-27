"""The busiest page in the app, one price at a time.

The terminal's session badge answers "is the market open". Every price on the
page was rendered as though that were the same question as "did this quote
arrive during it". It is not: the watchlist rail reads one row per ticker out
of the database, and a ticker whose live fetch failed sits on a stored
snapshot. That row looked exactly like one that refreshed four seconds ago —
same weight, same colour, same absence of a timestamp.

Two narrower holes in the same place:

  - twApplyQuote returns early when a poll brings no price, to keep the last
    good print. Nothing marked the row, so a ticker that stopped being quoted
    stayed on screen indefinitely looking live.

  - get_market_tape returns stale items rather than blocking the page, so a
    frozen tape scrolled exactly like a moving one.
"""
from pathlib import Path

import pytest

import market_engine as me
import quote_provenance as qp


TERMINAL = Path("templates/terminal.html").read_text()
APP = Path("app.py").read_text()


def _fn(src: str, name: str) -> str:
    """The body of one JS function, up to the next top-level function."""
    start = src.index(f"function {name}(")
    rest = src[start + 10:]
    end = rest.find("\nfunction ")
    return rest if end == -1 else rest[:end]


class TestEachRowCarriesItsOwnProvenance:
    def test_the_rail_row_records_whether_its_quote_is_live(self):
        assert 'data-live="{{' in TERMINAL
        assert "data-asof=" in TERMINAL
        assert "data-source=" in TERMINAL

    def test_a_row_that_is_not_live_gets_a_marker(self):
        assert "tw-wl-stale" in TERMINAL
        assert "data-wlstale" in TERMINAL

    def test_the_marker_is_styled_as_a_caution_not_as_decoration(self):
        assert ".tw-wl-stale { font-style: normal; margin-left: 3px; color: var(--warn-500)" in TERMINAL

    def test_the_api_sends_it_per_row_not_once_per_page(self):
        body = APP[APP.index("def api_terminal_quotes"):]
        body = body[:body.index("\n@app.route")]
        for key in ('"live":', '"as_of":', '"source":', '"quote_note":'):
            assert key in body, key
        # Read off each row's own annotate(), not a page-level session value.
        assert 'annotated.get("quote")' in body


class TestThePollKeepsTheMarkerHonest:
    """The poll rewrites every price every four seconds. A marker left as
    static markup would end up attached to a number that had moved on."""

    def test_the_poller_reapplies_the_marker(self):
        assert "twApplyRowStamp" in _fn(TERMINAL, "twApplyQuote")

    def test_a_live_quote_removes_a_marker_that_is_no_longer_true(self):
        body = _fn(TERMINAL, "twApplyRowStamp")
        assert "if(q.live){" in body
        assert "mark.remove()" in body

    def test_a_poll_with_no_price_marks_the_row_instead_of_just_returning(self):
        """The regression: `if(px === null || !isFinite(px)) return;` with
        nothing before it, so a ticker that stopped being quoted kept its last
        print and said nothing."""
        body = _fn(TERMINAL, "twApplyQuote")
        head = body[:body.index("var text")]
        assert "twApplyRowStamp" in head
        assert "No price arrived on the last refresh" in head

    def test_the_selected_symbol_has_its_own_stamp(self):
        assert 'id="tw-c-stamp"' in TERMINAL
        assert "function twRenderStamp" in TERMINAL

    def test_the_stamp_follows_the_selection(self):
        """It describes one symbol, so clicking another row has to move it —
        otherwise it keeps describing whichever ticker loaded first."""
        assert "twRenderStamp" in _fn(TERMINAL, "twSelect")

    def test_the_stamp_never_claims_a_time_it_does_not_have(self):
        body = _fn(TERMINAL, "twRenderStamp")
        assert "time unknown" in body


class TestTheTapeSaysWhetherItIsMoving:
    def test_a_cold_tape_has_no_age_rather_than_an_age_of_zero(self):
        """Zero would read as "just refreshed", which is the opposite."""
        me._TAPE_CACHE.update(at=0.0, items=[])
        assert me.tape_age_seconds() is None

    def test_a_filled_tape_reports_its_age(self):
        import time
        me._TAPE_CACHE.update(at=time.time() - 120, items=[{"symbol": "SPY"}])
        age = me.tape_age_seconds()
        assert 119 <= age <= 122
        me._TAPE_CACHE.update(at=0.0, items=[])

    def test_items_without_a_timestamp_are_still_unknown(self):
        me._TAPE_CACHE.update(at=0.0, items=[{"symbol": "SPY"}])
        assert me.tape_age_seconds() is None
        me._TAPE_CACHE.update(at=0.0, items=[])

    def test_the_route_passes_it_and_never_raises(self):
        import app as legacy
        assert "tape_age_seconds=_tape_age()" in APP
        assert legacy._tape_age() is None or isinstance(legacy._tape_age(), float)

    def test_the_status_line_reports_a_tape_that_is_behind(self):
        assert "tape_age_seconds is not none and tape_age_seconds > 60" in TERMINAL
        assert "min behind" in TERMINAL
        assert "not loaded" in TERMINAL


class TestTheSessionBadgeAndTheQuoteStampAnswerDifferentQuestions:
    def test_an_open_market_does_not_make_an_old_quote_live(self):
        """Which is the whole reason the stamp exists beside the badge."""
        import datetime as dt
        et = qp._et_tz()
        wed_open = dt.datetime(2026, 10, 28, 11, 0, tzinfo=et)
        old = int(dt.datetime(2026, 10, 28, 9, 31, tzinfo=et).timestamp())
        assert qp.session(wed_open)["trading"] is True
        assert qp.describe({"price_source": "finnhub", "current_price": 1.0,
                            "quote_time": old}, now=wed_open)["live"] is False

    def test_a_stored_snapshot_is_not_live_even_mid_session(self):
        import datetime as dt
        et = qp._et_tz()
        wed_open = dt.datetime(2026, 10, 28, 11, 0, tzinfo=et)
        fresh = int(dt.datetime(2026, 10, 28, 10, 59, tzinfo=et).timestamp())
        d = qp.describe({"price_source": "finnhub", "current_price": 1.0,
                         "quote_time": fresh, "data_source": "stale_snapshot"},
                        now=wed_open)
        assert d["live"] is False
