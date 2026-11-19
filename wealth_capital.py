"""Phase 4: transparent balanced allocation scoring and risk controls.

No trade execution, no ledger writes. Unknown evidence never becomes zero.
"""
from __future__ import annotations
import math

WEIGHTS = {"quality": 25, "valuation": 25, "financials": 20, "growth": 15, "diversification": 15}


def number(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def portfolio_state(ledger_summary, broker_positions, *, broker_cash=None):
    """Broker positions supersede matching ledger tickers; no duplicate exposure."""
    positions = {}
    for h in ledger_summary.get("holdings") or []:
        v = number(h.get("market_value"))
        if v is not None and v >= 0:
            positions[h["ticker"]] = v
    unknown = []
    for h in broker_positions:
        v = number(h.get("market_value"))
        if v is None or v < 0:
            unknown.append(h["ticker"])
            positions.pop(h["ticker"], None)
        else:
            positions[h["ticker"]] = v
    cash = number(broker_cash)
    if cash is None:
        cash = number(ledger_summary.get("cash"))
    if cash is None or cash < 0:
        cash = 0.0
    total = sum(positions.values()) + cash
    return {"holdings": positions, "cash": cash, "total_value": total,
            "unknown_market_values": sorted(set(unknown)), "source": "broker snapshots + nonoverlapping ledger"}


def balanced_rank(amount, candidates, state, *, position_limit=25, sector_limit=40,
                  cash_is_existing=False, cash_reserve=0):
    amount = number(amount)
    if amount is None or amount <= 0:
        raise ValueError("Investment amount must be positive")
    total = number(state.get("total_value")) or 0
    holdings = state.get("holdings") or {}
    cash = number(state.get("cash")) or 0
    denom = total if cash_is_existing else total + amount
    reserve = max(number(cash_reserve) or 0, 0)
    rows = []
    for c in candidates:
        ev = c.get("evaluation") or {}
        gates = ev.get("gates") or {}
        valuation = ev.get("valuation") or {}
        sec = c.get("sector") or "Unclassified"
        current = number(holdings.get(c["ticker"])) or 0
        pos_after = 100 * (current + amount) / denom if denom else None
        sec_value = sum(number(x.get("market_value")) or 0 for x in candidates
                        if x.get("sector") == sec and x.get("ticker") in holdings)
        # The caller supplies a complete sector-value mapping where possible.
        sector_value = number((state.get("sectors") or {}).get(sec))
        sector_after = 100 * (sector_value + amount) / denom if sector_value is not None and denom else None
        components = {}
        for label, gate in (("quality", "moat"), ("financials", "financials"), ("growth", "future")):
            score = number((gates.get(gate) or {}).get("score"))
            components[label] = min(100, max(0, score * 10)) if score is not None else None
        # Valuation is an eligibility gate, not a fabricated intrinsic-value estimate.
        components["valuation"] = (100 if valuation.get("in_zone") is True
                                    else 0 if valuation.get("in_zone") is False else None)
        components["diversification"] = (max(0, 100 - max(0, pos_after - 10) * 4)
                                          if pos_after is not None else None)
        available = sum(WEIGHTS[k] for k, v in components.items() if v is not None)
        score = (round(sum(WEIGHTS[k] * v for k, v in components.items() if v is not None) / available, 1)
                 if available else None)
        blocks = []
        if ev.get("status") != "BUY ZONE":
            blocks.append("M.C.F.F. gates or valuation do not qualify: " + str(ev.get("status") or "unverified"))
        if pos_after is None:
            blocks.append("Portfolio total is unknown")
        elif pos_after > position_limit:
            blocks.append("Position would exceed the " + str(position_limit) + "% limit")
        if sector_after is not None and sector_after > sector_limit:
            blocks.append("Sector would exceed the " + str(sector_limit) + "% limit")
        if cash_is_existing and amount > cash - reserve:
            blocks.append("Insufficient available cash after reserve")
        if state.get("unknown_market_values"):
            blocks.append("Missing broker market values: concentration cannot be verified")
        if available < 80:
            blocks.append("Insufficient verified evidence for a confident balanced ranking")
        rows.append({"ticker": c["ticker"], "company": c.get("company"), "sector": sec,
                     "price": c.get("price"), "status": ev.get("status"), "eligible": not blocks,
                     "score": score, "coverage_pct": available, "components": components,
                     "weight_before_pct": round(100 * current / total, 2) if total else None,
                     "weight_after_pct": round(pos_after, 2) if pos_after is not None else None,
                     "sector_after_pct": round(sector_after, 2) if sector_after is not None else None,
                     "zone": valuation.get("zone_text"), "blocks": blocks,
                     "reasons": ev.get("reasons") or []})
    rows.sort(key=lambda r: (not r["eligible"], -(r["score"] if r["score"] is not None else -1), r["ticker"]))
    return {"amount": amount, "mode": "balanced", "portfolio_value": total,
            "assumed_new_money": not cash_is_existing,
            "rows": rows, "verdict": (rows[0]["ticker"] + " leads eligible candidates"
                      if rows and rows[0]["eligible"] else "HOLD CASH — no candidate passes all controls"),
            "limits": {"position_pct": position_limit, "sector_pct": sector_limit},
            "warnings": (["Broker market values missing: allocation may be incomplete"]
                         if state.get("unknown_market_values") else [])}
