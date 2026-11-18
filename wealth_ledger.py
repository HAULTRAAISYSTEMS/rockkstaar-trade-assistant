"""Wealth Architect ledger: transactions in, positions and tax lots out.

Pure functions, no database or network. The transaction list is the
authoritative record; positions, cost basis, cash, realized gains and
income are all derived from it, so fixing a transaction fixes everything
downstream.

Transaction types
-----------------
DEPOSIT       amount            cash in from outside (a contribution)
WITHDRAWAL    amount            cash out to outside
BUY           ticker shares price fees
SELL          ticker shares price fees      lots consumed first-in, first-out
DIVIDEND      ticker amount     cash dividend (income, not a contribution)
REINVEST      ticker shares price [amount]  dividend used to buy shares: counts
                                as dividend income and opens a lot; cash nets out
INTEREST      amount            income
FEE           amount            account fee
TAX           amount            withholding (e.g. foreign tax on an ADR dividend)
SPLIT         ticker ratio       new shares per old share (10-for-1 → 10;
                                1-for-5 reverse → 0.2), or shares = shares
                                received (as a broker statement shows it)
TRANSFER_IN   ticker shares price [amount]  shares moved in from another account;
                                price = cost per share carried over, amount =
                                market value on the day (for return maths; cost
                                basis is used when it is missing)
TRANSFER_OUT  ticker shares [amount]

Cost basis rules
----------------
- A lot's cost includes the purchase fees.
- A sale's proceeds are net of fees; the gain is proceeds minus the cost of
  the lots it consumed (FIFO, the broker default for stocks).
- A split changes share counts, never total cost.
- Holding period: more than one year from the lot's date is long-term.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

EPS = 1e-9

TYPES = ("DEPOSIT", "WITHDRAWAL", "BUY", "SELL", "DIVIDEND", "REINVEST", "INTEREST",
         "FEE", "TAX", "SPLIT", "TRANSFER_IN", "TRANSFER_OUT")
NEEDS_TICKER = {"BUY", "SELL", "DIVIDEND", "REINVEST", "SPLIT", "TRANSFER_IN", "TRANSFER_OUT"}

# Within one day: splits first (a split dated the same day as a purchase
# applies to shares held before it), money in before money spent, sales after
# purchases, money out last.
_DAY_ORDER = {"SPLIT": 0, "DEPOSIT": 1, "TRANSFER_IN": 2, "BUY": 3, "REINVEST": 3,
              "DIVIDEND": 4, "INTEREST": 4, "SELL": 5, "FEE": 6, "TAX": 6,
              "TRANSFER_OUT": 7, "WITHDRAWAL": 8}


def _f(v, default=0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return default if x != x else x


def _d(v) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def validate(tx: dict) -> list[str]:
    """Problems that make a transaction unusable, in plain words."""
    errs = []
    t = str(tx.get("type") or "").upper()
    if t not in TYPES:
        return [f"Unknown transaction type “{tx.get('type')}”."]
    try:
        _d(tx.get("date"))
    except (TypeError, ValueError):
        errs.append("Date must be YYYY-MM-DD.")
    if t in NEEDS_TICKER and not str(tx.get("ticker") or "").strip():
        errs.append(f"{t.title()} needs a ticker.")
    shares, price, amount = _f(tx.get("shares")), _f(tx.get("price")), _f(tx.get("amount"))
    if t in ("BUY", "SELL", "REINVEST", "TRANSFER_IN") and shares <= 0:
        errs.append("Shares must be more than zero.")
    if t in ("BUY", "SELL", "REINVEST") and price <= 0:
        errs.append("Price must be more than zero.")
    if t == "TRANSFER_IN" and price < 0:
        errs.append("Cost per share cannot be negative.")
    if t == "TRANSFER_OUT" and shares <= 0:
        errs.append("Shares must be more than zero.")
    if t in ("DEPOSIT", "WITHDRAWAL", "DIVIDEND", "INTEREST", "FEE", "TAX") and amount <= 0:
        errs.append("Amount must be more than zero.")
    if t == "SPLIT" and _f(tx.get("ratio")) <= 0 and shares <= 0:
        errs.append("A split needs a ratio (e.g. 10 for 10-for-1) or the shares received.")
    if _f(tx.get("fees")) < 0:
        errs.append("Fees cannot be negative.")
    return errs


@dataclass
class Lot:
    date: date
    shares: float
    cost: float          # total, fees included

    @property
    def per_share(self) -> float:
        return self.cost / self.shares if self.shares > EPS else 0.0


@dataclass
class LedgerResult:
    positions: dict = field(default_factory=dict)     # ticker -> list[Lot]
    cash: float = 0.0
    deposits: float = 0.0
    withdrawals: float = 0.0
    transfers_in: float = 0.0
    transfers_out: float = 0.0
    dividends: float = 0.0
    dividends_by_ticker: dict = field(default_factory=dict)
    interest: float = 0.0
    fees: float = 0.0
    taxes: float = 0.0
    realized: list = field(default_factory=list)
    flows: list = field(default_factory=list)          # (date, amount) money the investor put in (+) or took out (-)
    splits: list = field(default_factory=list)         # (ticker, date, ratio) as applied
    snapshots: list = field(default_factory=list)      # (date, cash, {ticker: shares}) after each day with activity
    issues: list = field(default_factory=list)
    first_date: date | None = None

    @property
    def net_contributions(self) -> float:
        return self.deposits - self.withdrawals + self.transfers_in - self.transfers_out

    @property
    def realized_total(self) -> float:
        return sum(r["gain"] for r in self.realized)

    def realized_term(self, term: str) -> float:
        return sum(r["gain"] for r in self.realized if r["term"] == term)

    def holding(self, ticker: str) -> dict | None:
        lots = self.positions.get(ticker)
        if not lots:
            return None
        shares = sum(l.shares for l in lots)
        cost = sum(l.cost for l in lots)
        return {"ticker": ticker, "shares": shares, "cost_basis": cost,
                "avg_cost": cost / shares if shares > EPS else 0.0,
                "lots": [{"date": l.date.isoformat(), "shares": l.shares, "cost": l.cost,
                          "per_share": l.per_share} for l in lots],
                "first_date": min(l.date for l in lots).isoformat()}

    def holdings(self) -> list[dict]:
        return [h for h in (self.holding(t) for t in sorted(self.positions)) if h]


def _sort_key(tx):
    return (_d(tx["date"]), _DAY_ORDER.get(str(tx["type"]).upper(), 9), int(tx.get("id") or 0))


def _long_term(acquired: date, sold: date) -> bool:
    # More than one year: held past the anniversary of the purchase date.
    try:
        anniversary = acquired.replace(year=acquired.year + 1)
    except ValueError:                       # Feb 29
        anniversary = acquired.replace(year=acquired.year + 1, day=28)
    return sold > anniversary


def _take_fifo(lots: list[Lot], shares: float) -> tuple[list[tuple[Lot, float, float]], float]:
    """Remove `shares` from the oldest lots. Returns [(lot, shares, cost)] and
    any shortfall (shares asked for that were not held)."""
    taken, left = [], shares
    while left > EPS and lots:
        lot = lots[0]
        n = min(lot.shares, left)
        cost = lot.cost * (n / lot.shares) if lot.shares > EPS else 0.0
        taken.append((lot, n, cost))
        lot.shares -= n
        lot.cost -= cost
        left -= n
        if lot.shares <= EPS:
            lots.pop(0)
    return taken, max(left, 0.0)


def build(transactions: list[dict]) -> LedgerResult:
    """Replay every transaction in date order."""
    r = LedgerResult()
    txs = []
    for tx in transactions or []:
        errs = validate(tx)
        if errs:
            r.issues.append(f"Skipped {tx.get('type')} {tx.get('ticker') or ''} on {tx.get('date')}: {' '.join(errs)}")
            continue
        txs.append(tx)
    for tx in sorted(txs, key=_sort_key):
        t = str(tx["type"]).upper()
        day = _d(tx["date"])
        r.first_date = r.first_date or day
        tk = str(tx.get("ticker") or "").upper().strip()
        shares, price = _f(tx.get("shares")), _f(tx.get("price"))
        amount, fees = _f(tx.get("amount")), _f(tx.get("fees"))
        lots = r.positions.setdefault(tk, []) if tk else None

        if t == "DEPOSIT":
            r.cash += amount; r.deposits += amount; r.flows.append((day, amount))
        elif t == "WITHDRAWAL":
            r.cash -= amount; r.withdrawals += amount; r.flows.append((day, -amount))
        elif t == "BUY":
            cost = shares * price + fees
            r.cash -= cost; r.fees += fees
            lots.append(Lot(day, shares, cost))
        elif t == "REINVEST":
            value = amount if amount > 0 else shares * price
            r.dividends += value
            r.dividends_by_ticker[tk] = r.dividends_by_ticker.get(tk, 0.0) + value
            r.cash += value - (shares * price + fees)   # nets to about zero
            r.fees += fees
            lots.append(Lot(day, shares, shares * price + fees))
        elif t == "SELL":
            taken, short = _take_fifo(lots, shares)
            sold = shares - short
            if short > EPS:
                r.issues.append(f"{day} sell of {shares:g} {tk}: only {sold:g} shares were held; "
                                f"the rest was ignored. Check for a missing buy or transfer.")
            if sold <= EPS:
                continue
            proceeds_total = sold * price - fees
            r.cash += proceeds_total; r.fees += fees
            for lot, n, cost in taken:
                proceeds = proceeds_total * (n / sold)
                r.realized.append({"ticker": tk, "date": day.isoformat(), "acquired": lot.date.isoformat(),
                                   "shares": n, "proceeds": proceeds, "cost": cost, "gain": proceeds - cost,
                                   "term": "long" if _long_term(lot.date, day) else "short"})
        elif t == "DIVIDEND":
            r.cash += amount; r.dividends += amount
            r.dividends_by_ticker[tk] = r.dividends_by_ticker.get(tk, 0.0) + amount
        elif t == "INTEREST":
            r.cash += amount; r.interest += amount
        elif t == "FEE":
            r.cash -= amount; r.fees += amount
        elif t == "TAX":
            r.cash -= amount; r.taxes += amount
        elif t == "SPLIT":
            held = sum(l.shares for l in lots)
            ratio = _f(tx.get("ratio"))
            if ratio <= 0:
                if held <= EPS:
                    r.issues.append(f"{day} split of {tk}: no shares were held, so the shares received "
                                    f"cannot be turned into a ratio. Skipped.")
                    continue
                ratio = (held + shares) / held
            for lot in lots:
                lot.shares *= ratio           # total cost unchanged
            r.splits.append((tk, day, ratio))
        elif t == "TRANSFER_IN":
            basis = shares * price
            lots.append(Lot(day, shares, basis))
            value = amount if amount > 0 else basis
            r.transfers_in += value; r.flows.append((day, value))
        elif t == "TRANSFER_OUT":
            taken, short = _take_fifo(lots, shares)
            if short > EPS:
                r.issues.append(f"{day} transfer out of {shares:g} {tk}: only {shares - short:g} shares were held.")
            moved_cost = sum(c for _l, _n, c in taken)
            value = amount if amount > 0 else moved_cost
            r.transfers_out += value; r.flows.append((day, -value))
        if tk and not r.positions.get(tk):
            r.positions.pop(tk, None)
        snap = (day, r.cash, {t: sum(l.shares for l in ls) for t, ls in r.positions.items() if ls})
        if r.snapshots and r.snapshots[-1][0] == day:
            r.snapshots[-1] = snap
        else:
            r.snapshots.append(snap)
    if r.cash < -0.005:
        r.issues.append(f"Cash is below zero ({r.cash:,.2f}). A deposit may be missing, or the "
                        f"account uses margin.")
    return r


def shares_on(transactions: list[dict], ticker: str, as_of: date) -> float:
    """Shares of `ticker` held at the end of `as_of` (for history and checks)."""
    tk = ticker.upper()
    upto = [t for t in transactions or [] if _d(t["date"]) <= as_of]
    lots = build(upto).positions.get(tk) or []
    return sum(l.shares for l in lots)
