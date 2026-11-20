from wealth_financial_data import financial_snapshot, clear_cache


def test_unknown_issuer_never_fetches():
    def fail(_ticker):
        raise AssertionError("Must not fetch unsupported issuer")
    result = financial_snapshot("UNKNOWN", fetcher=fail)
    assert result["ok"] is False
    assert result["scoring_eligible"] is False


def test_single_source_powers_dashboard_and_explanations():
    calls = []
    def source(ticker):
        calls.append(ticker)
        return {"ticker": ticker, "cik": "0001181412", "status": "available",
                "filings": [], "facts": {}, "warnings": []}
    result = financial_snapshot("spcx", fetcher=source)
    assert calls == ["SPCX"]
    assert result["dashboard"]["ticker"] == result["ticker"]
    assert result["analysis"]["mcff_scoring_enabled"] is False
    assert result["scoring_eligible"] is False


def test_missing_facts_not_invented():
    result = financial_snapshot("SPCX", fetcher=lambda t: {
        "ticker": t, "cik": "0001181412", "status": "unavailable",
        "filings": [], "facts": {}, "warnings": ["offline"]})
    assert result["dashboard"]["metrics"] == {}
    assert result["warnings"] == ["offline"]
