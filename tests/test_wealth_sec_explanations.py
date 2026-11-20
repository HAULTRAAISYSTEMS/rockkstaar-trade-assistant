import unittest
from wealth_sec_explanations import compare_periods, explain_changes


class EvidenceExplanationTests(unittest.TestCase):
    def test_matching_duration_reports_change_without_cause(self):
        facts = {"revenue": [
            {"value": 120, "period_end": "2026-06-30", "start": "2026-04-01",
             "filed": "2026-08-01", "tag": "Revenues", "unit": "USD", "form": "10-Q"},
            {"value": 100, "period_end": "2025-06-30", "start": "2025-04-01",
             "filed": "2025-08-01", "tag": "Revenues", "unit": "USD", "form": "10-Q"}]}
        result = compare_periods(facts)["revenue"]
        self.assertEqual(result["change_pct"], 20)
        narrative = explain_changes({"revenue": result})
        self.assertFalse(narrative["causal_claims_verified"])
        self.assertFalse(narrative["mcff_scoring_enabled"])

    def test_missing_period_start_blocks_income_comparison(self):
        facts = {"net_income": [
            {"value": 20, "period_end": "2026-06-30", "filed": "2026-08-01", "tag": "NetIncomeLoss", "unit": "USD"},
            {"value": 10, "period_end": "2025-06-30", "filed": "2025-08-01", "tag": "NetIncomeLoss", "unit": "USD"}]}
        self.assertEqual(compare_periods(facts)["net_income"]["status"], "not_comparable")

    def test_different_tags_do_not_compare(self):
        facts = {"revenue": [
            {"value": 20, "period_end": "2026-06-30", "filed": "2026-08-01", "tag": "Revenues", "unit": "USD", "start": "2026-04-01"},
            {"value": 10, "period_end": "2025-06-30", "filed": "2025-08-01", "tag": "SalesRevenueNet", "unit": "USD", "start": "2025-04-01"}]}
        self.assertEqual(compare_periods(facts)["revenue"]["status"], "not_comparable")


if __name__ == "__main__":
    unittest.main()
