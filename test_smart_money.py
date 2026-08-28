import os
import copy
import unittest
from datetime import date
from unittest.mock import Mock, patch

import smart_money
from terminal_intelligence import build_insider_payload


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

RICH_FORM4_XML = b"""<ownershipDocument>
<aff10b5One>true</aff10b5One>
<reportingOwner><reportingOwnerId><rptOwnerCik>000123</rptOwnerCik><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isOfficer><value>1</value></isOfficer><officerTitle>Chief Financial Officer</officerTitle></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable>
<nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle><transactionDate><value>2026-08-26</value></transactionDate>
<transactionCoding><transactionCode>P</transactionCode><footnoteId id="F1"/></transactionCoding>
<transactionAmounts><transactionShares><value>1000</value></transactionShares><transactionPricePerShare><value>200</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>11000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
<ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature></nonDerivativeTransaction>
<nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle><transactionDate><value>2026-08-26</value></transactionDate>
<transactionCoding><transactionCode>F</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>25</value></transactionShares><transactionPricePerShare><value>200</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>10975</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
<ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature></nonDerivativeTransaction>
</nonDerivativeTable>
<derivativeTable><derivativeTransaction><securityTitle><value>Stock Option</value></securityTitle><conversionOrExercisePrice><value>75</value></conversionOrExercisePrice><transactionDate><value>2026-08-26</value></transactionDate><transactionCoding><transactionCode>M</transactionCode></transactionCoding><transactionAmounts><transactionShares><value>50</value></transactionShares><transactionPricePerShare><value>0</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts><postTransactionAmounts><numberOfDerivativeSecuritiesBeneficiallyOwnedFollowingTransaction><value>450</value></numberOfDerivativeSecuritiesBeneficiallyOwnedFollowingTransaction></postTransactionAmounts><ownershipNature><directOrIndirectOwnership><value>I</value></directOrIndirectOwnership><natureOfOwnership><value>By trust</value></natureOfOwnership></ownershipNature><underlyingSecurity><underlyingSecurityTitle><value>Common Stock</value></underlyingSecurityTitle><underlyingSecurityShares><value>50</value></underlyingSecurityShares></underlyingSecurity></derivativeTransaction></derivativeTable>
<footnotes><footnote id="F1">Transaction made pursuant to a Rule 10b5-1 trading plan.</footnote></footnotes>
</ownershipDocument>"""


class TestSecForm4Parser(unittest.TestCase):
    def tearDown(self):
        smart_money._cache.clear()

    def test_form4_refresh_clears_results_but_preserves_cik_map(self):
        smart_money._cache.update({
            "sec:ticker-ciks": (1, {"META": "0001326801"}),
            "sec:form4:META": (1, [{"ticker": "META"}]),
        })

        smart_money.clear_sec_form4_cache()

        self.assertIn("sec:ticker-ciks", smart_money._cache)
        self.assertNotIn("sec:form4:META", smart_money._cache)

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

    def test_malformed_shares_are_ignored_and_missing_holdings_are_not_inferred(self):
        malformed = FORM4_XML.replace(b"<value>100</value>", b"<value>not-a-number</value>", 1)
        self.assertEqual([], smart_money._transaction_rows(malformed, "TEST", "https://sec/filing", "2026-08-03"))
        missing = FORM4_XML.replace(b"<sharesOwnedFollowingTransaction><value>450</value></sharesOwnedFollowingTransaction>", b"")
        row = smart_money._transaction_rows(missing, "TEST", "https://sec/filing", "2026-08-03")[0]
        self.assertIsNone(row["ownership_after"])
        self.assertIsNone(row["holdings_change_pct"])

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

    def test_rich_parser_preserves_ownership_footnotes_derivatives_and_contract(self):
        rows = smart_money._transaction_rows(
            RICH_FORM4_XML, "TEST", "https://www.sec.gov/filing", "2026-08-27",
            accession="0001-26-000001", form_type="4",
        )
        self.assertEqual(3, len(rows))
        purchase = rows[0]
        for key in ("ticker", "owner", "role", "kind", "code", "shares", "price", "value", "ownership_after", "trade_date", "filed_at", "source_url"):
            self.assertIn(key, purchase)
        self.assertEqual("000123", purchase["owner_cik"])
        self.assertEqual("D", purchase["direct_indirect"])
        self.assertEqual("A", purchase["acquired_disposed"])
        self.assertAlmostEqual(10.0, purchase["holdings_change_pct"])
        self.assertTrue(purchase["transaction_10b5_1"])
        self.assertIn("Rule 10b5-1", purchase["footnotes"][0])
        derivative = rows[2]
        self.assertTrue(derivative["derivative"])
        self.assertEqual("Stock Option", derivative["security_title"])
        self.assertEqual("Common Stock", derivative["underlying_security_title"])
        self.assertEqual("I", derivative["direct_indirect"])

    def test_terminal_consumer_accepts_enriched_raw_rows(self):
        rows = smart_money._transaction_rows(
            RICH_FORM4_XML, "TEST", "https://www.sec.gov/filing", "2026-08-27",
            accession="0001-26-000001", form_type="4",
        )
        payload = build_insider_payload("TEST", rows, {"available": True})
        self.assertTrue(payload["ok"])
        self.assertEqual(3, len(payload["rows"]))
        self.assertEqual("DOE JANE", payload["rows"][0]["person"])
        self.assertEqual("https://www.sec.gov/filing", payload["rows"][0]["sec_url"])


class TestInsiderDashboard(unittest.TestCase):
    def setUp(self):
        self.rows = smart_money._transaction_rows(
            RICH_FORM4_XML, "TEST", "https://www.sec.gov/filing", "2026-08-27",
            accession="0001-26-000001", form_type="4",
        )

    def tearDown(self):
        smart_money._cache.clear()

    def test_transactions_are_aggregated_without_losing_raw_details(self):
        events = smart_money.aggregate_form4_events(self.rows)
        self.assertEqual(1, len(events))
        self.assertEqual(3, events[0]["transaction_count"])
        self.assertEqual({"P", "F", "M"}, set(events[0]["codes"]))
        self.assertEqual(3, len(events[0]["transactions"]))
        self.assertEqual("https://www.sec.gov/filing", events[0]["source_url"])

    def test_non_market_codes_do_not_become_directional_signals(self):
        non_market = [row for row in self.rows if row["code"] in {"F", "M"}]
        for row in non_market:
            row["filing_key"] += row["code"]
            row["accession"] += row["code"]
        dashboard = smart_money.build_insider_dashboard(non_market, today=date(2026, 8, 27))
        self.assertTrue(all(event["activity"] == "NON_MARKET" for event in dashboard["events"]))
        self.assertTrue(all(event["signal"]["label"] == "Neutral" for event in dashboard["events"]))
        self.assertTrue(all(event["signal"]["score"] == 0 for event in dashboard["events"]))

    def test_non_market_event_exposes_reported_facts_without_netting_them(self):
        non_market = [copy.deepcopy(row) for row in self.rows if row["code"] in {"F", "M"}]
        event = smart_money.aggregate_form4_events(non_market)[0]

        self.assertEqual("NON_MARKET", event["activity"])
        self.assertEqual(2, event["non_market_count"])
        self.assertEqual(0, event["non_market_acquired_shares"])
        self.assertEqual(75, event["non_market_disposed_shares"])
        self.assertEqual(5000, event["non_market_reported_value"])
        self.assertEqual(2, event["non_market_priced_count"])
        self.assertTrue(event["non_market_value_complete"])
        self.assertIsNone(event["holdings_change_pct"])

    def test_executive_purchase_can_be_strong_bullish_with_reasons(self):
        purchase = [copy.deepcopy(row) for row in self.rows if row["code"] == "P"]
        dashboard = smart_money.build_insider_dashboard(purchase, today=date(2026, 8, 27))
        signal = dashboard["events"][0]["signal"]
        self.assertEqual("Strong Bullish", signal["label"])
        self.assertGreaterEqual(signal["score"], 60)
        self.assertTrue(any("CEO/CFO" in reason for reason in signal["reasons"]))

    def test_simple_sale_is_not_strong_bearish_and_shows_percentage(self):
        sale = copy.deepcopy(self.rows[0])
        sale.update({"code": "S", "kind": "SELL", "label": "OPEN-MARKET SALE", "acquired_disposed": "D", "shares": 100, "price": 20, "value": 2000, "ownership_after": 900, "holdings_change_pct": -10.0, "transaction_10b5_1": False, "filing_10b5_1": False})
        dashboard = smart_money.build_insider_dashboard([sale], today=date(2026, 8, 27))
        event = dashboard["events"][0]
        self.assertAlmostEqual(-10.0, event["holdings_change_pct"])
        self.assertNotEqual("Strong Bearish", event["signal"]["label"])
        self.assertIn("sale alone does not establish", event["why_this_matters"])

    def test_10b5_sale_has_reduced_weight(self):
        sale = copy.deepcopy(self.rows[0])
        sale.update({"code": "S", "kind": "SELL", "label": "OPEN-MARKET SALE", "acquired_disposed": "D", "shares": 400, "price": 20, "value": 8000, "ownership_after": 600, "holdings_change_pct": -40.0, "transaction_10b5_1": True, "filing_10b5_1": True})
        dashboard = smart_money.build_insider_dashboard([sale], today=date(2026, 8, 27))
        signal = dashboard["events"][0]["signal"]
        self.assertTrue(any("10b5-1" in reason for reason in signal["reasons"]))
        self.assertGreater(signal["score"], -26)

    def test_cluster_repeat_summary_filters_and_alert_matches(self):
        rows = []
        for index, owner in enumerate(("ONE", "TWO", "THREE"), 1):
            row = copy.deepcopy(self.rows[0])
            row.update({"owner": owner, "owner_cik": str(index), "filing_key": f"filing-{index}", "accession": f"accession-{index}", "source_url": f"https://sec/{index}"})
            rows.append(row)
        repeat = copy.deepcopy(rows[0]); repeat.update({"filing_key": "filing-repeat", "accession": "accession-repeat", "source_url": "https://sec/repeat"}); rows.append(repeat)
        rules = {"cluster_buy_3": True, "holdings_increase_5": True}
        dashboard = smart_money.build_insider_dashboard(rows, filters={"cluster": True, "days": 30}, alert_rules=rules, today=date(2026, 8, 27))
        self.assertEqual(4, dashboard["summary_30"]["buy_events"])
        self.assertEqual(1, dashboard["summary_30"]["cluster_buys"])
        self.assertEqual(4, len(dashboard["events"]))
        self.assertTrue(all(event["cluster_buyers"] == 3 for event in dashboard["events"]))
        self.assertTrue(all(event["alert_matches"] for event in dashboard["events"]))
        self.assertEqual(2, next(event for event in dashboard["events"] if event["owner"] == "ONE")["repeat_purchase_count"])

    def test_code_and_minimum_value_filters(self):
        dashboard = smart_money.build_insider_dashboard(self.rows, filters={"transaction_type": "F", "minimum_value": 1000, "days": 30}, today=date(2026, 8, 27))
        self.assertEqual(1, len(dashboard["events"]))
        empty = smart_money.build_insider_dashboard(self.rows, filters={"minimum_value": 9999999, "days": 30}, today=date(2026, 8, 27))
        self.assertEqual([], empty["events"])

    def test_seven_and_thirty_day_summaries_use_transaction_dates(self):
        recent = copy.deepcopy(self.rows[0])
        older = copy.deepcopy(self.rows[0])
        older.update({"trade_date": "2026-08-10", "filing_key": "older", "accession": "older", "source_url": "https://sec/older"})
        dashboard = smart_money.build_insider_dashboard([recent, older], today=date(2026, 8, 27))
        self.assertEqual(1, dashboard["summary_7"]["buy_events"])
        self.assertEqual(2, dashboard["summary_30"]["buy_events"])

    def test_cached_rows_are_sliced_per_consumer_limit(self):
        key = "sec:form4:TEST:days=recent"
        smart_money._cache[key] = (smart_money.time.time(), ([copy.deepcopy(self.rows[0]), copy.deepcopy(self.rows[0])], 0, 0))
        rows, status = smart_money.fetch_sec_form4(["TEST"], limit=1)
        self.assertEqual(1, len(rows))
        self.assertTrue(status["coverage_complete"])


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
