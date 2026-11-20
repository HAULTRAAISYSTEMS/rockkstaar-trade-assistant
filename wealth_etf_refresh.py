"""Read-only ETF feed refresh coordinator.

In-process TTL avoids repeated provider calls per request. This is NOT durable
storage or a background scheduler: refresh occurs on request after TTL expiry.
Never use expired holdings as verified exposure.
"""
from __future__ import annotations
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from wealth_etf_ingestion import fetch_issuer_snapshots, configured_feeds
from wealth_etf_lookthrough import age_days

_LOCK = Lock()
_CACHE = {}
_TTL_SECONDS = 3600


def refresh(tickers, *, fetch=None, now=None):
    now = monotonic() if now is None else now
    tickers = sorted(set(str(t).upper() for t in tickers))
    feeds = configured_feeds()
    results, errors, statuses = {}, {}, {}
    missing = []
    with _LOCK:
        for ticker in tickers:
            cfg = feeds.get(ticker)
            if not cfg:
                statuses[ticker] = "not_configured"
                continue
            identity = (cfg.get("url"), cfg.get("hostname"), cfg.get("identifier"), cfg.get("source"))
            entry = _CACHE.get(ticker)
            if entry and entry["identity"] == identity and now - entry["at"] < _TTL_SECONDS:
                snap = entry["snapshot"]
                if age_days(snap.get("as_of")) is not None and age_days(snap.get("as_of")) <= 35:
                    results[ticker] = snap
                    statuses[ticker] = "cached_verified_snapshot"
                    continue
            missing.append(ticker)
    if missing:
        fetched, failures = fetch_issuer_snapshots(missing, fetch=fetch)
        with _LOCK:
            for ticker, snap in fetched.items():
                cfg = feeds[ticker]
                identity = (cfg.get("url"), cfg.get("hostname"), cfg.get("identifier"), cfg.get("source"))
                _CACHE[ticker] = {"identity": identity, "at": now, "snapshot": snap}
                results[ticker] = snap
                statuses[ticker] = "refreshed_verified_snapshot"
            for ticker, error in failures.items():
                _CACHE.pop(ticker, None)
                errors[ticker] = error
                statuses[ticker] = "refresh_failed"
    return results, errors, statuses


def reset_for_tests():
    with _LOCK:
        _CACHE.clear()
