"""Regressions from the AEIS research audit, including cross-company rules."""
import os
from datetime import date, datetime
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-only-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import app
import company_research_data as cr
import filing_signals as fs
import fundamentals_engine as fe
from test_edgar_pipeline import facts, duration, YEARS, _Resp
from test_filing_signals import submissions


def test_net_income_survives_a_tag_change_without_using_comprehensive_income():
    payload = facts()
    gaap = payload["facts"]["us-gaap"]
    gaap["NetIncomeLoss"]["units"]["USD"] = gaap["NetIncomeLoss"]["units"]["USD"][1:]
    gaap["ProfitLoss"] = duration([148400000])
    gaap["ComprehensiveIncome"] = duration([999999999])
    with patch.object(fe, "_edgar_cik", return_value=("0000927003", "Advanced Energy")), \
         patch.object(fe._req_module, "get", return_value=_Resp(payload)):
        raw = fe.fetch_fundamentals_edgar("AEIS")
    assert raw["net_income"][0] == 148400000
    assert raw["net_income"][1] == 4062e6
    del gaap["ProfitLoss"]
    fe.clear_edgar_extract_cache()
    with patch.object(fe, "_edgar_cik", return_value=("0000927003", "Advanced Energy")), \
         patch.object(fe._req_module, "get", return_value=_Resp(payload)):
        assert fe.fetch_fundamentals_edgar("AEIS")["net_income"][0] is None


def test_disclosure_categories_do_not_assert_departure_or_default():
    result = fs.extract_signals(submissions([("8-K", "2026-09-01", "2.04,5.02")]), today=date(2026, 9, 25))
    by_item = {s["item"]: s for s in result["signals"]}
    assert by_item["5.02"]["severity"] == "info"
    assert "compensation" in by_item["5.02"]["label"]
    assert by_item["2.04"]["severity"] == "review"
    assert "does not establish" in by_item["2.04"]["why"]
    assert result["coverage_complete"] is False


def test_empty_item_values_are_unknown_not_clean():
    result = fs.extract_signals(submissions([("8-K", "2026-09-01", "")]), today=date(2026, 9, 25))
    assert result["available"] is False


def test_latest_report_date_comes_from_sec_not_the_ttm_provider():
    payload = submissions([("10-Q", "2026-08-04", ""), ("10-K", "2026-02-13", "")])
    payload["filings"]["recent"]["reportDate"] = ["2026-06-30", "2025-12-31"]
    result = fs.extract_signals(payload, cik="927003", today=date(2026, 9, 25))
    assert result["latest_financial_report"]["period_end"] == "2026-06-30"
    assert result["latest_financial_report"]["form"] == "10-Q"


def test_untracked_company_uses_valuation_quote_and_rejects_past_earnings(monkeypatch):
    monkeypatch.setattr(app, "_et_now", lambda: datetime(2026, 9, 25))
    monkeypatch.setattr(app, "get_user_setting", lambda *a: "")
    monkeypatch.setattr(app._intel, "get_intel_summary", lambda: {"earnings": {"today": [
        {"ticker": "AEIS", "date": "2026-08-03"}]}})
    with patch("research_feed_phase2.list_published", return_value=[]), \
         patch("research_memory.list_cards", return_value=[]):
        result = app._company_research_context(1, "AEIS", {
            "valuation": {"price": 279.68, "change_pct": 0.59, "quote_as_of": "2026-09-25T20:00:00+00:00", "source": "Finnhub"},
            "history": [{"period_end": "2025-12-31"}], "ttm_period_end": "2026-03-31 00:00:00",
        }, {}, {"profile": {"description": "Power conversion solutions"}})
    assert result["price"] == 279.68
    assert result["market_as_of"].startswith("2026-09-25")
    assert result["description"] == "Power conversion solutions"
    assert result["earnings"] is None
    assert result["fundamentals_as_of"] == "2025-12-31"


def test_snapshot_fetches_the_requested_symbol_and_caches_per_ticker(monkeypatch):
    cr._CACHE.clear()
    calls = []
    monkeypatch.setattr(cr, "_market", lambda ticker, *a: {"price": 100 if ticker == "AEIS" else 200})
    monkeypatch.setattr(cr, "_profile", lambda ticker: {"description": ticker})
    monkeypatch.setattr(cr, "_news", lambda ticker: calls.append(ticker) or [])
    monkeypatch.setattr(cr, "_earnings", lambda ticker: [])
    assert cr.fetch_company_snapshot("AEIS")["valuation"]["price"] == 100
    assert cr.fetch_company_snapshot("AMD")["valuation"]["price"] == 200
    cr.fetch_company_snapshot("AEIS")
    assert calls == ["AEIS", "AMD"]
    cr._CACHE.clear()


def test_ttm_rejects_a_missing_quarter_even_with_four_reports():
    from finnhub_ttm import compute_ttm
    from test_ttm_periods import four_quarters, q
    reports = four_quarters()
    reports[1] = q(start="2025-01-01", end="2025-03-31")
    result = compute_ttm(reports)
    assert result["computed"] is False
    assert result["ttm_revenue"] is None


def test_ttm_deduplicates_amendments_before_summing():
    from finnhub_ttm import compute_ttm
    from test_ttm_periods import four_quarters, q
    reports = four_quarters()
    amended = q(rev=150, filedDate="2026-05-15")
    result = compute_ttm([amended] + reports)
    assert result["computed"] is True
    assert result["ttm_revenue"] == 450


def test_company_workspace_renders_when_fundamentals_are_unavailable():
    from flask import render_template, session
    import web_app
    with web_app.app.test_request_context("/research?ticker=AEIS"):
        session.update(user_id=7, username="researcher", is_admin=0)
        html = render_template("research.html", ticker="AEIS", data=None, error="Unavailable",
                               stock={}, suggestions=[], research={
                                   "price": None, "change_pct": None, "changes": [],
                                   "memory_cards": [], "source_count": 0})
    assert "Historical filing data is unavailable" in html
    assert "Filing check unavailable" in html


def test_profile_rejects_unrelated_acronym_match_and_repairs_cached_aeis(monkeypatch):
    import intel_engine as intel
    bad = "Associação de Estudantes is a Portuguese multisports club."
    assert not intel._profile_description_matches("Advanced Energy Industries Inc", bad)
    assert intel._profile_description_matches("Advanced Energy Industries Inc", "Advanced Energy Industries designs precision power solutions.")
    monkeypatch.setattr("database.get_company_profile", lambda ticker: {
        "company_name": "Advanced Energy Industries Inc", "description": bad,
        "fetched_at": datetime.now().isoformat()})
    assert "precision power" in intel.fetch_company_profile("AEIS")["description"]


def test_failed_profile_refresh_does_not_resurrect_an_unverified_stock_description(monkeypatch):
    monkeypatch.setattr(app, "get_user_setting", lambda *a: "")
    monkeypatch.setattr(app._intel, "get_intel_summary", lambda: {})
    with patch("research_feed_phase2.list_published", return_value=[]), patch("research_memory.list_cards", return_value=[]):
        result = app._company_research_context(1, "TEST", None, {"company_description": "Wrong old entity"}, {"profile": {}})
    assert result["description"] == ""


def test_quote_above_provider_high_identifies_a_lagging_range():
    from valuation import build_valuation
    value = build_valuation({"52WeekHigh": 624.69, "52WeekLow": 154.78}, {"c": 630.63, "pc": 629.24})
    row = next(row for row in value["rows"] if row["key"] == "off_high")
    assert row["label"] == "Above reported high"
    assert "range may lag" in row["note"]
