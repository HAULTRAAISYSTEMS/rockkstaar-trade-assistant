import unittest
from wealth_sec_evidence import normalize_facts, normalize_filings, fetch_sec_evidence


class SecEvidenceTests(unittest.TestCase):
    def test_filing_source_url_and_forms(self):
        p = {"cik": 1181412, "filings": {"recent": {
            "form": ["10-K", "S-1"], "filingDate": ["2026-09-01", "2026-08-01"],
            "accessionNumber": ["0001181412-26-000001", "0001181412-26-000002"],
            "primaryDocument": ["report.htm", "registration.htm"]}}}
        rows = normalize_filings(p)
        self.assertEqual(len(rows), 1)
        self.assertIn("Archives/edgar/data/1181412/", rows[0]["url"])

    def test_facts_keep_form_and_filing_dates(self):
        payload = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
            {"val": 12, "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"}]}}}}}
        row = normalize_facts(payload)["net_income"][0]
        self.assertEqual(row["form"], "10-Q")
        self.assertEqual(row["filed"], "2026-08-01")

    def test_unknown_ticker_does_not_fetch(self):
        self.assertEqual(fetch_sec_evidence("UNKNOWN", get=lambda *a, **k: self.fail("network"))["status"], "unsupported")


if __name__ == "__main__":
    unittest.main()
