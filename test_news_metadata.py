"""A headline you cannot attribute or go and read is barely news.

The provider returns a publisher, a link and a publish time with every story.
The fetcher kept only the title, so every surface downstream showed bare
headlines: no source, no date, nothing to click. These check that the metadata
survives collection, that both yfinance payload shapes are read, and that rows
already stored as bare strings keep rendering.
"""
import os

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

from data_fetcher import _news_item, headline_text


class TestTheOlderPayloadShape:
    """yfinance 0.2.5x returns these fields flat on the item."""

    RAW = {"title": "Chips rally on memory demand", "publisher": "Reuters",
           "link": "https://example.com/a", "providerPublishTime": 1788480000}

    def test_the_headline_survives(self):
        assert _news_item(self.RAW)["headline"] == "Chips rally on memory demand"

    def test_the_publisher_survives(self):
        assert _news_item(self.RAW)["source"] == "Reuters"

    def test_the_link_survives(self):
        assert _news_item(self.RAW)["url"] == "https://example.com/a"

    def test_the_timestamp_becomes_a_date(self):
        assert _news_item(self.RAW)["published"].count("-") == 2


class TestTheNewerPayloadShape:
    """Later versions nest everything under a content key."""

    RAW = {"content": {
        "title": "Fed holds rates",
        "provider": {"displayName": "Bloomberg"},
        "canonicalUrl": {"url": "https://example.com/b"},
        "pubDate": "2026-09-04T13:30:00Z"}}

    def test_the_headline_is_found(self):
        assert _news_item(self.RAW)["headline"] == "Fed holds rates"

    def test_the_provider_display_name_is_found(self):
        assert _news_item(self.RAW)["source"] == "Bloomberg"

    def test_a_url_wrapped_in_an_object_is_unwrapped(self):
        assert _news_item(self.RAW)["url"] == "https://example.com/b"

    def test_the_publish_date_is_found(self):
        assert _news_item(self.RAW)["published"].startswith("2026-09-04")

    def test_a_click_through_url_is_accepted_too(self):
        raw = {"content": {"title": "x", "clickThroughUrl": "https://example.com/c"}}
        assert _news_item(raw)["url"] == "https://example.com/c"


class TestBadInput:
    @pytest.mark.parametrize("raw", [
        None, "a string", 42, {}, {"title": ""}, {"title": "   "},
        {"content": {"title": ""}},
    ])
    def test_a_story_with_no_headline_is_dropped(self, raw):
        assert _news_item(raw) is None

    def test_a_headline_with_nothing_else_still_comes_through(self):
        item = _news_item({"title": "Bare"})
        assert item["headline"] == "Bare"
        assert item["source"] == "" and item["url"] == ""

    @pytest.mark.parametrize("stamp", ["not a number", None, -1e30])
    def test_an_unreadable_timestamp_does_not_raise(self, stamp):
        assert _news_item({"title": "x", "providerPublishTime": stamp})["published"] == ""


class TestBothShapesStillRender:
    """Rows written before this change are bare strings and stay that way
    until the ticker is next refreshed."""

    def test_a_record_yields_its_headline(self):
        assert headline_text({"headline": "From a record"}) == "From a record"

    def test_a_bare_string_yields_itself(self):
        assert headline_text("From a string") == "From a string"

    @pytest.mark.parametrize("value", [None, "", {}, {"headline": None}])
    def test_nothing_yields_an_empty_string_rather_than_raising(self, value):
        assert headline_text(value) == ""

    def test_the_stock_page_renders_both(self):
        page = open("templates/stock_detail.html").read()
        assert "headline is mapping" in page
        assert "headline.url" in page and "headline.source" in page

    def test_the_terminal_already_read_both(self):
        """_news_row has always handled a string and a dict."""
        import terminal_intelligence as TI
        assert TI._news_row("bare", "NVDA")["headline"] == "bare"
        assert TI._news_row({"headline": "rich", "source": "Reuters"},
                            "NVDA")["source"] == "Reuters"


class TestTheFetcherReturnsRecords:
    def test_it_maps_a_provider_response_to_records(self, monkeypatch):
        import data_fetcher

        class _Ticker:
            news = [
                {"title": "One", "publisher": "Reuters", "link": "https://x/1"},
                {"title": "Two", "publisher": "Bloomberg", "link": "https://x/2"},
                {"title": ""},
            ]

            def __init__(self, *a, **k):
                pass

        monkeypatch.setattr(data_fetcher, "_YF_AVAILABLE", True)
        monkeypatch.setattr(data_fetcher, "yf",
                            type("yf", (), {"Ticker": _Ticker}), raising=False)
        monkeypatch.setattr(data_fetcher, "_get_yf_session", lambda: None, raising=False)
        summary, headlines = data_fetcher.fetch_news_headlines("NVDA")
        assert summary == "One"
        assert [h["headline"] for h in headlines] == ["One", "Two"]
        assert headlines[0]["source"] == "Reuters"

    def test_a_provider_failure_still_returns_the_placeholder(self, monkeypatch):
        import data_fetcher
        monkeypatch.setattr(data_fetcher, "_YF_AVAILABLE", False)
        summary, headlines = data_fetcher.fetch_news_headlines("NVDA")
        assert summary and headlines
