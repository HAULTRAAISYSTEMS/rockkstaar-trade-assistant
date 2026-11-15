"""New US listings (no annual report yet) and contradicting numbers on the scorecard."""
from datetime import date

import filing_signals
import fundamentals_engine as fe


def _subs(forms, fye="1231"):
    return {"fiscalYearEnd": fye, "filings": {"recent": {
        "form": [f for f, _ in forms], "filingDate": [d for _, d in forms],
        "reportDate": ["" for _ in forms], "accessionNumber": ["0001-26-000001" for _ in forms],
        "primaryDocument": ["d.htm" for _ in forms], "items": ["" for _ in forms]}}}


def test_new_foreign_listing_gets_a_notice_with_due_date():
    sig = filing_signals.extract_signals(_subs([("6-K", "2026-10-28"), ("424B4", "2026-07-10"), ("F-1/A", "2026-06-30"), ("F-1", "2026-06-12")]),
                                         cik="1", today=date(2026, 11, 15))
    lst = sig["listing"]
    assert lst["annual_reports_on_file"] == 0 and lst["foreign_filer"] and lst["registered"]["form"] == "F-1"
    n = fe.listing_notice(sig, "SK hynix Inc.")
    assert n and n["form"] == "20-F" and n["expected"] == "Apr 2027"
    assert "hasn't filed its first annual report (20-F)" in n["text"] and "Jun 2026" in n["text"]


def test_established_filer_gets_no_notice():
    sig = filing_signals.extract_signals(_subs([("10-Q", "2026-10-30"), ("10-K", "2026-02-20"), ("S-3", "2025-05-01")]),
                                         cik="1", today=date(2026, 11, 15))
    assert fe.listing_notice(sig, "X") is None
    assert fe.listing_notice({}, "X") is None


def test_data_checks_flag_contradictions():
    c = fe.data_checks(85.6, 72.0, 451.8, "KRW")
    keys = [x["key"] for x in c]
    assert keys == ["net_margin", "eps_growth"]
    assert "above operating margin" in c[0]["text"] and "KRW" in c[1]["text"]
    assert fe.data_checks(25.0, 30.0, 40.0) == []
    assert [x["key"] for x in fe.data_checks(85.0, None, None)] == ["net_margin"]


def test_template_shows_notice_checks_and_row_flag():
    tpl = open("templates/fundamentals.html", encoding="utf-8").read()
    assert "data.listing_notice" in tpl and "data.data_checks" in tpl and "row.suspect" in tpl
    assert fe.SCORECARD_VERSION == "2026-11-15.1"      # cached cards rebuild with the new fields
