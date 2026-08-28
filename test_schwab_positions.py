import unittest
from pathlib import Path
from unittest.mock import patch

import schwab


def account_with(*positions):
    return {
        "hashValue": "hash-1",
        "securitiesAccount": {
            "accountNumber": "1234",
            "currentBalances": {"liquidationValue": 1000},
            "initialBalances": {"liquidationValue": 900},
            "positions": list(positions),
        },
    }


def equity(symbol="AAPL", quantity=5, **overrides):
    row = {
        "instrument": {"assetType": "EQUITY", "symbol": symbol},
        "longQuantity": quantity,
        "shortQuantity": 0,
        "averagePrice": 10,
        "marketValue": 60,
        "longOpenProfitLoss": 10,
        "currentDayProfitLoss": 2,
        "currentDayProfitLossPercentage": 3.4,
    }
    row.update(overrides)
    return row


class SchwabPositionNormalizationTests(unittest.TestCase):
    def test_one_equity_position_has_complete_display_mapping(self):
        acct = schwab._normalize_account(account_with(equity()))
        self.assertEqual(acct["position_count"], 1)
        self.assertEqual(len(acct["positions"]), 1)
        pos = acct["positions"][0]
        self.assertEqual(pos["symbol"], "AAPL")
        self.assertEqual(pos["quantity"], 5)
        self.assertEqual(pos["avg_price"], 10)
        self.assertEqual(pos["last_price"], 12)
        self.assertEqual(pos["unrealized"], 10)
        self.assertEqual(pos["unrealized_pct"], 20)

    def test_multiple_equity_and_option_positions_share_canonical_list(self):
        option = {
            "instrument": {"assetType": "OPTION", "symbol": "SPY  260828C00770000", "multiplier": 100},
            "longQuantity": 1,
            "averageLongPrice": 3,
            "marketValue": 400,
            "longOpenProfitLoss": 100,
        }
        acct = schwab._normalize_account(account_with(equity(), option))
        self.assertEqual(acct["position_count"], 2)
        self.assertEqual(len(acct["positions"]), 2)
        self.assertEqual(len(acct["equity_positions"]), 1)
        self.assertEqual(len(acct["option_positions"]), 1)
        opt = next(p for p in acct["positions"] if p["asset_type"] == "OPTION")
        self.assertEqual(opt["last_price"], 4)
        self.assertEqual(opt["cost_basis"], 300)
        self.assertAlmostEqual(opt["unrealized_pct"], 33.33, places=2)

    def test_zero_positions_returns_matching_empty_collections(self):
        acct = schwab._normalize_account(account_with())
        self.assertEqual(acct["position_count"], 0)
        self.assertEqual(acct["positions"], [])

    def test_malformed_and_missing_position_fields_do_not_crash_or_render(self):
        malformed = {"instrument": {"assetType": "EQUITY", "symbol": ""}, "longQuantity": "bad"}
        acct = schwab._normalize_account(account_with(None, {}, malformed))
        self.assertEqual(acct["position_count"], 0)
        self.assertEqual(acct["total_unrealized"], 0)

    def test_positive_quantity_is_retained(self):
        acct = schwab._normalize_account(account_with(equity(quantity=2)))
        self.assertEqual(acct["positions"][0]["quantity"], 2)

    def test_zero_quantity_record_is_ignored(self):
        closed = equity(quantity=0, shortQuantity=0, longOpenProfitLoss=99)
        acct = schwab._normalize_account(account_with(closed))
        self.assertEqual(acct["position_count"], 0)
        self.assertEqual(acct["positions"], [])
        self.assertEqual(acct["total_unrealized"], 0)

    def test_summary_count_matches_canonical_rendered_positions(self):
        accounts = [
            schwab._normalize_account(account_with(equity("AAPL"))),
            schwab._normalize_account(account_with(equity("MSFT"), equity("NVDA"))),
        ]
        with patch("schwab.fetch_accounts", return_value=accounts):
            summary = schwab.get_account_summary()
        self.assertEqual(summary["open_positions"], 3)
        self.assertEqual(summary["open_positions"], sum(len(a["positions"]) for a in summary["accounts"]))

    def test_short_position_uses_short_average_and_open_pnl(self):
        short = equity(
            quantity=0,
            shortQuantity=4,
            averagePrice=None,
            averageShortPrice=25,
            marketValue=-80,
            longOpenProfitLoss=999,
            shortOpenProfitLoss=20,
        )
        acct = schwab._normalize_account(account_with(short))
        pos = acct["positions"][0]
        self.assertEqual(pos["quantity"], -4)
        self.assertEqual(pos["avg_price"], 25)
        self.assertEqual(pos["last_price"], 20)
        self.assertEqual(pos["unrealized"], 20)
        self.assertEqual(pos["unrealized_pct"], 20)
        self.assertEqual(acct["total_unrealized"], 20)

    def test_account_template_renders_the_same_canonical_collection_as_count(self):
        template = Path("templates/account.html").read_text()
        self.assertIn("{% for p in a.positions %}", template)
        self.assertNotIn("{% for p in a.equity_positions %}", template)
        self.assertIn("p.last_price", template)
        self.assertIn("p.unrealized_pct", template)


if __name__ == "__main__":
    unittest.main()
