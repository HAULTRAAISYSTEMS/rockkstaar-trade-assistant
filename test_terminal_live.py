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
        assert "Keep the last good print" in TEMPLATE
        assert "!isFinite(px)" in TEMPLATE

    def test_but_it_no_longer_leaves_it_looking_live(self):
        """Keeping the print was right; keeping it unmarked was not. A ticker
        that stopped being quoted sat there indefinitely, indistinguishable
        from one still updating, because the early return happened before
        anything could say so."""
        assert "No price arrived on the last refresh" in TEMPLATE

    def test_clicking_a_row_after_a_refresh_uses_the_refreshed_price(self):
        """twSelect() reads data-price, so the poll has to update it too."""
        assert "row.dataset.price  = text;" in TEMPLATE

    def test_live_quote_patches_the_active_intraday_candle(self):
        assert "function twPatchActiveCandle(q,px)" in TEMPLATE
        assert "twChartState.candle.update(twCandleDatum(bar));" in TEMPLATE
        assert "q.market_session=data.session;q.as_of_epoch=data.server_epoch" in TEMPLATE

    def test_live_candle_keeps_the_providers_session_alignment(self):
        assert "lastTime+Math.floor((stamp-lastTime)/seconds)*seconds" in TEMPLATE

    def test_provider_candles_still_reconcile_every_thirty_seconds(self):
        assert "twScheduleChartRefresh();" in TEMPLATE
        assert "},30000);" in TEMPLATE


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
        assert body["session_label"] == _app._session_label(body["session"])
        assert isinstance(body["server_epoch"], int)

    def test_it_never_waits_on_a_price_fetch(self, client):
        """A four-second poll cannot hold a request open behind yfinance.

        The refresh is fired into a thread; the response is a database read.
        The watchlist is stubbed rather than read from whatever database the
        run happens to find — the point under test is which function the
        endpoint calls, not what this machine's dev data contains.
        """
        import threading
        request_thread = threading.get_ident()
        callers = []
        # Other tests can leave an asynchronous refresh running. Only a fetch
        # on this request's thread would make the polling endpoint block.
        def record_fetch(*args, **kwargs):
            callers.append(threading.get_ident())
            return {}

        with patch.object(_app, "get_active_wl_id", return_value=1), \
             patch.object(_app, "get_watchlist_stocks", return_value=["NVDA"]), \
             patch.object(_app, "get_all_stock_data",
                          return_value=[{"ticker": "NVDA", "current_price": 230.36}]), \
             patch.object(_app, "_trigger_exec_state_refresh") as trigger, \
             patch.object(_app, "batch_refresh_exec_states", side_effect=record_fetch):
            client.get("/api/terminal/quotes")
        assert trigger.called
        assert request_thread not in callers

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


class TestAfterHours:
    """After the bell a row's % is the move since today's close, not a mix of
    the after-hours price against yesterday's close (NVDA read -6.27% when it
    was +2.87% on the day and -8.88% after hours)."""

    def test_quotes_carry_the_after_hours_move(self, client):
        live = {"NVDA": {"price": 158.30, "prev_close": 168.88, "change_pct": -6.27, "ext": "post",
                         "day_close": 173.73, "epoch": 1_800_000_000, "source": "Schwab real-time"}}
        with patch.object(_app, "get_active_wl_id", return_value=1), \
             patch.object(_app, "get_watchlist_stocks", return_value=["NVDA"]), \
             patch.object(_app, "get_all_stock_data", return_value=[{"ticker": "NVDA", "current_price": 173.73}]), \
             patch.object(_app, "_trigger_exec_state_refresh"), \
             patch.object(_app._mkt, "live_quotes", return_value=live):
            q = client.get("/api/terminal/quotes").get_json()["quotes"][0]
        assert q["ext"] == "post" and q["regular_close"] == 173.73
        assert q["ext_pct"] == -8.88 and q["ext_chg"] == -15.43 and q["day_pct"] == 2.87

    def test_terminal_shows_extended_on_its_own_line_and_defaults_extended_on(self):
        assert 'id="tw-c-ext"' in TEMPLATE and "function twShowExt(" in TEMPLATE
        assert "twSavedExtended = _xv === null ? true : _xv === '1';" in TEMPLATE
        # Switching to 1D/4H must not switch the intraday Extended view off.
        assert "twChartState.extended = twExtendedPref && twChartState.interval !== '1d';" in TEMPLATE
        assert "chartExtended.v2" not in TEMPLATE
        assert "var extTag = {post:'AH ', pre:'PRE ', overnight:'O/N '}[q.ext]" in TEMPLATE


def test_live_feed_measures_after_hours_from_todays_close(client):
    live = {"NVDA": {"price": 160.19, "prev_close": 168.89, "ext": "post", "day_close": 173.73,
                     "epoch": 1_800_000_000, "source": "Schwab real-time"}}
    with patch.object(_app._mkt, "live_quotes", return_value=live), \
         patch.object(_app._mkt, "overnight_window_start", return_value=None):
        q = client.get("/api/terminal/live/NVDA").get_json()["quote"]
    assert q["regular_close"] == 173.73 and q["prev_close"] == 168.89


def test_selecting_a_row_after_hours_and_candle_timeout():
    assert "if(d.ext && d.ext !== 'pre' && Number(d.regclose) > 0){" in TEMPLATE
    assert "row.dataset.regclose = q.regular_close || '';" in TEMPLATE
    assert "candleCtl.abort(); }, 20000)" in TEMPLATE and "e.name==='AbortError'" in TEMPLATE


def test_extended_4h_candles_follow_the_et_clock():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from terminal_intelligence import aggregate_extended_4h
    et = ZoneInfo("America/New_York")
    def bar(h, m, px, sess):
        t = int(datetime(2026, 11, 11, h, m, tzinfo=et).timestamp())
        return {"time": t, "open": px, "high": px + 1, "low": px - 1, "close": px + .5, "volume": 10, "session": sess}
    bars = [bar(7, 0, 170, "premarket"), bar(9, 30, 171, "regular"), bar(15, 30, 174, "regular"),
            bar(16, 0, 173, "after_hours"), bar(19, 0, 160, "after_hours")]
    out = aggregate_extended_4h(bars)
    starts = [datetime.fromtimestamp(c["time"], et).hour for c in out]
    assert starts == [4, 8, 12, 16]
    assert [c["session"] for c in out] == ["premarket", "regular", "regular", "after_hours"]
    assert out[3]["open"] == 173 and out[3]["close"] == 160.5 and out[3]["low"] == 159


def test_4h_accepts_extended_and_the_toggle_allows_it():
    assert "supported=['1m','5m','15m','1h','4h']" in TEMPLATE
    assert '"4h":14400' in TEMPLATE
    src = Path(_app.__file__).read_text()
    assert 'interval not in {"1m", "5m", "15m", "1h", "4h"}' in src


def test_extended_bad_prints_are_filtered_not_drawn():
    """SPY at 756 drew after-hours wicks down to 715; TradingView doesn't."""
    from terminal_intelligence import clean_extended_wicks
    bars = []
    for i in range(30):
        px = 756 + (i % 3) * 0.05
        bars.append({"time": i * 300, "open": px, "high": px + 0.1, "low": px - 0.1, "close": px + 0.02,
                     "volume": 100, "session": "after_hours" if i >= 20 else "regular"})
    bars[24]["low"] = 715.0                               # stray print
    bars[26] = dict(bars[26], open=640.0, close=641.0, high=641.0, low=640.0)   # whole bar bad
    bars[5]["low"] = 740.0                                # regular session: left alone
    out = clean_extended_wicks(bars)
    by_t = {b["time"]: b for b in out}
    assert by_t[24 * 300]["low"] == min(bars[24]["open"], bars[24]["close"])
    assert 26 * 300 not in by_t
    assert by_t[5 * 300]["low"] == 740.0
    assert len(out) == 29
