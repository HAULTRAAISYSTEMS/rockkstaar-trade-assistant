import json
import os
import unittest
from datetime import date
from unittest.mock import patch
import wealth_etf_ingestion as ingestion
from wealth_portfolio_intelligence import classify


class IssuerFeedTests(unittest.TestCase):
    def test_skyhynix_is_equity(self):
        self.assertEqual(classify("SKHY")["asset_type"], "equity")

    def test_ambiguous_spcx_blocked(self):
        self.assertEqual(classify("SPCX")["asset_type"], "unresolved")

    def test_rejects_untrusted_host(self):
        config = {"SPCK": {"url": "https://other.example/holdings.json",
                           "hostname": "issuer.example", "identifier": "ABC", "source": "issuer"}}
        with patch.dict(os.environ, {"TRADESTAAR_ETF_ISSUER_FEEDS_JSON": json.dumps(config)}):
            result, errors = ingestion.fetch_issuer_snapshots(["SPCK"],
                fetch=lambda *a, **k: self.fail("Must not call untrusted host"))
        self.assertEqual(result, {})
        self.assertIn("SPCK", errors)

    def test_identifier_match_and_overlap(self):
        config = {"SPCK": {"url": "https://issuer.example/holdings.json",
                           "hostname": "issuer.example", "identifier": "ABC", "source": "Issuer export"}}
        class Response:
            content = b'{}'
            def raise_for_status(self): pass
            def json(self):
                return {"identifier": "ABC", "as_of": date.today().isoformat(),
                        "constituents": [{"ticker": "NVDA", "weight_pct": 100, "sector": "Technology"}]}
        with patch.dict(os.environ, {"TRADESTAAR_ETF_ISSUER_FEEDS_JSON": json.dumps(config)}):
            result, errors = ingestion.fetch_issuer_snapshots(["SPCK"],
                fetch=lambda *a, **k: Response())
        self.assertFalse(errors)
        overlaps = ingestion.company_overlap({"holdings": {"NVDA": 500, "SPCK": 500},
                                              "total_value": 1000}, result)
        self.assertEqual(overlaps[0]["combined_weight_pct"], 100)


if __name__ == "__main__":
    unittest.main()
