import unittest
from pathlib import Path
from unittest.mock import patch

import intel_engine
from news_fetcher import CatalystNews


class IntelNewsTests(unittest.TestCase):
    def tearDown(self):
        intel_engine.clear_intel_cache()
        with intel_engine._news_state_lock:
            intel_engine._news_refreshing = False

    def test_news_only_cache_clear_preserves_other_feeds(self):
        intel_engine._cache.update({
            "market_news": {"ts": 1, "data": ["story"]},
            "earnings": {"ts": 1, "data": {"today": []}},
        })

        intel_engine.clear_intel_cache("market_news")

        self.assertNotIn("market_news", intel_engine._cache)
        self.assertIn("earnings", intel_engine._cache)

    def test_intel_page_injects_refresh_response_without_reloading(self):
        template = Path("templates/intel.html").read_text()

        self.assertIn("/api/intel/news-refresh", template)
        self.assertNotIn("location.reload()", template)

    @patch("intel_engine._get_watchlist_tickers", return_value=[])
    @patch("news_fetcher.fetch_headlines")
    def test_failed_refresh_enters_cooldown_instead_of_refreshing_forever(self, fetch, _watchlist):
        fetch.return_value = CatalystNews([], "", [], None, "none")

        self.assertEqual(intel_engine.fetch_market_news(["META"]), [])
        intel_engine._cset("earnings", {"today": [], "tomorrow": [], "this_week": [], "coming_up": []})
        intel_engine._cset("splits", [])
        intel_engine._cset("economic", [])
        summary = intel_engine.get_intel_summary()

        self.assertFalse(summary["news_status"]["refreshing"])
        self.assertIn("retry automatically", summary["news_status"]["message"])

    @patch("intel_engine._get_watchlist_tickers", return_value=["META"])
    @patch("news_fetcher.fetch_headlines")
    def test_ordinary_low_impact_provider_headline_remains_visible(self, fetch, _watchlist):
        fetch.return_value = CatalystNews(
            ["Meta announces a routine product update"],
            "Meta announces a routine product update",
            [], 4, "finnhub",
        )
        rows = intel_engine.fetch_market_news(["META"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["impact"], "LOW")
        self.assertEqual(rows[0]["source"], "Finnhub")

    @patch("intel_engine._get_watchlist_tickers", return_value=["META"])
    @patch("news_fetcher.fetch_headlines")
    def test_rich_article_metadata_reaches_news_cards(self, fetch, _watchlist):
        fetch.return_value = CatalystNews(
            ["Meta launches a new product"],
            "Meta launches a new product",
            ["product_launch"], 2, "finnhub",
            articles=({
                "headline": "Meta launches a new product",
                "source": "Reuters",
                "url": "https://example.com/meta",
                "image": "https://example.com/meta.jpg",
                "summary": "A provider-supplied story summary.",
                "published_at": "2026-08-10T20:00:00+00:00",
            },),
        )

        row = intel_engine.fetch_market_news(["META"])[0]

        self.assertEqual(row["source"], "Reuters")
        self.assertEqual(row["url"], "https://example.com/meta")
        self.assertEqual(row["image"], "https://example.com/meta.jpg")
        self.assertEqual(row["summary"], "A provider-supplied story summary.")

    @patch("intel_engine._get_watchlist_tickers", return_value=["USER1", "USER2"])
    @patch("news_fetcher.fetch_headlines")
    def test_news_fanout_is_bounded_and_watchlist_first(self, fetch, _watchlist):
        fetch.return_value = CatalystNews([], "", [], None, "none")

        intel_engine.fetch_market_news([f"T{i}" for i in range(30)])

        requested = [call.args[0] for call in fetch.call_args_list]
        self.assertEqual(len(requested), 18)
        self.assertIn("USER1", requested)
        self.assertIn("USER2", requested)
        self.assertNotIn("T16", requested)

    @patch("intel_engine._get_watchlist_tickers", return_value=["META"])
    @patch("news_fetcher.fetch_headlines")
    def test_empty_fallback_message_is_never_presented_as_news(self, fetch, _watchlist):
        fetch.return_value = CatalystNews(
            ["No headlines available — configure a provider."],
            "No catalyst loaded.", [], None, "none",
        )
        self.assertEqual(intel_engine.fetch_market_news(["META"]), [])

    @patch("intel_engine.get_ndx_watch_data", return_value={})
    @patch("intel_engine._get_watchlist_tickers", return_value=[])
    @patch("intel_engine._fh_is_rate_limited", return_value=False)
    @patch("intel_engine.trigger_background_refresh")
    def test_missing_news_cache_independently_triggers_refresh(self, trigger, *_mocks):
        warm = {
            "market_news": None,
            "earnings": {"today": [], "tomorrow": [], "this_week": [], "coming_up": [], "meta": {}},
            "splits": [], "dividends": [], "economic": [], "macro": {},
        }
        with patch("intel_engine._cget", side_effect=lambda key: warm[key]):
            payload = intel_engine.get_intel_summary()
        trigger.assert_called_once()
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["news_status"]["empty"])


if __name__ == "__main__":
    unittest.main()
