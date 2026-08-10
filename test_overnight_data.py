import os
import unittest
from unittest.mock import patch

from overnight_data import _parse_bar, fetch_overnight_bars, merge_session_bars


class OvernightDataTests(unittest.TestCase):
    def test_missing_credentials_returns_explicit_not_configured_state(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ALPACA_API_KEY", None)
            os.environ.pop("ALPACA_SECRET_KEY", None)
            os.environ.pop("APCA_API_KEY_ID", None)
            os.environ.pop("APCA_API_SECRET_KEY", None)
            result = fetch_overnight_bars("NVDA", "5m", "1d")
        self.assertEqual(result["bars"], [])
        self.assertEqual(result["status"]["state"], "not_configured")

    def test_alpaca_bar_contract_is_normalized(self):
        bar = _parse_bar({
            "t": "2026-08-10T01:05:00Z", "o": 100, "h": 102,
            "l": 99, "c": 101, "v": 250,
        })
        self.assertEqual(bar["time"], 1786323900)
        self.assertEqual(bar["source"], "alpaca_boats")

    def test_dedicated_overnight_bar_wins_timestamp_collision(self):
        merged = merge_session_bars(
            [{"time": 1, "close": 100}],
            [{"time": 1, "close": 101, "source": "alpaca_boats"}],
        )
        self.assertEqual(merged, [{"time": 1, "close": 101, "source": "alpaca_boats"}])


if __name__ == "__main__":
    unittest.main()
