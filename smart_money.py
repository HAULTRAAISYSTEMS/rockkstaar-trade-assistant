"""Verified smart-money data adapters for Tradestaar Elite.

SEC insider transactions are read from public Form 4 XML filings. Congressional
records are accepted only from a configured JSON feed when every record links
back to an official House or Senate disclosure page.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests


SEC_HEADERS = {
    "User-Agent": os.environ.get(
        "SEC_USER_AGENT", "Tradestaar Elite info@haultraai.com"
    ),
    "Accept-Encoding": "gzip, deflate",
}
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,7}$")
_OFFICIAL_CONGRESS_HOSTS = {
    "disclosures-clerk.house.gov",
    "efdsearch.senate.gov",
}
_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()
_sec_request_lock = threading.Lock()
_sec_last_request = 0.0
_SEC_MIN_INTERVAL = 0.12  # stay below the SEC's published 10 requests/second ceiling

# Dollar threshold for the large-sale alert. Override with INSIDER_LARGE_SALE_USD.
LARGE_SALE_THRESHOLD = int(os.environ.get("INSIDER_LARGE_SALE_USD", "1000000") or 1_000_000)

INSIDER_ALERT_RULES = {
    "executive_buy_100k": "Senior officer open-market buy over $100K",
    "cluster_buy_3": "3+ insiders buy the same ticker within 30 days",
    "holdings_increase_5": "Insider increases holdings by more than 5%",
    "holdings_sale_10": "Insider sells more than 10% of prior holdings",
    "large_sale_1m": f"Insider open-market sale over ${LARGE_SALE_THRESHOLD:,}",
    "cluster_sell_3": "3+ insiders sell the same ticker within 30 days",
}

# A rule with no stored row falls back to this. Existing users have rows for the
# original four, so only the newer rules switch themselves on.
INSIDER_ALERT_DEFAULTS = {key: True for key in INSIDER_ALERT_RULES}


def resolve_alert_rules(stored: dict | None) -> dict[str, bool]:
    """Merge a user's saved rule toggles over the defaults."""
    resolved = dict(INSIDER_ALERT_DEFAULTS)
    for key, value in (stored or {}).items():
        if key in resolved:
            resolved[key] = bool(value)
    return resolved

_FORM4_CODES = {
    "P": ("OPEN-MARKET BUY", "Insider purchased shares on the open market or privately."),
    "S": ("OPEN-MARKET SALE", "Insider sold shares on the open market or privately."),
    "A": ("STOCK AWARD", "Company grant, award, or other compensation-related acquisition."),
    "D": ("RETURN TO COMPANY", "Shares were disposed of back to the company."),
    "F": ("TAX WITHHOLDING", "Shares were withheld or delivered for taxes or an exercise price."),
    "I": ("DISCRETIONARY PLAN", "Transaction reported under a company discretionary plan."),
    "M": ("OPTION EXERCISE", "Exercise or conversion of a company-issued derivative security."),
    "C": ("DERIVATIVE CONVERSION", "A derivative security was converted."),
    "E": ("SHORT POSITION EXPIRED", "A short derivative position expired."),
    "H": ("OPTION EXPIRED", "A long derivative position expired or was cancelled for value."),
    "O": ("OPTION EXERCISE", "An out-of-the-money derivative security was exercised."),
    "X": ("OPTION EXERCISE", "An in- or at-the-money derivative security was exercised."),
    "G": ("GIFT", "A bona fide gift transferred shares to or from the insider."),
    "L": ("SMALL ACQUISITION", "A small acquisition was reported under Rule 16a-6."),
    "W": ("WILL / ESTATE TRANSFER", "Shares changed ownership by will or laws of descent."),
    "Z": ("VOTING TRUST", "Shares moved into or out of a voting trust."),
    "J": ("OTHER — SEE FOOTNOTE", "The filing footnote describes this uncommon transaction."),
    "K": ("EQUITY SWAP", "Equity swap or similar hedging transaction."),
    "U": ("TENDER OF SHARES", "Shares were tendered in a change-of-control transaction."),
    "V": ("VOLUNTARY REPORT", "The insider voluntarily reported this transaction early."),
}


def _cached(key: str, ttl: int, loader):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = loader()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def clear_sec_form4_cache() -> None:
    """Clear only cached Form 4 results; retain the daily ticker/CIK map."""
    with _cache_lock:
        for key in list(_cache):
            if key.startswith("sec:form4:"):
                _cache.pop(key, None)


def _sec_get(url: str, timeout: int = 12):
    """Make a globally paced SEC request across the bounded worker pool."""
    global _sec_last_request
    with _sec_request_lock:
        wait = _SEC_MIN_INTERVAL - (time.monotonic() - _sec_last_request)
        if wait > 0:
            time.sleep(wait)
        response = requests.get(url, headers=SEC_HEADERS, timeout=timeout)
        _sec_last_request = time.monotonic()
    return response


def _get_json(url: str, timeout: int = 12):
    response = _sec_get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _filing_urls(cik: str, accession: str, primary_document: str) -> tuple[str, str]:
    """Return raw XML and human-readable SEC filing index URLs.

    SEC submission metadata may prefix a primary document with an ``xsl.../``
    display directory. That URL returns rendered HTML, not ownership XML.
    """
    cik_plain = str(int(cik))
    accession_plain = accession.replace("-", "")
    raw_name = str(primary_document).rsplit("/", 1)[-1]
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_plain}/{accession_plain}"
    return (
        f"{base}/{raw_name}",
        f"{base}/{accession}-index.html",
    )


def _ticker_ciks() -> dict[str, str]:
    def load():
        payload = _get_json("https://www.sec.gov/files/company_tickers.json")
        return {
            str(row.get("ticker", "")).upper(): str(row.get("cik_str", "")).zfill(10)
            for row in payload.values()
            if row.get("ticker") and row.get("cik_str") is not None
        }

    return _cached("sec:ticker-ciks", 24 * 3600, load)


def _text(root, name: str, default: str = "") -> str:
    node = root.find(f".//{{*}}{name}")
    if node is None:
        return default
    value = (node.text or "").strip()
    if not value:
        nested = node.find(".//{*}value")
        value = (nested.text or "").strip() if nested is not None else ""
    return value or default


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "x"}


def _footnote_map(root) -> dict[str, str]:
    notes = {}
    for node in root.findall(".//{*}footnote"):
        note_id = str(node.attrib.get("id") or "").strip()
        if note_id:
            notes[note_id] = " ".join("".join(node.itertext()).split())
    return notes


def _footnote_ids(node) -> list[str]:
    ids = []
    for child in node.iter():
        if str(child.tag).rsplit("}", 1)[-1] != "footnoteId":
            continue
        note_id = str(child.attrib.get("id") or "").strip()
        if note_id and note_id not in ids:
            ids.append(note_id)
    return ids


def _reporting_owners(root) -> list[dict]:
    owners = []
    for reporting_owner in root.findall(".//{*}reportingOwner"):
        relationship = reporting_owner.find(".//{*}reportingOwnerRelationship")
        roles = []
        if relationship is not None:
            for flag, label in (
                ("isDirector", "Director"),
                ("isOfficer", "Officer"),
                ("isTenPercentOwner", "10% Owner"),
                ("isOther", "Other"),
            ):
                if _truthy(_text(relationship, flag)):
                    roles.append(label)
            for field in ("officerTitle", "otherText"):
                detail = _text(relationship, field)
                if detail:
                    roles.append(detail)
        owners.append({
            "name": _text(reporting_owner, "rptOwnerName", "Unknown insider"),
            "cik": _text(reporting_owner, "rptOwnerCik"),
            "role": ", ".join(dict.fromkeys(roles)) or "Reporting owner",
        })
    return owners or [{"name": "Unknown insider", "cik": "", "role": "Reporting owner"}]


def _transaction_rows(
    xml: bytes,
    ticker: str,
    filing_url: str,
    filed_at: str,
    accession: str = "",
    form_type: str = "4",
):
    """Parse Form 4 XML into the stable transaction-row contract.

    Existing keys consumed by the Terminal remain unchanged. Additional filing,
    ownership, footnote, and derivative facts are additive and power the Smart
    Money dashboard without forcing Terminal consumers to understand events.
    """
    root = ElementTree.fromstring(xml)
    owners = _reporting_owners(root)
    primary_owner = owners[0]
    notes = _footnote_map(root)
    filing_10b5_1 = _truthy(_text(root, "aff10b5One"))
    filing_key = accession or filing_url
    common = {
        "ticker": ticker,
        "owner": primary_owner["name"],
        "owner_cik": primary_owner["cik"],
        "role": primary_owner["role"],
        "reporting_owners": owners,
        "filed_at": filed_at,
        "form_type": form_type,
        "accession": accession,
        "filing_key": filing_key,
        "source": "SEC Form 4",
        "source_url": filing_url,
        "filing_10b5_1": filing_10b5_1,
    }

    rows = []
    transaction_sets = (
        (root.findall(".//{*}nonDerivativeTransaction"), False),
        (root.findall(".//{*}derivativeTransaction"), True),
    )
    for transactions, derivative in transaction_sets:
        for sequence, tx in enumerate(transactions):
            code = _text(tx, "transactionCode").upper()
            shares_n = _float_or_none(_text(tx, "transactionShares"))
            if shares_n is None:
                continue
            price_n = _float_or_none(_text(tx, "transactionPricePerShare"))
            kind = "BUY" if code == "P" else "SELL" if code == "S" else "OTHER"
            label, explanation = form4_code_details(code)
            note_ids = _footnote_ids(tx)
            footnotes = [notes[note_id] for note_id in note_ids if notes.get(note_id)]
            acquired_disposed = _text(tx, "transactionAcquiredDisposedCode").upper()
            ownership_after = _float_or_none(_text(
                tx,
                "numberOfDerivativeSecuritiesBeneficiallyOwnedFollowingTransaction"
                if derivative else "sharesOwnedFollowingTransaction",
            ))
            direct_indirect = _text(tx, "directOrIndirectOwnership").upper()
            row = dict(common)
            row.update({
                "kind": kind,
                "code": code or "—",
                "label": label,
                "explanation": explanation,
                "shares": shares_n,
                "price": price_n,
                "value": shares_n * price_n if price_n is not None else None,
                "ownership_after": ownership_after,
                "trade_date": _text(tx, "transactionDate") or filed_at,
                "security_title": _text(tx, "securityTitle", "Security not specified"),
                "acquired_disposed": acquired_disposed or "—",
                "direct_indirect": direct_indirect or "—",
                "nature_of_ownership": _text(tx, "natureOfOwnership"),
                "footnote_ids": note_ids,
                "footnotes": footnotes,
                "derivative": derivative,
                "underlying_security_title": _text(tx, "underlyingSecurityTitle"),
                "underlying_shares": _float_or_none(_text(tx, "underlyingSecurityShares")),
                "conversion_or_exercise_price": _float_or_none(_text(tx, "conversionOrExercisePrice")),
                "equity_swap": _truthy(_text(tx, "equitySwapInvolved")),
                "transaction_timeliness": _text(tx, "transactionTimeliness"),
                "sequence": sequence,
                "transaction_10b5_1": bool(
                    filing_10b5_1 and any(re.search(r"10b5\s*[-–]?\s*1", note, re.I) for note in footnotes)
                ),
            })
            row["holdings_change_pct"] = _transaction_holdings_change_pct(row)
            rows.append(row)

    market_rows = [row for row in rows if row["code"] in {"P", "S"}]
    if filing_10b5_1 and len(market_rows) == 1:
        market_rows[0]["transaction_10b5_1"] = True
    return rows


def _float_or_none(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _transaction_holdings_change_pct(row) -> float | None:
    after = _float_or_none(row.get("ownership_after"))
    shares = _float_or_none(row.get("shares"))
    direction = str(row.get("acquired_disposed") or "").upper()
    if after is None or shares is None or direction not in {"A", "D"}:
        return None
    before = after - shares if direction == "A" else after + shares
    if before <= 0:
        return None
    signed = shares if direction == "A" else -shares
    return (signed / before) * 100


def form4_code_details(code: str) -> tuple[str, str]:
    """Return a plain-English SEC transaction label and restrained explanation."""
    normalized = str(code or "").strip().upper()
    return _FORM4_CODES.get(
        normalized,
        (f"CODE {normalized}" if normalized else "UNSPECIFIED", "Open the official filing for transaction details."),
    )


def _parse_date(value) -> date | None:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _sum_values(rows) -> tuple[float, int, int]:
    values = [row.get("value") for row in rows if isinstance(row.get("value"), (int, float))]
    return sum(values), len(values), len(rows)


def _weighted_price(rows) -> float | None:
    priced = [row for row in rows if isinstance(row.get("price"), (int, float)) and row.get("shares", 0) > 0]
    shares = sum(row["shares"] for row in priced)
    return sum(row["shares"] * row["price"] for row in priced) / shares if shares else None


def _event_holdings_change(rows) -> tuple[float | None, float | None, float | None]:
    market_rows = [row for row in rows if row.get("code") in {"P", "S"} and not row.get("derivative")]
    buckets = {
        (
            row.get("security_title"),
            row.get("direct_indirect"),
            row.get("nature_of_ownership"),
        )
        for row in market_rows
    }
    if not market_rows or len(buckets) != 1 or any(row.get("ownership_after") is None for row in market_rows):
        return None, None, None
    final_after = _float_or_none(market_rows[-1].get("ownership_after"))
    net_change = sum(
        row["shares"] if row.get("acquired_disposed") == "A" else -row["shares"]
        if row.get("acquired_disposed") == "D" else 0
        for row in market_rows
    )
    if final_after is None:
        return None, None, None
    before = final_after - net_change
    if before <= 0:
        return before, final_after, None
    return before, final_after, (net_change / before) * 100


def aggregate_form4_events(rows: list[dict]) -> list[dict]:
    """Group transaction rows by ticker, reporting owner, and SEC filing."""
    grouped = defaultdict(list)
    for row in rows or []:
        owner_key = row.get("owner_cik") or str(row.get("owner") or "").upper()
        grouped[(row.get("ticker"), owner_key, row.get("filing_key") or row.get("source_url"))].append(row)

    events = []
    for (_, _, _), transactions in grouped.items():
        transactions.sort(key=lambda row: (row.get("trade_date") or "", row.get("sequence", 0), row.get("derivative", False)))
        first = transactions[0]
        buys = [row for row in transactions if row.get("code") == "P"]
        sells = [row for row in transactions if row.get("code") == "S"]
        non_market = [row for row in transactions if row.get("code") not in {"P", "S"}]
        buy_value, buy_priced, buy_count = _sum_values(buys)
        sell_value, sell_priced, sell_count = _sum_values(sells)
        non_market_value, non_market_priced, non_market_count = _sum_values(non_market)
        non_market_acquired_shares = sum(
            row.get("shares", 0) for row in non_market if row.get("acquired_disposed") == "A"
        )
        non_market_disposed_shares = sum(
            row.get("shares", 0) for row in non_market if row.get("acquired_disposed") == "D"
        )
        before, after, change_pct = _event_holdings_change(transactions)
        ownership_row = next(
            (row for row in transactions if row.get("code") in {"P", "S"} and not row.get("derivative") and row.get("direct_indirect") not in {None, "", "—"}),
            next((row for row in transactions if row.get("direct_indirect") not in {None, "", "—"}), {}),
        )
        codes = list(dict.fromkeys(str(row.get("code") or "—") for row in transactions))
        if buys and sells:
            activity = "MIXED"
        elif buys:
            activity = "BUY"
        elif sells:
            activity = "SELL"
        else:
            activity = "NON_MARKET"
        event = {
            "event_id": re.sub(r"[^A-Za-z0-9_-]", "-", str(first.get("filing_key") or first.get("source_url") or len(events))),
            "ticker": first.get("ticker"),
            "owner": first.get("owner"),
            "owner_cik": first.get("owner_cik"),
            "reporting_owners": first.get("reporting_owners") or [],
            "role": first.get("role"),
            "trade_date": max((row.get("trade_date") or "" for row in transactions), default=""),
            "filed_at": first.get("filed_at"),
            "form_type": first.get("form_type") or "4",
            "accession": first.get("accession"),
            "source_url": first.get("source_url"),
            "activity": activity,
            "codes": codes,
            "transactions": transactions,
            "transaction_count": len(transactions),
            "buy_shares": sum(row.get("shares", 0) for row in buys),
            "sell_shares": sum(row.get("shares", 0) for row in sells),
            "buy_value": buy_value if buy_priced else None,
            "sell_value": sell_value if sell_priced else None,
            "buy_value_complete": buy_priced == buy_count,
            "sell_value_complete": sell_priced == sell_count,
            "buy_average_price": _weighted_price(buys),
            "sell_average_price": _weighted_price(sells),
            "non_market_count": non_market_count,
            "non_market_acquired_shares": non_market_acquired_shares,
            "non_market_disposed_shares": non_market_disposed_shares,
            "non_market_reported_value": non_market_value if non_market_priced else None,
            "non_market_priced_count": non_market_priced,
            "non_market_value_complete": non_market_priced == non_market_count,
            "holdings_before": before,
            "holdings_after": after,
            "holdings_change_pct": change_pct,
            "direct_indirect": ownership_row.get("direct_indirect") or "—",
            "nature_of_ownership": ownership_row.get("nature_of_ownership") or "",
            "filing_10b5_1": any(row.get("filing_10b5_1") for row in transactions),
            "planned_10b5_1": any(row.get("transaction_10b5_1") for row in transactions),
            "exercise_linked_sale": bool(sells and any(row.get("code") in {"M", "C", "O", "X"} for row in transactions)),
        }
        events.append(event)
    events.sort(key=lambda event: (event.get("trade_date") or "", event.get("filed_at") or ""), reverse=True)
    return events


def _is_executive(role: str) -> bool:
    """Senior officers whose trades carry the most signal.

    Originally CEO/CFO only, which under-weighted COO/President/Chairman exits.
    """
    normalized = re.sub(r"[^A-Z]", "", str(role or "").upper())
    return any(token in normalized for token in (
        "CEO", "CFO", "COO", "CHIEFEXECUTIVE", "CHIEFFINANCIAL", "CHIEFOPERATING",
        "PRESIDENT", "CHAIRMAN", "CHAIRPERSON",
    ))


def _is_major_holder(role: str) -> bool:
    """A 10% owner is often the largest holder in the filing."""
    normalized = re.sub(r"[^A-Z0-9]", "", str(role or "").upper())
    return "10OWNER" in normalized or "TENPERCENT" in normalized or "10PERCENT" in normalized


def _score_event(event: dict, all_events: list[dict]) -> dict:
    score = 0
    reasons = []
    has_buy = event["activity"] in {"BUY", "MIXED"}
    has_sell = event["activity"] in {"SELL", "MIXED"}
    role = event.get("role") or ""
    owner_key = event.get("owner_cik") or str(event.get("owner") or "").upper()
    ticker = event.get("ticker")

    buyers = {
        other.get("owner_cik") or str(other.get("owner") or "").upper()
        for other in all_events
        if other.get("ticker") == ticker and other.get("activity") in {"BUY", "MIXED"}
    }
    repeat_buys = sum(
        1 for other in all_events
        if other.get("ticker") == ticker
        and (other.get("owner_cik") or str(other.get("owner") or "").upper()) == owner_key
        and other.get("activity") in {"BUY", "MIXED"}
    )
    sellers = {
        other.get("owner_cik") or str(other.get("owner") or "").upper()
        for other in all_events
        if other.get("ticker") == ticker and other.get("activity") in {"SELL", "MIXED"}
    }
    repeat_sells = sum(
        1 for other in all_events
        if other.get("ticker") == ticker
        and (other.get("owner_cik") or str(other.get("owner") or "").upper()) == owner_key
        and other.get("activity") in {"SELL", "MIXED"}
    )
    event["cluster_buyers"] = len(buyers)
    event["repeat_purchase_count"] = repeat_buys
    event["cluster_sellers"] = len(sellers)
    event["repeat_sale_count"] = repeat_sells

    if has_buy:
        score += 35
        reasons.append("+35 verified Code P open-market/private purchase")
        if _is_executive(role):
            score += 15
            reasons.append("+15 CEO/CFO open-market purchase")
        elif "DIRECTOR" in role.upper():
            score += 8
            reasons.append("+8 director open-market purchase")
        buy_value = event.get("buy_value") or 0
        value_points = 15 if buy_value >= 1_000_000 else 10 if buy_value >= 500_000 else 5 if buy_value >= 100_000 else 0
        if value_points:
            score += value_points
            reasons.append(f"+{value_points} reported purchase value is ${buy_value:,.0f}")
        change_pct = event.get("holdings_change_pct")
        holding_points = 20 if change_pct is not None and change_pct > 20 else 10 if change_pct is not None and change_pct > 5 else 0
        if holding_points:
            score += holding_points
            reasons.append(f"+{holding_points} holdings increased {change_pct:.1f}%")
        if repeat_buys > 1:
            score += 10
            reasons.append(f"+10 repeat buyer: {repeat_buys} purchase filings in 30 days")
        if len(buyers) >= 3:
            score += 20
            reasons.append(f"+20 cluster buying: {len(buyers)} insiders in 30 days")
        elif len(buyers) == 2:
            score += 10
            reasons.append("+10 two insiders bought this ticker in 30 days")

    if has_sell:
        sale_points = -8
        change_pct = event.get("holdings_change_pct")
        sold_pct = abs(change_pct) if change_pct is not None and change_pct < 0 else None
        if sold_pct is not None and sold_pct < 2:
            sale_points = -3
            reasons.append("−3 sale was less than 2% of calculated prior holdings")
        else:
            reasons.append("−8 verified Code S open-market/private sale")
        if sold_pct is not None:
            size_points = -30 if sold_pct > 50 else -18 if sold_pct > 25 else -8 if sold_pct > 10 else 0
            if size_points:
                sale_points += size_points
                reasons.append(f"{size_points} sale represented {sold_pct:.1f}% of calculated prior holdings")
        # Seniority. Mirrors the buy side, which already weights CEO/CFO and
        # directors; without this a CEO exit scored the same as a junior VP's.
        if _is_executive(role):
            sale_points -= 15
            reasons.append("-15 senior officer open-market sale")
        elif _is_major_holder(role):
            sale_points -= 12
            reasons.append("-12 10% owner open-market sale")
        elif "DIRECTOR" in role.upper():
            sale_points -= 8
            reasons.append("-8 director open-market sale")
        # Dollar size. sell_value was already computed on every event but never
        # scored, so a $50M sale tied with a $180K one.
        sell_value = event.get("sell_value") or 0
        sale_value_points = -15 if sell_value >= 1_000_000 else -10 if sell_value >= 500_000 else -5 if sell_value >= 100_000 else 0
        if sale_value_points:
            sale_points += sale_value_points
            reasons.append(f"{sale_value_points} reported sale value is ${sell_value:,.0f}")
        if repeat_sells > 1:
            sale_points -= 10
            reasons.append(f"-10 repeat seller: {repeat_sells} sale filings in 30 days")
        if len(sellers) >= 3:
            sale_points -= 20
            reasons.append(f"-20 cluster selling: {len(sellers)} insiders in 30 days")
        elif len(sellers) == 2:
            sale_points -= 10
            reasons.append("-10 two insiders sold this ticker in 30 days")
        if event.get("planned_10b5_1"):
            original = sale_points
            sale_points = int(round(sale_points * 0.35))
            reasons.append(f"10b5-1 filing evidence reduces sale weight from {original} to {sale_points}")
        elif event.get("exercise_linked_sale"):
            original = sale_points
            sale_points = int(round(sale_points * 0.5))
            reasons.append(f"same-filing option exercise reduces sale weight from {original} to {sale_points}")
        score += sale_points

    if not has_buy and not has_sell:
        reasons.append("0 non-market codes are not treated as voluntary purchases or sales")

    label = "Strong Bullish" if score >= 60 else "Bullish" if score >= 25 else "Neutral" if score > -25 else "Bearish" if score > -60 else "Strong Bearish"
    return {"score": score, "label": label, "reasons": reasons}


def _why_this_matters(event: dict) -> str:
    owner = event.get("owner") or "The reporting insider"
    role = event.get("role") or "Reporting owner"
    facts = []
    if event["activity"] in {"BUY", "MIXED"}:
        fact = f"reported a Code P purchase of {event['buy_shares']:,.0f} shares"
        if event.get("buy_value") is not None:
            fact += f" valued at ${event['buy_value']:,.0f}"
        facts.append(fact)
    if event["activity"] in {"SELL", "MIXED"}:
        fact = f"reported a Code S sale of {event['sell_shares']:,.0f} shares"
        if event.get("sell_value") is not None:
            fact += f" valued at ${event['sell_value']:,.0f}"
        facts.append(fact)
    if not facts:
        labels = list(dict.fromkeys(row.get("label") for row in event["transactions"] if row.get("label")))
        facts.append("reported " + ", ".join(labels).lower())
    statement = f"{owner} ({role}) " + " and ".join(facts) + "."
    change_pct = event.get("holdings_change_pct")
    if change_pct is not None:
        direction = "increased" if change_pct > 0 else "decreased"
        statement += f" Calculated comparable holdings {direction} by {abs(change_pct):.1f}%."
    if event.get("planned_10b5_1"):
        statement += " The filing links the reported market transaction to a Rule 10b5-1 plan."
    elif event.get("filing_10b5_1"):
        statement += " The filing contains a Rule 10b5-1 indicator, but transaction-level allocation is not verified."
    if event["activity"] == "NON_MARKET":
        statement += " These codes are not comparable to a voluntary open-market purchase or sale."
    elif event["activity"] in {"SELL", "MIXED"}:
        statement += " A reported sale alone does not establish a bearish outlook for the stock."
    return statement


def _summary(events: list[dict], days: int, today: date) -> dict:
    cutoff = today - timedelta(days=days - 1)
    included = [event for event in events if (_parse_date(event.get("trade_date")) or date.min) >= cutoff]
    buys = [event for event in included if event["activity"] in {"BUY", "MIXED"}]
    sells = [event for event in included if event["activity"] in {"SELL", "MIXED"}]
    buy_value = sum(event.get("buy_value") or 0 for event in buys)
    sell_value = sum(event.get("sell_value") or 0 for event in sells)
    cluster_tickers = sorted({event["ticker"] for event in buys if event.get("cluster_buyers", 0) >= 3})
    return {
        "days": days,
        "buy_events": len(buys),
        "sale_events": len(sells),
        "buy_value": buy_value,
        "sale_value": sell_value,
        "net_value": buy_value - sell_value,
        "cluster_buys": len(cluster_tickers),
        "cluster_tickers": cluster_tickers,
        "largest_buy": max(buys, key=lambda event: event.get("buy_value") or -1, default=None),
        "largest_sale": max(sells, key=lambda event: event.get("sell_value") or -1, default=None),
        "value_complete": all(event.get("buy_value_complete", True) and event.get("sell_value_complete", True) for event in included),
    }


def match_alert_rules(event: dict, enabled_rules: dict | None) -> list[str]:
    enabled = enabled_rules or {}
    matches = []
    change_pct = event.get("holdings_change_pct")
    checks = {
        "executive_buy_100k": event["activity"] in {"BUY", "MIXED"} and _is_executive(event.get("role")) and (event.get("buy_value") or 0) >= 100_000,
        "cluster_buy_3": event["activity"] in {"BUY", "MIXED"} and event.get("cluster_buyers", 0) >= 3,
        "holdings_increase_5": change_pct is not None and change_pct > 5,
        "holdings_sale_10": change_pct is not None and change_pct < -10,
        "large_sale_1m": event["activity"] in {"SELL", "MIXED"} and (event.get("sell_value") or 0) >= LARGE_SALE_THRESHOLD,
        "cluster_sell_3": event["activity"] in {"SELL", "MIXED"} and event.get("cluster_sellers", 0) >= 3,
    }
    for key, matched in checks.items():
        if enabled.get(key) and matched:
            matches.append(INSIDER_ALERT_RULES[key])
    return matches


def build_insider_dashboard(rows: list[dict], filters: dict | None = None, alert_rules: dict | None = None, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    events = aggregate_form4_events(rows)
    cutoff_30 = today - timedelta(days=29)
    context_events = [event for event in events if (_parse_date(event.get("trade_date")) or date.min) >= cutoff_30]
    for event in events:
        event["signal"] = _score_event(event, context_events)
        event["why_this_matters"] = _why_this_matters(event)
        event["alert_matches"] = match_alert_rules(event, alert_rules)

    filters = filters or {}
    filtered = list(events)
    ticker = str(filters.get("ticker") or "").strip().upper()
    role = str(filters.get("role") or "").strip().lower()
    transaction_type = str(filters.get("transaction_type") or "all").strip()
    transaction_mode = transaction_type.lower()
    minimum_value = max(0.0, _float_or_none(filters.get("minimum_value")) or 0.0)
    try:
        days = int(filters.get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    days = 7 if days == 7 else 30
    cutoff = today - timedelta(days=days - 1)
    if ticker:
        filtered = [event for event in filtered if event.get("ticker") == ticker]
    if role:
        filtered = [event for event in filtered if role in str(event.get("role") or "").lower()]
    if transaction_mode == "buy":
        filtered = [event for event in filtered if event["activity"] in {"BUY", "MIXED"}]
    elif transaction_mode == "sell":
        filtered = [event for event in filtered if event["activity"] in {"SELL", "MIXED"}]
    elif transaction_mode == "non_market":
        filtered = [event for event in filtered if event["activity"] == "NON_MARKET"]
    elif transaction_type.upper() in {"F", "G", "A", "M", "P", "S"}:
        code = transaction_type.upper()
        filtered = [event for event in filtered if code in event["codes"]]
    if minimum_value:
        filtered = [event for event in filtered if max(event.get("buy_value") or 0, event.get("sell_value") or 0) >= minimum_value]
    if _truthy(filters.get("cluster")):
        filtered = [event for event in filtered if event.get("cluster_buyers", 0) >= 3]
    filtered = [event for event in filtered if (_parse_date(event.get("trade_date")) or date.min) >= cutoff]

    roles = sorted({event.get("role") for event in events if event.get("role")})
    tickers = sorted({event.get("ticker") for event in events if event.get("ticker")})
    return {
        "events": filtered,
        "all_events": events,
        "summary_7": _summary(events, 7, today),
        "summary_30": _summary(events, 30, today),
        "roles": roles,
        "tickers": tickers,
        "filters": {**filters, "minimum_value": minimum_value, "days": days},
        "alert_rules": {key: bool((alert_rules or {}).get(key)) for key in INSIDER_ALERT_RULES},
        "alert_rule_labels": INSIDER_ALERT_RULES,
    }


def fetch_sec_form4(tickers, limit: int = 30, history_days: int | None = None) -> tuple[list[dict], dict]:
    """Return recent Form 4 transaction rows for a small ticker universe.

    ``history_days`` is used by the dashboard for honest 7/30-day summaries.
    Omitting it preserves the Terminal consumer's existing three-filing fetch.
    """
    clean = list(dict.fromkeys(
        str(t).strip().upper() for t in tickers if _TICKER_RE.fullmatch(str(t).strip().upper())
    ))[:10]
    if not clean:
        return [], {"available": True, "message": "Add tickers to any watchlist to monitor SEC Form 4 filings."}

    def load():
        cik_map = _ticker_ciks()
        def load_ticker(ticker):
            cik = cik_map.get(ticker)
            if not cik:
                return [], False, False
            try:
                sub = _get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
                recent = (sub.get("filings") or {}).get("recent") or {}
                forms = recent.get("form") or []
                ticker_rows = []
                filing_count = 0
                truncated = False
                cutoff = datetime.now(timezone.utc).date() - timedelta(days=max(1, int(history_days or 1)) - 1)
                max_filings = 20 if history_days else 3
                for idx, form in enumerate(forms):
                    if form not in {"4", "4/A"}:
                        continue
                    filed_at = recent["filingDate"][idx]
                    filed_date = _parse_date(filed_at)
                    if history_days and filed_date and filed_date < cutoff:
                        break
                    if filing_count >= max_filings:
                        truncated = True
                        break
                    accession = recent["accessionNumber"][idx]
                    primary = recent["primaryDocument"][idx]
                    raw_url, filing_url = _filing_urls(cik, accession, primary)
                    response = _sec_get(raw_url, timeout=12)
                    response.raise_for_status()
                    ticker_rows.extend(_transaction_rows(
                        response.content,
                        ticker,
                        filing_url,
                        filed_at,
                        accession=accession,
                        form_type=form,
                    ))
                    filing_count += 1
                return ticker_rows, False, truncated
            except (requests.RequestException, KeyError, ValueError, ElementTree.ParseError):
                return [], True, False

        results = []
        errors = 0
        truncated_tickers = 0
        # A bounded pool keeps a watchlist refresh responsive without creating
        # an aggressive burst against SEC public-data services.
        with ThreadPoolExecutor(max_workers=min(4, len(clean))) as pool:
            futures = [pool.submit(load_ticker, ticker) for ticker in clean]
            for future in as_completed(futures):
                ticker_rows, failed, truncated = future.result()
                results.extend(ticker_rows)
                errors += int(failed)
                truncated_tickers += int(truncated)
        results.sort(key=lambda row: (row["trade_date"], row["filed_at"]), reverse=True)
        return results, errors, truncated_tickers

    try:
        cache_key = "sec:form4:" + ",".join(clean) + f":days={history_days or 'recent'}"
        rows, errors, truncated_tickers = _cached(cache_key, 15 * 60, load)
        available = errors < len(clean)
        partial = 0 < errors < len(clean)
        if not available:
            message = "The SEC filing service could not refresh this watchlist. Try again shortly."
        elif not rows:
            message = "No qualifying transactions were found in the latest verified Form 4 filings."
        else:
            message = "SEC filings may appear after the underlying transaction and can be amended."
        status = {
            "available": available,
            "partial": partial,
            "checked_tickers": len(clean),
            "failed_tickers": errors,
            "history_days": history_days,
            "truncated_tickers": truncated_tickers,
            "coverage_complete": not truncated_tickers,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "message": message,
        }
        return (rows if limit is None else rows[:limit]), status
    except requests.RequestException:
        return [], {"available": False, "message": "The SEC filing service is temporarily unavailable."}


def _official_congress_url(url: str) -> bool:
    parsed = urlparse(str(url))
    return parsed.scheme == "https" and parsed.hostname in _OFFICIAL_CONGRESS_HOSTS


def fetch_congress_trades(limit: int = 30) -> tuple[list[dict], dict]:
    """Load provider-normalized disclosures, requiring an official filing link.

    CONGRESS_TRADES_JSON_URL is intentionally optional because neither chamber
    currently exposes a stable, unified transaction JSON API. A deployment can
    connect a licensed/public provider, but records without an official filing
    URL are rejected.
    """
    url = os.environ.get("CONGRESS_TRADES_JSON_URL", "").strip()
    if not url:
        return [], {
            "available": False,
            "message": "A verified congressional disclosure feed is not connected yet.",
            "house_url": "https://disclosures-clerk.house.gov/FinancialDisclosure",
            "senate_url": "https://efdsearch.senate.gov/search/",
        }

    def load():
        response = requests.get(url, timeout=12)
        response.raise_for_status()
        payload = response.json()
        source_rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        rows = []
        for raw in source_rows if isinstance(source_rows, list) else []:
            source_url = str(raw.get("source_url") or raw.get("disclosure_url") or "")
            ticker = str(raw.get("ticker") or "").upper()
            if not _official_congress_url(source_url) or not _TICKER_RE.fullmatch(ticker):
                continue
            rows.append({
                "ticker": ticker,
                "member": str(raw.get("member") or raw.get("representative") or "Member of Congress"),
                "chamber": str(raw.get("chamber") or "Congress").upper(),
                "kind": str(raw.get("type") or raw.get("transaction_type") or "DISCLOSED").upper(),
                "amount": str(raw.get("amount") or raw.get("amount_range") or "Not specified"),
                "trade_date": str(raw.get("trade_date") or raw.get("transaction_date") or ""),
                "filed_at": str(raw.get("filed_at") or raw.get("disclosure_date") or ""),
                "source_url": source_url,
            })
        rows.sort(key=lambda row: (row["trade_date"], row["filed_at"]), reverse=True)
        return rows[:limit]

    try:
        rows = _cached("congress:" + url, 30 * 60, load)
        return rows, {
            "available": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "message": "Transaction dates and disclosure dates differ; amount values may be statutory ranges.",
        }
    except (requests.RequestException, ValueError):
        return [], {"available": False, "message": "The congressional disclosure feed is temporarily unavailable."}
