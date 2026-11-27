from datetime import date

import pytest

import wealth_schwab_history as parser
from test_wealth_routes import client, env, make_portfolio  # noqa: F401

DAY = date(2026, 2, 2)


def activity(kind, id_="fixture-id", **fields):
    return {"activityId": id_, "type": kind, "time": DAY.isoformat() + "T14:30:00Z",
            "status": "VALID", **fields}


def test_normalization_accepts_only_supported_and_balanced_actual_activity():
    usd = {"instrument": {"assetType": "CURRENCY", "symbol": "USD"}}
    equity = {"instrument": {"assetType": "EQUITY", "symbol": "AAPL"}}
    trade = activity("TRADE", "purchase", netAmount=-202, transferItems=[
        {**equity, "amount": 2, "price": 100, "positionEffect": "OPENING"},
        {**usd, "cost": -2, "feeType": "COMMISSION"},
    ])
    rows = [
        trade, trade,
        activity("ACH_RECEIPT", "deposit", netAmount=1000),
        activity("ACH_DISBURSEMENT", "withdraw", netAmount=-25),
        activity("DIVIDEND_OR_INTEREST", "dividend", netAmount=2.5, description="Qualified dividend",
                 transferItems=[equity, usd]),
        activity("DIVIDEND_OR_INTEREST", "interest", netAmount=.3, description="Bank interest", transferItems=[usd]),
        activity("DIVIDEND_OR_INTEREST", "drip", netAmount=3, description="Dividend reinvestment",
                 transferItems=[{**equity, "amount": .03}, usd]),
        activity("JOURNAL", "transfer", netAmount=300),
        {**trade, "activityId": "mismatch", "netAmount": -201},
        {**trade, "activityId": "no-date", "time": ""},
        {**trade, "activityId": "pending", "status": "PENDING"},
    ]
    result = parser.normalize(rows, DAY, DAY)
    assert [(row["type"], row.get("amount")) for row in result["transactions"]] == [
        ("BUY", None), ("DEPOSIT", 1000), ("WITHDRAWAL", 25), ("DIVIDEND", 2.5), ("INTEREST", .3),
    ]
    assert result["transactions"][0]["fees"] == 2
    assert len(result["skipped"]) == 5


def test_schwab_history_endpoint_uses_only_encoded_get_and_valid_dates(monkeypatch):
    import schwab
    requests = []
    monkeypatch.setattr(schwab, "_get", lambda url, params, **kw: requests.append((url, params, kw)) or [])
    assert schwab.fetch_transaction_history("test/hash", DAY, DAY, user_id=17) == []
    path, params, options = requests[0]
    assert path == "/accounts/test%2Fhash/transactions"
    assert params["startDate"] == "2026-02-02T05:00:00.000Z"
    assert params["endDate"] == "2026-02-03T04:59:59.999Z"
    assert "TRADE" in params["types"] and "DIVIDEND_OR_INTEREST" in params["types"]
    assert options == {"user_id": 17}


def test_owned_account_preview_commit_ledger_and_csv_overlap(env, monkeypatch):
    import schwab
    import wealth_store

    app, *_ = env
    a, b = client(app, 1), client(app, 2)
    pid, another = make_portfolio(a), make_portfolio(a)
    number, broker_hash = "11006751", "test-hash"
    wealth_store.update_portfolio(1, pid, broker_account=number)
    calls = []
    def account_hashes(user_id):
        calls.append(user_id)
        return {number: broker_hash}
    def history(account_hash, start, end, *, user_id):
        assert account_hash == broker_hash
        assert user_id == 1 and start <= DAY <= end
        return [activity("ACH_RECEIPT", "deposit", netAmount=500),
                activity("ACH_RECEIPT", "extra", netAmount=100)]
    monkeypatch.setattr(schwab, "fetch_account_hashes", account_hashes)
    monkeypatch.setattr(schwab, "fetch_transaction_history", history)
    path = f"/api/wealth/performance/history/{pid}"
    assert app.test_client().post(path + "/preview", json={}).status_code == 401
    assert b.get(path).status_code == 404
    assert b.post(path + "/preview", json={}).status_code == 404
    assert calls == []
    data = a.post(path + "/preview", json={"start_date": "2026-01-01", "end_date": "2026-02-28"}).get_json()
    assert data["ok"] and len(data["transactions"]) == 2 and data["skipped"] == []
    assert number not in str(data) and broker_hash not in str(data)
    assert a.get(path).get_json()["transactions"] == []
    review = {"token": data["token"], "indexes": [0]}
    assert a.post(f"/api/wealth/performance/history/{another}/commit", json=review).status_code == 404
    assert a.post(path + "/commit", json={**review, "token": review["token"] + "!"}).status_code == 400
    assert a.post(path + "/commit", json={**review, "indexes": [99]}).status_code == 400
    committed = a.post(path + "/commit", json=review).get_json()
    assert committed["added"] == 1
    assert a.post(path + "/commit", json=review).get_json()["duplicates"] == 1
    response = a.get(path).get_json()["transactions"]
    assert len(response) == 1 and response[0]["source"] == "schwab_api"
    csv = "Date,Action,Symbol,Description,Quantity,Price,Fees & Comm,Amount\n02/02/2026,ACH deposit,,,,,,500\n"
    preview = a.post("/api/wealth/import/preview", json={"text": csv}).get_json()
    assert a.post("/api/wealth/import/commit", json={"portfolio_id": pid,
        "transactions": preview["transactions"]}).get_json()["duplicates"] == 1
    wealth_store.update_portfolio(1, pid, broker_account="relinked")
    assert a.post(path + "/commit", json=review).status_code == 409


def test_api_trade_overlap_after_existing_csv_preserves_distinct_fills(env):
    import wealth_store

    app, *_ = env
    pid = make_portfolio(client(app, 1))
    common = {"date": "2026-02-02", "type": "BUY", "ticker": "ABC", "shares": 3, "price": 50}
    first = {**common, "source": "schwab_csv", "fingerprint": "csv-id"}
    assert wealth_store.add_transactions(1, pid, [first], source="csv")["added"] == 1
    rows = [{**common, "date": "2026-02-03", "source": "schwab_api", "fingerprint": f"api-{i}"} for i in range(2)]
    assert wealth_store.add_transactions(1, pid, rows, source="schwab_api") == {"added": 1, "duplicates": 1}
    assert wealth_store.add_transactions(1, pid, rows, source="schwab_api") == {"added": 0, "duplicates": 2}
