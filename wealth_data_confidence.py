"""Conservative confidence indicators for portfolio market and issuer data."""
from datetime import datetime, timezone
from wealth_security_identity import identity


def data_confidence(ticker, *, broker_cusip=None, broker_description=None,
                    price_meta=None, financials_as_of=None):
    ticker = str(ticker or "").upper()
    result = {"ticker": ticker, "identity": "unverified", "price": "missing",
              "financials": "missing", "usable_for_scoring": False, "warnings": []}
    if ticker == "SPCX":
        matched = identity(ticker, cusip=broker_cusip, description=broker_description)
        result["identity"] = "verified" if matched["verified"] else "unverified"
        if not matched["verified"]:
            result["warnings"].append(matched["reason"])
    else:
        result["warnings"].append("Issuer identity not validated by this registry")
    meta = price_meta or {}
    if meta.get("as_of"):
        try:
            stamp = datetime.fromisoformat(str(meta["as_of"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            days = (datetime.now(timezone.utc) - stamp).total_seconds() / 86400
            result["price"] = "current" if 0 <= days <= 5 else "stale"
        except (ValueError, TypeError):
            result["price"] = "invalid"
    if financials_as_of:
        try:
            stamp = datetime.fromisoformat(str(financials_as_of)[:10]).date()
            age = (datetime.now(timezone.utc).date() - stamp).days
            result["financials"] = "current" if 0 <= age <= 180 else "stale"
        except (ValueError, TypeError):
            result["financials"] = "invalid"
    if result["price"] != "current":
        result["warnings"].append("Market price missing or stale")
    if result["financials"] != "current":
        result["warnings"].append("Financial statement missing or stale")
    result["usable_for_scoring"] = all(result[k] == "verified" if k == "identity"
                                      else result[k] == "current"
                                      for k in ("identity", "price", "financials"))
    return result
