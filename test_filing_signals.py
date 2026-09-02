"""Governance red flags read from 8-K item codes.

The scorecard reads what a company reports. This reads what it had to disclose
and would rather you skipped. Every signal is a filed fact with a date and a
document behind it — an 8-K's item codes say exactly which disclosure
obligation the filing satisfied, so there is no keyword guessing here.
"""
from datetime import date
from unittest.mock import patch

import pytest

import filing_signals as fs

TODAY = date(2026, 9, 2)


def submissions(rows, *, with_items=True):
    """SEC's filings.recent is column-oriented — each field its own array."""
    payload = {
        "form": [r[0] for r in rows],
        "filingDate": [r[1] for r in rows],
        "accessionNumber": [f"0000319201-26-{i:06d}" for i, _ in enumerate(rows)],
        "primaryDocument": ["a.htm" for _ in rows],
    }
    if with_items:
        payload["items"] = [r[2] for r in rows]
    return {"filings": {"recent": payload}}


def labels(result):
    return [s["label"] for s in result["signals"]]


# ── What it catches ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("code,expected", [
    ("4.02", "Financial statements restated"),
    ("4.01", "Auditor changed"),
    ("5.02", "Officer or director departure"),
    ("1.03", "Bankruptcy or receivership"),
    ("3.01", "Delisting or listing-standard notice"),
    ("2.06", "Material asset impairment"),
    ("2.04", "Debt acceleration or covenant trigger"),
])
def test_each_item_code_is_recognised(code, expected):
    r = fs.extract_signals(submissions([("8-K", "2026-05-01", code)]), today=TODAY)
    assert labels(r) == [expected]


def test_a_late_annual_report_is_its_own_signal():
    """No item code involved — the form type is the disclosure."""
    r = fs.extract_signals(submissions([("NT 10-K", "2026-05-01", "")]), today=TODAY)
    assert labels(r) == ["Annual report filed late"]


def test_several_items_on_one_filing_all_register():
    r = fs.extract_signals(
        submissions([("8-K", "2026-05-01", "4.01,5.02,9.01")]), today=TODAY)
    assert set(labels(r)) == {"Auditor changed", "Officer or director departure"}


def test_routine_filings_produce_nothing():
    rows = [("10-K", "2026-08-01", ""), ("10-Q", "2026-05-01", ""),
            ("8-K", "2026-04-01", "2.02,9.01"), ("4", "2026-03-01", "")]
    assert fs.extract_signals(submissions(rows), today=TODAY)["signals"] == []


# ── Ordering and windowing ───────────────────────────────────────────────────

def test_the_worst_thing_is_listed_first():
    rows = [("8-K", "2026-08-01", "5.02"), ("8-K", "2026-07-01", "4.02"),
            ("8-K", "2026-06-01", "4.01")]
    r = fs.extract_signals(submissions(rows), today=TODAY)
    assert labels(r)[0] == "Financial statements restated"
    assert r["worst"] == "critical"


def test_equally_severe_signals_are_newest_first():
    rows = [("8-K", "2024-01-01", "4.01"), ("8-K", "2026-01-01", "4.01")]
    r = fs.extract_signals(submissions(rows), today=TODAY)
    assert [s["date"] for s in r["signals"]] == ["2026-01-01", "2024-01-01"]


def test_old_filings_fall_outside_the_window():
    rows = [("8-K", "2015-01-01", "4.02"), ("8-K", "2026-01-01", "4.01")]
    r = fs.extract_signals(submissions(rows), today=TODAY)
    assert labels(r) == ["Auditor changed"]


def test_a_future_dated_filing_is_ignored():
    r = fs.extract_signals(submissions([("8-K", "2027-01-01", "4.02")]), today=TODAY)
    assert r["signals"] == []


# ── Not knowing, versus knowing there is nothing ─────────────────────────────

def test_a_feed_without_item_codes_reports_itself_unavailable():
    """"Nothing filed" and "we could not look" are different answers, and
    showing a clean record for the second one is the dangerous direction."""
    r = fs.extract_signals(submissions([("8-K", "2026-05-01", "4.02")],
                                       with_items=False), today=TODAY)
    assert r["available"] is False
    assert "item codes" in r["reason"]


def test_an_empty_index_is_unavailable_not_clean():
    r = fs.extract_signals({}, today=TODAY)
    assert r["available"] is False and r["signals"] == []


def test_a_clean_company_is_available_with_nothing_to_report():
    r = fs.extract_signals(submissions([("10-K", "2026-08-01", "")]), today=TODAY)
    assert r["available"] is True and r["signals"] == [] and r["worst"] is None


def test_mismatched_columns_do_not_splice_filings_together():
    """Zipping past the shortest array would pair one filing's form with
    another's date."""
    payload = submissions([("8-K", "2026-05-01", "4.02"),
                           ("8-K", "2026-04-01", "4.01")])
    payload["filings"]["recent"]["filingDate"] = ["2026-05-01"]
    r = fs.extract_signals(payload, today=TODAY)
    assert len(r["signals"]) == 1


# ── Links back to the source ─────────────────────────────────────────────────

def test_each_signal_links_to_the_filing():
    r = fs.extract_signals(submissions([("8-K", "2026-05-01", "4.02")]),
                           cik="0000319201", today=TODAY)
    url = r["signals"][0]["url"]
    assert url.startswith("https://www.sec.gov/Archives/edgar/data/319201/")


# ── Failure handling ─────────────────────────────────────────────────────────

def test_a_lookup_failure_never_raises():
    """This card must not be able to take down a page that is fine without it."""
    fs.clear_cache()
    import fundamentals_engine as fe
    with patch.object(fe, "_edgar_cik", side_effect=RuntimeError("boom")):
        r = fs.fetch_filing_signals("KLAC")
    assert r["available"] is False and "lookup failed" in r["reason"]


def test_an_unknown_ticker_is_reported_not_raised():
    fs.clear_cache()
    import fundamentals_engine as fe
    with patch.object(fe, "_edgar_cik", return_value=(None, None)):
        r = fs.fetch_filing_signals("NOPE")
    assert r["available"] is False and r["signals"] == []


# ── What the scorecard does with them ────────────────────────────────────────

def _card(signals):
    import fundamentals_engine as fe
    from test_edgar_stale_concepts import kla_shaped
    from test_edgar_pipeline import _Resp
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", return_value=_Resp(kla_shaped())):
        raw = fe.fetch_fundamentals_edgar("KLAC")
    raw["_filing_signals"] = {"available": True, "signals": signals}
    return fe.score_fundamentals(raw)


def _flag_keys(card):
    return {f["key"] for f in card["red_flags"]}


def test_a_restatement_becomes_a_red_flag():
    card = _card([{"item": "4.02", "form": "8-K", "date": "2026-05-01",
                   "label": "x", "why": "y", "severity": "critical"}])
    assert "restated_financials" in _flag_keys(card)


def test_a_restatement_drops_the_verdict_a_band():
    """No score computed from numbers the company has disowned earns the top
    verdict."""
    import fundamentals_engine as fe
    clean = _card([])
    flagged = _card([{"item": "4.02", "form": "8-K", "date": "2026-05-01",
                      "label": "x", "why": "y", "severity": "critical"}])
    assert flagged["downgraded"] is True
    assert (fe.VERDICT_BANDS.index(flagged["verdict"])
            < fe.VERDICT_BANDS.index(clean["verdict"]))


def test_a_late_filing_is_flagged_from_the_form_alone():
    card = _card([{"item": "", "form": "NT 10-K", "date": "2026-05-01",
                   "label": "x", "why": "y", "severity": "high"}])
    assert "late_filing" in _flag_keys(card)


def test_an_auditor_change_is_shown_but_does_not_downgrade():
    """Worth knowing, not damning on its own."""
    import fundamentals_engine as fe
    card = _card([{"item": "4.01", "form": "8-K", "date": "2026-05-01",
                   "label": "x", "why": "y", "severity": "high"}])
    assert "auditor_changed" in _flag_keys(card)
    assert "auditor_changed" not in fe.DOWNGRADE_TRIGGERS


def test_repeated_signals_do_not_stack_into_duplicate_flags():
    card = _card([
        {"item": "5.02", "form": "8-K", "date": "2026-05-01", "label": "x",
         "why": "y", "severity": "medium"},
        {"item": "5.02", "form": "8-K", "date": "2026-02-01", "label": "x",
         "why": "y", "severity": "medium"},
    ])
    assert len(card["red_flags"]) == len({f["key"] for f in card["red_flags"]})


def test_no_signals_means_no_extra_flags():
    assert "restated_financials" not in _flag_keys(_card([]))
