"""Preference behavior: reversible, account-scoped and non-destructive."""
from schwab_account_preferences import select_accounts


def test_visibility_and_wealth_are_independent():
    accounts = [{"account_hash": "a"}, {"account_hash": "b"}]
    prefs = {"a": {"visible": False, "include_wealth": True},
             "b": {"visible": True, "include_wealth": False}}
    assert [a["account_hash"] for a in select_accounts(accounts, prefs)] == ["b"]
    assert [a["account_hash"] for a in select_accounts(accounts, prefs, for_wealth=True)] == ["a"]
    assert len(accounts) == 2


def test_unconfigured_accounts_are_visible_by_default():
    assert select_accounts([{"account_hash": "new"}], {}) == [{"account_hash": "new"}]
