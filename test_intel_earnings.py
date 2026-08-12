import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    @patch("intel_engine.as_completed")
    @patch("intel_engine._nasdaq_cal")
    def test_marketwide_feed_keeps_partial_results_when_one_day_times_out(self, calendar, completed):
        calendar.return_value = []
        successful = MagicMock()
        successful.done.return_value = True
        successful.result.return_value = [
            {"symbol": "MEGA", "name": "Mega Corp", "marketCap": "$42B", "time": "after hours"},
        ]

        def partial_then_timeout(_futures, timeout):
            yield successful
            raise intel_engine.FuturesTimeoutError()

        completed.side_effect = partial_then_timeout
        with patch("intel_engine.ThreadPoolExecutor") as executor:
            pool = executor.return_value
            pool.submit.side_effect = [successful] + [MagicMock() for _ in range(7)]
            items = intel_engine._earnings_from_nasdaq(date(2026, 8, 11), set(), set())

        self.assertEqual([item["ticker"] for item in items], ["MEGA"])
        pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)

    def test_empty_earnings_cache_expires_after_five_minutes(self):
        empty = {"today": [], "tomorrow": [], "this_week": [], "coming_up": [], "meta": {}}
        intel_engine._cache["earnings"] = {"data": empty, "ts": 100.0}
        with patch("intel_engine._time.monotonic", return_value=401.0):
            self.assertIsNone(intel_engine._cget("earnings"))
        intel_engine._cache.pop("earnings", None)

    def test_intel_card_exposes_earnings_refresh_control(self):
        template = Path("templates/intel.html").read_text()

        self.assertIn("/api/intel/earnings-radar", template)
        self.assertIn("refreshEarningsRadar", template)

    @patch("intel_engine._get_watchlist_tickers", return_value=["MINE"])
    @patch("intel_engine._earnings_from_nasdaq")
    def test_bounded_radar_returns_direct_calendar_results(self, calendar, _watchlist):
        calendar.return_value = [
            {"ticker": "LARGE", "days_away": 0, "on_watchlist": False, "market_cap": 50_000_000_000},
            {"ticker": "MINE", "days_away": 0, "on_watchlist": True, "market_cap": 1_000_000_000},
        ]
        with patch.object(intel_engine, "EARNINGS_OVERRIDES", []):
            rows = intel_engine.fetch_earnings_radar(limit=2)
        self.assertEqual([row["ticker"] for row in rows], ["MINE", "LARGE"])


if __name__ == "__main__":
    unittest.main()
