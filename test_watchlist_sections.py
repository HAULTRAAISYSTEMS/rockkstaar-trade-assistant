import os
import tempfile
import unittest
from unittest.mock import patch

import database


class WatchlistSectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = database.DB_PATH
        database.DB_PATH = os.path.join(self.tmp.name, "watchlists.db")
        with patch.dict(os.environ, {"APP_PASSWORD": "test-password"}, clear=False):
            database.init_db()
        self.watchlist = next(
            row for row in database.get_all_watchlists(1) if row["name"] == "BUY & HOLD"
        )

    def tearDown(self):
        database.DB_PATH = self.old_path
        self.tmp.cleanup()

    def test_sections_and_ticker_order_are_persistent(self):
        wl_id = self.watchlist["id"]
        for ticker in ("AAPL", "NVDA", "SCHD"):
            database.add_ticker_to_watchlist(wl_id, ticker)

        foundation = database.create_watchlist_section(wl_id, "Foundation")
        growth = database.create_watchlist_section(wl_id, "Growth")
        self.assertTrue(database.move_watchlist_ticker(wl_id, "SCHD", foundation))
        self.assertTrue(database.move_watchlist_ticker(wl_id, "NVDA", growth))

        database.save_watchlist_order(wl_id, [
            {"section_id": growth, "tickers": ["NVDA"]},
            {"section_id": foundation, "tickers": ["SCHD"]},
            {"section_id": "none", "tickers": ["AAPL"]},
        ])

        structure = database.get_watchlist_structure(wl_id)
        self.assertEqual([s["name"] for s in structure["sections"]], ["Growth", "Foundation"])
        self.assertEqual(structure["sections"][0]["tickers"], ["NVDA"])
        self.assertEqual(structure["unsectioned"], ["AAPL"])
        self.assertEqual(database.get_watchlist_stocks(wl_id), ["NVDA", "SCHD", "AAPL"])

    def test_deleting_section_keeps_its_tickers(self):
        wl_id = self.watchlist["id"]
        database.add_ticker_to_watchlist(wl_id, "GLD")
        safe_haven = database.create_watchlist_section(wl_id, "Safe Haven")
        database.move_watchlist_ticker(wl_id, "GLD", safe_haven)

        database.delete_watchlist_section(safe_haven, wl_id)

        structure = database.get_watchlist_structure(wl_id)
        self.assertEqual(structure["sections"], [])
        self.assertEqual(structure["unsectioned"], ["GLD"])

    def test_personal_ticker_is_mirrored_to_exactly_one_automatic_bucket(self):
        lists = {row["name"]: row["id"] for row in database.get_all_watchlists(1)}
        database.add_ticker_to_watchlist(lists["BATTLEFIELD"], "NVDA")
        database.add_ticker_to_watchlist(lists["SETUPS FORMING"], "NVDA")

        changed = database.sync_ticker_auto_bucket("NVDA", 1, "A+ READY")

        self.assertTrue(changed)
        memberships = set(database.get_ticker_watchlist_ids("NVDA", 1))
        self.assertIn(lists["BATTLEFIELD"], memberships)
        self.assertIn(lists["A+ READY"], memberships)
        self.assertNotIn(lists["SETUPS FORMING"], memberships)
        self.assertEqual(
            memberships & {lists[name] for name in database.DEFAULT_WATCHLISTS},
            {lists["A+ READY"]},
        )

    def test_untracked_ticker_is_not_inserted_into_automatic_bucket(self):
        changed = database.sync_ticker_auto_bucket("GHOST", 1, "SETUPS FORMING")

        self.assertFalse(changed)
        self.assertEqual(database.get_ticker_watchlist_ids("GHOST", 1), [])

    def test_all_tracked_tickers_prioritize_personal_lists_and_deduplicate(self):
        lists = {row["name"]: row["id"] for row in database.get_all_watchlists(1)}
        database.add_ticker_to_watchlist(lists["A+ READY"], "AUTO")
        database.add_ticker_to_watchlist(lists["A+ READY"], "NVDA")
        database.add_ticker_to_watchlist(lists["BATTLEFIELD"], "NVDA")
        database.add_ticker_to_watchlist(lists["BATTLEFIELD"], "META")

        tickers = database.get_user_tracked_tickers(1)

        self.assertEqual(tickers[:2], ["NVDA", "META"])
        self.assertEqual(tickers.count("NVDA"), 1)
        self.assertIn("AUTO", tickers)


if __name__ == "__main__":
    unittest.main()
