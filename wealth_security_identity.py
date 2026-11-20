"""Security identity registry: distinguish issuer equity from recycled symbols.

Source: SEC Space Exploration Technologies Corp. June 2026 free-writing
prospectus (CIK 1181412), CUSIP 84615Q103.
A matching ticker alone never proves brokerage instrument identity.
"""
from datetime import date

SPACEX = {
    "ticker": "SPCX",
    "issuer": "Space Exploration Technologies Corp.",
    "security": "Class A common stock",
    "asset_type": "equity",
    "cusip": "84615Q103",
    "cik": "0001181412",
    "first_trade_date": "2026-06-12",
    "exchange": "Nasdaq",
    "sec_filings_url": "https://www.sec.gov/edgar/browse/?CIK=1181412&owner=exclude",
    "source": "SEC Form 433 free writing prospectus, filed 2026",
}


def identity(symbol, *, cusip=None, description=None):
    symbol = str(symbol or "").strip().upper()
    if symbol != "SPCX":
        return None
    supplied = str(cusip or "").strip().upper().replace(" ", "")
    desc = str(description or "").upper()
    if supplied and supplied != SPACEX["cusip"]:
        return {"ticker": symbol, "verified": False, "reason": "Broker CUSIP conflicts with SEC identity"}
    if supplied == SPACEX["cusip"]:
        return {**SPACEX, "verified": True, "reason": "Broker CUSIP matches SEC security"}
    # Description can support identity but cannot substitute for the broker CUSIP.
    supported = "SPACE" in desc and ("EX" in desc or "EXPLORATION" in desc)
    return {**SPACEX, "verified": False,
            "reason": "Broker description consistent; CUSIP still required" if supported
                      else "Broker security identifier not supplied"}


def filter_spcx_price_history(rows, *, date_key="date"):
    """Remove reused-symbol observations before SpaceX's IPO.

    Date-key must be an ISO date/datetime string; unparseable rows are excluded.
    """
    minimum = date.fromisoformat(SPACEX["first_trade_date"])
    kept = []
    for row in rows:
        try:
            day = date.fromisoformat(str(row[date_key])[:10])
        except (KeyError, ValueError, TypeError):
            continue
        if day >= minimum:
            kept.append(row)
    return kept
