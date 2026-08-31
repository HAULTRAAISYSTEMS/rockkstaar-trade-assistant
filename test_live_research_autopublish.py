"""Tests for the stage-two auto-publish gate.

The gate is an opt-in bypass for primary-source regulatory filings. These tests
pin down three things: it is inert by default, it publishes only what it should,
and the Phase 5/6 review boundaries it sits beside are still intact.
"""
import os
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import research_feed as rf
import live_research_ingestion as ing
import live_research_autopublish as ap
from migrations.m0001_live_research_feed import upgrade
from migrations.m0002_live_research_triage import upgrade as triage_upgrade

ADMIN = {"id": 1, "is_admin": True}
NON_ADMIN = {"id": 2, "is_admin": False}


def _iso(hours_ago=0.0):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _enabled(**extra):
    env = {"LIVE_RESEARCH_AUTO_PUBLISH": "1"}
    env.update(extra)
    return mock.patch.dict(os.environ, env, clear=False)


class AutoPublishGateTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        upgrade(self.db)
        triage_upgrade(self.db)
        for key in list(os.environ):
            if key.startswith("LIVE_RESEARCH_AUTO_PUBLISH"):
                del os.environ[key]

    # ---- helpers -------------------------------------------------------
    def sec_filing(self, **overrides):
        """A qualifying primary-source SEC item."""
        data = dict(provider="sec", external_id="0001", ticker="NVDA", company_name="NVIDIA Corp",
                    headline="NVIDIA files 8-K on material agreement",
                    source_name="SEC EDGAR", source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000012.htm",
                    category="SEC Filing", sentiment="Neutral", source_kind="sec",
                    facts=["Registrant entered into a material definitive agreement."],
                    published_at=_iso(1), metadata={"form": "8-K"})
        data.update(overrides)
        return data

    def wire_story(self, **overrides):
        """A wire-service story that must never auto-publish."""
        data = dict(provider="finnhub", external_id="w-1", ticker="NVDA", company_name="NVIDIA Corp",
                    headline="NVIDIA in talks to acquire rival, sources say",
                    source_name="Reuters", source_url="https://www.reuters.com/technology/nvidia-deal",
                    category="Acquisition", sentiment="Bullish", source_kind="provider",
                    facts=["Two people familiar with the matter described early discussions."],
                    published_at=_iso(1), metadata={"event_type": "acquisition_merger"})
        data.update(overrides)
        return data

    def make(self, item):
        result = ing.create_suggestion(item, ADMIN, self.db)
        return self.db.execute("select * from research_posts where id=?", (result["post_id"],)).fetchone()

    def status_of(self, post_id):
        return self.db.execute("select status from research_posts where id=?", (post_id,)).fetchone()["status"]

    # ---- default-off behaviour ----------------------------------------
    def test_disabled_by_default(self):
        self.assertFalse(ap.is_enabled())

    def test_unrecognised_flag_value_is_disabled(self):
        for value in ("", "0", "false", "no", "maybe", "2", "off"):
            with mock.patch.dict(os.environ, {"LIVE_RESEARCH_AUTO_PUBLISH": value}, clear=False):
                self.assertFalse(ap.is_enabled(), f"{value!r} should not enable the gate")

    def test_select_publishable_returns_nothing_when_disabled(self):
        self.make(self.sec_filing())
        self.assertEqual([], ap.select_publishable(self.db))

    def test_auto_publish_is_noop_when_disabled(self):
        row = self.make(self.sec_filing())
        summary = ap.auto_publish(self.db, ADMIN)
        self.assertFalse(summary["enabled"])
        self.assertEqual(0, summary["published"])
        self.assertEqual("incoming", self.status_of(row["id"]))

    # ---- what qualifies ------------------------------------------------
    def test_primary_source_filing_publishes_when_enabled(self):
        row = self.make(self.sec_filing())
        with _enabled():
            summary = ap.auto_publish(self.db, ADMIN)
        self.assertTrue(summary["enabled"])
        self.assertEqual(1, summary["published"])
        self.assertEqual("published", self.status_of(row["id"]))

    def test_published_row_is_stamped_for_audit(self):
        row = self.make(self.sec_filing())
        with _enabled():
            ap.auto_publish(self.db, ADMIN)
        after = self.db.execute("select * from research_posts where id=?", (row["id"],)).fetchone()
        self.assertIn("[auto-published:primary-source]", after["research_notes"])
        self.assertEqual(1, after["reviewed_by_user_id"])
        self.assertIsNotNone(after["published_at"])

    # ---- what must not qualify -----------------------------------------
    def test_wire_service_story_never_publishes(self):
        row = self.make(self.wire_story())
        with _enabled():
            summary = ap.auto_publish(self.db, ADMIN)
        self.assertEqual(0, summary["published"])
        self.assertEqual("incoming", self.status_of(row["id"]))

    def test_wire_service_rejected_even_at_critical_priority(self):
        """Critical priority is not a licence to publish: an M&A rumour is stopped
        at the catalyst check, before the host check is even reached."""
        row = self.make(self.wire_story(metadata={"event_type": "acquisition_merger", "priority": "Critical"}))
        self.assertEqual("Critical", row["priority"])
        ok, reason = ap.evaluate(row)
        self.assertFalse(ok)
        self.assertEqual("not_regulatory_catalyst", reason)

    def test_regulatory_catalyst_on_untrusted_host_rejected(self):
        """A story claiming to be a filing but hosted off sec.gov fails the host check."""
        row = self.make(self.wire_story(external_id="w-2", category="SEC Filing",
                                        source_url="https://www.reuters.com/markets/nvidia-8k",
                                        metadata={"form": "8-K"}))
        self.assertEqual("8-K", row["catalyst_type"])
        ok, reason = ap.evaluate(row)
        self.assertFalse(ok)
        self.assertEqual("not_primary_source", reason)

    def test_stale_filing_rejected(self):
        row = self.make(self.sec_filing(published_at=_iso(48)))
        ok, reason = ap.evaluate(row)
        self.assertFalse(ok)
        self.assertEqual("stale_or_undated", reason)

    def test_future_dated_filing_rejected(self):
        row = self.make(self.sec_filing(published_at=_iso(-5)))
        ok, reason = ap.evaluate(row)
        self.assertFalse(ok)
        self.assertEqual("stale_or_undated", reason)

    def test_lookalike_host_rejected(self):
        row = self.make(self.sec_filing(external_id="0002", source_url="https://www.sec.gov.evil.example/filing"))
        ok, reason = ap.evaluate(row)
        self.assertFalse(ok)
        self.assertEqual("not_primary_source", reason)

    def test_sec_subdomain_accepted(self):
        self.assertTrue(ap.is_primary_host("https://efts.sec.gov/LATEST/search-index?q=x"))
        self.assertFalse(ap.is_primary_host("https://notsec.gov/x"))
        self.assertFalse(ap.is_primary_host(""))

    def test_already_published_row_is_not_republished(self):
        row = self.make(self.sec_filing())
        with _enabled():
            ap.auto_publish(self.db, ADMIN)
            second = ap.auto_publish(self.db, ADMIN)
        self.assertEqual(0, second["published"])

    def test_hand_written_admin_post_is_not_auto_published(self):
        """Rows without an ingestion fingerprint are human-authored; leave them alone."""
        post_id = rf.create_incoming({
            "ticker": "NVDA", "company_name": "NVIDIA Corp",
            "headline": "My own note on the 8-K", "research_notes": "Written by hand.",
            "category": "SEC Filing", "sentiment": "Neutral", "source_name": "SEC EDGAR",
            "source_url": "https://www.sec.gov/Archives/edgar/data/1045810/x.htm",
            "priority": "High", "catalyst_type": "8-K", "source_published_at": _iso(1),
        }, ADMIN, conn=self.db)
        row = self.db.execute("select * from research_posts where id=?", (post_id,)).fetchone()
        ok, reason = ap.evaluate(row)
        self.assertFalse(ok)
        self.assertEqual("not_provider_ingested", reason)

    # ---- limits and permissions ----------------------------------------
    def test_publish_limit_caps_one_run(self):
        for i in range(5):
            self.make(self.sec_filing(external_id=f"lim-{i}",
                                      source_url=f"https://www.sec.gov/Archives/edgar/data/1045810/{i}.htm"))
        with _enabled(LIVE_RESEARCH_AUTO_PUBLISH_LIMIT="2"):
            summary = ap.auto_publish(self.db, ADMIN)
        self.assertEqual(2, summary["published"])

    def test_limit_falls_back_on_garbage_value(self):
        with _enabled(LIVE_RESEARCH_AUTO_PUBLISH_LIMIT="not-a-number"):
            self.assertEqual(10, ap.publish_limit())
        with _enabled(LIVE_RESEARCH_AUTO_PUBLISH_LIMIT="9999"):
            self.assertEqual(10, ap.publish_limit())

    def test_max_age_falls_back_on_garbage_value(self):
        with _enabled(LIVE_RESEARCH_AUTO_PUBLISH_MAX_AGE_HOURS="huge"):
            self.assertEqual(6, ap.max_age_hours())

    def test_non_admin_actor_is_rejected(self):
        self.make(self.sec_filing())
        with _enabled():
            with self.assertRaises(rf.ResearchPermissionError):
                ap.auto_publish(self.db, NON_ADMIN)

    def test_malformed_row_denies_rather_than_raises(self):
        ok, reason = ap.evaluate({"status": "incoming"})
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("priority_below_threshold") or reason.startswith("evaluation_error"))


class BoundariesStillHoldTests(unittest.TestCase):
    """The gate must not have loosened the Phase 5/6 review boundaries."""

    def test_ingestion_module_still_exposes_no_publish_api(self):
        self.assertFalse(hasattr(ing, "publish_post"))
        self.assertFalse(hasattr(ing, "announce_published"))

    def test_take_module_still_exposes_no_publish_api(self):
        import tradestaar_take as t
        self.assertFalse(hasattr(t, "publish_take"))
        self.assertFalse(hasattr(t, "announce_published"))

    def test_publish_post_still_requires_admin_and_draft_status(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        upgrade(db)
        triage_upgrade(db)
        result = ing.create_suggestion(
            dict(provider="sec", external_id="b-1", ticker="NVDA", company_name="NVIDIA Corp",
                 headline="NVIDIA files 8-K", source_name="SEC EDGAR",
                 source_url="https://www.sec.gov/Archives/edgar/data/1045810/b1.htm",
                 category="SEC Filing", sentiment="Neutral", source_kind="sec",
                 facts=["A fact."], published_at=_iso(1), metadata={"form": "8-K"}),
            ADMIN, db)
        with self.assertRaises(rf.ResearchPermissionError):
            rf.publish_post(result["post_id"], NON_ADMIN, conn=db)
        # status is 'incoming', not 'draft' -> publication still refused
        with self.assertRaises(rf.ResearchValidationError):
            rf.publish_post(result["post_id"], ADMIN, conn=db)

    def test_ai_take_is_excluded_from_auto_publication(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        upgrade(db)
        triage_upgrade(db)
        post_id = rf.create_incoming({
            "ticker": "NVDA", "company_name": "NVIDIA Corp", "headline": "AI take on the filing",
            "research_notes": "Generated.\n[ingestion:" + ("a" * 64) + "]",
            "category": "SEC Filing", "sentiment": "Neutral", "source_name": "SEC EDGAR",
            "source_url": "https://www.sec.gov/Archives/edgar/data/1045810/ai.htm",
            "priority": "High", "catalyst_type": "8-K", "take_origin": "ai",
            "source_published_at": _iso(1),
        }, ADMIN, conn=db)
        row = db.execute("select * from research_posts where id=?", (post_id,)).fetchone()
        ok, reason = ap.evaluate(row)
        self.assertFalse(ok)
        self.assertEqual("ai_generated", reason)


if __name__ == "__main__":
    unittest.main()
