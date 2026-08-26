import sqlite3
import unittest

import research_feed
from migrations import m0001_live_research_feed


class ResearchFeedPhase1Tests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, is_admin INTEGER NOT NULL)")
        self.conn.execute("INSERT INTO users (id, is_admin) VALUES (1, 1), (2, 0)")
        m0001_live_research_feed.upgrade(self.conn)
        self.admin = {"id": 1, "is_admin": True}
        self.user = {"id": 2, "is_admin": False}
        self.base = {
            "ticker": "crwd",
            "company_name": "CrowdStrike",
            "headline": "CrowdStrike reports quarterly results",
            "research_notes": "Revenue beat consensus while guidance changed.",
            "category": "Earnings",
            "sentiment": "Neutral",
            "source_name": "Company IR",
            "source_url": "https://example.com/results",
        }

    def tearDown(self):
        self.conn.close()

    def test_non_admin_cannot_create_draft(self):
        with self.assertRaises(research_feed.ResearchPermissionError):
            research_feed.create_draft(self.base, self.user, conn=self.conn)

    def test_provider_or_ai_content_is_created_as_draft(self):
        data = dict(self.base, take_origin="ai", tradestaar_take="Draft AI summary")
        post_id = research_feed.create_draft(data, self.admin, conn=self.conn)
        row = self.conn.execute("SELECT status, take_origin, published_at FROM research_posts WHERE id=?", (post_id,)).fetchone()
        self.assertEqual(row["status"], "draft")
        self.assertEqual(row["take_origin"], "ai")
        self.assertIsNone(row["published_at"])

    def test_drafts_never_appear_in_published_query(self):
        research_feed.create_draft(self.base, self.admin, conn=self.conn)
        self.assertEqual(research_feed.list_published(conn=self.conn), [])

    def test_publish_requires_admin_and_makes_post_visible(self):
        post_id = research_feed.create_draft(self.base, self.admin, conn=self.conn)
        with self.assertRaises(research_feed.ResearchPermissionError):
            research_feed.publish_post(post_id, self.user, conn=self.conn)
        research_feed.publish_post(post_id, self.admin, conn=self.conn)
        rows = research_feed.list_published(ticker="CRWD", conn=self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "CRWD")
        self.assertIsNotNone(rows[0]["published_at"])

    def test_earnings_metrics_are_structured(self):
        metrics = [
            {"metric_type": "revenue", "label": "Revenue", "actual_value": 1200,
             "expected_value": 1175, "unit": "USD millions", "comparison": "beat"},
            {"metric_type": "eps", "label": "Adjusted EPS", "actual_value": 1.25,
             "expected_value": 1.18, "unit": "USD/share", "comparison": "beat"},
            {"metric_type": "guidance", "label": "FY revenue guidance",
             "previous_value": 5000, "actual_value": 4945, "unit": "USD millions",
             "comparison": "lowered"},
        ]
        post_id = research_feed.create_draft(self.base, self.admin, metrics=metrics, conn=self.conn)
        rows = self.conn.execute("SELECT * FROM research_metrics WHERE post_id=? ORDER BY sort_order", (post_id,)).fetchall()
        self.assertEqual([r["metric_type"] for r in rows], ["revenue", "eps", "guidance"])
        self.assertEqual(rows[0]["comparison"], "beat")
        self.assertEqual(rows[2]["comparison"], "lowered")

    def test_rejects_invalid_category_sentiment_ticker_and_source_scheme(self):
        cases = [
            dict(self.base, category="Rumor"),
            dict(self.base, sentiment="Guaranteed Win"),
            dict(self.base, ticker="../BAD"),
            dict(self.base, source_url="javascript:alert(1)"),
        ]
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaises(research_feed.ResearchValidationError):
                    research_feed.validate_post(data)

    def test_migration_is_idempotent(self):
        m0001_live_research_feed.upgrade(self.conn)
        tables = {row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"research_posts", "research_metrics", "research_saved_posts", "research_alert_preferences"}.issubset(tables))


if __name__ == "__main__":
    unittest.main()
