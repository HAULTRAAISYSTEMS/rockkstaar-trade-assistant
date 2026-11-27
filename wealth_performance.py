"""Performance analytics shared by the Wealth dashboard and Scoreboard."""
from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from math import sqrt
from statistics import mean, stdev
import re

import wealth_calc
import wealth_history
from wealth_ledger import EPS, OPTION_TYPES, _f

BENCHMARK = "^SP500TR"
BENCHMARK_LABEL = "S&P 500 Total Return Index"
STRATEGIES = {"long_term": "Long-Term Investing", "active": "Active Trading", "unclassified": "Unclassified"}
DEFAULT_GOAL = {"return_pct": 20.0, "contribution": 25000.0}
TRANSFER_REF = re.compile(r"(?<!\S)transfer:([\w.-]{1,64})(?!\S)", re.IGNORECASE)
TRANSFER_TYPES = {"DEPOSIT", "WITHDRAWAL", "TRANSFER_IN", "TRANSFER_OUT"}
EXTERNAL_MARKER = re.compile(r"(?<!\S)external-transfer(?!\S)", re.IGNORECASE)


def transfer_review(note):
    note = note or ""
    match = TRANSFER_REF.search(note)
    if match:
        return "internal", match[1]
    return ("external" if EXTERNAL_MARKER.search(note) else "pending"), ""


def transfer_note(note, mode, reference):
    clean = EXTERNAL_MARKER.sub("", TRANSFER_REF.sub("", note)).strip()
    if mode == "pending":
        result = clean
    elif mode == "external":
        result = f"{clean} external-transfer".strip()
    elif mode == "internal" and re.fullmatch(r"[\w.-]{1,64}", reference):
        result = f"{clean} transfer:{reference}".strip()
    else:
        raise ValueError("Enter a reference of up to 64 letters, digits, underscores, periods or hyphens.")
    if len(result) > 200:
        raise ValueError("The note and reference together must be 200 characters or less.")
    return result


def clean_contribution_plan(year, plan):
    """Validate a saved assumption. A null plan clears the schedule."""
    if plan is None:
        return None
    if isinstance(plan, list):
        if len(plan) > 30:
            raise ValueError("Use up to 30 contribution schedules per year.")
        if any(not isinstance(entry, dict) for entry in plan):
            raise ValueError("Enter a valid contribution schedule.")
        return [clean_contribution_plan(year, entry) for entry in plan] or None
    if not isinstance(plan, dict) or plan.get("cadence") not in ("weekly", "biweekly", "monthly", "once"):
        raise ValueError("Choose weekly, every other week, monthly, or one-time contributions.")
    amount = wealth_calc._f(plan.get("amount"))
    if amount is None or isinstance(plan.get("amount"), bool) or not .01 <= amount <= 1e10:
        raise ValueError("Enter a positive contribution amount.")
    cents = Decimal(str(amount)) * 100
    if cents != cents.to_integral_value():
        raise ValueError("Enter the contribution amount to the nearest cent.")
    try:
        start = date.fromisoformat(plan.get("start_date"))
    except (TypeError, ValueError):
        raise ValueError("Enter a first contribution date.") from None
    if start.year != year:
        raise ValueError("Choose a first contribution date in the goal year.")
    result = {"cadence": plan["cadence"], "amount": amount, "start_date": start.isoformat()}
    if plan.get("end_date") and plan["cadence"] != "once":
        try:
            end = date.fromisoformat(plan["end_date"])
        except (TypeError, ValueError):
            raise ValueError("Enter a valid last contribution date.") from None
        if end.year != year or end < start:
            raise ValueError("The last contribution must be on or after the first date in the same year.")
        result["end_date"] = end.isoformat()
    return result


def _scheduled_contributions(year, plan):
    if not plan:
        return []
    entries = plan if isinstance(plan, list) else [plan]
    scheduled = []
    for entry in entries:
        first = date.fromisoformat(entry["start_date"])
        last = date.fromisoformat(entry["end_date"]) if entry.get("end_date") else date(year, 12, 31)
        cadence = entry["cadence"]
        if cadence == "once":
            dates = [first]
        elif cadence in ("weekly", "biweekly"):
            every = 7 if cadence == "weekly" else 14
            dates = [first + timedelta(days=offset) for offset in range(0, (last - first).days + 1, every)]
        else:
            dates = [date(year, month, min(first.day, monthrange(year, month)[1]))
                     for month in range(first.month, last.month + 1)]
        amount = Decimal(str(entry["amount"]))
        scheduled.extend((day, amount) for day in dates if day <= last)
    return scheduled


def contribution_projection(year, plan, target, actual, today, *, all_accounts=True):
    """Schedule assumptions never affect ledger totals or historical returns."""
    if not plan:
        return None
    scheduled = _scheduled_contributions(year, plan)
    remaining = [(day, amount) for day, amount in scheduled if day > today]
    remaining_amount = float(sum(amount for _, amount in remaining))
    baseline = 0. if year > today.year else actual if all_accounts else None
    projected = round(baseline + remaining_amount, 2) if baseline is not None else None
    return dict(total=float(sum(amount for _, amount in scheduled)), count=len(scheduled), remaining=remaining_amount,
                remaining_count=len(remaining), next_date=min(day for day, _ in remaining).isoformat() if remaining else None,
                year_end=projected, gap=round(projected - target, 2) if projected is not None else None)


def contribution_chart(year, plan, target, today, actual=None, first_recorded=None):
    """Cumulative month-end funding; the current actual month is through today."""
    planned = defaultdict(lambda: Decimal(0))
    for day, amount in _scheduled_contributions(year, plan):
        planned[day.month] += amount
    plan_total = Decimal(0)
    actual_total = 0.
    rows = []
    for month in range(1, 13):
        end = date(year, month, monthrange(year, month)[1])
        plan_total += planned[month]
        actual_total += (actual or {}).get((year, month), 0.)
        has_coverage = (actual is not None and first_recorded and end >= first_recorded
                        and date(year, month, 1) <= today)
        rows.append(dict(month=month, target=round(target * month / 12, 2),
                         planned=float(plan_total) if plan else None,
                         actual=round(actual_total, 2) if has_coverage else None))
    return rows


def _cash_movement(event, paired):
    kind = event["transaction"]["type"]
    return kind in ("DEPOSIT", "WITHDRAWAL") and id(event) not in paired


def monthly_contributions(ledgers, paired, today):
    totals = defaultdict(float)
    for ledger in ledgers.values():
        for event in ledger.events:
            day = event["date"]
            if day <= today and _cash_movement(event, paired):
                totals[day.year, day.month] += event["flow"]
    return totals


def transfers(ledgers):
    """Reconcile only explicitly identified opposite legs. Never guess by size alone."""
    keys = defaultdict(list)
    issues = defaultdict(list)
    unmarked = defaultdict(list)
    for pid, ledger in ledgers.items():
        for event in ledger.events:
            tx = event["transaction"]
            kind = tx["type"]
            if kind not in TRANSFER_TYPES:
                continue
            note = tx.get("note") or ""
            mode, reference = transfer_review(note)
            if mode == "internal":
                keys[reference.lower()].append((pid, event))
            elif mode == "pending":
                cash = kind in {"DEPOSIT", "WITHDRAWAL"}
                quantity = abs(event["flow"]) if cash else _f(tx.get("shares"))
                unmarked[event["date"], None if cash else event["ticker"], round(quantity, 7)].append(
                    (pid, kind in {"DEPOSIT", "TRANSFER_IN"}))
                if note.lower().startswith(("internal transfer", "journal", "cash in lieu")):
                    issues[pid].append(f"{event['date']}: verify this cash movement in the Ledger.")
    for (day, _, _), legs in unmarked.items():
        incoming = {pid for pid, credit in legs if credit}
        outgoing = {pid for pid, credit in legs if not credit}
        if incoming and outgoing and len(incoming | outgoing) > 1:
            message = f"{day}: possible internal transfer. Add a common reference or mark each movement external."
            for pid in incoming | outgoing:
                issues[pid].append(message)
    paired = {}
    for legs in keys.values():
        if len(legs) != 2:
            for pid, _ in legs:
                issues[pid].append("An internal transfer reference needs exactly two ledger entries.")
            continue
        (p1, a), (p2, b) = sorted(legs, key=lambda leg: leg[1]["transaction"]["type"])
        ta, tb = a["transaction"], b["transaction"]
        cash = (ta["type"], tb["type"]) == ("DEPOSIT", "WITHDRAWAL")
        security = (ta["type"], tb["type"]) == ("TRANSFER_IN", "TRANSFER_OUT")
        equal_cash = cash and abs(a["flow"] + b["flow"]) < 1e-7
        equal_shares = (security and a["ticker"] == b["ticker"]
                        and abs(_f(ta["shares"]) - _f(tb["shares"])) < EPS)
        if p1 == p2 or a["date"] != b["date"] or not (equal_cash or equal_shares):
            for pid in (p1, p2):
                issues[pid].append("An internal transfer must match across two accounts on the same effective date.")
            continue
        paired[id(a)] = p2
        paired[id(b)] = p1
        if security and abs(a["moved_basis"] - b["moved_basis"]) > .02:
            for pid in (p1, p2):
                issues[pid].append(f"{a['date']}: the {a['ticker']} transfer has mismatched carrying cost basis.")
    return paired, issues


def external_flows(ledgers, paired, closes, provider_splits):
    flows, security_flows, issues = [], {}, []
    market = {ticker: wealth_history._Closes(rows) for ticker, rows in closes.items() if rows}
    for pid, ledger in ledgers.items():
        for event in ledger.events:
            amount = event["flow"]
            if amount is None or paired.get(id(event)) in ledgers:
                continue
            tx = event["transaction"]
            if tx["type"] in {"TRANSFER_IN", "TRANSFER_OUT"}:
                day, ticker = event["date"], event["ticker"]
                amount = _f(tx.get("amount"))
                if not amount and ticker in market:
                    px = market[ticker].on(day)
                    if px:
                        splits = ledger.splits if provider_splits is None else provider_splits.get(ticker, ())
                        amount = px * _f(tx["shares"]) * wealth_history._split_factor_after(splits, ticker, day)
                if not amount:
                    issues.append(f"{day}: transfer of {ticker} needs a market value.")
                    continue
                amount *= 1 if tx["type"] == "TRANSFER_IN" else -1
                security_flows[id(event)] = amount
            flows.append((event["date"], amount))
    return flows, security_flows, issues


def ledger_issues(ledger):
    """Report potential duplicate DRIP income instead of counting it twice."""
    cash_dividends = {(e["date"], e["ticker"], round(_f(e["transaction"].get("amount")), 2))
                      for e in ledger.events if e["transaction"]["type"] == "DIVIDEND"}
    issues = list(ledger.issues)
    for event in ledger.events:
        tx = event["transaction"]
        if tx["type"] == "REINVEST":
            amount = _f(tx.get("amount")) or _f(tx.get("shares")) * _f(tx.get("price"))
            key = (event["date"], event["ticker"], round(amount, 2))
            if key in cash_dividends:
                issues.append(f"{key[0]}: check whether the {key[1]} cash dividend duplicates a recorded reinvestment.")
    return issues


def split_issues(ledger, provider_splits, today):
    issues = []
    for ticker, rows in provider_splits.items():
        applied = {(day, round(ratio, 6)) for symbol, day, ratio in ledger.splits if symbol == ticker}
        reported = {(day, round(ratio, 6)) for _, day, ratio in rows}
        for day, ratio in reported - applied:
            prior = next((held for session, _, held in reversed(ledger.snapshots) if session < day), {})
            if prior.get(ticker) and day <= today:
                issues.append(f"{day}: verify the {ticker} split in the Ledger.")
        for day, _ in applied - reported:
            issues.append(f"{day}: the {ticker} ledger split differs from the closing price provider.")
    return issues


def ledger_activity(ledgers, paired, today):
    """Use booked ledger amounts. Trade fees already reside in costs/proceeds."""
    years = defaultdict(lambda: dict(deposits=0., withdrawals=0., dividends=0., interest=0., fees=0.,
                                     realized=0., standalone_fees=0., taxes=0., option_count=0,
                                     option_realized=0., option_fees=0.))
    for ledger in ledgers.values():
        for event in ledger.events:
            day, tx = event["date"], event["transaction"]
            if day > today:
                continue
            row = years[day.year]
            kind, amount = tx["type"], _f(tx.get("amount"))
            if _cash_movement(event, paired):
                row["deposits" if kind == "DEPOSIT" else "withdrawals"] += amount
            if kind in ("DIVIDEND", "REINVEST"):
                row["dividends"] += amount or _f(tx.get("shares")) * _f(tx.get("price"))
            if kind == "INTEREST":
                row["interest"] += amount
            if kind in ("BUY", "SELL", "REINVEST"):
                row["fees"] += _f(tx.get("fees"))
            if kind in OPTION_TYPES:
                fees = _f(tx.get("fees"))
                row["fees"] += fees
                row["option_fees"] += fees
                row["option_realized"] += event["realized"]
                row["option_count"] += 1
            if kind == "FEE":
                row["fees"] += amount
                row["standalone_fees"] += amount
            if kind == "TAX":
                row["taxes"] += amount
            row["realized"] += event["realized"]
    return years


def _shift_months(day, months):
    from calendar import monthrange
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


def _return(start, end):
    return (end / start - 1) * 100 if start and end is not None else None


def _spread(a, b):
    return a - b if a is not None and b is not None else None


def _target_path(days, start, year, annual_pct):
    year_start = date(year, 1, 1) - timedelta(days=1)
    year_days = (date(year, 12, 31) - year_start).days
    rate = 1 + annual_pct / 100
    return [(rate ** (max(0, (day - start).days) / year_days) - 1) * 100 for day in days]


def reference_history(benchmark, today, goal):
    """Index returns and goal pace can be shown while portfolio history is blocked."""
    year_start = date(today.year, 1, 1) - timedelta(days=1)
    levels = {day: level for day, level in benchmark if day <= today and level > 0}
    index = sorted(levels.items())
    base_day = max((day for day in levels if day <= year_start), default=None)
    has_ytd_base = base_day is not None and (year_start - base_day).days <= 7
    sessions = [day for day, _ in index if day.year == today.year]
    has_index_path = has_ytd_base and bool(sessions)
    if has_index_path:
        days = [base_day, *sessions]
        values = [_return(levels[base_day], levels[day]) for day in days]
    else:
        days = [year_start + timedelta(days=offset) for offset in range(0, (today - year_start).days, 7)]
        days.append(today)
        values = []

    rows = periods(index, levels, trimmed=True) if len(index) > 1 else []
    rows = [dict(row, portfolio=None, excess=None) for row in rows if row["label"] != "Available Period"]
    for row in rows:
        if row["label"] == "YTD" and not has_index_path:
            row.update(start=None, benchmark=None)
    return {
        "periods": [*rows, {"label": "All-Time"}] if rows else [],
        "chart": {"days": [day.isoformat() for day in days], "benchmark": values,
                  "target": _target_path(days, year_start, today.year, goal["return_pct"])},
    }


def periods(index, benchmark, *, trimmed=False):
    days = [day for day, _ in index]
    end = days[-1]
    from bisect import bisect_right
    ytd = date(end.year, 1, 1) - timedelta(days=1)
    windows = (("Today", days[-2]), ("1 Week", end - timedelta(days=7)),
               ("1 Month", _shift_months(end, 1)), ("3 Months", _shift_months(end, 3)),
               ("YTD", ytd), ("1 Year", _shift_months(end, 12)),
               ("Available Period" if trimmed else "All-Time", days[0]))
    result = []
    for label, start in windows:
        offset = bisect_right(days, start) - 1
        if label == "YTD" and offset < 0 and days[0].year == end.year and not trimmed:
            offset = 0
        row = {"label": label, "start": None, "end": end.isoformat(),
               "portfolio": None, "benchmark": None, "excess": None}
        if 0 <= offset < len(days) - 1:
            p = _return(index[offset][1], index[-1][1])
            b = _return(benchmark.get(days[offset]), benchmark.get(end))
            row.update(start=days[offset].isoformat(), portfolio=p, benchmark=b, excess=_spread(p, b))
        result.append(row)
    return result


def heatmap(index):
    result = defaultdict(dict)
    last = None
    for day, growth in index:
        if last and (day.year, day.month) != (last[0].year, last[0].month):
            start = last[1]
        if last is None:
            start = growth
        result[day.year][day.month] = _return(start, growth) if day != index[0][0] else None
        last = day, growth
    return dict(result)


def _daily(index):
    return {day: growth / prior - 1 for (_, prior), (day, growth) in zip(index, index[1:])
            if prior > 0 and growth is not None}


def _risk_stats(returns, risk_free):
    if len(returns) < 30:
        return dict(volatility_pct=None, sharpe=None, sortino=None)
    vol = stdev(returns)
    excess = [r - risk_free for r in returns]
    downside = sqrt(mean(min(x, 0) ** 2 for x in excess))
    return dict(volatility_pct=vol * sqrt(252) * 100,
                sharpe=mean(excess) / vol * sqrt(252) if vol > 1e-12 else None,
                sortino=mean(excess) / downside * sqrt(252) if downside > 1e-12 else None)


def risk(index, benchmark, annual_twr, annual_risk_free=0.0):
    rf = (1 + annual_risk_free) ** (1 / 252) - 1
    portfolio = _daily(index)
    aligned = [(day, benchmark[day]) for day, _ in index if benchmark.get(day) is not None]
    # Never treat a benchmark gap as a one-session return.
    market = {day: benchmark[day] / benchmark[prev] - 1
              for (prev, _), (day, _) in zip(index, index[1:])
              if benchmark.get(prev) and benchmark.get(day)}
    pairs = sorted(portfolio.keys() & market.keys())
    comparison = _risk_stats([market[day] for day in pairs], rf)
    comparable_portfolio = _risk_stats([portfolio[day] for day in pairs], rf)
    dd = wealth_calc.max_drawdown(index)
    out = dict(_risk_stats(list(portfolio.values()), rf), max_drawdown=dd,
               calmar=annual_twr / -dd["pct"] if annual_twr is not None and dd and dd["pct"] < 0 else None,
               benchmark=comparison, aligned_portfolio=comparable_portfolio,
               benchmark_max_drawdown=wealth_calc.max_drawdown(aligned) if len(aligned) == len(index) else None,
               beta=None, alpha_pct=None, observations=len(pairs), risk_free_pct=annual_risk_free * 100)
    if len(pairs) >= 60:
        p = [portfolio[day] - rf for day in pairs]
        b = [market[day] - rf for day in pairs]
        avg_p, avg_b = mean(p), mean(b)
        variance = sum((x - avg_b) ** 2 for x in b)
        if variance > 1e-12:
            out["beta"] = sum((x - avg_b) * (y - avg_p) for x, y in zip(b, p)) / variance
            out["alpha_pct"] = (avg_p - out["beta"] * avg_b) * 252 * 100
    return out


def attribution(series, index, ledgers, security_flows):
    """Geometrically linked return contribution by security, including closed holdings."""
    values = series["values"]
    start = date(index[-1][0].year, 1, 1)
    baseline = max((i for i, (day, _) in enumerate(index) if day < start), default=0)
    base = index[baseline][1]
    if not base:
        return []
    events = sorted((e for lg in ledgers.values() for e in lg.events), key=lambda e: e["date"])
    cursor = 0
    inflows = defaultdict(float)
    for day, amount in series["flows"]:
        inflows[day] += max(amount, 0)
    contributions, profits = defaultdict(float), defaultdict(float)
    marks = [row for _, row in series["security_values"]]
    for i, (day, _) in enumerate(index):
        cash = defaultdict(float)
        while cursor < len(events) and events[cursor]["date"] <= day:
            event = events[cursor]
            cursor += 1
            ticker, tx = event["ticker"], event["transaction"]
            if ticker and tx["type"] in {"BUY", "SELL", "DIVIDEND", "REINVEST", "TRANSFER_IN", "TRANSFER_OUT"}:
                cash[ticker] += event["cash"] - security_flows.get(id(event), 0)
        if i <= baseline:
            continue
        denominator = values[i - 1][1] + inflows[day]
        if denominator <= 0:
            continue
        for ticker in set(marks[i - 1]) | set(marks[i]) | set(cash):
            pnl = marks[i].get(ticker, 0) - marks[i - 1].get(ticker, 0) + cash[ticker]
            contributions[ticker] += pnl / denominator * index[i - 1][1] / base * 100
            profits[ticker] += pnl
    end_value = values[-1][1]
    return sorted((dict(ticker=ticker, contribution_pct=pct, profit=profits[ticker],
                        weight_pct=marks[-1].get(ticker, 0) / end_value * 100 if end_value > 0 else None)
                   for ticker, pct in contributions.items()), key=lambda row: (-row["contribution_pct"], row["ticker"]))


def analyze(series, benchmark, ledgers, security_flows, goals):
    summary = wealth_history.scoreboard(series, benchmark)
    index = wealth_history.growth_index(series["values"], series["flows"])
    levels = dict(benchmark)
    days = [day for day, _ in index]
    end = days[-1]
    ytd_start = date(end.year, 1, 1) - timedelta(days=1)
    offset = max((i for i, day in enumerate(days) if day <= ytd_start), default=0)
    start, base = index[offset]
    ytd_days = days[offset:]
    trimmed = series.get("option_history_trimmed", False)
    partial_ytd = trimmed and start > ytd_start
    target_start = start if partial_ytd else ytd_start
    chart = {
        "days": [day.isoformat() for day in ytd_days],
        "portfolio": [_return(base, level) for _, level in index[offset:]],
        "benchmark": [_return(levels.get(start), levels.get(day)) for day in ytd_days],
        "target": _target_path(ytd_days, target_start, end.year, goals.get(end.year, DEFAULT_GOAL)["return_pct"]),
    }
    months = heatmap(index)
    if trimmed:
        months[days[0].year].pop(days[0].month, None)
    return {**summary, "periods": periods(index, levels, trimmed=trimmed), "heatmap": months,
            "risk": risk(index, levels, summary["twr_annual_pct"]),
            "attribution": [] if partial_ytd else attribution(series, index, ledgers, security_flows),
            "growth_chart": chart, "partial_ytd": partial_ytd}
