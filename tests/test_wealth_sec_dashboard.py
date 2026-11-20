import unittest
from wealth_sec_dashboard import build_dashboard


class SecDashboardTests(unittest.TestCase):
    def test_missing_facts_not_fabricated(self):
        result = build_dashboard({"ticker": "SPCX", "status": "partial", "facts": {}, "filings": []})
        self.assertEqual(result["metrics"], {})
        self.assertFalse(result["valuation_ready"])

    def test_balance_sheet_change_with_source_dates(self):
        facts = {"assets": [
            {"value": 120, "period_end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q", "tag": "Assets"},
            {"value": 100, "period_end": "2025-06-30", "filed": "2025-08-01", "form": "10-Q", "tag": "Assets"}]}
        row = build_dashboard({"facts": facts})["metrics"]["assets"]
        self.assertEqual(row["balance_sheet_change_pct"], 20)
        self.assertEqual(row["latest"]["filed"], "2026-08-01")

    def test_income_comparison_not_assumed(self):
        facts = {"revenue": [
            {"value": 120, "period_end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q", "tag": "Revenues"},
            {"value": 100, "period_end": "2025-06-30", "filed": "2025-08-01", "form": "10-Q", "tag": "Revenues"}]}
        row = build_dashboard({"facts": facts})["metrics"]["revenue"]
        self.assertIsNone(row["balance_sheet_change_pct"])


if __name__ == "__main__":
    unittest.main()
