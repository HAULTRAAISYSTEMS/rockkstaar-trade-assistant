"""Read-only Wealth brokerage discovery. Never synthesizes ledger transactions.

The broker's current positions are an independent snapshot, not evidence of
historical deposits, trade dates, realized returns, or tax lots.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def account_keys(account: dict) -> tuple[str, ...]:
    """Broker identifiers used server-side; the preferred key comes first."""
    return tuple(dict.fromkeys(str(value) for value in (
        account.get("account_hash"), account.get("account_number"), account.get("verified_account_hash")) if value))


def account_last_four(account: dict) -> str | None:
    number = str(account.get("account_number") or "").strip()
    return number[-4:] if re.fullmatch(r"[0-9]{4,}", number) else None


def account_aliases(accounts: list[dict]) -> dict[str, str]:
    """Resolve legacy IDs only when Schwab identifies the same account exactly."""
    aliases = {}
    ambiguous = set()
    for account in accounts:
        keys = account_keys(account)
        for key in keys:
            if key in aliases and aliases[key] != keys[0]:
                ambiguous.add(key)
            aliases[key] = keys[0]
    return {key: canonical for key, canonical in aliases.items() if key not in ambiguous}


def include_stored_aliases(live_aliases, snapshots):
    """Keep a previously verified alias only while its account is still present."""
    aliases = dict(live_aliases)
    for row in snapshots:
        canonical = str(row["snapshot"].get("account_id") or row["account_key"])
        if canonical in live_aliases:
            aliases.setdefault(row["account_key"], live_aliases[canonical])
    return aliases


def preference_keys(account, aliases):
    keys = account_keys(account)
    if not keys:
        return keys
    return (*keys, *(key for key, canonical in aliases.items() if canonical == keys[0] and key not in keys))


def is_in_wealth(keys, preferences):
    for key in keys:
        if key in preferences:
            return preferences[key].get("include_wealth", True)
    return True


def portfolio_label(portfolio, snapshot=None):
    last_four = (snapshot or {}).get("account_last_four")
    if isinstance(last_four, str) and re.fullmatch(r"[0-9]{4}", last_four):
        return f"•••• {last_four} · {portfolio['name']}"
    if portfolio.get("broker_account"):
        return f"{portfolio['name']} · Account ending unavailable"
    return portfolio["name"]



def _cash(account: dict):
    """Cash & cash invest for the account, or None when unknown."""
    if "performance_cash" in account:
        return account["performance_cash"]
    for k in ("cash_and_sweep", "cash_balance"):
        v = account.get(k)
        if isinstance(v, (int, float)) and v >= 0:
            return round(float(v), 2)
    return None


def discover(summary: dict | None, portfolios: list[dict], transactions: list[dict], *, aliases=None) -> dict:
    if not summary or not summary.get("connected"):
        return {"connected": False, "accounts": [], "new_holdings": 0,
                "error": (summary or {}).get("error") or "Brokerage not connected"}
    accounts = []
    new_count = 0
    seen = set()
    if aliases is None:
        aliases = account_aliases(summary.get("accounts") or [])
    for account in summary.get("accounts") or []:
        keys = account_keys(account)
        if not keys or keys[0] in seen:
            continue
        account_id = keys[0]
        seen.add(account_id)
        mapped = next((p for p in portfolios if aliases.get(str(p.get("broker_account") or "")) == account_id), None)
        known = {str(t.get("ticker") or "").upper() for t in transactions
                 if mapped and t.get("portfolio_id") == mapped["id"]}
        holdings = []
        for pos in account.get("equity_positions") or []:
            ticker = str(pos.get("symbol") or "").upper()
            qty = pos.get("quantity")
            if not _SYMBOL.fullmatch(ticker) or not isinstance(qty, (int, float)) or qty <= 0:
                continue
            new = ticker not in known
            new_count += int(new)
            holdings.append({"ticker": ticker, "shares": qty,
                             "market_value": pos.get("market_value"),
                             "cost_basis_snapshot": pos.get("cost_basis"),
                             "verified_cost_basis": pos.get("performance_cost_basis", pos.get("cost_basis") or None),
                             "verified_market_value": pos.get("performance_market_value", pos.get("market_value") or None),
                             "unrealized_pnl": pos.get("unrealized"),
                             "new": new, "source": "schwab"})
        raw_positions = account.get("positions")
        unsupported = (max(0, len(raw_positions) - len(holdings)) if isinstance(raw_positions, list)
                       else len(account.get("option_positions") or []) +
                       max(0, len(account.get("equity_positions") or []) - len(holdings)))
        total = account.get("performance_liquidation_value", account.get("total_value"))
        accounts.append({"account_id": account_id, "account_type": account.get("account_type"),
                         "account_last_four": account_last_four(account),
                         "portfolio_id": mapped["id"] if mapped else None,
                         "portfolio_name": mapped["name"] if mapped else None,
                         "holdings": holdings,
                         "cash": _cash(account),
                         "total_value": total,
                         "available_funds": account.get("performance_available_funds", account.get("available_funds")),
                         "unsupported_positions": unsupported})
    return {"connected": True, "source": "schwab", "as_of": datetime.now(timezone.utc).isoformat(),
            "accounts": accounts, "new_holdings": new_count,
            "note": "Live broker positions are displayed automatically. Ledger and tax lots remain unchanged."}


def reconcile(snapshot: dict, ledger_positions: dict) -> list[dict]:
    """Compare broker quantities with ledger quantities, never modifying either."""
    result = []
    for h in snapshot.get("holdings", []):
        symbol = h["ticker"]
        broker_qty = float(h["shares"])
        ledger_qty = float(ledger_positions.get(symbol, 0))
        if abs(broker_qty - ledger_qty) > 0.000001:
            result.append({"ticker": symbol, "broker_shares": broker_qty,
                           "ledger_shares": ledger_qty,
                           "difference": round(broker_qty - ledger_qty, 8),
                           "status": "missing_history" if ledger_qty == 0 else "quantity_mismatch"})
    broker_symbols = {h["ticker"] for h in snapshot.get("holdings", [])}
    for symbol, qty in ledger_positions.items():
        if symbol not in broker_symbols and float(qty) != 0:
            result.append({"ticker": symbol, "broker_shares": 0,
                           "ledger_shares": float(qty), "difference": -float(qty),
                           "status": "not_at_broker"})
    return result
