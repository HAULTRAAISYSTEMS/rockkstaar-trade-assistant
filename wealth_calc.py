"""Wealth Architect calculations that are not bookkeeping.

Cost Basis Lab, returns (XIRR, time-weighted, annualized), drawdown.
Pure functions, no I/O.
"""
from __future__ import annotations

import math
from datetime import date

# Price moves applied to the purchase price for the scenario table.
SCENARIO_MOVES = (-0.30, -0.15, 0.0, 0.25, 0.50, 1.00)


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x or math.isinf(x) else x


def simulate_purchase(*, existing_shares, existing_avg_cost, purchase_price, invest_amount,
                      future_price=None, current_price=None, fees=0.0, fractional=True,
                      cash_available=None, portfolio_value=None) -> dict:
    """What one more purchase does to a position.

    New average cost = (existing cost basis + new purchase cost) / total shares.

    The scenario table shows the new money's own outcome beside the whole
    position, because a lower average cost does not by itself raise the
    expected return: the added shares earn exactly what the stock does from
    the purchase price, whatever was paid for the earlier shares.
    """
    s0 = _f(existing_shares) or 0.0
    a0 = _f(existing_avg_cost) or 0.0
    p = _f(purchase_price)
    amt = _f(invest_amount)
    fees = max(_f(fees) or 0.0, 0.0)
    errors = []
    if s0 < 0:
        errors.append("Existing shares cannot be negative.")
    if a0 < 0:
        errors.append("Existing average cost cannot be negative.")
    if s0 > 0 and a0 <= 0:
        errors.append("Enter the existing average cost.")
    if not p or p <= 0:
        errors.append("Enter a purchase price above zero.")
    if amt is None or amt <= 0:
        errors.append("Enter an amount to invest above zero.")
    if errors:
        return {"ok": False, "errors": errors}

    spendable = amt - fees
    if spendable <= 0:
        return {"ok": False, "errors": ["The fees use up the whole amount."]}
    new_shares = spendable / p if fractional else math.floor(spendable / p + 1e-9)
    if new_shares <= 0:
        return {"ok": False, "errors": [f"${amt:,.2f} does not buy one whole share at ${p:,.2f}. "
                                        f"Turn on fractional shares or raise the amount."]}
    buy_cost = new_shares * p + fees
    left_of_amount = amt - buy_cost
    cur = _f(current_price) or p
    fut = _f(future_price)

    basis0 = s0 * a0
    total_shares = s0 + new_shares
    total_basis = basis0 + buy_cost
    new_avg = total_basis / total_shares

    def pl(shares, basis, price):
        return shares * price - basis

    out = {
        "ok": True,
        "new_shares": new_shares,
        "purchase_cost": buy_cost,
        "fees": fees,
        "left_of_amount": left_of_amount,
        "remaining_cash": (_f(cash_available) - buy_cost) if _f(cash_available) is not None else None,
        "total_shares": total_shares,
        "existing_basis": basis0,
        "total_basis": total_basis,
        "old_avg_cost": a0 if s0 > 0 else None,
        "new_avg_cost": new_avg,
        "avg_cost_change": (new_avg - a0) if s0 > 0 else None,
        "current_price": cur,
        "pl_now_before": pl(s0, basis0, cur) if s0 > 0 else None,
        "pl_now_after": pl(total_shares, total_basis, cur),
        # Breakeven moves with the average; it says nothing about expected return.
        "breakeven_price": new_avg,
    }
    if fut is not None and fut > 0:
        out["future_price"] = fut
        out["pl_future_before"] = pl(s0, basis0, fut) if s0 > 0 else None
        out["pl_future_after"] = pl(total_shares, total_basis, fut)
        out["return_future_after_pct"] = (out["pl_future_after"] / total_basis * 100) if total_basis else None
        out["new_money_gain_future"] = new_shares * fut - buy_cost
        out["new_money_return_pct"] = (out["new_money_gain_future"] / buy_cost * 100) if buy_cost else None

    rows = []
    prices = sorted({round(p * (1 + m), 4) for m in SCENARIO_MOVES} | ({round(fut, 4)} if fut else set()))
    for px in prices:
        rows.append({
            "price": px,
            "move_pct": (px / p - 1) * 100,
            "position_pl": pl(total_shares, total_basis, px),
            "position_return_pct": pl(total_shares, total_basis, px) / total_basis * 100,
            "new_money_pl": new_shares * px - buy_cost,
            "without_purchase_pl": pl(s0, basis0, px) if s0 > 0 else 0.0,
            "is_target": bool(fut) and abs(px - fut) < 1e-6,
        })
    out["scenarios"] = rows

    pv = _f(portfolio_value)
    if pv and pv > 0:
        before = s0 * cur / pv * 100
        # Buying with cash already in the portfolio moves money from cash to
        # the stock; the portfolio total does not change.
        after = total_shares * cur / pv * 100
        out["weight_before_pct"] = before
        out["weight_after_pct"] = after
    return out


# ── Returns ──────────────────────────────────────────────────────────────

def xirr(flows: list[tuple[date, float]], guess: float = 0.1) -> float | None:
    """Money-weighted annual return. Flows are from the investor's side:
    money put in is negative, money taken out (or the ending value) positive.
    None when it cannot be solved (no sign change, or too short)."""
    flows = sorted((d, a) for d, a in flows if a)
    if len(flows) < 2 or not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        return None
    t0 = flows[0][0]
    yrs = [((d - t0).days / 365.0, a) for d, a in flows]
    if yrs[-1][0] <= 0:
        return None

    def npv(r):
        return sum(a / (1 + r) ** y for y, a in yrs)

    def dnpv(r):
        return sum(-y * a / (1 + r) ** (y + 1) for y, a in yrs)

    r = guess
    for _ in range(100):
        try:
            v, dv = npv(r), dnpv(r)
        except (OverflowError, ZeroDivisionError):
            break
        if abs(v) < 1e-7:
            return r
        if dv == 0:
            break
        nr = r - v / dv
        if nr <= -0.9999 or not math.isfinite(nr):
            break
        if abs(nr - r) < 1e-10:
            return nr
        r = nr
    lo, hi = -0.9999, 10.0                       # fall back to bisection
    try:
        flo, fhi = npv(lo), npv(hi)
    except (OverflowError, ZeroDivisionError):
        return None
    if flo * fhi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        fm = npv(mid)
        if abs(fm) < 1e-7:
            return mid
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def twr(values: list[tuple[date, float]], flows: list[tuple[date, float]]) -> float | None:
    """Time-weighted return over the series, as a fraction.

    `values` are end-of-day portfolio values; `flows` are money added (+) or
    removed (-) by the investor. A flow on a day is treated as arriving at
    the start of that day, so the day's return is
    (value_end - flow) / value_start - 1. Removes the effect of when money was
    added, which is what makes it comparable with an index.
    """
    if len(values) < 2:
        return None
    by_day = {}
    for d, a in flows:
        by_day[d] = by_day.get(d, 0.0) + a
    growth = 1.0
    prev = values[0][1]
    for d, v in values[1:]:
        f = by_day.get(d, 0.0)
        if prev <= 0:
            # Nothing invested before today: start measuring from today's money.
            prev = v
            continue
        r = (v - f) / prev - 1
        growth *= (1 + r)
        prev = v
    return growth - 1


def annualize(total_return: float | None, days: int) -> float | None:
    if total_return is None or days < 1 or total_return <= -1:
        return None
    if days < 365:
        return None          # under a year an annual rate overstates; show the period return
    return (1 + total_return) ** (365.0 / days) - 1


def max_drawdown(series: list[tuple[date, float]]) -> dict | None:
    """Largest peak-to-trough fall in a value series (use growth-of-$1, not
    raw values, so deposits do not hide a fall)."""
    if len(series) < 2:
        return None
    peak_d, peak = series[0]
    worst = {"pct": 0.0, "peak": peak_d, "trough": peak_d}
    for d, v in series:
        if v > peak:
            peak, peak_d = v, d
        if peak > 0:
            dd = v / peak - 1
            if dd < worst["pct"]:
                worst = {"pct": dd, "peak": peak_d, "trough": d}
    return {"pct": worst["pct"] * 100, "peak": worst["peak"].isoformat(), "trough": worst["trough"].isoformat()}
