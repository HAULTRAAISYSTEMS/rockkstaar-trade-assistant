import unittest
from datetime import date, timedelta

from terminal_intelligence import build_insider_payload, build_terminal_intelligence


class TerminalIntelligenceTests(unittest.TestCase):
    def test_builds_ticker_scoped_panels_without_fabricating_missing_data(self):
        payload = build_terminal_intelligence(
            "meta",
            {"ticker": "META", "current_price": 600, "rel_volume": 1.4,
             "news_headlines": ["Company-specific headline"]},
            {"news": [{"ticker": "OTHER", "headline": "Ignore me"}], "earnings": {}},
        )
        self.assertEqual(payload["ticker"], "META")
        self.assertEqual([row["headline"] for row in payload["news"]], ["Company-specific headline"])
        self.assertEqual(payload["earnings"], [])
        self.assertIsNone(payload["context"]["next_earnings"])

    def test_earnings_becomes_chart_event_contract(self):
        earnings_date = (date.today() + timedelta(days=1)).isoformat()
        payload = build_terminal_intelligence(
            "META", {}, {"earnings": {"this_week": [
                {"ticker": "META", "date": earnings_date, "time_label": "After close", "source": "NASDAQ"}
            ]}}
        )
        self.assertEqual(payload["events"][0]["type"], "earnings")
        self.assertEqual(payload["events"][0]["date"], earnings_date)

    def test_stale_stock_snapshot_is_not_presented_as_next_earnings(self):
        payload = build_terminal_intelligence("META", {"earnings_date": "2020-04-30"}, {})
        self.assertEqual(payload["earnings"], [])
        self.assertIsNone(payload["context"]["next_earnings"])

    def test_etf_is_labelled_so_corporate_events_are_not_shown_as_missing(self):
        payload = build_terminal_intelligence("VOO", {"company_name": "Vanguard S&P 500 ETF"}, {})
        self.assertEqual(payload["asset_type"], "ETF")
        self.assertIsNone(payload["context"]["next_earnings"])

    def test_insider_summary_uses_only_reported_values(self):
        payload = build_insider_payload("META", [
            {"ticker": "META", "owner": "Jane Doe", "role": "Director", "kind": "BUY", "code": "P",
             "shares": 100, "value": 2500, "trade_date": "2026-08-01", "filed_at": "2026-08-03",
             "ownership_after": 400, "source_url": "https://www.sec.gov/example"},
        ], {"available": True})
        self.assertEqual(payload["rows"][0]["ownership_after"], 400)
        self.assertEqual(payload["summary"]["reported_buy_value"], 2500)
        self.assertEqual(payload["summary"]["sells"], 0)


if __name__ == "__main__":
    unittest.main()
