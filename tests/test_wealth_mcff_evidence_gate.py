import unittest
from wealth_mcff import financials_gate, evaluate


class FinancialEvidenceGateTests(unittest.TestCase):
    def test_missing_metric_evidence_blocks_score(self):
        fund = {"ticker": "KLAC", "score_percent": 95, "scored_metrics": 10, "total_metrics": 10, "sections": []}
        gate = financials_gate(fund)
        self.assertIsNone(gate["score"])
        self.assertIsNone(gate["passed"])

    def test_low_coverage_cannot_pass(self):
        fund = {"ticker": "KLAC", "score_percent": 100, "scored_metrics": 2, "total_metrics": 10,
                "sections": [{"name": "Profit", "rows": [{"label": "Margin", "value": "20%", "passed": True}]}]}
        gate = financials_gate(fund)
        self.assertIsNone(gate["passed"])
        result = evaluate({}, gate, price=100, value_zone={"zone": "load"})
        self.assertIsNone(result["gates"]["financials"]["passed"])

    def test_sufficient_evidence_preserves_score(self):
        fund = {"ticker": "KLAC", "score_percent": 80, "scored_metrics": 9, "total_metrics": 10,
                "sections": [{"name": "Profit", "rows": [{"label": "Margin", "value": "20%", "passed": True}]}]}
        gate = financials_gate(fund)
        self.assertEqual(gate["score"], 8)
        self.assertTrue(gate["passed"])

    def test_red_flags_veto(self):
        fund = {"ticker": "KLAC", "score_percent": 90, "scored_metrics": 9, "total_metrics": 10,
                "sections": [{"name": "Profit", "rows": [{"label": "Margin", "value": "20%", "passed": True}]}],
                "red_flags": ["Debt concern"]}
        self.assertFalse(financials_gate(fund)["passed"])


if __name__ == "__main__":
    unittest.main()
