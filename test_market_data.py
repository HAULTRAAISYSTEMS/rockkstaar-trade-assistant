import os
import unittest
from unittest.mock import patch

import market_data


class _Response:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._payload


class MarketDataContractTests(unittest.TestCase):
    def test_auto_feed_falls_back_from_restricted_sip_to_delayed_sip(self):
        bar = {"t": "2026-08-10T14:00:00Z", "o": 100, "h": 102, "l": 99, "c": 101, "v": 500}
        responses = [_Response(403), _Response(200, {"bars": [bar]})]
        with patch.dict(os.environ, {
            "APCA_API_KEY_ID": "key", "APCA_API_SECRET_KEY": "secret",
            "ALPACA_MARKET_DATA_FEED": "auto",
        }, clear=False), patch("market_data.requests.get", side_effect=responses) as get:
            data, meta = market_data._alpaca_bars("TEST", "1m", "1d", False)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(data["closes"], [101.0])
        self.assertEqual(meta["feed"], "delayed_sip")
        self.assertFalse(meta["realtime"])
        self.assertTrue(meta["comprehensive"])
        self.assertEqual(meta["delay_seconds"], 900)

    def test_regular_session_filter_removes_extended_bars(self):
        rows = [
            {"t": "2026-08-10T12:00:00Z", "o": 99, "h": 100, "l": 98, "c": 99, "v": 10},
            {"t": "2026-08-10T14:00:00Z", "o": 100, "h": 102, "l": 99, "c": 101, "v": 500},
        ]
        data = market_data._parse_bars(rows, include_extended=False, interval="1m")
        self.assertEqual(data["closes"], [101.0])

    def test_yahoo_fallback_is_never_labelled_realtime(self):
        fallback = {
            "timestamps": [1786370400], "opens": [1.0], "closes": [1.0],
            "highs": [1.0], "lows": [1.0], "volumes": [1],
        }
        with patch("market_data._alpaca_bars", return_value=(None, None)), patch(
            "data_fetcher._fetch_ohlcv_via_chart_api", return_value=fallback
        ):
            data, meta = market_data.fetch_chart_bars("TEST", "1m", "1d")
        self.assertIs(data, fallback)
        self.assertTrue(meta["fallback"])
        self.assertFalse(meta["realtime"])
        self.assertFalse(meta["official"])


if __name__ == "__main__":
    unittest.main()
