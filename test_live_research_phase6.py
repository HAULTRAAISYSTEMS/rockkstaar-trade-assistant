import sqlite3
import unittest

import research_feed as rf
import live_research_ingestion as ing
from migrations.m0001_live_research_feed import upgrade


ADMIN = {"id": 1, "is_admin": True}


class FakeTakeProvider:
    name = "fake"
    def generate(self, context):
        return {"take": "Verified facts only.", "source_ids": [context["sources"][0].id], "provider": self.name, "model": "test"}


class Phase6Tests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        upgrade(self.db)

    def item(self, **overrides):
        data = dict(provider="sec", external_id="0001", ticker="NVDA", company_name="NVIDIA Corp",
                    headline="NVIDIA reports quarterly results", source_name="NVIDIA Investor Relations",
                    source_url="https://investor.nvidia.com/results", category="Earnings", sentiment="Neutral",
                    facts=["Revenue was reported as $1.0 billion."], source_kind="primary",
                    metrics=[{"metric_type":"revenue","label":"Revenue","actual_value":1.0,"expected_value":0.9,"unit":"USD bn","comparison":"beat"}])
        data.update(overrides)
        return data

    def test_primary_source_creates_draft_only(self):
        result = ing.create_suggestion(self.item(), ADMIN, self.db)
        row = self.db.execute("select * from research_posts where id=?", (result["post_id"],)).fetchone()
        self.assertEqual("draft", row["status"])
        self.assertEqual("provider", row["take_origin"])
        self.assertEqual(0, row["should_notify"])
        self.assertIsNone(row["published_at"])
        self.assertTrue(result["primary_source"])

    def test_deduplication(self):
        first = ing.create_suggestion(self.item(), ADMIN, self.db)
        second = ing.create_suggestion(self.item(), ADMIN, self.db)
        self.assertEqual("draft", first["status"])
        self.assertEqual("duplicate", second["status"])
        self.assertEqual(1, self.db.execute("select count(*) from research_posts").fetchone()[0])

    def test_source_attribution_survives(self):
        result = ing.create_suggestion(self.item(), ADMIN, self.db)
        row = self.db.execute("select source_name,source_url,research_notes from research_posts where id=?", (result["post_id"],)).fetchone()
        self.assertEqual("NVIDIA Investor Relations", row["source_name"])
        self.assertEqual("https://investor.nvidia.com/results", row["source_url"])
        self.assertIn("Revenue was reported", row["research_notes"])

    def test_missing_or_invalid_source_rejected(self):
        with self.assertRaises(Exception): ing.create_suggestion(self.item(source_url="javascript:bad"), ADMIN, self.db)
        with self.assertRaises(Exception): ing.create_suggestion(self.item(facts=[]), ADMIN, self.db)
        self.assertEqual(0, self.db.execute("select count(*) from research_posts").fetchone()[0])

    def test_partial_earnings_does_not_invent_numbers(self):
        metrics = ing.earnings_metrics({"period":"Q2", "revenue_actual":10.0})
        self.assertEqual(1, len(metrics))
        self.assertEqual(10.0, metrics[0]["actual_value"])
        self.assertIsNone(metrics[0]["expected_value"])
        self.assertEqual("not_applicable", metrics[0]["comparison"])

    def test_provider_failure_isolated_per_item(self):
        summary = ing.ingest([self.item(), {"provider":"bad"}], ADMIN, self.db)
        self.assertEqual(1, summary["created"])
        self.assertEqual(1, summary["skipped"])

    def test_ai_take_is_separate_draft_and_never_notifies(self):
        result = ing.create_suggestion(self.item(), ADMIN, self.db, take_provider=FakeTakeProvider())
        rows = self.db.execute("select status,take_origin,should_notify,published_at from research_posts order by created_at").fetchall()
        self.assertEqual(2, len(rows))
        for row in rows:
            self.assertEqual("draft", row["status"])
            self.assertEqual(0, row["should_notify"])
            self.assertIsNone(row["published_at"])
        self.assertEqual({"provider","ai"}, {r["take_origin"] for r in rows})

    def test_module_has_no_publish_realtime_or_notification_api(self):
        self.assertFalse(hasattr(ing, "publish_post"))
        self.assertFalse(hasattr(ing, "announce_published"))
        self.assertFalse(hasattr(ing, "send_notification"))

    def test_finnhub_adapter_reuses_existing_article_metadata(self):
        items = ing.finnhub_articles_to_items("AAPL", "Apple Inc", [{"id":9,"headline":"Apple update","url":"https://example.com/a","source":"Reuters","summary":"Verified provider summary.","datetime":123}])
        self.assertEqual(1, len(items)); self.assertEqual("finnhub", items[0].provider)
        self.assertEqual("https://example.com/a", items[0].source_url)


if __name__ == "__main__":
    unittest.main()
