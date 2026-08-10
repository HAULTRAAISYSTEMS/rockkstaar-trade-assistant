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
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>450</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
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
        self.assertEqual(rows[0]["ownership_after"], 450)

    def test_non_market_acquisition_is_not_mislabeled_as_buy(self):
        xml = FORM4_XML.replace(b"<transactionCode>P</transactionCode>", b"<transactionCode>A</transactionCode>")
        rows = smart_money._transaction_rows(xml, "TEST", "https://www.sec.gov/filing", "2026-08-03")
        self.assertEqual(rows[0]["kind"], "OTHER")

    def test_xsl_primary_document_is_normalized_to_raw_xml(self):
        raw_url, filing_url = smart_money._filing_urls(
            "0001326801",
            "0000950103-26-011964",
            "xslF345X06/ownership.xml",
        )
        self.assertEqual(
            raw_url,
            "https://www.sec.gov/Archives/edgar/data/1326801/000095010326011964/ownership.xml",
        )
        self.assertEqual(
            filing_url,
            "https://www.sec.gov/Archives/edgar/data/1326801/000095010326011964/0000950103-26-011964-index.html",
        )

    def test_compensation_codes_have_plain_english_non_buy_labels(self):
        self.assertEqual(smart_money.form4_code_details("F")[0], "TAX WITHHOLDING")
        self.assertEqual(smart_money.form4_code_details("G")[0], "GIFT")
        self.assertEqual(smart_money.form4_code_details("A")[0], "STOCK AWARD")
        self.assertEqual(smart_money.form4_code_details("P")[0], "OPEN-MARKET BUY")


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
