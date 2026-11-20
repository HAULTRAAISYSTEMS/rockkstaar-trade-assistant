from unittest.mock import patch
from types import SimpleNamespace
import wealth_store


def test_only_funded_schwab_portfolio_is_visible():
    portfolios = [
        {"id": 1, "name": "Roth IRA", "broker_account": None},
        {"id": 2, "name": "Schwab taxable", "broker_account": "empty"},
        {"id": 3, "name": "Schwab taxable", "broker_account": "funded"},
    ]
    snapshots = [
        {"account_key": "empty", "snapshot": {"holdings": []}},
        {"account_key": "funded", "snapshot": {"holdings": [
            {"ticker": "KLAC", "shares": 5}]}},
    ]
    with patch("schwab_account_preferences.get_preferences", return_value={}), \
         patch.object(wealth_store, "list_broker_snapshots", return_value=snapshots), \
         patch.object(wealth_store, "list_transactions", return_value=[]):
        result = wealth_store.visible_portfolio_tabs(7, portfolios)
    assert [p["id"] for p in result] == [1, 3]


def test_disabled_schwab_account_is_hidden_even_if_funded():
    portfolios = [{"id": 3, "broker_account": "funded"}]
    snapshots = [{"account_key": "funded", "snapshot": {"holdings": [
        {"ticker": "KLAC", "shares": 5}]}}]
    with patch("schwab_account_preferences.get_preferences",
               return_value={"funded": {"include_wealth": False}}), \
         patch.object(wealth_store, "list_broker_snapshots", return_value=snapshots), \
         patch.object(wealth_store, "list_transactions", return_value=[]):
        assert wealth_store.visible_portfolio_tabs(7, portfolios) == []


def test_historical_buys_keep_broker_tab_visible_without_snapshot():
    portfolios = [{"id": 3, "broker_account": "funded"}]
    with patch("schwab_account_preferences.get_preferences", return_value={}), \
         patch.object(wealth_store, "list_broker_snapshots", return_value=[]), \
         patch.object(wealth_store, "list_transactions",
                      return_value=[{"portfolio_id": 3, "type": "BUY"}]), \
         patch("wealth_ledger.build", return_value=SimpleNamespace(positions={"KLAC": [SimpleNamespace(shares=5)]})):
        assert wealth_store.visible_portfolio_tabs(7, portfolios) == portfolios
