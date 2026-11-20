"""Unified SEC intelligence contract: shared payload, no invented values."""
from unittest.mock import patch
from wealth_sec_dashboard import build_dashboard
from wealth_sec_explanations import compare_periods, explain_changes
from wealth_sec_evidence import normalize_facts


def test_missing_sec_values_stay_missing():
    evidence = {"ticker": "SPCX", "cik": "0001181412", "status": "unavailable",
                "facts": {}, "filings": [], "warnings": ["offline"]}
    view = build_dashboard(evidence)
    assert view["metrics"] == {}
    assert view["valuation_ready"] is False
    assert explain_changes(compare_periods(evidence["facts"]))["mcff_scoring_enabled"] is False


def test_source_periods_are_retained():
    raw = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        {"form": "10-K", "filed": "2026-03-01", "start": "2025-01-01",
         "end": "2025-12-31", "val": 100, "accn": "123"}]}}}}}
    rows = normalize_facts(raw)["net_income"]
    assert rows[0]["start"] == "2025-01-01"
    assert rows[0]["accession"] == "123"
    assert rows[0]["unit"] == "USD"


def test_sec_endpoints_require_login():
    from flask import Flask
    from wealth_routes import create_wealth_blueprint
    app = Flask(__name__)
    app.register_blueprint(create_wealth_blueprint(current_user=lambda: None))
    with app.test_client() as client:
        for route in ("financial-intelligence", "sec-evidence", "sec-dashboard", "sec-explanations"):
            assert client.get("/api/wealth/" + route + "/SPCX").status_code == 401


def test_unverified_symbol_fails_closed():
    from flask import Flask
    from wealth_routes import create_wealth_blueprint
    app = Flask(__name__)
    app.register_blueprint(create_wealth_blueprint(current_user=lambda: {"id": 1}))
    with app.test_client() as client:
        assert client.get("/api/wealth/financial-intelligence/UNKNOWN").status_code == 404
