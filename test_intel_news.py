import unittest
from unittest.mock import patch

import intel_engine
from news_fetcher import CatalystNews


class IntelNewsTests(unittest.TestCase):
    def tearDown(self):
        intel_engine._cache.clear()

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
