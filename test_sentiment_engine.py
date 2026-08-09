import unittest

from sentiment_engine import build_sentiment_snapshot, score_headline


class SentimentEngineTests(unittest.TestCase):
    def test_explainable_bullish_and_bearish_scores(self):
        bullish = score_headline("Company beats estimates and raises guidance")
        bearish = score_headline("Company missed estimates and cuts guidance")
        self.assertEqual(bullish["label"], "BULLISH")
        self.assertGreater(bullish["score"], 0)
        self.assertIn("raises guidance", bullish["bullish_terms"])
        self.assertEqual(bearish["label"], "BEARISH")
        self.assertLess(bearish["score"], 0)

    def test_neutral_headline_has_no_invented_evidence(self):
        result = score_headline("Company schedules annual shareholder meeting")
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["evidence_count"], 0)
        self.assertEqual(result["label"], "NEUTRAL")

    def test_snapshot_groups_sources_and_watchlist(self):
        snapshot = build_sentiment_snapshot([
            {"ticker": "NVDA", "headline": "NVDA beats estimates", "source": "Wire A"},
            {"ticker": "NVDA", "headline": "NVDA growth continues", "source": "Wire B"},
            {"ticker": "TSLA", "headline": "TSLA faces investigation", "source": "Wire A"},
        ], ["NVDA"])
        self.assertEqual(snapshot["article_count"], 3)
        self.assertEqual(snapshot["sources"][0], {"name": "Wire A", "articles": 2})
        nvda = next(row for row in snapshot["tickers"] if row["ticker"] == "NVDA")
        self.assertTrue(nvda["on_watchlist"])
        self.assertEqual(nvda["articles"], 2)


if __name__ == "__main__":
    unittest.main()
