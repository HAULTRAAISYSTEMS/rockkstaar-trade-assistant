"""Canonical SEC financial snapshot with bounded shared cache and provenance."""
from datetime import datetime, timezone
from threading import Lock
import time
from wealth_sec_evidence import SEC_CIK, fetch_sec_evidence
from wealth_sec_dashboard import build_dashboard
from wealth_sec_explanations import compare_periods, explain_changes

_CACHE = {}
_LOCK = Lock()
_TTL_SECONDS = 300


def financial_snapshot(ticker, *, fetcher=None, now=None):
    symbol = str(ticker or "").strip().upper()
    if symbol not in SEC_CIK:
        return {"ok": False, "ticker": symbol, "status": "unsupported",
                "error": "No verified SEC issuer mapping; figures withheld",
                "scoring_eligible": False}
    provider = fetcher or fetch_sec_evidence
    clock = time.monotonic() if now is None else now
    cacheable = fetcher is None
    if cacheable:
        with _LOCK:
            cached = _CACHE.get(symbol)
            if cached and clock - cached[0] < _TTL_SECONDS:
                return dict(cached[1], cache_status="hit")
    evidence = provider(symbol)
    result = {"ok": True, "ticker": symbol, "cik": evidence.get("cik"),
              "status": evidence.get("status"),
              "dashboard": build_dashboard(evidence),
              "analysis": explain_changes(compare_periods(evidence.get("facts"))),
              "scoring_eligible": False,
              "source": "SEC EDGAR companyfacts and submissions",
              "warnings": evidence.get("warnings") or [],
              "retrieved_at": datetime.now(timezone.utc).isoformat(),
              "cache_status": "miss"}
    if cacheable and evidence.get("status") == "available":
        with _LOCK:
            _CACHE[symbol] = (clock, result)
    return result


def clear_cache():
    with _LOCK:
        _CACHE.clear()
