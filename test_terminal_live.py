"""The Terminal's numbers move, and the tape is the first thing on the page.

Three faults, one page. The tape was rendered *below* the status line, so the
banner the user actually reads was never at the top. An entire second home
page — a hero, a search box, SPY/VIX tiles and a "Discover stocks" list —
was stacked above the Terminal at mobile widths, pushing the tape roughly
900px down and printing SPY and VIX a third time. And no page-level refresh
function was ever registered, so every price on the busiest screen in the app
was frozen at whatever the server rendered: open the tab before the bell and
it still showed the previous close at noon.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

import app as _app


TEMPLATE = Path("templates/terminal.html").read_text()


class TestTheLayout:
    def test_the_tape_comes_before_everything_else(self):
        assert TEMPLATE.index('class="tw-tape"') < TEMPLATE.index('class="tw-status"')
        assert TEMPLATE.index('class="tw-status"') < TEMPLATE.index('class="tw-body"')

    def test_the_duplicate_mobile_home_page_is_gone(self):
        """It was a second page's content, hero and all, stacked on this one."""
        for orphan in ("mobile-home", "mobile-discovery", "twDiscoveryTab"):
            assert orphan not in TEMPLATE

    def test_the_market_chips_are_printed_once(self):
        """SPY and VIX appeared in the tiles, the chips and the tape."""
        assert TEMPLATE.count("tw-chip-spy") == 2       # the element, and the JS
        assert TEMPLATE.count("tw-chip-vix") == 2

    def test_the_pill_does_not_repeat_the_navigations_market_badge(self):
        """The nav already says MARKET CLOSED; twice, inches apart, is clutter.

        This pill is about the quotes below it, so it names them.
        """
        assert _app.SESSION_LABELS["closed"] == "At the close"
        assert "Market closed" not in _app.SESSION_LABELS.values()

    def test_the_page_says_whether_the_tape_is_moving(self):
        assert 'id="tw-live"' in TEMPLATE
        assert 'data-session="{{ market_session }}"' in TEMPLATE
        assert '.tw-live[data-session="regular"]' in TEMPLATE

    def test_the_session_variable_does_not_shadow_flasks_session(self):
        """Passing session= to render_template broke the nav's session.get()."""
        assert "\n        session=" not in Path("app.py").read_text()


class TestTheLiveNumbers:
    def test_a_refresh_function_is_registered(self):
        """Without this the shared 4-second poller has nothing to call."""
        assert "window._arRefreshFn = twQuotesRefresh;" in TEMPLATE

    def test_the_tape_items_are_addressable_by_ticker(self):
        assert 'data-tape="{{ s.ticker }}"' in TEMPLATE
        assert "[data-tape-px]" in TEMPLATE

    def test_a_null_price_leaves_the_last_good_print_alone(self):
        """A gap in the feed must not blank the screen."""
        assert "keep the last good print" in TEMPLATE

    def test_clicking_a_row_after_a_refresh_uses_the_refreshed_price(self):
        """twSelect() reads data-price, so the poll has to update it too."""
        assert "row.dataset.price  = text;" in TEMPLATE


@pytest.fixture
def client():
    import web_app
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "test"
        sess["logged_in"] = True
    with patch.object(_app, "_auth_required", lambda *a, **k: False):
        yield c


class TestTheQuotesEndpoint:
    def test_it_answers(self, client):
        resp = client.get("/api/terminal/quotes")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_it_reports_the_session_and_a_label_for_it(self, client):
        body = client.get("/api/terminal/quotes").get_json()
        assert body["session"] in _app.SESSION_LABELS
        assert body["session_label"] == _app.SESSION_LABELS[body["session"]]

    def test_it_never_waits_on_a_price_fetch(self, client):
        """A four-second poll cannot hold a request open behind yfinance.

        The refresh is fired into a thread; the response is a database read.
        """
        with patch.object(_app, "_trigger_exec_state_refresh") as trigger, \
             patch.object(_app, "batch_refresh_exec_states") as blocking:
            client.get("/api/terminal/quotes")
        assert trigger.called
        assert not blocking.called

    def test_a_broken_watchlist_still_returns_the_chips(self, client):
        """Half a page of live numbers beats an error."""
        with patch.object(_app, "get_watchlist_stocks", side_effect=RuntimeError("db")):
            body = client.get("/api/terminal/quotes").get_json()
        assert body["ok"] is True
        assert body["quotes"] == []
        assert "regime" in body["chips"]

    def test_a_broken_market_context_still_returns_the_quotes(self, client):
        with patch.object(_app, "_get_mkt_ctx", side_effect=RuntimeError("cold")):
            body = client.get("/api/terminal/quotes").get_json()
        assert body["ok"] is True
        assert body["chips"] == {}

    def test_the_page_renders(self, client):
        assert client.get("/terminal").status_code == 200


class TestTodaysSetups:
    """A row of four dashes is not a setup."""

    def _panel(self, ranked):
        """The filter the Terminal applies before it takes the top three."""
        return [
            s for s in ranked
            if (s.get("entry_zone_display") or "—") != "—" and s.get("stop_level")
        ]

    def test_a_plan_with_no_levels_is_not_shown(self):
        ranked = [{"ticker": "SPY", "entry_zone_display": "—", "stop_level": None}]
        assert self._panel(ranked) == []

    def test_a_plan_with_an_entry_but_no_stop_is_not_shown(self):
        """Half a plan is worse than none — there is nothing to risk against."""
        ranked = [{"ticker": "MU", "entry_zone_display": "$88.10 – $89.40",
                   "stop_level": None}]
        assert self._panel(ranked) == []

    def test_a_real_plan_survives(self):
        row = {"ticker": "AMD", "entry_zone_display": "$477.00 – $479.50",
               "stop_level": 468.0}
        assert self._panel([row]) == [row]


class TestOnePercentageForOneStock:
    """SPY read -0.38% in the chart header and -0.39% in the tape beside it.

    The header's percentage is computed on the client from the chart's own
    bars. Without the official previous close it used the previous day's last
    five-minute bar as the baseline, which misses the closing auction — so the
    same stock's day change appeared twice on one screen with two values.
    """

    def test_the_quote_carries_the_official_previous_close(self, client):
        body = client.get("/api/terminal/quotes").get_json()
        for row in body["quotes"]:
            assert "prev_close" in row

    def test_the_watchlist_row_carries_it_too(self):
        """So the first chart load has a baseline before the first poll."""
        assert 'data-prevclose="' in TEMPLATE

    def test_the_chart_prefers_it_over_the_previous_days_last_bar(self):
        assert "var baseline=twPrevClose[twChartState.ticker];" in TEMPLATE
        assert "if(!isFinite(baseline)||!baseline)baseline=previous;" in TEMPLATE
