"""Ticker-specific research inputs, independent of the scanner/watchlist cache."""
from __future__ import annotations

import copy
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from finnhub_ttm import _finnhub_get, fetch_finnhub_metrics, fetch_finnhub_quote
from valuation import build_valuation

log = logging.getLogger(__name__)
_CACHE = {}


def _market(ticker, home_symbol, force):
    metrics = fetch_finnhub_metrics(ticker, force_refresh=force) or {}
    result = build_valuation(metrics.get("_raw_metric") or {}, fetch_finnhub_quote(ticker), home_symbol)
    result["metrics_fetched_at"] = metrics.get("fetched_at") or ""
    return result


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
    hit = _CACHE.get(key)
    if hit and not force and time.monotonic() - hit[0] < 300:
        return copy.deepcopy(hit[1])
    result = {"valuation": {}, "profile": {}, "news": [], "earnings": [], "unavailable": []}
    jobs = {"valuation": lambda: _market(ticker, home_symbol, force),
            "profile": lambda: _profile(ticker), "news": lambda: _news(ticker),
            "earnings": lambda: _earnings(ticker)}
    pool = ThreadPoolExecutor(max_workers=4)
    pending = {pool.submit(fn): name for name, fn in jobs.items()}
    try:
        for future in as_completed(pending, timeout=20):
            name = pending[future]
            try:
                result[name] = future.result()
            except Exception as exc:
                log.debug("company research %s %s unavailable: %s", ticker, name, type(exc).__name__)
                result["unavailable"].append(name)
    except TimeoutError:
        result["unavailable"].extend(name for f, name in pending.items() if not f.done())
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    result["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    if len(_CACHE) >= 64:
        _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k][0]), None)
    _CACHE[key] = (time.monotonic(), copy.deepcopy(result))
    return result
