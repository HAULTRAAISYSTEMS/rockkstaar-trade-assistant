"""Authorized overnight-equities market data for the Terminal.

Yahoo covers the US premarket, regular, and after-hours sessions but not the
8 PM-4 AM ET ATS session.  Alpaca's BOATS feed supplies that missing session.
No prices are synthesized when credentials or feed access are unavailable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Any

import requests


_BASE_URL = "https://data.alpaca.markets/v2/stocks"
_TIMEFRAMES = {"1m": "1Min", "5m": "5Min", "15m": "15Min", "1h": "1Hour"}
_RANGE_DAYS = {"1d": 2, "5d": 7, "1mo": 32, "3mo": 95, "1y": 370}


def _credentials() -> tuple[str, str]:
    return (
        (os.environ.get("ALPACA_API_KEY") or os.environ.get("APCA_API_KEY_ID") or "").strip(),
        (os.environ.get("ALPACA_SECRET_KEY") or os.environ.get("APCA_API_SECRET_KEY") or "").strip(),
    )


def overnight_provider_status() -> dict:
    key, secret = _credentials()
    configured = bool(key and secret)
    return {
        "provider": "Alpaca BOATS",
        "configured": configured,
        "state": "configured" if configured else "not_configured",
        "message": (
            "Alpaca BOATS overnight feed configured."
            if configured
            else "Set Alpaca API credentials on Render for true 8 PM-4 AM ET candles."
        ),
        "delay": "Plan-dependent; free historical BOATS data can be 15 minutes delayed.",
    }


def _timestamp(value: Any) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(parsed.timestamp())
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_bar(raw: dict, source: str = "alpaca_boats") -> dict | None:
    timestamp = _timestamp(raw.get("t"))
    try:
        bar = {
            "time": timestamp,
            "open": float(raw["o"]),
            "high": float(raw["h"]),
            "low": float(raw["l"]),
            "close": float(raw["c"]),
            "volume": int(raw.get("v") or 0),
            "source": source,
        }
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return bar if timestamp and min(bar["open"], bar["high"], bar["low"], bar["close"]) > 0 else None


def _fetch_basic_overnight_latest(ticker: str, headers: dict[str, str]) -> dict:
    """Return Alpaca Basic's derived latest overnight bar when BOATS is gated.

    The free ``overnight`` feed does not expose historical candles.  Keeping its
    latest verified bar separate from BOATS avoids implying that a complete
    overnight history was loaded.
    """
    try:
        response = requests.get(
            f"{_BASE_URL}/{ticker}/bars/latest",
            params={"feed": "overnight"},
            headers=headers,
            timeout=8,
        )
        if response.status_code != 200:
            return {"bar": None, "http_status": response.status_code}
        payload = response.json()
        return {
            "bar": _parse_bar(payload.get("bar") or {}, "alpaca_overnight"),
            "http_status": 200,
        }
    except (requests.RequestException, ValueError):
        return {"bar": None, "http_status": None}


def fetch_overnight_bars(ticker: str, interval: str, range_str: str) -> dict:
    """Fetch genuine 8 PM-4 AM ET bars, returning an explicit status contract."""
    status = overnight_provider_status()
    if not status["configured"]:
        return {"bars": [], "status": status}
    timeframe = _TIMEFRAMES.get(interval)
    days = _RANGE_DAYS.get(range_str)
    if not timeframe or not days:
        return {
            "bars": [],
            "status": {**status, "state": "unsupported", "message": "This overnight interval/range is not supported."},
        }

    key, secret = _credentials()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    params = {
        "timeframe": timeframe,
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "limit": 10000,
        "adjustment": "split",
        "feed": "boats",
        "sort": "asc",
    }
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    try:
        response = requests.get(
            f"{_BASE_URL}/{ticker}/bars", params=params, headers=headers, timeout=8
        )
        if response.status_code != 200:
            if response.status_code == 403:
                fallback = _fetch_basic_overnight_latest(ticker, headers)
                if fallback["bar"]:
                    return {
                        "bars": [fallback["bar"]],
                        "status": {
                            **status,
                            "state": "limited",
                            "message": (
                                "Basic overnight feed: latest verified print only. "
                                "Full BOATS candle history requires Algo Trader Plus."
                            ),
                            "bar_count": 1,
                            "historical": False,
                        },
                    }
                return {
                    "bars": [],
                    "status": {
                        **status,
                        "state": "restricted",
                        "message": (
                            "BOATS history requires Algo Trader Plus; Alpaca's free "
                            "latest overnight feed is unavailable right now."
                        ),
                        "historical": False,
                    },
                }
            state = "unauthorized" if response.status_code == 401 else "unavailable"
            return {
                "bars": [],
                "status": {**status, "state": state, "message": f"Alpaca BOATS returned HTTP {response.status_code}."},
            }
        payload = response.json()
        bars = [parsed for raw in (payload.get("bars") or []) if (parsed := _parse_bar(raw))]
        return {
            "bars": bars,
            "status": {
                **status,
                "state": "live" if bars else "empty",
                "message": "True overnight candles loaded from Alpaca BOATS." if bars else "No BOATS overnight trades were reported for this symbol and range.",
                "bar_count": len(bars),
            },
        }
    except (requests.RequestException, ValueError) as exc:
        return {
            "bars": [],
            "status": {**status, "state": "unavailable", "message": f"Alpaca BOATS is temporarily unavailable: {type(exc).__name__}."},
        }


def merge_session_bars(primary: list[dict], overnight: list[dict]) -> list[dict]:
    """Merge provider bars by timestamp, preferring the dedicated overnight feed."""
    merged = {int(bar["time"]): dict(bar) for bar in primary}
    merged.update({int(bar["time"]): dict(bar) for bar in overnight})
    return [merged[timestamp] for timestamp in sorted(merged)]
