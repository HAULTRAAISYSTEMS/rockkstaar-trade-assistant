"""
portfolio.py — one balance across every connected brokerage.

Two brokers reach this app by different routes. Schwab answers directly over
its own API; Robinhood and the rest answer through SnapTrade. Both are
normalised to the same account shape upstream, so the only work left is
adding them up — and being careful about the one field that does not add up.

The field that does not add up
──────────────────────────────
Day P&L. Schwab reports a start-of-day value, so a day change can be
computed. SnapTrade reports no such mark for any brokerage behind it, so
Robinhood has no day change at all. Summing "Schwab's day change" with
"nothing" and printing the result beside a combined balance would show a
number that answers a different question than the label asks.

So the sum carries a flag. `daily_pnl` is the total across the brokers that
actually report one, `daily_pnl_partial` says whether any connected broker
was left out, and `daily_pnl_label` names the ones that are in it. A template
that prints the figure without the label is printing half a fact.

What this is not
────────────────
It is not a risk input. Buying power sums here for display, but the two
pools are not fungible — cash in Robinhood cannot settle a Schwab order —
so position sizing deliberately does not read this module.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The brokers in display order, with the module that answers for each.
BROKERS = (
    ("schwab",    "Charles Schwab"),
    ("snaptrade", "Robinhood & others"),
)


def _summary_for(broker: str, user_id: int) -> dict:
    """One broker's summary. May raise; the caller decides what that means."""
    if broker == "schwab":
        import schwab as mod
    else:
        import snaptrade as mod
    return mod.get_account_summary(user_id)


def _safe_summary_for(broker: str, user_id: int) -> dict:
    """_summary_for, with an outage turned into a disconnected stub.

    One broker being unreachable must leave the other one's balance on the
    screen. The error travels in the stub so the page can say which broker is
    missing rather than silently showing a smaller number.
    """
    try:
        return _summary_for(broker, user_id)
    except Exception as exc:
        logger.warning("portfolio: %s summary failed: %s", broker, exc)
        return {"connected": False, "accounts": [], "error": str(exc)}


def _f(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def combine(summaries: list[tuple[str, str, dict]]) -> dict:
    """Fold per-broker summaries into one, in the shape a single broker returns.

    Taking (key, label, summary) triples rather than fetching makes this
    testable without a network and keeps the ordering the caller's business.
    """
    accounts: list[dict] = []
    sources: list[dict] = []
    total_value = buying_power = unrealized = 0.0
    positions = 0
    connected_any = False

    daily_total = 0.0
    daily_reported: list[str] = []
    daily_missing: list[str] = []

    for key, label, summary in summaries:
        summary = summary if isinstance(summary, dict) else {}
        is_on = bool(summary.get("connected"))
        broker_accounts = [a for a in (summary.get("accounts") or []) if isinstance(a, dict)]

        # Tag each account with the broker it came from, so a merged positions
        # table can still say where a holding lives.
        for account in broker_accounts:
            account.setdefault("broker", key)
            account.setdefault("broker_label", label)
        accounts.extend(broker_accounts)

        sources.append({
            "broker":      key,
            "label":       label,
            "connected":   is_on,
            "total_value": summary.get("total_value"),
            "error":       summary.get("error"),
        })

        if not is_on:
            continue
        connected_any = True
        total_value  += _f(summary.get("total_value"))
        buying_power += _f(summary.get("buying_power"))
        unrealized   += _f(summary.get("total_unrealized"))
        positions    += int(summary.get("open_positions") or 0)

        if summary.get("daily_pnl") is None:
            daily_missing.append(label)
        else:
            daily_total += _f(summary.get("daily_pnl"))
            daily_reported.append(label)

    partial = bool(daily_reported and daily_missing)
    return {
        "connected":         connected_any,
        "total_value":       round(total_value, 2) if connected_any else None,
        "buying_power":      round(buying_power, 2) if connected_any else None,
        # Adding zero collapses the negative zero a rounded tiny loss leaves.
        "daily_pnl":         round(daily_total, 2) + 0 if daily_reported else None,
        "daily_pnl_partial": partial,
        "daily_pnl_label":   ", ".join(daily_reported) if partial else None,
        "daily_pnl_missing": daily_missing,
        "total_unrealized":  round(unrealized, 2) + 0 if connected_any else None,
        "open_positions":    positions,
        "accounts":          accounts,
        "sources":           sources,
        "broker_count":      sum(1 for s in sources if s["connected"]),
        "error":             None,
    }


def get_account_summary(user_id: int = 1) -> dict:
    """Every connected brokerage, added up. Same shape one broker returns."""
    return combine([
        (key, label, _safe_summary_for(key, user_id))
        for key, label in BROKERS
    ])
