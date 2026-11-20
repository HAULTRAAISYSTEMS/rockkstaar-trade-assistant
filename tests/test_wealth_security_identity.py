import unittest
from wealth_security_identity import identity, filter_spcx_price_history
from wealth_portfolio_intelligence import classify


class SPCXSecurityTests(unittest.TestCase):
    def test_class_a_equity_not_etf(self):
        self.assertEqual(classify("SPCX")["asset_type"], "equity")
        self.assertFalse(classify("SPCX")["verified"])

    def test_cusip_verification(self):
        self.assertTrue(identity("SPCX", cusip="84615Q103")["verified"])
        self.assertFalse(identity("SPCX", cusip="WRONG")["verified"])
        self.assertFalse(identity("SPCX", description="SPACE EX TECH SPACEX A")["verified"])

    def test_old_ticker_bars_removed(self):
        rows = [{"date": "2026-06-11", "close": 1},
                {"date": "2026-06-12", "close": 2}]
        self.assertEqual(filter_spcx_price_history(rows), rows[1:])


if __name__ == "__main__":
    unittest.main()
