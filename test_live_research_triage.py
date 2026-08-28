import sqlite3
import unittest
from pathlib import Path

import live_research_ingestion as ingestion
import research_feed as core
import research_feed_phase2 as triage
from migrations import m0001_live_research_feed, m0002_live_research_triage


ADMIN = {"id": 1, "is_admin": True}


def database():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, is_admin INTEGER NOT NULL)")
    conn.execute("INSERT INTO users (id, is_admin) VALUES (1, 1)")
    m0001_live_research_feed.upgrade(conn)
    m0002_live_research_triage.upgrade(conn)
    return conn


def payload(ticker="NVDA", **overrides):
    data = {
        "ticker": ticker,
        "company_name": "NVIDIA",
        "headline": "NVIDIA reports quarterly results",
        "research_notes": "Verified quarterly results.",
        "category": "Earnings",
        "sentiment": "Neutral",
        "source_name": "Company IR",
        "source_url": "https://investor.example.com/results",
        "priority": "Critical",
        "catalyst_type": "EARNINGS",
        "source_published_at": "2020-08-27T12:00:00+00:00",
    }
    data.update(overrides)
    return data


def provider_item(external_id="event-1", **overrides):
    data = {
        "provider": "company-ir",
        "external_id": external_id,
        "ticker": "NVDA",
        "company_name": "NVIDIA",
        "headline": "NVIDIA reports quarterly results",
        "source_name": "NVIDIA Investor Relations",
        "source_url": "https://investor.example.com/results",
        "category": "Earnings",
        "facts": ["Revenue and EPS were reported."],
        "metrics": [{"metric_type": "Revenue", "label": "Revenue", "actual_value": 2.74,
                     "expected_value": 2.71, "unit": "B", "comparison": "Beat"}],
        "published_at": "2026-08-27T12:00:00+00:00",
        "source_kind": "primary",
    }
    data.update(overrides)
    return data


class TriageWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.conn = database()

    def tearDown(self):
        self.conn.close()

    def test_automatic_discovery_is_incoming_with_ranked_metadata(self):
        result = ingestion.create_suggestion(provider_item(), ADMIN, self.conn)
        row = self.conn.execute("SELECT status,priority,catalyst_type,should_notify,published_at FROM research_posts WHERE id=?", (result["post_id"],)).fetchone()
        self.assertEqual((row["status"], row["priority"], row["catalyst_type"]), ("incoming", "Critical", "EARNINGS"))
        self.assertEqual(row["should_notify"], 0)
        self.assertIsNone(row["published_at"])

    def test_manual_research_remains_draft(self):
        post_id = core.create_draft(payload(), ADMIN, conn=self.conn)
        self.assertEqual("draft", self.conn.execute("SELECT status FROM research_posts WHERE id=?", (post_id,)).fetchone()[0])

    def test_approve_and_reject_only_transition_incoming(self):
        approved = ingestion.create_suggestion(provider_item("approve"), ADMIN, self.conn)["post_id"]
        rejected = ingestion.create_suggestion(provider_item("reject", source_url="https://investor.example.com/reject"), ADMIN, self.conn)["post_id"]
        self.assertEqual("draft", triage.transition_post(approved, "approve", ADMIN, self.conn))
        self.assertEqual("rejected", triage.transition_post(rejected, "reject", ADMIN, self.conn))
        with self.assertRaises(core.ResearchValidationError):
            triage.transition_post(approved, "reject", ADMIN, self.conn)

    def test_rejected_fingerprint_prevents_rediscovery_and_cannot_be_deleted(self):
        item = provider_item("retained")
        first = ingestion.create_suggestion(item, ADMIN, self.conn)
        triage.transition_post(first["post_id"], "reject", ADMIN, self.conn)
        self.assertEqual("duplicate", ingestion.create_suggestion(item, ADMIN, self.conn)["status"])
        with self.assertRaises(core.ResearchValidationError):
            triage.update_post(first["post_id"], payload(), ADMIN, conn=self.conn)
        with self.assertRaises(core.ResearchValidationError):
            triage.delete_post(first["post_id"], ADMIN, self.conn)

    def test_only_draft_can_publish_and_public_query_is_published_only(self):
        incoming = ingestion.create_suggestion(provider_item("incoming"), ADMIN, self.conn)["post_id"]
        rejected = ingestion.create_suggestion(provider_item("rejected", source_url="https://investor.example.com/rejected"), ADMIN, self.conn)["post_id"]
        triage.transition_post(rejected, "reject", ADMIN, self.conn)
        with self.assertRaises(core.ResearchValidationError): core.publish_post(incoming, ADMIN, self.conn)
        with self.assertRaises(core.ResearchValidationError): core.publish_post(rejected, ADMIN, self.conn)
        draft = core.create_draft(payload("AMD", company_name="AMD"), ADMIN, conn=self.conn)
        core.publish_post(draft, ADMIN, self.conn)
        self.assertEqual([draft], [row["id"] for row in core.list_published(conn=self.conn)])

    def test_bulk_transitions_are_status_guarded_and_atomic(self):
        first = ingestion.create_suggestion(provider_item("bulk-a"), ADMIN, self.conn)["post_id"]
        second = ingestion.create_suggestion(provider_item("bulk-b", source_url="https://investor.example.com/b"), ADMIN, self.conn)["post_id"]
        triage.bulk_transition([first, second], "approve", ADMIN, self.conn)
        self.assertEqual({"draft"}, {row[0] for row in self.conn.execute("SELECT status FROM research_posts WHERE id IN (?,?)", (first, second))})
        third = ingestion.create_suggestion(provider_item("bulk-c", source_url="https://investor.example.com/c"), ADMIN, self.conn)["post_id"]
        with self.assertRaises(core.ResearchValidationError): triage.bulk_transition([first, third], "publish", ADMIN, self.conn)
        self.assertEqual("draft", self.conn.execute("SELECT status FROM research_posts WHERE id=?", (first,)).fetchone()[0])
        result = triage.bulk_transition([first, second], "publish", ADMIN, self.conn)
        self.assertEqual("published", result["status"])

    def test_filters_metrics_and_status_counts(self):
        post_id = core.create_incoming(payload(priority="High", catalyst_type="10-Q", category="SEC Filing", take_origin="provider"), ADMIN,
            metrics=[{"metric_type": "EPS", "label": "EPS", "actual_value": .94, "expected_value": .93, "comparison": "Beat"}], conn=self.conn)
        rows = triage.list_admin_posts(ADMIN, status="incoming", priority="High", catalyst="10-Q", source="Company", query="NVIDIA", time_window="24h", conn=self.conn)
        # Fixed historic timestamp is outside 24h; without the time filter the structured row matches.
        self.assertEqual([], rows)
        rows = triage.list_admin_posts(ADMIN, status="incoming", priority="High", catalyst="10-Q", source="Company", query="NVIDIA", conn=self.conn)
        self.assertEqual(post_id, rows[0]["id"])
        self.assertEqual("eps", rows[0]["metrics"][0]["metric_type"])
        self.assertEqual(1, triage.admin_status_counts(ADMIN, self.conn)["incoming"])


class TriageMigrationTests(unittest.TestCase):
    def test_migration_preserves_existing_statuses_and_is_idempotent(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, is_admin INTEGER NOT NULL)")
        m0001_live_research_feed.upgrade(conn)
        for status in ("draft", "published"):
            conn.execute("INSERT INTO research_posts (id,ticker,company_name,headline,research_notes,category,sentiment,status,author_user_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (status, "NVDA", "NVIDIA", status, "notes", "Earnings", "Neutral", status, 1, "2026-01-01", "2026-01-01"))
        m0002_live_research_triage.upgrade(conn)
        m0002_live_research_triage.upgrade(conn)
        self.assertEqual(["draft", "published"], [row[0] for row in conn.execute("SELECT status FROM research_posts ORDER BY id")])
        columns = {row[1] for row in conn.execute("PRAGMA table_info(research_posts)")}
        self.assertTrue({"priority", "catalyst_type", "source_published_at", "reviewed_at", "reviewed_by_user_id"}.issubset(columns))
        conn.close()

    def test_admin_assets_expose_triage_controls(self):
        root = Path(__file__).resolve().parent
        template = (root / "templates/admin_live_research.html").read_text()
        script = (root / "static/js/live_research_admin.js").read_text()
        for text in ("Urgent Review", "Needs Review", "Rejected / Skipped", "Tradestaar Take", "Bulk approve"):
            self.assertIn(text, template)
        self.assertIn('/api/admin/live-research/bulk', script)
        self.assertIn('/approve', script)
        self.assertIn('/reject', script)


if __name__ == "__main__":
    unittest.main()
