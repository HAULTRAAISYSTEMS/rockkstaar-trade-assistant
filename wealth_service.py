"""Wealth Architect: turns a user's ledger and prices into the dashboard.

Prices are passed in (`price_lookup`) so this module stays testable and
never fetches on its own. Every priced number carries where the price came
from and when; a holding with no price is left out of market value and
listed, never filled in.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import wealth_calc
import wealth_ledger
import wealth_store

STALE_AFTER_DAYS = 3          # a stored price older than this is flagged


def default_price_lookup(tickers) -> dict:
    """Live shared quote first; the last stored price, dated, as a fallback."""
    tickers = sorted({t.upper() for t in tickers or [] if t})
    out = {}
    try:
        import market_engine
        live = market_engine.live_quotes(tickers) or {}
    except Exception:
        live = {}
    for t in tickers:
        q = live.get(t) or {}
        px = q.get("price")
        if isinstance(px, (int, float)) and px > 0:
            ep = q.get("epoch") or q.get("ts")
            as_of = datetime.fromtimestamp(ep, timezone.utc).strftime("%Y-%m-%dT%H:%MZ") if isinstance(ep, (int, float)) else None
            out[t] = {"price": float(px), "as_of": as_of, "source": q.get("source") or "live", "live": True}
    missing = [t for t in tickers if t not in out]
    for t, (px, upd) in wealth_store.stored_prices(missing).items():
        out[t] = {"price": float(px), "as_of": upd, "source": "last stored price", "live": False}
    return out


def _stale(as_of, today: date) -> bool:
    if not as_of:
        return True
    try:
        d = date.fromisoformat(str(as_of)[:10])
    except ValueError:
        return True
    return (today - d).days > STALE_AFTER_DAYS


def summarize(ledgers: dict, prices: dict, sectors: dict, *, today: date | None = None,
              max_position_pct: float | None = None) -> dict:
    """Combine one or more portfolio ledgers into dashboard figures.

    `ledgers` is {portfolio_id: LedgerResult}.
    """
    today = today or date.today()
    holdings = {}
    cash = deposits = withdrawals = t_in = t_out = 0.0
    dividends = interest = fees = taxes = 0.0
    realized, flows, issues = [], [], []
    for pid, lg in ledgers.items():
        cash += lg.cash
        deposits += lg.deposits; withdrawals += lg.withdrawals
        t_in += lg.transfers_in; t_out += lg.transfers_out
        dividends += lg.dividends; interest += lg.interest; fees += lg.fees; taxes += lg.taxes
        realized += lg.realized
        flows += lg.flows
        issues += lg.issues
        for h in lg.holdings():
            agg = holdings.setdefault(h["ticker"], {"ticker": h["ticker"], "shares": 0.0, "cost_basis": 0.0,
                                                    "lots": [], "dividends": 0.0})
            agg["shares"] += h["shares"]
            agg["cost_basis"] += h["cost_basis"]
            agg["lots"] += h["lots"]
        for tk, amt in lg.dividends_by_ticker.items():
            if tk in holdings:
                holdings[tk]["dividends"] += amt

    rows, unpriced, stale = [], [], []
    mv = 0.0
    priced_basis = 0.0
    for tk, h in sorted(holdings.items()):
        h["avg_cost"] = h["cost_basis"] / h["shares"] if h["shares"] else 0.0
        q = prices.get(tk)
        sec = sectors.get(tk) or (None, None)
        h["sector"], h["industry"] = sec[0] or "Unclassified", sec[1]
        if not q or not q.get("price"):
            h.update(price=None, market_value=None, unrealized=None, unrealized_pct=None,
                     price_source=None, price_as_of=None, stale=None)
            unpriced.append(tk)
        else:
            px = float(q["price"])
            h["price"] = px
            h["market_value"] = px * h["shares"]
            h["unrealized"] = h["market_value"] - h["cost_basis"]
            h["unrealized_pct"] = (h["unrealized"] / h["cost_basis"] * 100) if h["cost_basis"] else None
            h["price_source"], h["price_as_of"] = q.get("source"), q.get("as_of")
            h["stale"] = (not q.get("live")) and _stale(q.get("as_of"), today)
            if h["stale"]:
                stale.append(tk)
            mv += h["market_value"]
            priced_basis += h["cost_basis"]
        rows.append(h)

    total_value = mv + cash
    for h in rows:
        h["weight_pct"] = (h["market_value"] / total_value * 100) if (h["market_value"] is not None and total_value > 0) else None
    rows.sort(key=lambda h: -(h["market_value"] or 0))

    # Concentration
    # How many equal positions the invested money behaves like (cash aside).
    invested = [h["market_value"] for h in rows if h["market_value"]]
    effective_n = (1 / sum((v / mv) ** 2 for v in invested)) if (invested and mv > 0) else None
    over_limit = [h["ticker"] for h in rows if max_position_pct and (h["weight_pct"] or 0) > max_position_pct]
    sectors_out = {}
    for h in rows:
        if h["market_value"] is None:
            continue
        sectors_out[h["sector"]] = sectors_out.get(h["sector"], 0.0) + h["market_value"]
    if cash > 0.005:
        sectors_out["Cash"] = sectors_out.get("Cash", 0.0) + cash
    sector_rows = sorted(({"sector": k, "value": v, "pct": v / total_value * 100 if total_value > 0 else None}
                          for k, v in sectors_out.items()), key=lambda r: -r["value"])

    net_contrib = deposits - withdrawals + t_in - t_out
    complete = not unpriced
    total_return = total_value - net_contrib if complete else None
    total_return_pct = (total_return / net_contrib * 100) if (complete and net_contrib > 0) else None

    # Money-weighted annual return: the investor's own timing included.
    mwr = None
    first = min((d for d, _ in flows), default=None)
    if complete and flows and first and (today - first).days >= 30:
        cf = [(d, -a) for d, a in flows] + [(today, total_value)]
        r = wealth_calc.xirr(cf)
        mwr = r * 100 if r is not None else None

    realized_total = sum(r["gain"] for r in realized)
    return {
        "as_of": today.isoformat(),
        "holdings": rows,
        "market_value": mv,
        "cash": cash,
        "total_value": total_value,
        "cost_basis": sum(h["cost_basis"] for h in rows),
        "priced_cost_basis": priced_basis,
        "unrealized": mv - priced_basis,
        "unrealized_pct": ((mv - priced_basis) / priced_basis * 100) if priced_basis else None,
        "realized": realized_total,
        "realized_short": sum(r["gain"] for r in realized if r["term"] == "short"),
        "realized_long": sum(r["gain"] for r in realized if r["term"] == "long"),
        "realized_rows": sorted(realized, key=lambda r: r["date"], reverse=True),
        "dividends": dividends,
        "interest": interest,
        "fees": fees,
        "taxes": taxes,
        "deposits": deposits,
        "withdrawals": withdrawals,
        "transfers_in": t_in,
        "transfers_out": t_out,
        "net_contributions": net_contrib,
        "total_return": total_return,
        "total_return_pct": total_return_pct,
        "money_weighted_pct": mwr,
        "first_date": first.isoformat() if first else None,
        "effective_holdings": effective_n,
        "top_weight_pct": rows[0]["weight_pct"] if rows and rows[0]["weight_pct"] is not None else None,
        "top5_weight_pct": sum(h["weight_pct"] or 0 for h in rows[:5]) if rows else None,
        "over_limit": over_limit,
        "sectors": sector_rows,
        "unpriced": unpriced,
        "stale": stale,
        "issues": issues,
        "flows": flows,
    }


def spy_shadow(flows: list, spy_bars: dict | None, today: date, spy_now: float | None) -> dict | None:
    """What the same deposits and withdrawals would be worth had each one
    bought or sold SPY at that day's close. Compares like for like: same
    money, same dates. SPY closes here are dividend-adjusted when the bar
    source adjusts them; the result says which."""
    if not flows or not spy_bars or not spy_now:
        return None
    closes = _closes(spy_bars)
    if not closes:
        return None
    units = 0.0
    for d, amt in sorted(flows):
        px = _close_on(closes, d)
        if not px:
            return None
        units += amt / px
    value = units * spy_now
    net = sum(a for _, a in flows)
    cf = [(d, -a) for d, a in flows] + [(today, value)]
    r = wealth_calc.xirr(cf) if (today - min(d for d, _ in flows)).days >= 30 else None
    return {"value": value, "gain": value - net, "money_weighted_pct": r * 100 if r is not None else None}


def _closes(bars) -> list[tuple[date, float]]:
    """[(date, close)] from the app's bar dict ({"t": [...], "c": [...]}) or a
    list of {"time"/"t", "close"/"c"} rows."""
    out = []
    try:
        if isinstance(bars, dict) and "closes" in bars:
            for t, c in zip(bars.get("timestamps") or [], bars.get("closes") or []):
                out.append((_bar_date(t), float(c)))
        elif isinstance(bars, dict) and "c" in bars:
            for t, c in zip(bars.get("t") or [], bars.get("c") or []):
                out.append((_bar_date(t), float(c)))
        else:
            rows = bars.get("bars") if isinstance(bars, dict) else bars
            for b in rows or []:
                t = b.get("time", b.get("t"))
                c = b.get("close", b.get("c"))
                if t is not None and c is not None:
                    out.append((_bar_date(t), float(c)))
    except (TypeError, ValueError):
        return []
    return sorted({d: c for d, c in out if d and c > 0 and wealth_calc._f(c) is not None}.items())


def _bar_date(t) -> date | None:
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(t / 1000 if t > 1e11 else t, timezone.utc).date()
    try:
        return date.fromisoformat(str(t)[:10])
    except ValueError:
        return None


def _close_on(closes, d: date) -> float | None:
    """The close on `d`, or the last close before it (weekends, holidays)."""
    best = None
    for cd, c in closes:
        if cd <= d:
            best = c
        else:
            break
    if best is None and closes and (closes[0][0] - d).days <= 5:
        best = closes[0][1]
    return best


def reconcile(ledger_holdings: list[dict], ledger_cash: float, broker_account: dict) -> dict:
    """Ledger against what the broker reports for one account."""
    rows = []
    broker = {p["symbol"]: p for p in broker_account.get("equity_positions") or []}
    mine = {h["ticker"]: h for h in ledger_holdings}
    for tk in sorted(set(broker) | set(mine)):
        b, m = broker.get(tk), mine.get(tk)
        bs, ms = (b or {}).get("quantity") or 0.0, (m or {}).get("shares") or 0.0
        ba, ma = (b or {}).get("avg_price"), (m or {}).get("avg_cost")
        if not m:
            status = "missing_in_ledger"
        elif not b:
            status = "missing_at_broker"
        elif abs(bs - ms) > 1e-4:
            status = "share_mismatch"
        elif ba and ma and abs(ba - ma) / ba > 0.005:
            status = "cost_mismatch"
        else:
            status = "match"
        rows.append({"ticker": tk, "ledger_shares": ms, "broker_shares": bs,
                     "ledger_avg": ma, "broker_avg": ba, "status": status})
    bcash = broker_account.get("cash_balance")
    return {"rows": rows, "ledger_cash": ledger_cash, "broker_cash": bcash,
            "cash_diff": (ledger_cash - bcash) if bcash is not None else None,
            "matched": sum(1 for r in rows if r["status"] == "match"), "total": len(rows)}


def performance_balance(ledgers, broker_accounts, prices, today):
    """Combine one ledger or one Schwab observation per account."""
    summary = summarize(ledgers, prices, {}, today=today) if ledgers else None
    balances = []
    holdings = list(summary["holdings"] if summary else [])
    if summary:
        missing = bool(summary["unpriced"])
        balances.append(dict(value=None if missing else summary["total_value"], cash=summary["cash"],
                             available_cash=summary["cash"], basis=summary["cost_basis"],
                             unrealized=None if missing else summary["unrealized"]))
    for account in broker_accounts:
        positions = account.get("holdings", [])
        unsupported = account.get("unsupported_positions")
        known = unsupported == 0
        cash = wealth_calc._f(account.get("cash"))
        value = wealth_calc._f(account.get("total_value"))
        basis = 0.0 if known else None
        unrealized = 0.0 if known else None
        market_value = 0.0 if known else None
        for h in positions:
            mv = wealth_calc._f(h.get("verified_market_value", h.get("market_value") or None))
            cost = wealth_calc._f(h.get("verified_cost_basis", h.get("cost_basis_snapshot") or None))
            if cost is not None and cost < 0:
                cost = None
            if mv is not None and mv < 0:
                mv = None
            pnl = mv - cost if mv is not None and cost is not None else None
            market_value = market_value + mv if market_value is not None and mv is not None else None
            basis = basis + cost if basis is not None and cost is not None else None
            unrealized = unrealized + pnl if unrealized is not None and pnl is not None else None
            holdings.append(dict(ticker=h["ticker"], shares=h["shares"], market_value=mv, cost_basis=cost,
                                 unrealized=pnl, price_source="Schwab", price_as_of=account.get("as_of")))
        if value is None and not account.get("live") and cash is not None and market_value is not None:
            value = cash + market_value
        balances.append(dict(value=value, cash=cash, available_cash=wealth_calc._f(account.get("available_funds")),
                             basis=basis, unrealized=unrealized))

    def total(key):
        entries = [b[key] for b in balances]
        return sum(entries) if entries and all(v is not None for v in entries) else None

    value, basis, unrealized = total("value"), total("basis"), total("unrealized")
    combined = {}
    sources = set()
    for h in holdings:
        if h.get("price_source"):
            sources.add((h["price_source"], h.get("price_as_of") or ""))
        row = combined.setdefault(h["ticker"], dict(ticker=h["ticker"], market_value=0., cost_basis=0.))
        for field in ("market_value", "cost_basis"):
            v = h.get(field)
            row[field] = row[field] + v if row[field] is not None and v is not None else None
    for h in combined.values():
        mv, cost = h["market_value"], h["cost_basis"]
        h["return_pct"] = (mv / cost - 1) * 100 if mv is not None and cost and cost > 0 else None
        h["weight_pct"] = mv / value * 100 if mv is not None and value and value > 0 else None
    rows = sorted(combined.values(), key=lambda h: (-(h["market_value"] or 0), h["ticker"]))
    invested = [h["market_value"] for h in rows]
    position_total = sum(invested) if invested and all(v is not None for v in invested) else None
    concentration = None
    if position_total and all(a.get("unsupported_positions") == 0 for a in broker_accounts):
        concentration = dict(largest_pct=max(invested) / position_total * 100,
                             top5_pct=sum(invested[:5]) / position_total * 100,
                             effective_holdings=1 / sum((v / position_total) ** 2 for v in invested))
    return dict(value=value, cash=total("cash"), available_cash=total("available_cash"),
                basis=basis, unrealized=unrealized, unrealized_pct=unrealized / basis * 100
                if basis and unrealized is not None else None, holdings=rows, concentration=concentration,
                quote_sources=[dict(source=source, as_of=timestamp or None) for source, timestamp in sorted(sources)],
                stale=summary["stale"] if summary else [],
                unpriced=summary["unpriced"] if summary else [])
