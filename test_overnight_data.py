import os
import unittest
from unittest.mock import patch

from overnight_data import _parse_bar, fetch_overnight_bars, merge_session_bars


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


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

    @patch("overnight_data.requests.get")
    def test_basic_plan_falls_back_to_latest_derived_overnight_bar(self, get):
        get.side_effect = [
            _Response(403),
            _Response(403),
            _Response(200, {"bar": {
                "t": "2026-08-10T02:15:00Z", "o": 100, "h": 101,
                "l": 99.5, "c": 100.5, "v": 42,
            }}),
        ]
        with patch.dict(os.environ, {
            "APCA_API_KEY_ID": "paper-key",
            "APCA_API_SECRET_KEY": "paper-secret",
        }):
            result = fetch_overnight_bars("META", "5m", "1d")
        self.assertEqual(result["status"]["state"], "limited")
        self.assertFalse(result["status"]["historical"])
        self.assertEqual(result["bars"][0]["source"], "alpaca_overnight")
        self.assertEqual(get.call_args_list[2].kwargs["params"], {"feed": "overnight"})

    @patch("overnight_data.requests.get")
    def test_basic_fallback_failure_explains_plan_requirement(self, get):
        get.side_effect = [_Response(403), _Response(403), _Response(403)]
        with patch.dict(os.environ, {
            "APCA_API_KEY_ID": "paper-key",
            "APCA_API_SECRET_KEY": "paper-secret",
        }):
            result = fetch_overnight_bars("META", "5m", "1d")
        self.assertEqual(result["bars"], [])
        self.assertEqual(result["status"]["state"], "restricted")
        self.assertIn("Algo Trader Plus", result["status"]["message"])

    @patch("overnight_data.requests.get")
    def test_basic_plan_uses_delayed_boats_history_when_available(self, get):
        get.side_effect = [
            _Response(403),
            _Response(200, {"bars": [{
                "t": "2026-08-10T02:15:00Z", "o": 100, "h": 101,
                "l": 99.5, "c": 100.5, "v": 42,
            }]}),
        ]
        with patch.dict(os.environ, {
            "APCA_API_KEY_ID": "paper-key",
            "APCA_API_SECRET_KEY": "paper-secret",
        }):
            result = fetch_overnight_bars("META", "5m", "1d")
        self.assertEqual(result["status"]["state"], "delayed")
        self.assertTrue(result["status"]["historical"])
        self.assertEqual(result["status"]["delay_seconds"], 900)
        self.assertEqual(result["bars"][0]["source"], "alpaca_boats")


if __name__ == "__main__":
    unittest.main()
