"""Ticker-specific research inputs, independent of the scanner/watchlist cache."""
from __future__ import annotations

import copy
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from threading import BoundedSemaphore, Lock

from finnhub_ttm import _finnhub_get, fetch_finnhub_metrics, fetch_finnhub_quote
from valuation import build_valuation
from filing_signals import fetch_filing_signals

log = logging.getLogger(__name__)
_CACHE = {}
_CACHE_LOCK = Lock()
_SNAPSHOT_TTL = 30
_FAILURE_TTL = 15
_FETCH_TIMEOUT = 8
# Shared, bounded capacity: slow providers cannot create a new thread pool
# on every page view or fill an unbounded executor queue.
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="company-input")
_SLOTS = BoundedSemaphore(16)


def _submit(fn):
    if not _SLOTS.acquire(blocking=False):
        return None
    try:
        future = _POOL.submit(fn)
    except Exception:
        _SLOTS.release()
        raise
    future.add_done_callback(lambda _: _SLOTS.release())
    return future


def _profile(ticker):
    from intel_engine import fetch_company_profile
    return fetch_company_profile(ticker)


def _news(ticker):
    from news_fetcher import fetch_headlines
    result = fetch_headlines(ticker)
    return list(result.articles) if result.source != "none" else []


def _earnings(ticker):
    from data_fetcher import _et_now
    today = _et_now().date()
    data = _finnhub_get("/calendar/earnings", {
        "symbol": ticker, "from": today.isoformat(),
        "to": (today + timedelta(days=120)).isoformat(),
    }) or {}
    events = []
    for row in data.get("earningsCalendar", []) if isinstance(data, dict) else []:
        if str(row.get("symbol") or "").upper() != ticker:
            continue
        day = str(row.get("date") or "")[:10]
        try:
            parsed = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            continue
        if not today <= parsed <= today + timedelta(days=120):
            continue
        events.append({"ticker": ticker, "date": day,
                       "time_label": {"bmo": "Before market open", "amc": "After market close", "dmh": "During market hours"}.get(row.get("hour"), "Time TBD"),
                       "source": "Finnhub calendar", "estimated": True})
    return sorted(events, key=lambda row: row["date"])


def fetch_company_snapshot(ticker: str, home_symbol="$", force=False) -> dict:
    ticker = ticker.upper().strip()
    key = (ticker, home_symbol)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        ttl = _FAILURE_TTL if hit and hit[1].get("unavailable") else _SNAPSHOT_TTL
        if hit and not force and time.monotonic() - hit[0] < ttl:
            return copy.deepcopy(hit[1])
    result = {"valuation": {}, "profile": {}, "news": [], "earnings": [], "unavailable": []}
    # Fetch quotes separately: a stalled metrics endpoint must not discard a
    # successful price response or make the quote wait for two HTTP timeouts.
    jobs = {"quote": lambda: fetch_finnhub_quote(ticker),
            "metrics": lambda: fetch_finnhub_metrics(ticker, force_refresh=force),
            "profile": lambda: _profile(ticker), "news": lambda: _news(ticker),
            "earnings": lambda: _earnings(ticker),
            "filings": lambda: fetch_filing_signals(ticker, force_refresh=force)}
    pending = {}
    values = {}
    for name, fn in jobs.items():
        future = _submit(fn)
        if future is None:
            result["unavailable"].append(name)
        else:
            pending[future] = name
    try:
        for future in as_completed(pending, timeout=_FETCH_TIMEOUT):
            name = pending[future]
            try:
                value = future.result()
                if value is None or (name in {"quote", "metrics", "profile"} and not value):
                    result["unavailable"].append(name)
                else:
                    values[name] = value
            except Exception as exc:
                log.debug("company research %s %s unavailable: %s", ticker, name, type(exc).__name__)
                result["unavailable"].append(name)
    except TimeoutError:
        # Include every uncollected result, even one completed at the deadline.
        result["unavailable"].extend(name for name in pending.values()
                                     if name not in values and name not in result["unavailable"])
    finally:
        for future in pending:
            future.cancel()
    metrics = values.get("metrics") or {}
    result["valuation"] = build_valuation(metrics.get("_raw_metric") or {}, values.get("quote") or {}, home_symbol)
    result["valuation"]["metrics_fetched_at"] = metrics.get("fetched_at") or ""
    for name in ("profile", "news", "earnings"):
        if name in values:
            result[name] = values[name]
    result["filing_signals"] = values.get("filings") or {"available": False, "reason": "Filing lookup is temporarily unavailable", "signals": []}
    result["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    with _CACHE_LOCK:
        if len(_CACHE) >= 64:
            _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k][0]), None)
        _CACHE[key] = (time.monotonic(), copy.deepcopy(result))
    return result
