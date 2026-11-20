from datetime import date
from wealth_financial_contract import normalize_financial_contract


def _snapshot():
    return {"ok": True, "ticker": "SPCX", "cik": "0001181412",
            "status": "available", "source": "SEC EDGAR",
            "retrieved_at": "2026-11-19T12:00:00Z", "cache_status": "hit",
            "dashboard": {"metrics": {"revenue": {"latest": {
                "value": 1500000, "unit": "USD", "start": "2025-01-01",
                "period_end": "2025-12-31", "filed": "2026-03-01",
                "form": "10-K", "accession": "0001", "tag": "Revenues"}}}}}


def test_provenance_and_period_are_preserved():
    result = normalize_financial_contract(_snapshot(), today=date(2026, 4, 1))
    revenue = result["metrics"]["revenue"]
    assert revenue["value"] == 1500000
    assert revenue["period_start"] == "2025-01-01"
    assert revenue["accession"] == "0001"
    assert revenue["freshness"]["status"] == "recent"
    assert result["scoring_eligible"] is False


def test_missing_metrics_are_explicitly_missing():
    result = normalize_financial_contract(_snapshot())
    assert result["metrics"]["assets"]["status"] == "missing"
    assert result["metrics"]["assets"]["value"] is None


def test_missing_provenance_cannot_be_reported():
    snapshot = _snapshot()
    del snapshot["dashboard"]["metrics"]["revenue"]["latest"]["accession"]
    result = normalize_financial_contract(snapshot)
    assert result["metrics"]["revenue"]["value"] is None


def test_stale_filing_is_flagged():
    result = normalize_financial_contract(_snapshot(), today=date(2027, 8, 1))
    assert result["metrics"]["revenue"]["freshness"]["status"] == "stale"


def test_unsupported_symbol_is_fail_closed():
    result = normalize_financial_contract({"ok": False, "ticker": "BAD", "status": "unsupported"})
    assert result["metrics"] == {}
    assert result["scoring_eligible"] is False
