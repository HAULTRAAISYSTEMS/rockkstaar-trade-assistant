from wealth_sec_evidence import SEC_CIK, fetch_sec_evidence


def test_us_issuer_mappings():
    assert SEC_CIK["KLAC"] == "0000319201"
    assert SEC_CIK["NVDA"] == "0001045810"
    assert SEC_CIK["GOOG"] == SEC_CIK["GOOGL"] == "0001652044"
    assert SEC_CIK["V"] == "0001403161"


def test_foreign_issuers_not_silently_mapped():
    assert "TSM" not in SEC_CIK
    assert "SKHY" not in SEC_CIK


def test_wrong_sec_cik_fails_closed():
    class Response:
        content = b'{}'
        def raise_for_status(self):
            return None
        def json(self):
            return {"cik": 123456, "filings": {}, "facts": {}}
    result = fetch_sec_evidence("KLAC", get=lambda *args, **kwargs: Response())
    assert result["status"] == "unavailable"
    assert result["facts"] == {}
    assert result["filings"] == []
    assert any("mismatch" in x.lower() or "valueerror" in x.lower() for x in result["warnings"])
