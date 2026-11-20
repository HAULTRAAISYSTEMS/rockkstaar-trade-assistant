import unittest
import wealth_portfolio_intelligence as pi


class PortfolioIntelligenceTests(unittest.TestCase):
    def test_etf_does_not_inherit_generic_technology_sector(self):
        c = pi.classify("SPCX", "Technology")
        self.assertIsNone(c["sector"])
        self.assertFalse(c["verified"])

    def test_unverified_exposure_not_counted_as_verified(self):
        state = {"holdings": {"KLAC": 1000, "SPCX": 500}, "total_value": 1500}
        e = pi.exposure(state, {"KLAC": pi.classify("KLAC", "Technology"),
                                "SPCX": pi.classify("SPCX", "Technology")})
        self.assertEqual(e["coverage_pct"], 0)
        self.assertEqual(e["sectors_pct"], {})
        self.assertEqual(len(e["unclassified"]), 2)

    def test_split_preserves_cash_when_no_eligible_candidates(self):
        plans = pi.split_options(750, [{"ticker": "KLAC", "eligible": False, "max_allocation": 0}])
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["cash_remaining"], 750)

    def test_split_does_not_exceed_budget_or_caps(self):
        plans = pi.split_options(750, [
            {"ticker": "A", "eligible": True, "max_allocation": 250, "score": 80},
            {"ticker": "B", "eligible": True, "max_allocation": 600, "score": 70}])
        self.assertEqual(plans[-1]["deployed"], 750)
        self.assertEqual(plans[-1]["allocations"], {"A": 250, "B": 500})

    def test_unknown_quote_is_flagged(self):
        checks = pi.quality_monitor([{"ticker": "KLAC", "valuation_as_of": None,
                                     "valuation_verified": False, "sector_verified": False}], {})
        self.assertTrue(any(c["category"] == "Market quote" and c["status"] == "stale_or_missing"
                            for c in checks))


if __name__ == "__main__":
    unittest.main()
