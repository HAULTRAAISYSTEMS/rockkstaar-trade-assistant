"""Next Dollar engine: where the next investment dollar goes, or whether it stays cash.

Ranks the investor's own candidates against their own rules. It does not
pick stocks the investor has not researched, and it never ranks a company
up because its price fell or because buying would lower an average cost.

Eligibility, in order:
  1. M.C.F.F. status is BUY ZONE (all four gates pass, price inside the zone)
  2. the purchase keeps the position at or under the portfolio's max weight
  3. the base-case annual return clears the investor's hurdle, if one is set
Eligible candidates rank by base-case annual return, then by the
conservative case (the better downside wins a tie). Holding cash is always a
row: it ranks first when nothing is eligible.
"""
from __future__ import annotations


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def rank(amount: float, candidates: list[dict], *, total_value: float, holdings: dict,
         sectors: dict, max_position_pct: float | None = None, hurdle_pct: float | None = None) -> dict:
    """`candidates`: [{ticker, price, sector, evaluation, scenarios}]
    `holdings`: {ticker: market_value}; `sectors`: {sector: market_value}.
    Total value includes cash, which is where `amount` comes from, so buying
    moves money from cash to the stock and the total does not change."""
    amount = _f(amount) or 0.0
    tv = _f(total_value) or 0.0
    hurdle = _f(hurdle_pct)
    rows = []
    for c in candidates:
        tk = c["ticker"]
        ev = c.get("evaluation") or {}
        sc = {s["case"]: s for s in c.get("scenarios") or []}
        base, cons, opt = (sc.get(k, {}).get("annual_return_pct") for k in ("base", "conservative", "optimistic"))
        price = _f(c.get("price"))
        held = _f(holdings.get(tk)) or 0.0
        after = held + amount
        w_before = held / tv * 100 if tv > 0 else None
        w_after = after / tv * 100 if tv > 0 else None
        sec = c.get("sector") or "Unclassified"
        s_after = ((_f(sectors.get(sec)) or 0.0) + amount) / tv * 100 if tv > 0 else None
        why, blocks = [], []
        status = ev.get("status") or "DO NOT BUY"
        if status != "BUY ZONE":
            blocks.append(status + ": " + " ".join(ev.get("reasons") or []))
        if max_position_pct and w_after is not None and w_after > max_position_pct:
            blocks.append(f"Would be {w_after:.1f}% of the portfolio, over your {max_position_pct:g}% limit.")
        if base is None:
            blocks.append("No base-case return: fill in growth and exit P/E.")
        elif hurdle is not None and base < hurdle:
            blocks.append(f"Base case {base:.1f}%/yr is under your {hurdle:g}% hurdle.")
        if status == "BUY ZONE":
            why.append(ev.get("valuation", {}).get("zone_text") or "Inside the buy zone.")
        if base is not None:
            why.append(f"Base case {base:+.1f}%/yr over {sc['base'].get('years')} years "
                       f"({(sc['base'].get('growth') or 0) * 100:+.1f}% growth, exit P/E {sc['base'].get('exit_pe')}).")
        if cons is not None:
            why.append(f"Conservative case {cons:+.1f}%/yr.")
        if s_after is not None:
            why.append(f"{sec} would be {s_after:.1f}% of the portfolio after this.")
        rows.append({"ticker": tk, "price": price, "status": status, "eligible": not blocks,
                     "base_pct": base, "conservative_pct": cons, "optimistic_pct": opt,
                     "shares": (amount / price) if price else None,
                     "weight_before_pct": w_before, "weight_after_pct": w_after,
                     "sector": sec, "sector_after_pct": s_after,
                     "why": why, "blocks": blocks})
    eligible = sorted([r for r in rows if r["eligible"]],
                      key=lambda r: (-(r["base_pct"] or -999), -(r["conservative_pct"] or -999), r["ticker"]))
    others = sorted([r for r in rows if not r["eligible"]],
                    key=lambda r: ({"BUY ZONE": 0, "HOLD": 1}.get(r["status"], 2), -(r["base_pct"] or -999)))
    cash = {"ticker": "CASH", "status": "HOLD CASH", "eligible": True, "base_pct": 0.0,
            "why": ["Keeps the money ready for a company that reaches its buy zone. "
                    "Cash interest, if any, is not included."], "blocks": []}
    ordered = (eligible + [cash] if eligible else [cash]) + others
    for i, r in enumerate(ordered, start=1):
        r["rank"] = i
    top = ordered[0]
    if top["ticker"] == "CASH":
        verdict = ("Hold the cash. None of these candidates passes all four gates inside its buy zone"
                   + (" and clears your hurdle" if hurdle is not None else "") + " right now.")
    else:
        verdict = (f"{top['ticker']} ranks first: it passes all four gates inside its buy zone and has the "
                   f"highest base-case return of the eligible candidates ({top['base_pct']:+.1f}%/yr).")
    return {"amount": amount, "rows": ordered, "verdict": verdict, "eligible_count": len(eligible)}
