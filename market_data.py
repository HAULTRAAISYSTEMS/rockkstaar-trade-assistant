"""Source-labelled market bars for the Tradestaar Terminal.

Alpaca is the preferred provider because it exposes an explicit exchange feed.
The caller always receives provenance metadata; a fallback is never described
as consolidated real-time data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Any
from zoneinfo import ZoneInfo

import requests


_BASE_URL = "https://data.alpaca.markets/v2/stocks"
_TIMEFRAMES = {
    "1m": "1Min",
    "5m": "5Min",
    "15m": "15Min",
    "1h": "1Hour",
    "1d": "1Day",
}
_RANGE_DAYS = {
    "1d": 2,
    "5d": 7,
    "1mo": 32,
    "3mo": 95,
    "6mo": 190,
    "1y": 370,
    "2y": 740,
    "5y": 1830,
    "10y": 3660,
}
_FEED_META = {
    "sip": {
        "label": "SIP LIVE",
        "coverage": "All US exchanges",
        "delay_seconds": 0,
        "realtime": True,
        "comprehensive": True,
    },
    "delayed_sip": {
        "label": "SIP DELAYED",
        "coverage": "All US exchanges",
        "delay_seconds": 900,
        "realtime": False,
        "comprehensive": True,
    },
    "iex": {
        "label": "IEX LIVE",
        "coverage": "IEX exchange only",
        "delay_seconds": 0,
        "realtime": True,
        "comprehensive": False,
    },
}


def _credentials() -> tuple[str, str]:
    return (
        (os.environ.get("ALPACA_API_KEY") or os.environ.get("APCA_API_KEY_ID") or "").strip(),
        (os.environ.get("ALPACA_SECRET_KEY") or os.environ.get("APCA_API_SECRET_KEY") or "").strip(),
    )


def _feed_candidates() -> list[str]:
    configured = os.environ.get("ALPACA_MARKET_DATA_FEED", "auto").strip().lower()
    if configured in _FEED_META:
        return [configured]
    # Prefer the complete consolidated tape. Basic accounts are rejected for
    # recent SIP data, then fall back to the complete 15-minute delayed tape.
    return ["sip", "delayed_sip"]


def _regular_session(timestamp: int) -> bool:
    local = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(
        ZoneInfo("America/New_York")
    )
    minutes = local.hour * 60 + local.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


def _parse_bars(rows: list[dict[str, Any]], include_extended: bool, interval: str) -> dict | None:
    parsed = []
    for row in rows:
        try:
            timestamp = int(datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")).timestamp())
            values = (
                float(row["o"]), float(row["c"]), float(row["h"]), float(row["l"]),
                int(row.get("v") or 0),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if min(values[:4]) <= 0:
            continue
        if interval != "1d" and not include_extended and not _regular_session(timestamp):
            continue
        parsed.append((timestamp, *values))
    if not parsed:
        return None
    parsed.sort(key=lambda item: item[0])
    deduped = {row[0]: row for row in parsed}
    ordered = [deduped[key] for key in sorted(deduped)]
    return {
        "timestamps": [row[0] for row in ordered],
        "opens": [row[1] for row in ordered],
        "closes": [row[2] for row in ordered],
        "highs": [row[3] for row in ordered],
        "lows": [row[4] for row in ordered],
        "volumes": [row[5] for row in ordered],
        "data_granularity": interval,
        "exchange_timezone": "America/New_York",
        "currency": "USD",
    }


def _alpaca_bars(ticker: str, interval: str, range_str: str,
                 include_extended: bool) -> tuple[dict | None, dict | None]:
    key, secret = _credentials()
    timeframe = _TIMEFRAMES.get(interval)
    days = _RANGE_DAYS.get(range_str)
    if not key or not secret or not timeframe or not days:
        return None, None
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    for feed in _feed_candidates():
        params = {
            "timeframe": timeframe,
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "limit": 10000,
            "adjustment": "split",
            "feed": feed,
            "sort": "asc",
        }
        try:
            response = requests.get(
                f"{_BASE_URL}/{ticker}/bars", params=params, headers=headers, timeout=10
            )
            if response.status_code in {401, 403, 422}:
                continue
            response.raise_for_status()
            data = _parse_bars(response.json().get("bars") or [], include_extended, interval)
            if not data:
                continue
            feed_meta = _FEED_META[feed]
            meta = {
                "provider": "Alpaca",
                "feed": feed,
                "feed_label": feed_meta["label"],
                "coverage": feed_meta["coverage"],
                "delay_seconds": feed_meta["delay_seconds"],
                "realtime": feed_meta["realtime"],
                "comprehensive": feed_meta["comprehensive"],
                "official": True,
                "fallback": False,
                "as_of": datetime.fromtimestamp(data["timestamps"][-1], timezone.utc).isoformat(),
                "message": (
                    "Consolidated real-time US market data."
                    if feed == "sip"
                    else "Consolidated US market data delayed by approximately 15 minutes."
                    if feed == "delayed_sip"
                    else "Real-time IEX data; prices and volume may differ from the consolidated market."
                ),
            }
            return data, meta
        except (requests.RequestException, ValueError, TypeError):
            continue
    return None, None


def fetch_chart_bars(ticker: str, interval: str, range_str: str,
                     include_extended: bool = False) -> tuple[dict | None, dict]:
    """Return chart bars and a truthful provider/freshness contract."""
    data, meta = _alpaca_bars(ticker, interval, range_str, include_extended)
    if data and meta:
        return data, meta

    from data_fetcher import _fetch_ohlcv_via_chart_api

    fallback = _fetch_ohlcv_via_chart_api(
        ticker, interval=interval, range_str=range_str, include_prepost=include_extended
    )
    timestamps = (fallback or {}).get("timestamps") or []
    return fallback, {
        "provider": "Yahoo",
        "feed": "yahoo_chart",
        "feed_label": "YAHOO FALLBACK",
        "coverage": "Unofficial fallback",
        "delay_seconds": None,
        "realtime": False,
        "comprehensive": False,
        "official": False,
        "fallback": True,
        "as_of": (
            datetime.fromtimestamp(timestamps[-1], timezone.utc).isoformat()
            if timestamps else None
        ),
        "message": "Alpaca was unavailable. This fallback is not labelled as real-time market data.",
    }
