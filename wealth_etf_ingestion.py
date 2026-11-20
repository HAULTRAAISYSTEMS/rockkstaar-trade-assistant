"""Trusted ETF issuer export ingestion and overlap calculations.

The allowlist is explicitly configured by the operator. No guessed URLs or
symbol-only identity matching. Network access is limited to HTTPS allowlisted hosts.
"""
from __future__ import annotations
import json
import os
from urllib.parse import urlparse
import requests
from wealth_etf_lookthrough import validated_snapshot, lookthrough


def configured_feeds():
    try:
        feeds = json.loads(os.environ.get("TRADESTAAR_ETF_ISSUER_FEEDS_JSON") or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(feeds, dict):
        return {}
    return {str(k).upper(): v for k, v in feeds.items() if isinstance(v, dict)}


def fetch_issuer_snapshots(tickers, *, fetch=None):
    """Read-only provider fetch, no persistence. Supply URL and allowed hostname.

    Required config per fund: url, hostname, source, identifier.
    Provider must return JSON with as_of and constituents. Identity is
    checked against configured identifier in the provider response.
    """
    fetch = fetch or requests.get
    result, errors = {}, {}
    for ticker in tickers:
        cfg = configured_feeds().get(ticker)
        if not cfg:
            continue
        url = str(cfg.get("url") or "")
        host = str(cfg.get("hostname") or "").lower()
        identifier = str(cfg.get("identifier") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or not host or parsed.hostname != host or not identifier:
            errors[ticker] = "Feed configuration invalid"
            continue
        try:
            response = fetch(url, timeout=8, allow_redirects=False)
            response.raise_for_status()
            if len(response.content) > 2000000:
                raise ValueError("Issuer response too large")
            data = response.json()
            if not isinstance(data, dict) or str(data.get("identifier") or "") != identifier:
                raise ValueError("Issuer security identifier mismatch")
            raw = {**data, "source": str(cfg.get("source") or "")}
            snap = validated_snapshot(ticker, raw)
            if not snap:
                raise ValueError("Issuer holdings incomplete, stale or invalid")
            snap["identifier"] = identifier
            result[ticker] = snap
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            errors[ticker] = type(exc).__name__
    return result, errors


def company_overlap(state, snapshots):
    """ETF constituent exposure plus direct positions, with missing data explicit."""
    holdings = state.get("holdings") or {}
    total = float(state.get("total_value") or 0)
    combined = {}
    for ticker, value in holdings.items():
        if ticker not in snapshots:
            combined[ticker] = combined.get(ticker, 0) + value
        else:
            for item in snapshots[ticker]["constituents"]:
                key = item["ticker"]
                combined[key] = combined.get(key, 0) + value * item["weight_pct"] / 100
    result = []
    for ticker, value in combined.items():
        direct = float(holdings.get(ticker) or 0)
        indirect = value - direct
        if direct > 0 and indirect > 0:
            result.append({"ticker": ticker, "direct_value": round(direct, 2),
                           "indirect_value": round(indirect, 2),
                           "combined_weight_pct": round(value * 100 / total, 2) if total else None})
    return sorted(result, key=lambda x: -x["indirect_value"])
