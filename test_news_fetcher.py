import unittest
from unittest.mock import patch

import news_fetcher
from news_fetcher import CatalystNews


class NewsFetcherTests(unittest.TestCase):
    def test_thumbnail_uses_largest_resolution(self):
        thumbnail = {
            "resolutions": [
                {"url": "small.jpg", "width": 120},
                {"url": "large.jpg", "width": 640},
            ]
        }
        self.assertEqual(news_fetcher._thumbnail_url(thumbnail), "large.jpg")

    def test_bounded_rss_runs_before_yfinance_fallback(self):
        rss_result = CatalystNews(["RSS headline"], "RSS headline", [], None, "yahoo_rss")
        with (
            patch("news_fetcher._try_finnhub", return_value=None),
            patch("news_fetcher._try_newsapi", return_value=None),
            patch("news_fetcher._try_polygon", return_value=None),
            patch("news_fetcher._try_rss", return_value=rss_result) as rss,
            patch("news_fetcher._try_yfinance") as yfinance,
        ):
            result = news_fetcher.fetch_headlines("META")

        self.assertIs(result, rss_result)
        rss.assert_called_once_with("META")
        yfinance.assert_not_called()


if __name__ == "__main__":
    unittest.main()
