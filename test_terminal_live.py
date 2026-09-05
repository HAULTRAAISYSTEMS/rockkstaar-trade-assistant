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
