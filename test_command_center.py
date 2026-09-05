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
        assert set(context) == {"week", "news", "setups", "watchlist", "watchlist_name"}

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

    def test_the_pieces_the_reader_asked_to_keep_are_still_there(self, client):
        """Risk meter, sector money flow and the AI briefing."""
        html = client.get("/opportunity").get_data(as_text=True)
        assert "MARKET RISK METER" in html.upper()
        assert "MONEY FLOW" in html.upper()
        assert "ai-brief-card" in html

    def test_nothing_leaked_an_unrendered_expression(self, client):
        html = client.get("/opportunity").get_data(as_text=True)
        assert "{{" not in html and "{%" not in html

    def test_the_four_link_pills_are_gone(self):
        page = open("templates/liquidity.html").read()
        assert "liq-command-strip" not in page

    def test_every_class_the_sections_use_is_styled(self):
        page = open("templates/liquidity.html").read()
        for name in ("cc-card", "cc-pair", "cc-head", "cc-empty", "cc-week",
                     "cc-week-row", "cc-when", "cc-what", "cc-impact",
                     "cc-news", "cc-news-row", "cc-sym", "cc-tag",
                     "cc-headline", "cc-meta", "cc-why", "cc-rows", "cc-row",
                     "cc-row-sym", "cc-row-mid", "cc-row-score"):
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
