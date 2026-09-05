"""The command centre shows the four things it used to link away for.

Its header advertised Elite News, Earnings, Watchlist and Trade Setup, and all
four were anchors to other pages — a menu wearing the name of a command
centre. These check the sections are assembled from cached data, that one cold
feed leaves a gap rather than an error page, and that nothing here starts a
round trip on page load.
"""
import os

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import app as legacy
import web_app


SUMMARY = {
    "market_news": [
        {"ticker": "NVDA", "headline": "Chips rally", "source": "Reuters",
         "time_ago": "2h ago", "url": "https://example.com/a", "impact": "high",
         "why_it_matters": "Momentum names lead the tape."},
        {"ticker": "MARKET", "headline": "Fed minutes land Wednesday",
         "source": "Bloomberg", "impact": "medium"},
    ],
    "economic_events": [
        {"date": "2026-09-11", "date_label": "Sep 11", "time": "8:30 AM ET",
         "event": "Consumer Price Index (CPI)", "impact": "HIGH", "days_away": 6},
        {"date": "2026-10-14", "date_label": "Oct 14", "time": "8:30 AM ET",
         "event": "CPI", "impact": "HIGH", "days_away": 39},
    ],
}


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr(legacy._intel, "get_intel_summary", lambda: SUMMARY)
    monkeypatch.setattr(legacy, "get_active_wl_id", lambda: 1)
    monkeypatch.setattr(legacy, "get_watchlist_stocks", lambda wl: ["NVDA", "TSLA"])
    monkeypatch.setattr(legacy, "get_stock_data", lambda t: {
        "company_name": f"{t} Inc", "current_price": 100.0,
        "swing_score": 80 if t == "NVDA" else 0,
        "swing_setup_type": "Bull Flag", "swing_confidence": "A"})
    with web_app.app.test_request_context("/opportunity"):
        return legacy._command_center_context()


class TestThisWeek:
    def test_it_shows_what_is_coming(self, context):
        assert context["week"]

    def test_it_stops_at_seven_days(self, context):
        """A landing page is a read, not the whole calendar."""
        assert all((row.get("days_away") or 99) <= 7 for row in context["week"])
        assert not any("Oct 14" == row.get("date_label") for row in context["week"])

    def test_it_is_capped(self, context):
        assert len(context["week"]) <= legacy.COMMAND_WEEK


class TestEliteNews:
    def test_it_shows_stories(self, context):
        assert context["news"]

    def test_a_story_keeps_its_source_and_link(self, context):
        story = context["news"][0]
        assert story["source"] == "Reuters"
        assert story["url"] == "https://example.com/a"

    def test_a_watchlist_name_is_marked(self, context):
        story = next(n for n in context["news"] if n["ticker"] == "NVDA")
        assert story["on_watchlist"] is True

    def test_a_market_wide_story_is_not(self, context):
        story = next(n for n in context["news"] if n["ticker"] == "MARKET")
        assert story["on_watchlist"] is False

    def test_it_carries_why_it_matters(self, context):
        assert context["news"][0]["why"]

    def test_it_is_capped(self, context):
        assert len(context["news"]) <= legacy.COMMAND_NEWS


class TestSetupsAndWatchlist:
    def test_the_watchlist_is_listed(self, context):
        assert [r["ticker"] for r in context["watchlist"]] == ["NVDA", "TSLA"]

    def test_only_scored_names_are_setups(self, context):
        assert [r["ticker"] for r in context["setups"]] == ["NVDA"]

    def test_setups_come_best_first(self, monkeypatch):
        monkeypatch.setattr(legacy._intel, "get_intel_summary", lambda: {})
        monkeypatch.setattr(legacy, "get_active_wl_id", lambda: 1)
        monkeypatch.setattr(legacy, "get_watchlist_stocks", lambda wl: ["A", "B", "C"])
        scores = {"A": 40, "B": 90, "C": 65}
        monkeypatch.setattr(legacy, "get_stock_data",
                            lambda t: {"swing_score": scores[t]})
        with web_app.app.test_request_context("/opportunity"):
            setups = legacy._command_center_context()["setups"]
        assert [r["ticker"] for r in setups] == ["B", "C", "A"]


class TestOneColdFeedIsAGapNotAnErrorPage:
    def _blow_up(self, *a, **k):
        raise RuntimeError("feed unavailable")

    def test_a_failing_intel_feed_still_renders_the_rest(self, monkeypatch):
        monkeypatch.setattr(legacy._intel, "get_intel_summary", self._blow_up)
        monkeypatch.setattr(legacy, "get_active_wl_id", lambda: 1)
        monkeypatch.setattr(legacy, "get_watchlist_stocks", lambda wl: ["NVDA"])
        monkeypatch.setattr(legacy, "get_stock_data", lambda t: {"swing_score": 70})
        with web_app.app.test_request_context("/opportunity"):
            context = legacy._command_center_context()
        assert context["news"] == [] and context["week"] == []
        assert context["watchlist"]

    def test_a_failing_watchlist_still_renders_the_news(self, monkeypatch):
        monkeypatch.setattr(legacy._intel, "get_intel_summary", lambda: SUMMARY)
        monkeypatch.setattr(legacy, "get_active_wl_id", self._blow_up)
        with web_app.app.test_request_context("/opportunity"):
            context = legacy._command_center_context()
        assert context["news"]
        assert context["watchlist"] == []

    def test_everything_failing_still_returns_the_shape(self, monkeypatch):
        monkeypatch.setattr(legacy._intel, "get_intel_summary", self._blow_up)
        monkeypatch.setattr(legacy, "get_active_wl_id", self._blow_up)
        with web_app.app.test_request_context("/opportunity"):
            context = legacy._command_center_context()
        assert set(context) == {"week", "news", "setups", "watchlist",
                                "watchlist_name", "pulse", "next_up",
                                "news_refreshing", "news_note"}

    def test_no_watchlist_is_not_an_error(self, monkeypatch):
        monkeypatch.setattr(legacy._intel, "get_intel_summary", lambda: SUMMARY)
        monkeypatch.setattr(legacy, "get_active_wl_id", lambda: None)
        with web_app.app.test_request_context("/opportunity"):
            context = legacy._command_center_context()
        assert context["watchlist"] == [] and context["setups"] == []


class TestThePage:
    @pytest.fixture
    def client(self):
        c = web_app.app.test_client()
        with c.session_transaction() as sess:
            sess["user_id"] = 1
        return c

    def test_it_renders(self, client):
        assert client.get("/opportunity").status_code == 200

    def test_the_landing_route_is_the_same_page(self, client):
        assert client.get("/").status_code == 200

    @pytest.mark.parametrize("heading", ["This week", "Elite news",
                                         "Trade setups", "Watchlist"])
    def test_each_section_is_on_the_page(self, client, heading):
        assert heading in client.get("/opportunity").get_data(as_text=True)

    def test_the_briefing_stays_on_the_home_page(self, client):
        assert "ai-brief-card" in client.get("/opportunity").get_data(as_text=True)

    def test_the_deep_macro_panels_moved_off_the_home_page(self, client):
        """Worth having, not worth scrolling past every morning."""
        html = client.get("/opportunity").get_data(as_text=True)
        assert "liq-card-flow" not in html
        assert "liq-card-scanner" not in html
        assert "liq-card-opp-alerts" not in html

    def test_they_are_all_on_the_macro_page(self, client):
        html = client.get("/macro").get_data(as_text=True)
        assert "MARKET RISK METER" in html.upper()
        assert "MONEY FLOW" in html.upper()
        assert "liq-card-scanner" in html

    def test_the_home_page_keeps_their_headline(self, client):
        """Nothing is lost — the liquidity score and the sector rotation come
        up into the pulse strip, with a way through to the detail."""
        html = client.get("/opportunity").get_data(as_text=True)
        assert "Liquidity" in html
        assert "/macro" in html

    def test_nothing_leaked_an_unrendered_expression(self, client):
        html = client.get("/opportunity").get_data(as_text=True)
        assert "{{" not in html and "{%" not in html

    def test_the_four_link_pills_are_gone(self):
        page = open("templates/liquidity.html").read()
        assert "liq-command-strip" not in page

    def test_every_class_the_sections_use_is_styled(self):
        page = rendered()
        for name in ("cc-hero", "cc-eyebrow", "cc-dot", "cc-title",
                     "cc-pulse", "cc-pulse-cell", "cc-pulse-k", "cc-pulse-v",
                     "cc-pulse-next", "cc-split", "cc-trio",
                     "cc-card", "cc-head", "cc-empty", "cc-week",
                     "cc-week-row", "cc-when", "cc-what", "cc-impact",
                     "cc-story-glow", "cc-story-badges", "cc-chip",
                     "cc-story-headline", "cc-story-bullets", "cc-story-perms",
                     "cc-news", "cc-news-row", "cc-sym", "cc-tag",
                     "cc-headline", "cc-meta", "cc-why", "cc-rows", "cc-row",
                     "cc-row-sym", "cc-row-mid", "cc-row-price", "cc-score",
                     "cc-rise"):
            assert f".{name}" in page, name


class TestOneBadTickerDoesNotEmptyTheSection:
    def test_a_ticker_that_cannot_be_read_costs_only_its_own_row(self, monkeypatch):
        """It used to cost the whole watchlist, and the setups built from it."""
        monkeypatch.setattr(legacy._intel, "get_intel_summary", lambda: {})
        monkeypatch.setattr(legacy, "get_active_wl_id", lambda: 1)
        monkeypatch.setattr(legacy, "get_watchlist_stocks", lambda wl: ["OK", "BAD", "FINE"])

        def _lookup(ticker):
            if ticker == "BAD":
                raise RuntimeError("row unreadable")
            return {"company_name": f"{ticker} Inc", "swing_score": 50}

        monkeypatch.setattr(legacy, "get_stock_data", _lookup)
        with web_app.app.test_request_context("/opportunity"):
            context = legacy._command_center_context()
        assert [r["ticker"] for r in context["watchlist"]] == ["OK", "BAD", "FINE"]
        assert [r["ticker"] for r in context["setups"]] == ["OK", "FINE"]

    def test_the_unreadable_row_is_blank_rather_than_invented(self, monkeypatch):
        monkeypatch.setattr(legacy._intel, "get_intel_summary", lambda: {})
        monkeypatch.setattr(legacy, "get_active_wl_id", lambda: 1)
        monkeypatch.setattr(legacy, "get_watchlist_stocks", lambda wl: ["BAD"])
        monkeypatch.setattr(legacy, "get_stock_data",
                            lambda t: (_ for _ in ()).throw(RuntimeError("x")))
        with web_app.app.test_request_context("/opportunity"):
            row = legacy._command_center_context()["watchlist"][0]
        assert row["ticker"] == "BAD" and row["price"] is None and row["setup"] == ""


class TestWhichWatchlistIsShown:
    """The reader has nine lists and several are automatic buckets that sit
    empty. Defaulting to the first one showed "no tickers" to someone with
    twenty-two names in another list."""

    LISTS = [
        {"id": 1, "name": "A+ Ready"},
        {"id": 2, "name": "Setups Forming"},
        {"id": 3, "name": "Buy & Hold"},
    ]

    def _wire(self, monkeypatch, active, contents):
        monkeypatch.setattr(legacy, "get_active_wl_id", lambda: active)
        monkeypatch.setattr(legacy, "get_all_watchlists", lambda uid: self.LISTS)
        monkeypatch.setattr(legacy, "get_watchlist_stocks",
                            lambda wl: contents.get(wl, []))

    def test_the_active_list_wins_when_it_has_names(self, monkeypatch):
        self._wire(monkeypatch, 1, {1: ["NVDA"], 3: ["AAPL", "MSFT"]})
        with web_app.app.test_request_context("/opportunity"):
            tickers, name = legacy._command_watchlist()
        assert tickers == ["NVDA"] and name == "A+ Ready"

    def test_an_empty_active_list_falls_through_to_one_with_names(self, monkeypatch):
        self._wire(monkeypatch, 1, {1: [], 3: ["AAPL", "MSFT"]})
        with web_app.app.test_request_context("/opportunity"):
            tickers, name = legacy._command_watchlist()
        assert tickers == ["AAPL", "MSFT"] and name == "Buy & Hold"

    def test_it_takes_the_first_non_empty_list_predictably(self, monkeypatch):
        self._wire(monkeypatch, 1, {1: [], 2: ["TSLA"], 3: ["AAPL"]})
        with web_app.app.test_request_context("/opportunity"):
            tickers, name = legacy._command_watchlist()
        assert name == "Setups Forming"

    def test_all_lists_empty_reports_nothing_rather_than_guessing(self, monkeypatch):
        self._wire(monkeypatch, 1, {})
        with web_app.app.test_request_context("/opportunity"):
            tickers, name = legacy._command_watchlist()
        assert tickers == [] and name == "A+ Ready"

    def test_no_lists_at_all_is_not_an_error(self, monkeypatch):
        monkeypatch.setattr(legacy, "get_active_wl_id", lambda: None)
        monkeypatch.setattr(legacy, "get_all_watchlists", lambda uid: [])
        with web_app.app.test_request_context("/opportunity"):
            assert legacy._command_watchlist() == ([], "")

    def test_a_list_that_cannot_be_read_is_skipped_not_fatal(self, monkeypatch):
        monkeypatch.setattr(legacy, "get_active_wl_id", lambda: 1)
        monkeypatch.setattr(legacy, "get_all_watchlists", lambda uid: self.LISTS)

        def _stocks(wl):
            if wl == 2:
                raise RuntimeError("unreadable")
            return {1: [], 3: ["AAPL"]}.get(wl, [])

        monkeypatch.setattr(legacy, "get_watchlist_stocks", _stocks)
        with web_app.app.test_request_context("/opportunity"):
            tickers, name = legacy._command_watchlist()
        assert tickers == ["AAPL"] and name == "Buy & Hold"

    def test_the_page_names_the_list_it_settled_on(self, monkeypatch):
        """With nine lists the reader should never have to guess which one."""
        page = open("templates/liquidity.html").read()
        assert "watchlist_name" in page


def rendered(path="/"):
    """What actually reaches the browser.

    The page is composed from several templates now, so reading one file no
    longer tells you what the reader sees.
    """
    client = web_app.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1
    return client.get(path).get_data(as_text=True)


class TestTheLayout:
    """The page opened on a 400px narrative banner and named itself second,
    then ran a column of identical full-width slabs."""

    PAGE = rendered()

    def test_the_title_comes_before_the_market_story(self):
        assert self.PAGE.index("Command Center") < self.PAGE.index("market-story-banner")

    def test_the_header_is_a_band_not_a_card(self):
        assert "<header class=\"cc-hero\">" in self.PAGE
        assert "liq-hero" not in self.PAGE

    def test_the_pulse_strip_is_above_the_sections(self):
        assert self.PAGE.index("cc-pulse") < self.PAGE.index("Today's read")

    def test_the_story_and_the_week_share_a_row(self):
        assert "cc-split" in self.PAGE

    def test_the_three_lists_share_a_row(self):
        assert "cc-trio" in self.PAGE

    def test_the_market_story_hooks_survived_the_rearrangement(self):
        """The banner is populated by JS; renaming its ids would blank it."""
        for hook in ("market-story-banner", "ms-glow", "ms-sentiment-badge",
                     "ms-regime-badge", "ms-headline", "ms-bullets",
                     "ms-permissions"):
            assert f'id="{hook}"' in self.PAGE, hook

    def test_the_controls_survived(self):
        for hook in ("setMode('both')", "refreshAll()", 'id="liq-last-updated"'):
            assert hook in self.PAGE, hook

    def test_nothing_loops_except_the_live_dot_and_loading_skeletons(self):
        """A page that keeps moving is harder to read a number off. A pulse on
        the live indicator earns its place; a shimmer on a skeleton stops when
        the data lands. Anything else is decoration competing with the data."""
        import re
        looping = re.findall(r"animation:\s*([a-z-]+)[^;]*infinite", self.PAGE)
        allowed = {"cc-pulse-dot", "brief-shimmer", "elite-pulse", "liq-shimmer"}
        assert set(looping) <= allowed, set(looping) - allowed

    def test_reduced_motion_is_honoured(self):
        assert "prefers-reduced-motion" in self.PAGE

    def test_it_collapses_to_one_column_on_a_phone(self):
        assert "max-width: 900px" in self.PAGE


class TestThePulseStrip:
    @pytest.fixture
    def client(self):
        c = web_app.app.test_client()
        with c.session_transaction() as sess:
            sess["user_id"] = 1
        return c

    def test_it_renders_the_four_tone_setting_numbers(self, client):
        html = client.get("/").get_data(as_text=True)
        for label in ("Regime", "VIX", "SPY", "QQQ"):
            assert f">{label}<" in html

    def test_it_counts_down_to_the_next_event(self, monkeypatch):
        monkeypatch.setattr(legacy._intel, "get_intel_summary", lambda: SUMMARY)
        monkeypatch.setattr(legacy, "get_active_wl_id", lambda: None)
        monkeypatch.setattr(legacy, "get_all_watchlists", lambda uid: [])
        with web_app.app.test_request_context("/opportunity"):
            context = legacy._command_center_context()
        assert context["next_up"]["title"].startswith("Consumer Price Index")

    def test_it_prefers_a_high_impact_event_to_the_merely_nearest(self, monkeypatch):
        summary = {"economic_events": [
            {"date": "2026-09-06", "date_label": "Sep 6", "time": "9:00 AM ET",
             "event": "Minor print", "impact": "MEDIUM", "days_away": 1},
            {"date": "2026-09-08", "date_label": "Sep 8", "time": "8:30 AM ET",
             "event": "CPI", "impact": "HIGH", "days_away": 3},
        ]}
        monkeypatch.setattr(legacy._intel, "get_intel_summary", lambda: summary)
        monkeypatch.setattr(legacy, "get_active_wl_id", lambda: None)
        monkeypatch.setattr(legacy, "get_all_watchlists", lambda uid: [])
        with web_app.app.test_request_context("/opportunity"):
            context = legacy._command_center_context()
        assert context["next_up"]["title"] == "CPI"

    def test_no_events_means_no_countdown_rather_than_a_guess(self, monkeypatch):
        monkeypatch.setattr(legacy._intel, "get_intel_summary", lambda: {})
        monkeypatch.setattr(legacy, "get_active_wl_id", lambda: None)
        monkeypatch.setattr(legacy, "get_all_watchlists", lambda uid: [])
        with web_app.app.test_request_context("/opportunity"):
            assert legacy._command_center_context()["next_up"] is None

    def test_a_failing_market_context_leaves_the_strip_blank_not_broken(self, monkeypatch):
        monkeypatch.setattr(legacy, "_get_mkt_ctx",
                            lambda: (_ for _ in ()).throw(RuntimeError("x")))
        monkeypatch.setattr(legacy._intel, "get_intel_summary", lambda: {})
        monkeypatch.setattr(legacy, "get_active_wl_id", lambda: None)
        monkeypatch.setattr(legacy, "get_all_watchlists", lambda uid: [])
        with web_app.app.test_request_context("/opportunity"):
            assert legacy._command_center_context()["pulse"] == {}


class TestTheStripReadsAsEnglish:
    @pytest.fixture
    def client(self):
        c = web_app.app.test_client()
        with c.session_transaction() as sess:
            sess["user_id"] = 1
        return c

    def test_the_regime_is_not_shown_as_a_key(self, client, monkeypatch):
        """It rendered "Risk_on" — the underscore is how the value is stored,
        not how it is read."""
        monkeypatch.setattr(legacy, "_get_mkt_ctx", lambda: {"regime": "risk_on"})
        html = client.get("/").get_data(as_text=True)
        assert "Risk On" in html
        assert "Risk_on" not in html and "Risk_On" not in html

    def test_a_hyphenated_regime_reads_the_same_way(self, client, monkeypatch):
        monkeypatch.setattr(legacy, "_get_mkt_ctx", lambda: {"regime": "Risk-Off"})
        html = client.get("/").get_data(as_text=True)
        assert "Risk Off" in html

    def test_the_loading_notice_does_not_sit_above_the_title(self):
        """The first thing the page said was that a lower panel was fetching."""
        page = rendered()
        assert page.index("Command Center") < page.index('id="liq-status-banner"')


class TestTheNewsSectionAsksToBeFilled:
    """The intel caches live in memory, so every deploy empties them. The
    section rendered "nothing cached yet" until something asked for a refill,
    and only startup ever did — so the news stayed blank for anyone who did not
    happen to visit the Intel page first."""

    def _wire(self, monkeypatch, stories, calls, status=None):
        monkeypatch.setattr(legacy._intel, "get_intel_summary",
                            lambda: {"market_news": stories,
                                     "news_status": status or {}})
        monkeypatch.setattr(legacy._intel, "trigger_background_refresh",
                            lambda: calls.append(1))
        monkeypatch.setattr(legacy, "get_active_wl_id", lambda: None)
        monkeypatch.setattr(legacy, "get_all_watchlists", lambda uid: [])

    def test_an_empty_feed_asks_for_a_refill(self, monkeypatch):
        calls = []
        self._wire(monkeypatch, [], calls)
        with web_app.app.test_request_context("/opportunity"):
            context = legacy._command_center_context()
        assert calls == [1]
        assert context["news_refreshing"] is True

    def test_a_full_feed_asks_for_nothing(self, monkeypatch):
        calls = []
        self._wire(monkeypatch, [{"headline": "A story", "ticker": "NVDA"}], calls)
        with web_app.app.test_request_context("/opportunity"):
            context = legacy._command_center_context()
        assert calls == []
        assert context["news_refreshing"] is False

    def test_the_request_never_blocks_the_page(self, monkeypatch):
        """It is fire-and-forget: a refill that raises must not cost the load."""
        monkeypatch.setattr(legacy._intel, "get_intel_summary", lambda: {})
        monkeypatch.setattr(legacy._intel, "trigger_background_refresh",
                            lambda: (_ for _ in ()).throw(RuntimeError("busy")))
        monkeypatch.setattr(legacy, "get_active_wl_id", lambda: None)
        monkeypatch.setattr(legacy, "get_all_watchlists", lambda uid: [])
        with web_app.app.test_request_context("/opportunity"):
            context = legacy._command_center_context()
        assert context["news_refreshing"] is False

    def test_the_empty_state_says_what_is_happening(self, monkeypatch):
        """"Nothing cached" to someone who cannot act on it is a shrug."""
        calls = []
        self._wire(monkeypatch, [], calls)
        client = web_app.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = 1
        html = client.get("/opportunity").get_data(as_text=True)
        assert "Fetching catalysts now" in html

    def test_it_prefers_the_engines_own_explanation(self, monkeypatch):
        """The engine knows whether it is mid-refresh, missing a key, or
        holding a provider error. That beats a guess made from an empty list."""
        calls = []
        self._wire(monkeypatch, [], calls, status={
            "configured": True,
            "message": "News refresh is running. This page will update when "
                       "provider responses arrive."})
        with web_app.app.test_request_context("/opportunity"):
            note = legacy._command_center_context()["news_note"]
        assert "provider responses arrive" in note

    def test_a_provider_error_is_shown_rather_than_hidden(self, monkeypatch):
        calls = []
        self._wire(monkeypatch, [], calls,
                   status={"configured": True, "last_error": "Finnhub 429 rate limited"})
        with web_app.app.test_request_context("/opportunity"):
            note = legacy._command_center_context()["news_note"]
        assert "429" in note

    def test_a_missing_api_key_says_which_keys(self, monkeypatch):
        """The one empty state the reader can actually do something about."""
        calls = []
        self._wire(monkeypatch, [], calls, status={"configured": False})
        with web_app.app.test_request_context("/opportunity"):
            note = legacy._command_center_context()["news_note"]
        assert "FINNHUB_API_KEY" in note

    def test_no_status_still_says_something_useful(self, monkeypatch):
        calls = []
        self._wire(monkeypatch, [], calls, status={})
        with web_app.app.test_request_context("/opportunity"):
            note = legacy._command_center_context()["news_note"]
        assert note


class TestTheHeaderRow:
    def test_the_timestamp_shares_the_row_with_the_controls(self):
        """flex-wrap sent it to a line of its own the moment the row ran short
        of space, so the header grew a mostly-empty row."""
        css = open("templates/_liq_scripts.html").read()
        assert ".cc-hero-right { display: flex; flex-wrap: nowrap;" in css

    def test_it_shrinks_rather_than_wrapping(self):
        css = open("templates/_liq_scripts.html").read()
        block = css[css.index(".cc-updated {"):css.index(".cc-updated {") + 300]
        assert "text-overflow: ellipsis" in block
        assert "min-width: 0" in block

    def test_a_phone_is_still_allowed_to_wrap(self):
        css = open("templates/_liq_scripts.html").read()
        assert ".cc-hero-right { flex-wrap: wrap }" in css
