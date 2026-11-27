"""Convert verified Schwab transaction rows to Wealth ledger entries."""
from datetime import date
import hashlib
import re

from wealth_calc import _f
from wealth_ledger import validate

CASH_IN = {"ACH_RECEIPT", "WIRE_IN"}
CASH_OUT = {"ACH_DISBURSEMENT", "WIRE_OUT"}


def _trade(items, net):
    securities = [i for i in items if (i.get("instrument") or {}).get("assetType") != "CURRENCY"]
    if len(securities) != 1:
        return None
    leg = securities[0]
    instrument = leg.get("instrument") or {}
    if instrument.get("assetType") not in {"EQUITY", "COLLECTIVE_INVESTMENT", "ETF", "MUTUAL_FUND"}:
        return None
    shares, price = _f(leg.get("amount")), _f(leg.get("price"))
    if not shares or not price or price <= 0:
        return None
    effect = leg.get("positionEffect")
    if effect and effect != ("OPENING" if shares > 0 else "CLOSING"):
        return None
    fees = 0.0
    for item in items:
        if item is leg:
            continue
        currency = (item.get("instrument") or {}).get("symbol")
        if currency not in (None, "USD"):
            return None
        if not item.get("feeType"):
            continue
        fee = _f(item.get("cost"))
        if fee is None:
            return None
        fees += abs(fee)
    if abs(net - (-shares * price - fees)) > .011:
        return None
    return dict(type="BUY" if shares > 0 else "SELL", ticker=instrument.get("symbol"),
                shares=abs(shares), price=price, fees=fees)


def _cash_income(items, description, net):
    if net <= 0 or any(item.get("feeType") for item in items):
        return None
    securities = [i for i in items if (i.get("instrument") or {}).get("assetType") != "CURRENCY"]
    if any(_f(item.get("amount")) not in (None, 0) for item in securities):
        return None
    if re.search(r"\breinvest", description, re.IGNORECASE):
        return None
    if re.search(r"\binterest\b", description, re.IGNORECASE):
        return dict(type="INTEREST", amount=net)
    if len(securities) == 1 and re.search(r"\b(dividend|div)\b", description, re.IGNORECASE):
        return dict(type="DIVIDEND", ticker=(securities[0].get("instrument") or {}).get("symbol"), amount=net)
    return None


def normalize(records, start, end):
    """Keep unsupported, unsettled or ambiguous rows visible for manual review."""
    output, skipped, seen = [], [], set()
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            skipped.append(dict(row=index, reason="Invalid transaction returned by Schwab."))
            continue
        kind = str(record.get("type") or "UNKNOWN")
        raw_day = str(record.get("tradeDate") or record.get("time") or "")[:10]
        try:
            day = date.fromisoformat(raw_day)
        except ValueError:
            skipped.append(dict(row=index, reason="Schwab transaction has no usable date."))
            continue
        # The provider may filter by posting time while a row has a different trade date.
        if not start <= day <= end:
            skipped.append(dict(row=index, reason="Trade date is outside the requested range.", date=raw_day))
            continue
        identity = record.get("activityId")
        if not isinstance(identity, (int, str)) or not str(identity):
            skipped.append(dict(row=index, reason="No stable Schwab transaction ID.", date=raw_day))
            continue
        stable_id = str(identity)
        if stable_id in seen:
            continue
        seen.add(stable_id)
        net = _f(record.get("netAmount"))
        items = record.get("transferItems") or []
        tx = None
        if (net is not None and record.get("currency") in (None, "", "USD")
                and record.get("status") in (None, "VALID", "POSTED", "SETTLED")
                and record.get("subAccount") in (None, "", "CASH", "MARGIN")
                and isinstance(items, list)
                and all(isinstance(i, dict) and isinstance(i.get("instrument") or {}, dict) for i in items)
                and all((i.get("instrument") or {}).get("assetType") != "CURRENCY"
                        or (i.get("instrument") or {}).get("symbol") in (None, "USD") for i in items)):
            if kind == "TRADE":
                tx = _trade(items, net)
            elif kind == "DIVIDEND_OR_INTEREST":
                tx = _cash_income(items, str(record.get("description") or ""), net)
            elif all((i.get("instrument") or {}).get("assetType") == "CURRENCY" and not i.get("feeType") for i in items):
                if kind in CASH_IN and net > 0:
                    tx = dict(type="DEPOSIT", amount=net)
                elif kind in CASH_OUT and net < 0:
                    tx = dict(type="WITHDRAWAL", amount=-net)
        if tx:
            ticker = str(tx.get("ticker") or "").upper().strip()
            tx.update(date=day.isoformat(), ticker=ticker or None, note="Imported from Schwab",
                      source="schwab_api", fingerprint="schwab:" + hashlib.sha256(stable_id.encode()).hexdigest())
            if (not ticker or re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker)) and not validate(tx):
                output.append(tx)
                continue
        kind_label = kind if re.fullmatch(r"[A-Z_]{1,40}", kind) else "UNKNOWN"
        skipped.append(dict(row=index, reason=f"{kind_label} requires manual review.", date=raw_day))
    return dict(transactions=sorted(output, key=lambda row: row["date"]), skipped=skipped)
