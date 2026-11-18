from wealth_broker_sync import reconcile


def test_quantity_mismatch_and_missing_history():
    snapshot = {"holdings": [{"ticker": "NVDA", "shares": 5},
                             {"ticker": "TSM", "shares": 2}]}
    result = reconcile(snapshot, {"NVDA": 3, "KLAC": 1})
    assert {r["ticker"]: r["status"] for r in result} == {
        "NVDA": "quantity_mismatch", "TSM": "missing_history",
        "KLAC": "not_at_broker"}


def test_matching_holdings_have_no_discrepancies():
    assert reconcile({"holdings": [{"ticker": "NVDA", "shares": 5}]},
                     {"NVDA": 5}) == []


def test_reconciliation_does_not_modify_inputs():
    snapshot = {"holdings": [{"ticker": "NVDA", "shares": 5}]}
    ledger = {"NVDA": 3}
    reconcile(snapshot, ledger)
    assert snapshot["holdings"][0]["shares"] == 5
    assert ledger == {"NVDA": 3}
