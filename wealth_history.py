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
                 today: date | None = None) -> dict:
    """Daily values across the given ledgers.

    `closes` is {ticker: [(date, close)]}; `calendar` is the trading days to
    value on (SPY's days). Returns values, flows, contributions and the
    tickers that had no price history (the series is not trusted then).
    """
    flows = sorted(f for lg in ledgers for f in lg.flows)
    if not flows:
        return {"ok": False, "reason": "No deposits or transfers yet."}
    start = flows[0][0]
    days = [d for d in calendar if d >= start]
    if today and today_prices and (not days or days[-1] < today):
        days.append(today)
    if len(days) < 2:
        return {"ok": False, "reason": "Not enough history yet: check back after a few trading days."}

    series = {t: _Closes(rows) for t, rows in closes.items() if rows}
    missing = sorted({t for lg in ledgers for _, _, held in lg.snapshots for t in held} - set(series))
    if missing:
        return {"ok": False, "reason": "No price history for " + ", ".join(missing) + ".", "missing": missing}

    # Walk each ledger's snapshots alongside the calendar.
    values, gaps = [], set()
    pointers = [0] * len(ledgers)
    states = [(0.0, {}) for _ in ledgers]
    for d in days:
        total = 0.0
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
                    total += sh * px                    # live price: actual shares, no adjustment
                    continue
                px = series[tk].on(d)
                if px is None:
                    gaps.add(tk)
                    continue
                total += sh * _split_factor_after(lg.splits, tk, d) * px
        values.append((d, total))

    # Flows dated on non-trading days count on the next valued day.
    pending = 0.0
    flow_on_days = []
    fi = 0
    for d in days:
        while fi < len(flows) and flows[fi][0] <= d:
            pending += flows[fi][1]
            fi += 1
        if pending:
            flow_on_days.append((d, pending))
            pending = 0.0
    contrib, running = [], 0.0
    fmap = dict(flow_on_days)
    for d in days:
        running += fmap.get(d, 0.0)
        contrib.append((d, running))
    return {"ok": True, "values": values, "flows": flow_on_days, "contributions": contrib,
            "gaps": sorted(gaps), "start": days[0], "end": days[-1]}


def growth_index(values, flows) -> list[tuple[date, float]]:
    """Growth of $1 with flows removed (the time-weighted path)."""
    fmap = {}
    for d, a in flows:
        fmap[d] = fmap.get(d, 0.0) + a
    out, idx, prev = [], 1.0, None
    for d, v in values:
        f = fmap.get(d, 0.0)
        if prev is None or prev <= 0:
            if v > 0:
                prev = v
            out.append((d, idx))
            continue
        idx *= (v - f) / prev
        prev = v
        out.append((d, idx))
    return out


def benchmark_index(spy: list[tuple[date, float]], days: list[date]) -> list[tuple[date, float]]:
    c = _Closes(spy)
    base = c.on(days[0])
    if not base:
        return []
    return [(d, (c.on(d) or base) / base) for d in days]


def yearly(index: list[tuple[date, float]]) -> dict:
    """Return per calendar year from a growth index."""
    out, start_val, year, last = {}, None, None, None
    for d, v in index:
        if year != d.year:
            if year is not None and start_val:
                out[year] = (last / start_val - 1) * 100
            year, start_val = d.year, (last if last is not None else v)
        last = v
    if year is not None and start_val:
        out[year] = (last / start_val - 1) * 100
    return out


def scoreboard(series: dict, spy_closes: list[tuple[date, float]] | None) -> dict:
    values, flows = series["values"], series["flows"]
    days = [d for d, _ in values]
    idx = growth_index(values, flows)
    twr = idx[-1][1] - 1 if idx else None
    span = (days[-1] - days[0]).days
    end_value = values[-1][1]
    contributed = series["contributions"][-1][1]
    cf = [(d, -a) for d, a in flows] + [(days[-1], end_value)]
    mwr = wealth_calc.xirr(cf) if span >= 30 else None
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
        if b:
            spy_tr = b[-1][1] - 1
            out["spy_pct"] = spy_tr * 100
            ann = wealth_calc.annualize(spy_tr, span)
            out["spy_annual_pct"] = ann * 100 if ann is not None else None
            out["excess_pct"] = (twr - spy_tr) * 100 if twr is not None else None
            out["spy_years"] = yearly(b)
            out["chart"]["spy"] = [round(v, 5) for _, v in b]
    return out
