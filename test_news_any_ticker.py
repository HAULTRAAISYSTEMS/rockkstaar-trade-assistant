"""News for any ticker, not just the scanner's capped universe.

The Elite News Scanner fetched 18 tickers (watchlist first, then a fixed set
of majors), so searching CEG found "No headlines match this filter". Now the
search fetches the typed ticker on demand, and the scanner also carries
Finnhub's market-wide stream.
"""
import io
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import app as legacy
import intel_engine
import web_app
from news_fetcher import CatalystNews


def _news(ticker, n=4, source="finnhub"):
    now = datetime.now(timezone.utc)
    arts = tuple({"headline": f"{ticker} story {i}", "source": "Reuters",
                  "url": f"https://example.com/{ticker}/{i}", "summary": "",
                  "image": "", "published_at": (now - timedelta(minutes=10 * (i + 1))).isoformat()}
                 for i in range(n))
    return CatalystNews(headlines=[a["headline"] for a in arts], summary="", categories=[],
                        freshness_minutes=10, source=source, articles=arts)


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    intel_engine.clear_intel_cache()
    monkeypatch.setattr(intel_engine, "_get_watchlist_tickers", lambda: ["NVDA"])
    yield
    intel_engine.clear_intel_cache()


def test_any_ticker_is_fetched_on_demand(monkeypatch):
    calls = []
    import news_fetcher
    monkeypatch.setattr(news_fetcher, "fetch_headlines", lambda t: calls.append(t) or _news(t))
    items = intel_engine.fetch_ticker_news("ceg")
    assert calls == ["CEG"] and len(items) == 4
    assert all(i["ticker"] == "CEG" for i in items)
    assert items[0]["time"] == "10m ago" and items[1]["time"] == "20m ago"   # each story's own age
    intel_engine.fetch_ticker_news("CEG")
    assert calls == ["CEG"]                                                   # cached


def test_nothing_found_is_not_cached(monkeypatch):
    import news_fetcher
    from news_fetcher import _EMPTY
    calls = []
    monkeypatch.setattr(news_fetcher, "fetch_headlines", lambda t: calls.append(t) or _EMPTY)
    assert intel_engine.fetch_ticker_news("ZZZZ") == []
    intel_engine.fetch_ticker_news("ZZZZ")
    assert calls == ["ZZZZ", "ZZZZ"]


def test_general_market_stream(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    ts = int(datetime.now(timezone.utc).timestamp()) - 300
    rows = [{"headline": "Constellation Energy wins data-center power deal", "related": "CEG",
             "source": "Reuters", "url": "https://x/1", "datetime": ts, "summary": "", "image": ""},
            {"headline": "Stocks rise as yields ease", "related": "", "source": "CNBC",
             "url": "https://x/2", "datetime": ts - 60, "summary": "", "image": ""}]
    monkeypatch.setattr(intel_engine, "_fh_urlopen", lambda url, timeout=8: io.BytesIO(json.dumps(rows).encode()))
    items = intel_engine.fetch_general_market_news()
    assert [i["ticker"] for i in items] == ["CEG", "MARKET"]
    assert items[0]["time"] == "5m ago"


def test_general_stream_needs_a_key(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    assert intel_engine.fetch_general_market_news() == []


def test_the_scanner_includes_the_market_stream(monkeypatch):
    import news_fetcher
    monkeypatch.setattr(news_fetcher, "fetch_headlines", lambda t: _news(t, 1))
    monkeypatch.setattr(intel_engine, "fetch_general_market_news", lambda: [
        {"ticker": "CEG", "headline": "CEG general story", "impact": "MEDIUM", "reason": "",
         "source": "Reuters", "url": "", "image": "", "summary": "", "published_at": "",
         "time": "5m ago", "on_watchlist": False}])
    news = intel_engine.fetch_market_news(["SPY"])
    assert any(n["headline"] == "CEG general story" for n in news)


@pytest.fixture
def client():
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
    return c


def test_the_endpoint_renders_story_cards(client, monkeypatch):
    import news_fetcher
    monkeypatch.setattr(news_fetcher, "fetch_headlines", lambda t: _news(t, 2))
    d = client.get("/api/intel/news/CEG").get_json()
    assert d["ok"] and d["count"] == 2 and d["ticker"] == "CEG"
    assert "CEG story 0" in d["html"] and "intel-news-item" in d["html"]


def test_the_endpoint_rejects_junk(client):
    assert client.get("/api/intel/news/" + "A" * 12).status_code == 400


def test_the_page_searches_live():
    page = open("templates/intel.html").read()
    assert "/api/intel/news/" in page and 'id="intel-ticker-news"' in page
