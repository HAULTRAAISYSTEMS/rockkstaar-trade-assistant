import unittest
from datetime import date
import wealth_etf_lookthrough as etf


class ETFLookthroughTests(unittest.TestCase):
    def sample(self):
        return {"source": "Issuer holdings export", "as_of": date.today().isoformat(),
                "constituents": [
                    {"ticker": "ABC", "weight_pct": 60, "sector": "Technology"},
                    {"ticker": "XYZ", "weight_pct": 40, "sector": "Industrials"}]}

    def test_accepts_dated_source_and_full_weights(self):
        snap = etf.validated_snapshot("SPCX", self.sample())
        self.assertEqual(snap["coverage_pct"], 100)
        self.assertTrue(snap["complete"])

    def test_rejects_overallocated_fund(self):
        data = self.sample()
        data["constituents"][1]["weight_pct"] = 50
        self.assertIsNone(etf.validated_snapshot("SPCX", data))

    def test_rejects_missing_source(self):
        data = self.sample()
        data["source"] = ""
        self.assertIsNone(etf.validated_snapshot("SPCX", data))

    def test_portfolio_denominator(self):
        snap = etf.validated_snapshot("SPCX", self.sample())
        result = etf.lookthrough({"holdings": {"SPCX": 500, "KLAC": 500},
                                  "total_value": 1000}, {"SPCX": snap})
        self.assertEqual(result["sector_exposure_pct"]["Technology"], 30)
        self.assertEqual(result["portfolio_lookthrough_coverage_pct"], 50)


if __name__ == "__main__":
    unittest.main()
