import os
import tempfile
import unittest
from pathlib import Path

import database


ROOT = Path(__file__).resolve().parent


class InsiderAlertRulePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.old_path = database.DB_PATH
        self.old_pg = database._USE_POSTGRES
        self.old_password = os.environ.get("APP_PASSWORD")
        os.environ["APP_PASSWORD"] = "smart-money-test-password"
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = handle.name
        handle.close()
        database.DB_PATH = self.db_path
        database._USE_POSTGRES = False
        database.init_db()

    def tearDown(self):
        database.DB_PATH = self.old_path
        database._USE_POSTGRES = self.old_pg
        if self.old_password is None:
            os.environ.pop("APP_PASSWORD", None)
        else:
            os.environ["APP_PASSWORD"] = self.old_password
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    def test_rules_are_user_scoped_and_can_be_disabled(self):
        database.set_insider_alert_rules(7, {"cluster_buy_3": True, "holdings_sale_10": False})
        database.set_insider_alert_rules(8, {"cluster_buy_3": False})
        self.assertEqual(
            {"cluster_buy_3": True, "holdings_sale_10": False},
            database.get_insider_alert_rules(7),
        )
        self.assertEqual({"cluster_buy_3": False}, database.get_insider_alert_rules(8))


class SmartMoneyPageContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "templates" / "smart_money.html").read_text()
        cls.javascript = (ROOT / "static" / "js" / "smart_money.js").read_text()
        cls.styles = (ROOT / "static" / "css" / "smart_money.css").read_text()
        cls.app_source = (ROOT / "app.py").read_text()

    def test_page_preserves_congress_and_raw_sec_access(self):
        self.assertIn("Congressional Trades", self.template)
        self.assertIn("Official disclosure", self.template)
        self.assertIn("underlying transaction", self.template)
        self.assertIn("Official SEC filing", self.template)
        self.assertIn("Open complete Form", self.template)
        self.assertIn("Shares reported", self.template)
        self.assertIn("Reported value", self.template)
        self.assertIn("not open-market activity", self.template)
        self.assertIn("Open raw facts for security-level holdings", self.template)

    def test_page_exposes_dashboard_controls_and_explanations(self):
        for text in (
            "Insider Signal", "Why this matters", "Holdings change", "Minimum value",
            "Cluster activity only", "Dashboard match rules", "No background or push delivery",
        ):
            self.assertIn(text, self.template)

    def test_styles_are_scoped_and_responsive(self):
        self.assertIn(".sm-dashboard", self.styles)
        self.assertIn("@media(max-width:720px)", self.styles)
        self.assertNotIn(".lr-", self.styles)

    def test_javascript_keeps_tabs_and_summary_ranges_interactive(self):
        self.assertIn("selectPanel", self.javascript)
        self.assertIn("selectSummary", self.javascript)
        self.assertIn('selectSummary("30")', self.javascript)

    def test_route_uses_history_window_and_separate_dashboard_builder(self):
        self.assertIn("fetch_sec_form4(tickers, limit=None, history_days=30)", self.app_source)
        self.assertIn("dashboard = build_insider_dashboard", self.app_source)
        self.assertIn('@app.route("/smart-money/alert-rules", methods=["POST"])', self.app_source)


if __name__ == "__main__":
    unittest.main()
