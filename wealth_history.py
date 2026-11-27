"""Wealth Scoreboard: the portfolio's value day by day, and what it earned.

Daily value = cash + Σ shares × close. The closes the app has are adjusted
for splits but not for dividends, so:
- a day before a split values the shares held then at the split-adjusted
  close, after scaling those shares by every later split (otherwise a
  10-for-1 split would look like a 90% fall);
- dividends are counted once, as the cash they paid into the account.

Money the investor added or removed is a flow, not a gain. Time-weighted
return removes it; money-weighted return (XIRR) keeps the investor's timing.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import date
from collections import defaultdict
from math import isfinite

import wealth_calc


def _split_factor_after(splits, ticker: str, d: date) -> float:
    f = 1.0
    for tk, sd, ratio in splits:
        if tk == ticker and sd > d:
            f *= ratio
    return f


class _Closes:
    def __init__(self, rows):
        rows = sorted(rows)
        self.days = [d for d, _ in rows]
        self.vals = [c for _, c in rows]

    def on(self, d: date):
        """Close on d, or the latest before it. None before the first bar."""
        i = bisect_right(self.days, d) - 1
        return self.vals[i] if i >= 0 else None


def build_series(ledgers: list, closes: dict, calendar: list[date], today_prices: dict | None = None,
                 today: date | None = None, *, flows=None, provider_splits=None) -> dict:
    """Daily values across the given ledgers.

    `closes` is {ticker: [(date, close)]}; `calendar` is the session calendar.
    Missing session closes invalidate the series while a position is open.
    """
    flows = sorted(flows if flows is not None else (f for lg in ledgers for f in lg.flows))
    if not flows:
        return {"ok": False, "reason": "No deposits or transfers yet."}
    start = flows[0][0]
    days = sorted({d for d in calendar if d >= start and (today is None or d <= today)})
    if today and today_prices and (not days or days[-1] < today):
        days.append(today)
    if len(days) < 2:
        return {"ok": False, "reason": "Not enough history yet: check back after a few trading days."}

    series = {t: dict(rows) for t, rows in closes.items() if rows}
    missing = sorted({t for lg in ledgers for _, _, held in lg.snapshots for t in held} - set(series))
    if missing:
        return {"ok": False, "reason": "No price history for " + ", ".join(missing) + ".", "missing": missing}

    # Walk each ledger's snapshots alongside the calendar.
    values, gaps, security_values = [], set(), []
    pointers = [0] * len(ledgers)
    states = [(0.0, {}) for _ in ledgers]
    for d in days:
        total = 0.0
        marks = defaultdict(float)
        for i, lg in enumerate(ledgers):
            snaps = lg.snapshots
            while pointers[i] < len(snaps) and snaps[pointers[i]][0] <= d:
                _, cash, held = snaps[pointers[i]]
                states[i] = (cash, held)
                pointers[i] += 1
            cash, held = states[i]
            total += cash
            for tk, sh in held.items():
                if today and d == today and today_prices and tk in today_prices:
                    px = today_prices[tk]
                    marks[tk] += sh * px                # live price: actual shares, no adjustment
                    continue
                px = series[tk].get(d)
                if px is None or not isfinite(px) or px <= 0:
                    gaps.add(tk)
                    continue
                splits = lg.splits if provider_splits is None else provider_splits.get(tk, ())
                marks[tk] += sh * _split_factor_after(splits, tk, d) * px
        values.append((d, total + sum(marks.values())))
        security_values.append((d, dict(marks)))
    if gaps:
        return {"ok": False, "reason": "Missing daily closing prices for " + ", ".join(sorted(gaps)) + ".",
                "gaps": sorted(gaps)}

    # Flows dated on non-trading days count on the next valued day.
    flow_on_days, contrib = [], []
    fi, running = 0, 0.0
    for d in days:
        while fi < len(flows) and flows[fi][0] <= d:
            amount = flows[fi][1]
            flow_on_days.append((d, amount))
            running += amount
            fi += 1
        contrib.append((d, running))
    if not growth_index(values, flow_on_days):
        return {"ok": False, "reason": "Valuations or cash flows do not support a continuous return series."}
    return {"ok": True, "values": values, "flows": flow_on_days, "dated_flows": flows[:fi],
            "contributions": contrib, "security_values": security_values,
            "gaps": [], "start": days[0], "end": days[-1]}


def growth_index(values, flows) -> list[tuple[date, float]]:
    return wealth_calc.growth_path(values, flows)


def benchmark_index(closes, days: list[date]) -> list[tuple[date, float]]:
    """Use exact session matches. A missing observation stays missing."""
    prices = dict(closes)
    base = prices.get(days[0])
    if not base:
        return []
    return [(d, prices[d] / base if prices.get(d) else None) for d in days]




def yearly(index: list[tuple[date, float]]) -> dict:
    """Return per calendar year from a growth index."""
    out, start_val, year, last = {}, None, None, None
    for d, v in index:
        if year != d.year:
            if year is not None and start_val and last is not None:
                out[year] = (last / start_val - 1) * 100
            year, start_val = d.year, (last if last is not None else v)
        last = v
    if year is not None and start_val and last is not None:
        out[year] = (last / start_val - 1) * 100
    return out


def benchmark_yearly(levels, days):
    """Match exactly the endpoints used by the portfolio's yearly return."""
    windows = {}
    for i, day in enumerate(days):
        start, _ = windows.get(day.year, (days[max(i - 1, 0)], None))
        windows[day.year] = start, day
    return {year: (levels[end] / levels[start] - 1) * 100
            for year, (start, end) in windows.items() if levels.get(start) and levels.get(end)}


def _verified_xirr(flows):
    # Consolidate same-day transactions. One chronological sign change
    # guarantees a unique economically relevant IRR if a solution exists.
    amounts = defaultdict(float)
    for day, amount in flows:
        amounts[day] += amount
    net = sorted((day, amount) for day, amount in amounts.items() if abs(amount) > 1e-9)
    changes = sum((a < 0) != (b < 0) for (_, a), (_, b) in zip(net, net[1:]))
    return wealth_calc.xirr(net) if changes == 1 else None


def scoreboard(series: dict, spy_closes: list[tuple[date, float]] | None) -> dict:
    values, flows = series["values"], series["flows"]
    days = [d for d, _ in values]
    idx = growth_index(values, flows)
    twr = idx[-1][1] - 1 if idx else None
    span = (days[-1] - days[0]).days
    end_value = values[-1][1]
    contributed = series["contributions"][-1][1]
    cf = [(d, -a) for d, a in series.get("dated_flows", flows)] + [(days[-1], end_value)]
    mwr = _verified_xirr(cf) if span >= 30 else None
    dd = wealth_calc.max_drawdown(idx)
    peak = max(v for _, v in idx) if idx else None
    current_dd = (idx[-1][1] / peak - 1) * 100 if peak else None
    out = {
        "start": days[0].isoformat(), "end": days[-1].isoformat(), "days": span,
        "end_value": end_value, "contributed": contributed, "gain": end_value - contributed,
        "twr_pct": twr * 100 if twr is not None else None,
        "twr_annual_pct": (wealth_calc.annualize(twr, span) or 0) * 100 if wealth_calc.annualize(twr, span) is not None else None,
        "mwr_annual_pct": mwr * 100 if mwr is not None else None,
        "max_drawdown": dd, "current_drawdown_pct": current_dd,
        "years": yearly(idx),
        "chart": {"days": [d.isoformat() for d in days],
                  "value": [round(v, 2) for _, v in values],
                  "contributed": [round(v, 2) for _, v in series["contributions"]],
                  "growth": [round(v, 5) for _, v in idx]},
        "gaps": series["gaps"],
    }
    if spy_closes:
        b = benchmark_index(spy_closes, days)
        if b and b[-1][1] is not None:
            spy_tr = b[-1][1] - 1
            out["spy_pct"] = spy_tr * 100
            ann = wealth_calc.annualize(spy_tr, span)
            out["spy_annual_pct"] = ann * 100 if ann is not None else None
            out["excess_pct"] = (twr - spy_tr) * 100 if twr is not None else None
            out["spy_years"] = benchmark_yearly(dict(spy_closes), days)
            out["chart"]["spy"] = [round(v, 5) if v is not None else None for _, v in b]
    return out
