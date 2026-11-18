"""Read-only Wealth brokerage discovery. Never synthesizes ledger transactions.

The broker's current positions are an independent snapshot, not evidence of
historical deposits, trade dates, realized returns, or tax lots.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def discover(summary: dict | None, portfolios: list[dict], transactions: list[dict]) -> dict:
    if not summary or not summary.get("connected"):
        return {"connected": False, "accounts": [], "new_holdings": 0,
                "error": (summary or {}).get("error") or "Brokerage not connected"}
    accounts = []
    new_count = 0
    for account in summary.get("accounts") or []:
        account_id = str(account.get("account_hash") or account.get("account_number") or "")
        if not account_id:
            continue
        mapped = next((p for p in portfolios if str(p.get("broker_account") or "") == account_id), None)
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
                             "unrealized_pnl": pos.get("unrealized"),
                             "new": new, "source": "schwab"})
        accounts.append({"account_id": account_id, "account_type": account.get("account_type"),
                         "portfolio_id": mapped["id"] if mapped else None,
                         "portfolio_name": mapped["name"] if mapped else None,
                         "holdings": holdings,
                         "unsupported_positions": len(account.get("option_positions") or [])})
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
