"""CSV import for the Wealth Architect ledger.

Two formats are read:

1. Schwab "Transaction History" export (Accounts → History → Export):
   "Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"
   Dates can read "11/14/2025 as of 11/13/2025" (the first date is used).
   Money is written "$1,234.56" or "-$1,234.56".
2. The app's own template:
   date,type,ticker,shares,price,amount,fees,ratio,note

Parsing only proposes transactions. Nothing is saved until the investor
reviews the preview and commits it; rows the parser does not understand are
listed with their reason rather than guessed.
"""
from __future__ import annotations

import csv
from collections import Counter
import hashlib
import io
import math
import re
from datetime import datetime

from wealth_ledger import TYPES, normalize_option_symbol, validate

TEMPLATE_HEADER = ["date", "type", "ticker", "shares", "price", "amount", "fees", "ratio", "note"]

# Schwab action text → ledger type. Matching is case-insensitive on the
# whole action. Anything not listed is reported back as "not imported".
SCHWAB_ACTIONS = {
    "buy": "BUY",
    "buy to open": "BUY",
    "sell": "SELL",
    "sell to close": "SELL",
    "reinvest shares": "BUY",            # the shares half of a DRIP; the cash half is "Reinvest Dividend"
    "reinvest dividend": "DIVIDEND",
    "qualified dividend": "DIVIDEND",
    "non-qualified div": "DIVIDEND",
    "cash dividend": "DIVIDEND",
    "special qual div": "DIVIDEND",
    "special dividend": "DIVIDEND",
    "pr yr div reinvest": "DIVIDEND",
    "pr yr cash div": "DIVIDEND",
    "qual div reinvest": "DIVIDEND",
    "long term cap gain": "DIVIDEND",
    "short term cap gain": "DIVIDEND",
    "bank interest": "INTEREST",
    "credit interest": "INTEREST",
    "bond interest": "INTEREST",
    "nra tax adj": "TAX",
    "foreign tax paid": "TAX",
    "adr mgmt fee": "FEE",
    "service fee": "FEE",
    "wire funds": "WITHDRAWAL",
    "stock split": "SPLIT",
    "forward split": "SPLIT",
    "reverse split": "SPLIT",
}
# Cash moving in or out of the account: the sign of Amount decides which.
SCHWAB_CASH_MOVES = ("moneylink transfer", "moneylink deposit", "funds received", "wire received",
                     "wire sent", "journal", "journaled shares", "internal transfer", "ach deposit",
                     "ach withdrawal", "cash in lieu")
SCHWAB_SHARE_MOVES = ("security transfer", "stock plan activity", "journaled shares", "spin-off")


def _money(s) -> float | None:
    if s is None:
        return None
    s = str(s).strip().replace("$", "").replace(",", "")
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _date(s) -> str | None:
    s = str(s or "").strip()
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    return None


def fingerprint(tx: dict) -> str:
    """Same transaction → same fingerprint, so re-importing a file that
    overlaps an earlier one does not double it."""
    key = "|".join(str(tx.get(k) if tx.get(k) is not None else "") for k in
                   ("date", "type", "ticker", "shares", "price", "amount", "fees", "ratio", "raw"))
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _round(v, n=6):
    return None if v is None else round(v, n)


def parse(text: str) -> dict:
    """{'format', 'transactions', 'skipped'} from CSV text."""
    text = (text or "").lstrip("﻿")
    lines = text.splitlines()
    # Schwab puts a title line above the header; find the header row.
    start = 0
    for i, line in enumerate(lines[:10]):
        low = line.lower()
        if ("action" in low and "symbol" in low) or low.startswith("date,type"):
            start = i
            break
    body = "\n".join(lines[start:])
    reader = csv.DictReader(io.StringIO(body))
    fields = [f.strip().lower() for f in (reader.fieldnames or [])]
    if "action" in fields and "symbol" in fields:
        return _parse_schwab(reader)
    if "type" in fields and "date" in fields:
        return _parse_template(reader)
    return {"format": None, "transactions": [], "skipped": [
        {"row": 0, "reason": "Not a Schwab transaction export or the Wealth Architect template "
                             "(expected columns Date, Action, Symbol… or date, type, ticker…)."}]}


def _row(raw: dict) -> dict:
    return {k.strip().lower(): (v or "").strip() for k, v in raw.items() if isinstance(k, str)}


def _parse_template(reader) -> dict:
    out, skipped = [], []
    for n, raw in enumerate(reader, start=2):
        r = _row(raw)
        if not any(r.values()):
            continue
        tx = {"date": _date(r.get("date")), "type": (r.get("type") or "").upper(),
              "ticker": (r.get("ticker") or "").upper() or None,
              "shares": _money(r.get("shares")), "price": _money(r.get("price")),
              "amount": _money(r.get("amount")), "fees": _money(r.get("fees")) or 0.0,
              "ratio": _money(r.get("ratio")), "note": r.get("note") or "", "source": "csv"}
        errs = validate(tx)
        if errs:
            skipped.append({"row": n, "reason": " ".join(errs), "text": ", ".join(v for v in r.values() if v)[:120]})
            continue
        tx["raw"] = f"template:{n}"
        tx["fingerprint"] = fingerprint(dict(tx, raw=""))
        out.append(tx)
    return {"format": "template", "transactions": out, "skipped": skipped}


def _parse_schwab(reader) -> dict:
    out, skipped = [], []
    occurrences = Counter()
    for n, raw in enumerate(reader, start=2):
        r = _row(raw)
        action = (r.get("action") or "").strip()
        low = action.lower()
        if not low:
            if r.get("date", "").lower().startswith("transactions total"):
                continue
            if any(r.values()):
                skipped.append({"row": n, "reason": "No action.", "text": ", ".join(v for v in r.values() if v)[:120]})
            continue
        day = _date(r.get("date"))
        raw_sym = (r.get("symbol") or "").upper().strip()
        sym = raw_sym.replace(" ", "")
        qty = _money(r.get("quantity"))
        price = _money(r.get("price"))
        fees = _money(r.get("fees & comm")) or 0.0
        amount = _money(r.get("amount"))
        text = f"{r.get('date')} {action} {sym} {r.get('quantity')} {r.get('amount')}".strip()
        tx = None
        if low in SCHWAB_ACTIONS:
            t = SCHWAB_ACTIONS[low]
            if t in ("BUY", "SELL"):
                if " " in raw_sym or "/" in raw_sym or re.search(r"\d{6}[CP]\d", sym):
                    option_kind = {"buy to open": "OPTION_BUY", "sell to close": "OPTION_SELL"}.get(low)
                    option_symbol = normalize_option_symbol(raw_sym)
                    candidate = {"date": day, "type": option_kind, "ticker": option_symbol, "shares": abs(qty or 0),
                                 "price": price, "fees": abs(fees), "amount": amount}
                    if option_kind and option_symbol and amount is not None and not validate(candidate):
                        tx = candidate
                    else:
                        review = {"row": n, "reason": "Option trade needs review: check action, contract and cash.", "text": text}
                        if amount is not None and math.isfinite(amount):
                            review["cash"] = amount
                        skipped.append(review)
                        continue
                else:
                    tx = {"type": t, "ticker": sym, "shares": abs(qty or 0), "price": abs(price or 0), "fees": abs(fees)}
            elif t == "SPLIT":
                tx = {"type": "SPLIT", "ticker": sym, "shares": qty if (qty or 0) > 0 else None,
                      "ratio": None}
                if (qty or 0) < 0:
                    skipped.append({"row": n, "reason": "A reverse split shows shares removed; enter it by hand "
                                                        "with its ratio (e.g. 0.2 for 1-for-5).", "text": text})
                    continue
            elif t in ("DIVIDEND", "INTEREST"):
                tx = {"type": t, "ticker": sym or None, "amount": abs(amount or 0)}
                if t == "DIVIDEND" and not sym:
                    skipped.append({"row": n, "reason": "Dividend without a symbol.", "text": text})
                    continue
                if (amount or 0) < 0:
                    # A negative "dividend" is a reversal; record it as a fee-like correction.
                    tx = {"type": "FEE", "amount": abs(amount), "note": f"{action} reversal {sym}"}
            elif t in ("TAX", "FEE"):
                tx = {"type": t, "amount": abs(amount or 0), "note": f"{action} {sym}".strip()}
                if (amount or 0) > 0:
                    tx = {"type": "INTEREST", "amount": amount, "note": f"{action} refund {sym}".strip()}
            elif t == "WITHDRAWAL":
                tx = {"type": "WITHDRAWAL" if (amount or 0) < 0 else "DEPOSIT", "amount": abs(amount or 0)}
        elif low == "moneylink adj" and not sym and amount and re.match(
                r"^TFR\s+.*\bBANK\b", r.get("description", ""), re.IGNORECASE):
            tx = {"type": "DEPOSIT" if amount > 0 else "WITHDRAWAL", "amount": abs(amount),
                  "note": "MoneyLink bank transfer adjustment"}
        elif any(low.startswith(k) for k in SCHWAB_CASH_MOVES) and not sym:
            if not amount:
                skipped.append({"row": n, "reason": "Transfer without an amount.", "text": text})
                continue
            tx = {"type": "DEPOSIT" if amount > 0 else "WITHDRAWAL", "amount": abs(amount)}
        elif any(low.startswith(k) for k in SCHWAB_SHARE_MOVES) and sym:
            # Shares moving between accounts carry no cost in the export. Bring
            # them in with the cost the investor enters on the preview.
            if (qty or 0) > 0:
                tx = {"type": "TRANSFER_IN", "ticker": sym, "shares": qty, "price": 0.0,
                      "needs_cost": True}
            elif (qty or 0) < 0:
                tx = {"type": "TRANSFER_OUT", "ticker": sym, "shares": abs(qty)}
        if tx is None:
            skipped.append({"row": n, "reason": f"“{action}” is not a type Wealth Architect imports yet.", "text": text})
            continue
        identity = tuple(sorted(r.items()))
        occurrences[identity] += 1
        tx.update({"date": day, "source": "schwab_csv",
                   "raw": repr((identity, occurrences[identity]))})
        tx.setdefault("fees", 0.0)
        tx.setdefault("note", action)
        for k in ("shares", "price", "amount", "fees", "ratio"):
            tx[k] = _round(tx.get(k))
        errs = validate(tx)
        if errs and not tx.get("needs_cost"):
            skipped.append({"row": n, "reason": " ".join(errs), "text": text})
            continue
        tx["fingerprint"] = fingerprint(tx)
        out.append(tx)
    # Schwab lists newest first; the ledger sorts by date anyway.
    out.sort(key=lambda t: t["date"] or "")
    return {"format": "schwab", "transactions": out, "skipped": skipped}


def template_csv() -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(TEMPLATE_HEADER)
    w.writerow(["2026-01-05", "DEPOSIT", "", "", "", "2000", "", "", "January contribution"])
    w.writerow(["2026-01-06", "BUY", "MSFT", "2.5", "410.00", "", "0", "", ""])
    w.writerow(["2026-03-12", "DIVIDEND", "MSFT", "", "", "2.08", "", "", ""])
    w.writerow(["2026-06-10", "SPLIT", "ABC", "", "", "", "", "10", "10-for-1"])
    return buf.getvalue()


assert set(SCHWAB_ACTIONS.values()) <= set(TYPES)
