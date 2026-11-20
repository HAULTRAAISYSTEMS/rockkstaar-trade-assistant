import unittest
from datetime import datetime, timezone
from market_data import _enforce_security_history
from wealth_data_confidence import data_confidence


class VerifiedMarketDataTests(unittest.TestCase):
    def test_spcx_reused_symbol_candles_filtered(self):
        def ts(day):
            return int(datetime.fromisoformat(day+"T14:30:00+00:00").timestamp())
        bars = {"timestamps": [ts("2026-06-11"), ts("2026-06-12")],
                "opens": [1, 2], "closes": [1, 2], "highs": [1, 2],
                "lows": [1, 2], "volumes": [10, 20]}
        filtered, meta = _enforce_security_history("SPCX", bars, {"as_of": "2026-06-12"})
        self.assertEqual(filtered["opens"], [2])
        self.assertIn("pre-IPO", meta["identity_warning"])

    def test_missing_financials_blocks_scoring(self):
        result = data_confidence("SPCX", broker_cusip="84615Q103",
                                 price_meta={"as_of": datetime.now(timezone.utc).isoformat()})
        self.assertEqual(result["identity"], "verified")
        self.assertFalse(result["usable_for_scoring"])

    def test_conflicting_cusip_never_verified(self):
        self.assertEqual(data_confidence("SPCX", broker_cusip="OTHER")["identity"], "unverified")


if __name__ == "__main__":
    unittest.main()
