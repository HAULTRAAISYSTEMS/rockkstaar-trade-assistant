import json
import unittest
from unittest.mock import MagicMock, patch

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

    def test_yahoo_brand_placeholder_is_suppressed(self):
        thumbnail = {"resolutions": [{"url": "purple-yahoo-placeholder.jpg", "width": 640}]}

        self.assertEqual(news_fetcher._display_image("Yahoo", thumbnail), "")
        self.assertEqual(news_fetcher._display_image("Reuters", thumbnail), "purple-yahoo-placeholder.jpg")

    @patch("urllib.request.urlopen")
    def test_yahoo_search_normalizes_thumbnail_and_story_link(self, urlopen):
        response = MagicMock()
        response.read.return_value = json.dumps({
            "news": [{
                "title": "Meta announces a product update",
                "publisher": "Reuters",
                "link": "https://example.com/meta-story",
                "providerPublishTime": 1786392000,
                "thumbnail": {"resolutions": [{"url": "story.jpg", "width": 640}]},
            }]
        }).encode()
        urlopen.return_value.__enter__.return_value = response

        result = news_fetcher._try_yahoo_search("META")

        self.assertIsNotNone(result)
        self.assertEqual(result.articles[0]["source"], "Reuters")
        self.assertEqual(result.articles[0]["url"], "https://example.com/meta-story")
        self.assertEqual(result.articles[0]["image"], "story.jpg")

    def test_bounded_rss_runs_before_yfinance_fallback(self):
        rss_result = CatalystNews(["RSS headline"], "RSS headline", [], None, "yahoo_rss")
        with (
            patch("news_fetcher._try_finnhub", return_value=None),
            patch("news_fetcher._try_newsapi", return_value=None),
            patch("news_fetcher._try_polygon", return_value=None),
            patch("news_fetcher._try_yahoo_search", return_value=None),
            patch("news_fetcher._try_rss", return_value=rss_result) as rss,
            patch("news_fetcher._try_yfinance") as yfinance,
        ):
            result = news_fetcher.fetch_headlines("META")

        self.assertIs(result, rss_result)
        rss.assert_called_once_with("META")
        yfinance.assert_not_called()


if __name__ == "__main__":
    unittest.main()
