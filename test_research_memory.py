from datetime import datetime, timezone
import sqlite3
import unittest

from migrations import m0001_live_research_feed, m0002_live_research_triage, m0005_research_memory
import research_memory as memory


def manual_card(**extra):
    data = {
        "ticker": "amd",
        "company_name": "Advanced Micro Devices",
        "headline": "AMD raises data-center guidance",
        "source_name": "Company IR",
        "source_url": "https://example.com/amd",
        "why_it_matters": "The data-center thesis is progressing faster than expected.",
        "key_change": "Guidance increased 8%.",
        "thesis_impact": "strengthened",
        "disconfirming_evidence": "Growth falls below the revised guide.",
    }
    data.update(extra)
    return data


class ResearchMemoryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
        self.db.execute("INSERT INTO users(id) VALUES (1),(2)")
        m0001_live_research_feed.upgrade(self.db)
        m0002_live_research_triage.upgrade(self.db)
        m0005_research_memory.upgrade(self.db)

    def tearDown(self):
        self.db.close()

    def test_create_lists_and_keeps_users_isolated(self):
        card_id = memory.create_card(1, manual_card(), conn=self.db)
        card = memory.get_card(1, card_id, conn=self.db)
        self.assertEqual("AMD", card["ticker"])
        self.assertLessEqual(card["due_at"], datetime.now(timezone.utc).isoformat())
        self.assertEqual(card_id, memory.list_cards(1, conn=self.db)[0]["id"])
        self.assertEqual([], memory.list_cards(2, conn=self.db))
        self.assertIsNone(memory.get_card(2, card_id, conn=self.db))

    def test_capture_from_published_research_is_idempotent(self):
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            """INSERT INTO research_posts
            (id,ticker,company_name,headline,research_notes,category,sentiment,status,
             author_user_id,created_at,updated_at,published_at,source_name,source_url)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("post-1", "NVDA", "NVIDIA", "NVIDIA files a 10-Q", "Notes", "Filings",
             "Neutral", "published", 1, now, now, now, "SEC", "https://sec.gov/filing"),
        )
        first = memory.create_card(1, {"research_post_id": "post-1", "why_it_matters": "Margins changed.", "thesis_impact": "uncertain"}, conn=self.db)
        second = memory.create_card(1, {"research_post_id": "post-1", "why_it_matters": "A duplicate note.", "thesis_impact": "neutral"}, conn=self.db)
        self.assertEqual(first, second)
        self.assertEqual(1, len(memory.list_cards(1, conn=self.db)))
        self.assertEqual("NVIDIA files a 10-Q", memory.get_card(1, first, conn=self.db)["headline"])

    def test_review_ratings_schedule_and_audit(self):
        card_id = memory.create_card(1, manual_card(), conn=self.db)
        remembered = memory.rate_card(1, card_id, "remembered", conn=self.db)
        self.assertEqual(1, remembered["box"])
        card = memory.get_card(1, card_id, conn=self.db)
        self.assertEqual(1, card["review_count"])
        self.assertEqual(1, card["remembered_count"])
        self.assertIsNone(memory.due_card(1, conn=self.db))
        self.db.execute("UPDATE research_memory_cards SET due_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", card_id))
        forgot = memory.rate_card(1, card_id, "forgot", conn=self.db)
        self.assertEqual(1, forgot["box"])
        self.assertEqual(2, self.db.execute("SELECT COUNT(*) AS n FROM research_memory_reviews").fetchone()["n"])

    def test_archive_and_validation(self):
        card_id = memory.create_card(1, manual_card(), conn=self.db)
        memory.archive_card(1, card_id, conn=self.db)
        self.assertEqual([], memory.list_cards(1, conn=self.db))
        with self.assertRaises(memory.ResearchMemoryError):
            memory.create_card(1, manual_card(source_url="javascript:alert(1)"), conn=self.db)
        with self.assertRaises(memory.ResearchMemoryError):
            memory.create_card(1, manual_card(why_it_matters=""), conn=self.db)


if __name__ == "__main__":
    unittest.main()
