import unittest

from watchlist_utils import parse_watchlist_symbols


class WatchlistSymbolTests(unittest.TestCase):
    def test_accepts_common_and_class_share_symbols(self):
        self.assertEqual(
            parse_watchlist_symbols("nvda, brk.b BF-B, abcdef"),
            ["NVDA", "BRK.B", "BF-B", "ABCDEF"],
        )

    def test_deduplicates_and_rejects_malformed_symbols(self):
        self.assertEqual(
            parse_watchlist_symbols("NVDA nvda $TSLA A/B -BAD"),
            ["NVDA"],
        )


if __name__ == "__main__":
    unittest.main()
