"""Read-only broker discovery contract tests."""
from wealth_broker_sync import discover


def account(symbol="NVDA", qty=5):
    return {"account_hash": "hash-1", "account_type": "MARGIN",
            "equity_positions": [{"symbol": symbol, "quantity": qty,
                                  "market_value": 1000, "cost_basis": 800}],
            "option_positions": [{"symbol": "NVDA_OPTION"}]}


def test_new_holdings_auto_discovered_without_ledger_mutation():
    portfolios = [{"id": 4, "name": "Aggressive", "broker_account": "hash-1"}]
    transactions = [{"portfolio_id": 4, "ticker": "KLAC", "type": "BUY"}]
    original = [dict(x) for x in transactions]
    result = discover({"connected": True, "accounts": [account()]}, portfolios, transactions)
    assert result["connected"]
    assert result["new_holdings"] == 1
    assert result["accounts"][0]["portfolio_id"] == 4
    assert result["accounts"][0]["holdings"][0]["ticker"] == "NVDA"
    assert result["accounts"][0]["unsupported_positions"] == 1
    assert transactions == original


def test_existing_holding_and_disconnected_account():
    positions = [{"portfolio_id": 4, "ticker": "NVDA"}]
    p = [{"id": 4, "name": "Aggressive", "broker_account": "hash-1"}]
    assert discover({"connected": True, "accounts": [account()]}, p, positions)["new_holdings"] == 0
    assert not discover({"connected": False, "error": "expired"}, p, positions)["connected"]


def test_excludes_options_short_and_invalid_symbols():
    a = account()
    a["equity_positions"] += [
        {"symbol": "AAPL", "quantity": -2},
        {"symbol": "bad ticker!", "quantity": 3},
        {"symbol": "TSM", "quantity": 2.5},
    ]
    result = discover({"connected": True, "accounts": [a]}, [], [])
    assert [h["ticker"] for h in result["accounts"][0]["holdings"]] == ["NVDA", "TSM"]
