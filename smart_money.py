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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
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


def _get_json(url: str, timeout: int = 12):
    response = requests.get(url, headers=SEC_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.json()


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


def _transaction_rows(xml: bytes, ticker: str, filing_url: str, filed_at: str):
    root = ElementTree.fromstring(xml)
    owner = _text(root, "rptOwnerName", "Unknown insider")
    relationship = next(iter(root.findall(".//{*}reportingOwnerRelationship")), None)
    roles = []
    if relationship is not None:
        for flag, label in (("isDirector", "Director"), ("isOfficer", "Officer"), ("isTenPercentOwner", "10% Owner")):
            if _text(relationship, flag) in {"1", "true", "True"}:
                roles.append(label)
        title = _text(relationship, "officerTitle")
        if title:
            roles.append(title)

    rows = []
    for tx in root.findall(".//{*}nonDerivativeTransaction"):
        code = _text(tx, "transactionCode").upper()
        shares = _text(tx, "transactionShares")
        price = _text(tx, "transactionPricePerShare")
        try:
            shares_n = float(shares)
            price_n = float(price) if price else None
        except (TypeError, ValueError):
            continue
        kind = "OTHER"
        # P is an open-market purchase; S is an open-market sale. Other codes
        # remain visible but are never mislabeled as discretionary buying/selling.
        if code == "P":
            kind = "BUY"
        elif code == "S":
            kind = "SELL"
        rows.append({
            "ticker": ticker,
            "owner": owner,
            "role": ", ".join(dict.fromkeys(roles)) or "Reporting owner",
            "kind": kind,
            "code": code or "—",
            "shares": shares_n,
            "price": price_n,
            "value": shares_n * price_n if price_n is not None else None,
            "ownership_after": _float_or_none(_text(tx, "sharesOwnedFollowingTransaction")),
            "trade_date": _text(tx, "transactionDate") or filed_at,
            "filed_at": filed_at,
            "source": "SEC Form 4",
            "source_url": filing_url,
        })
    return rows


def _float_or_none(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def fetch_sec_form4(tickers, limit: int = 30) -> tuple[list[dict], dict]:
    """Return recent Form 4 transaction rows for a small ticker universe."""
    clean = list(dict.fromkeys(
        str(t).strip().upper() for t in tickers if _TICKER_RE.fullmatch(str(t).strip().upper())
    ))[:10]
    if not clean:
        return [], {"available": True, "message": "Add tickers to your active watchlist to monitor SEC Form 4 filings."}

    def load():
        cik_map = _ticker_ciks()
        def load_ticker(ticker):
            cik = cik_map.get(ticker)
            if not cik:
                return [], False
            try:
                sub = _get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
                recent = (sub.get("filings") or {}).get("recent") or {}
                forms = recent.get("form") or []
                ticker_rows = []
                filing_count = 0
                for idx, form in enumerate(forms):
                    if form not in {"4", "4/A"}:
                        continue
                    accession = recent["accessionNumber"][idx]
                    primary = recent["primaryDocument"][idx]
                    filed_at = recent["filingDate"][idx]
                    accession_plain = accession.replace("-", "")
                    cik_plain = str(int(cik))
                    filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik_plain}/{accession_plain}/{primary}"
                    response = requests.get(filing_url, headers=SEC_HEADERS, timeout=12)
                    response.raise_for_status()
                    ticker_rows.extend(_transaction_rows(response.content, ticker, filing_url, filed_at))
                    filing_count += 1
                    if filing_count >= 3:
                        break
                return ticker_rows, False
            except (requests.RequestException, KeyError, ValueError, ElementTree.ParseError):
                return [], True

        results = []
        errors = 0
        # A bounded pool keeps a watchlist refresh responsive without creating
        # an aggressive burst against SEC public-data services.
        with ThreadPoolExecutor(max_workers=min(4, len(clean))) as pool:
            futures = [pool.submit(load_ticker, ticker) for ticker in clean]
            for future in as_completed(futures):
                ticker_rows, failed = future.result()
                results.extend(ticker_rows)
                errors += int(failed)
        results.sort(key=lambda row: (row["trade_date"], row["filed_at"]), reverse=True)
        return results[:limit], errors

    try:
        rows, errors = _cached("sec:form4:" + ",".join(clean), 15 * 60, load)
        return rows, {
            "available": errors < len(clean),
            "partial": bool(errors),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "message": "SEC filings may appear after the underlying transaction and can be amended.",
        }
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
