import unittest

from terminal_intelligence import aggregate_ohlcv_bars, normalize_ohlcv_data


class TerminalChartAggregationTests(unittest.TestCase):
    def test_normalization_sorts_deduplicates_and_rejects_invalid_candles(self):
        data = {
            "timestamps": [2, 1, 2, 3],
            "opens": [20, 10, 21, 30],
            "highs": [22, 12, 24, 29],
            "lows": [19, 9, 20, 28],
            "closes": [21, 11, 23, 31],
            "volumes": [200, 100, 250, 300],
        }
        result = normalize_ohlcv_data(data)
        self.assertEqual([row["time"] for row in result], [1, 2])
        self.assertEqual(result[-1]["open"], 21)
        self.assertEqual(result[-1]["volume"], 250)

    def test_four_hour_aggregation_preserves_ohlcv(self):
        base = 1786300200  # four same-session hourly bars
        bars = [
            {"time": base + i * 3600, "open": 100 + i, "high": 102 + i,
             "low": 99 + i, "close": 101 + i, "volume": 10 * (i + 1)}
            for i in range(4)
        ]
        result = aggregate_ohlcv_bars(bars, 4)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["open"], 100)
        self.assertEqual(result[0]["high"], 105)
        self.assertEqual(result[0]["low"], 99)
        self.assertEqual(result[0]["close"], 104)
        self.assertEqual(result[0]["volume"], 100)

    def test_aggregation_never_crosses_trading_days(self):
        bars = [
            {"time": 1786300200, "open": 100, "high": 102, "low": 99,
             "close": 101, "volume": 10},
            {"time": 1786386600, "open": 110, "high": 112, "low": 109,
             "close": 111, "volume": 20},
        ]
        result = aggregate_ohlcv_bars(bars, 4)
        self.assertEqual(len(result), 2)
        self.assertEqual([row["open"] for row in result], [100, 110])


if __name__ == "__main__":
    unittest.main()
