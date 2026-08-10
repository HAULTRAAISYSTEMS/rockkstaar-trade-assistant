import unittest

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
        payload = build_terminal_intelligence(
            "META", {}, {"earnings": {"this_week": [
                {"ticker": "META", "date": "2026-08-12", "time_label": "After close", "source": "NASDAQ"}
            ]}}
        )
        self.assertEqual(payload["events"][0]["type"], "earnings")
        self.assertEqual(payload["events"][0]["date"], "2026-08-12")

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
