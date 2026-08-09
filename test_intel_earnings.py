import unittest
from datetime import date
from unittest.mock import patch

import intel_engine


class MarketCapClassificationTests(unittest.TestCase):
    def test_parses_nasdaq_market_cap_formats(self):
        self.assertEqual(intel_engine._parse_market_cap("$42.7B"), 42_700_000_000)
        self.assertEqual(intel_engine._parse_market_cap("$6,250,000,000"), 6_250_000_000)
        self.assertEqual(intel_engine._parse_market_cap("900M"), 900_000_000)
        self.assertIsNone(intel_engine._parse_market_cap("--"))

    def test_classifies_large_mid_and_small_caps(self):
        self.assertEqual(intel_engine._market_cap_tier(10_000_000_000), "Large Cap")
        self.assertEqual(intel_engine._market_cap_tier(2_000_000_000), "Mid Cap")
        self.assertEqual(intel_engine._market_cap_tier(1_999_999_999), "Small Cap")


class NasdaqEarningsTests(unittest.TestCase):
    @patch("intel_engine._nasdaq_cal")
    def test_marketwide_feed_keeps_large_mid_and_all_watchlist_names(self, calendar):
        calendar.return_value = [
            {"symbol": "MEGA", "name": "Mega Corp", "marketCap": "$42B", "time": "after hours"},
            {"symbol": "MID", "name": "Mid Corp", "marketCap": "$4.5B", "time": "before open"},
            {"symbol": "TINY", "name": "Tiny Corp", "marketCap": "$500M", "time": "after hours"},
            {"symbol": "MINE", "name": "My Small Cap", "marketCap": "$300M", "time": "before open"},
        ]

        items = intel_engine._earnings_from_nasdaq(
            date(2026, 8, 9), {"MINE"}, set()
        )
        by_ticker = {item["ticker"]: item for item in items}

        self.assertEqual(set(by_ticker), {"MEGA", "MID", "MINE"})
        self.assertEqual(by_ticker["MEGA"]["cap_tier"], "Large Cap")
        self.assertEqual(by_ticker["MID"]["cap_tier"], "Mid Cap")
        self.assertTrue(by_ticker["MINE"]["on_watchlist"])
        self.assertNotIn("TINY", by_ticker)

    def test_earnings_universe_never_truncates_watchlist(self):
        tickers = [f"T{i}" for i in range(75)]
        with patch("intel_engine._get_watchlist_tickers", return_value=tickers):
            universe = intel_engine._earnings_universe()
        self.assertTrue(set(tickers).issubset(universe))


if __name__ == "__main__":
    unittest.main()
