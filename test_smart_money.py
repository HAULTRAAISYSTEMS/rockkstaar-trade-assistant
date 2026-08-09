import os
import unittest
from unittest.mock import Mock, patch

import smart_money


FORM4_XML = b"""<ownershipDocument>
<reportingOwner><reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector><value>1</value></isDirector></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable><nonDerivativeTransaction>
<transactionDate><value>2026-08-01</value></transactionDate>
<transactionCoding><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>100</value></transactionShares>
<transactionPricePerShare><value>25.50</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""


class TestSecForm4Parser(unittest.TestCase):
    def test_open_market_purchase_preserves_code_and_value(self):
        rows = smart_money._transaction_rows(
            FORM4_XML, "TEST", "https://www.sec.gov/filing", "2026-08-03"
        )
        self.assertEqual(rows[0]["kind"], "BUY")
        self.assertEqual(rows[0]["code"], "P")
        self.assertEqual(rows[0]["value"], 2550)
        self.assertEqual(rows[0]["role"], "Director")


class TestCongressVerification(unittest.TestCase):
    def tearDown(self):
        smart_money._cache.clear()

    def test_only_official_disclosure_hosts_are_accepted(self):
        self.assertTrue(smart_money._official_congress_url(
            "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/1.pdf"
        ))
        self.assertTrue(smart_money._official_congress_url(
            "https://efdsearch.senate.gov/search/view/ptr/abc/"
        ))
        self.assertFalse(smart_money._official_congress_url("https://example.com/fake"))

    @patch.dict(os.environ, {"CONGRESS_TRADES_JSON_URL": "https://feed.example/trades"})
    @patch("smart_money.requests.get")
    def test_unverified_provider_rows_are_rejected(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {"ticker": "NVDA", "source_url": "https://example.com/fake"},
            {"ticker": "AAPL", "member": "Example Member", "type": "purchase",
             "source_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/1.pdf"},
        ]
        get.return_value = response
        rows, status = smart_money.fetch_congress_trades()
        self.assertTrue(status["available"])
        self.assertEqual([row["ticker"] for row in rows], ["AAPL"])


if __name__ == "__main__":
    unittest.main()
