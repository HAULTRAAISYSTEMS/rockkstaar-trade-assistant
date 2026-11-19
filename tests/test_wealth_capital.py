import unittest
import wealth_capital


def candidate(ticker="KLAC", status="BUY ZONE", sector="Technology"):
    return {"ticker": ticker, "sector": sector, "price": 100,
            "evaluation": {"status": status, "valuation": {"in_zone": True, "zone_text": "Buy zone"},
                           "gates": {"moat": {"score": 9}, "financials": {"score": 8},
                                     "future": {"score": 9}}}}


class CapitalEngineTests(unittest.TestCase):
    def test_broker_overrides_ledger_without_double_count(self):
        state = wealth_capital.portfolio_state(
            {"holdings": [{"ticker": "KLAC", "market_value": 1000}], "cash": 100},
            [{"ticker": "KLAC", "market_value": 1500}])
        self.assertEqual(state["holdings"]["KLAC"], 1500)
        self.assertEqual(state["total_value"], 1600)

    def test_missing_broker_value_blocks_allocation(self):
        state = wealth_capital.portfolio_state(
            {"holdings": [{"ticker": "KLAC", "market_value": 1000}], "cash": 100},
            [{"ticker": "KLAC", "market_value": None}])
        self.assertEqual(state["unknown_market_values"], ["KLAC"])
        result = wealth_capital.balanced_rank(750, [candidate()], state)
        self.assertFalse(result["rows"][0]["eligible"])

    def test_concentration_guardrail(self):
        state = {"holdings": {"KLAC": 4000}, "cash": 1000, "total_value": 5000,
                 "sectors": {"Technology": 4000}, "unknown_market_values": []}
        result = wealth_capital.balanced_rank(750, [candidate()], state)
        self.assertFalse(result["rows"][0]["eligible"])
        self.assertTrue(any("Position" in b for b in result["rows"][0]["blocks"]))

    def test_new_money_changes_denominator(self):
        state = {"holdings": {"KLAC": 1000}, "cash": 2000, "total_value": 3000,
                 "sectors": {"Technology": 1000}, "unknown_market_values": []}
        a = wealth_capital.balanced_rank(750, [candidate()], state, position_limit=100, sector_limit=100)
        b = wealth_capital.balanced_rank(750, [candidate()], state, position_limit=100, sector_limit=100, cash_is_existing=True)
        self.assertLess(a["rows"][0]["weight_after_pct"], b["rows"][0]["weight_after_pct"])

    def test_missing_evidence_not_zero(self):
        c = candidate()
        c["evaluation"]["gates"]["financials"]["score"] = None
        state = {"holdings": {"KLAC": 100}, "cash": 2000, "total_value": 2100,
                 "sectors": {"Technology": 100}, "unknown_market_values": []}
        r = wealth_capital.balanced_rank(750, [c], state)
        self.assertIsNone(r["rows"][0]["components"]["financials"])
        self.assertFalse(r["rows"][0]["eligible"])

    def test_hold_cash_when_not_eligible(self):
        state = {"holdings": {"KLAC": 100}, "cash": 2000, "total_value": 2100,
                 "sectors": {"Technology": 100}, "unknown_market_values": []}
        r = wealth_capital.balanced_rank(750, [candidate(status="HOLD")], state)
        self.assertIn("HOLD CASH", r["verdict"])


if __name__ == "__main__":
    unittest.main()
