"""Ticker-scoped intelligence contracts for the Tradestaar Terminal.

This module deliberately normalizes data already owned by the app.  It does
not fetch providers itself, which keeps the Terminal routes fast and makes
missing data explicit instead of filling panels with guessed values.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ticker(value: Any) -> str:
    return _text(value).upper()


def _rows(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _is_upcoming(value: Any) -> bool:
    try:
        event_date = datetime.fromisoformat(_text(value)[:10]).date()
        return event_date >= datetime.now(timezone.utc).date()
    except (TypeError, ValueError):
        return False


def aggregate_ohlcv_bars(bars: list[dict], size: int = 4) -> list[dict]:
    """Aggregate intraday OHLCV bars without crossing US trading sessions."""
    if size < 1:
        raise ValueError("size must be positive")
    sessions: dict[str, list[dict]] = {}
    for bar in sorted(bars, key=lambda row: row["time"]):
        day = datetime.fromtimestamp(bar["time"], tz=timezone.utc).astimezone(
            ZoneInfo("America/New_York")
        ).date().isoformat()
        sessions.setdefault(day, []).append(bar)

    aggregated = []
    for day_bars in sessions.values():
        for start in range(0, len(day_bars), size):
            chunk = day_bars[start:start + size]
            aggregated.append({
                "time": chunk[0]["time"],
                "open": chunk[0]["open"],
                "high": max(row["high"] for row in chunk),
                "low": min(row["low"] for row in chunk),
                "close": chunk[-1]["close"],
                "volume": sum(row.get("volume") or 0 for row in chunk),
            })
    return aggregated


def normalize_ohlcv_data(data: dict | None) -> list[dict]:
    """Build sorted, unique, internally consistent candles from provider arrays."""
    if not data:
        return []
    keys = ("timestamps", "opens", "highs", "lows", "closes")
    arrays = [data.get(key) or [] for key in keys]
    volumes = data.get("volumes") or []
    bars_by_time: dict[int, dict] = {}
    for index, values in enumerate(zip(*arrays)):
        try:
            timestamp = int(values[0])
            open_, high, low, close = (float(value) for value in values[1:])
        except (TypeError, ValueError, OverflowError):
            continue
        if not all(math.isfinite(value) and value > 0 for value in (open_, high, low, close)):
            continue
        if high < max(open_, close) or low > min(open_, close) or low > high:
            continue
        bar = {
            "time": timestamp,
            "open": round(open_, 4),
            "high": round(high, 4),
            "low": round(low, 4),
            "close": round(close, 4),
            "volume": int(volumes[index] or 0) if index < len(volumes) else 0,
        }
        bars_by_time[timestamp] = bar
    return [bars_by_time[timestamp] for timestamp in sorted(bars_by_time)]


def annotate_market_sessions(
    bars: list[dict], timezone_name: str = "America/New_York"
) -> list[dict]:
    """Label intraday bars as premarket, regular, or after-hours."""
    try:
        market_tz = ZoneInfo(timezone_name)
    except (KeyError, ValueError):
        market_tz = ZoneInfo("America/New_York")
    annotated = []
    for source in bars:
        bar = dict(source)
        local = datetime.fromtimestamp(bar["time"], timezone.utc).astimezone(market_tz)
        minutes = local.hour * 60 + local.minute
        if minutes < 9 * 60 + 30:
            session = "premarket"
        elif minutes < 16 * 60:
            session = "regular"
        else:
            session = "after_hours"
        bar["session"] = session
        bar["market_date"] = local.date().isoformat()
        annotated.append(bar)
    return annotated


def summarize_extended_sessions(bars: list[dict]) -> dict:
    """Return the latest verified pre/post-market prints and comparisons."""
    last_regular = None
    result: dict[str, Any] = {"premarket": None, "after_hours": None, "latest": None}
    for bar in bars:
        session = bar.get("session", "regular")
        if session == "regular":
            last_regular = bar
            continue
        row = {
            "price": bar["close"],
            "time": bar["time"],
            "date": bar.get("market_date"),
            "volume": bar.get("volume", 0),
            "reference_close": last_regular["close"] if last_regular else None,
        }
        if row["reference_close"]:
            row["change_pct"] = round(
                (row["price"] - row["reference_close"]) / row["reference_close"] * 100,
                2,
            )
        result[session] = row
        result["latest"] = {**row, "session": session}
    return result


def _news_row(raw: Any, fallback_ticker: str = "") -> dict | None:
    if isinstance(raw, str):
        headline = raw.strip()
        if not headline:
            return None
        return {"ticker": fallback_ticker, "headline": headline, "source": "", "published": "", "url": ""}
    if not isinstance(raw, dict):
        return None
    headline = _text(raw.get("headline") or raw.get("title") or raw.get("summary"))
    if not headline:
        return None
    return {
        "ticker": _ticker(raw.get("ticker") or fallback_ticker),
        "headline": headline,
        "source": _text(raw.get("source") or raw.get("publisher")),
        "published": _text(raw.get("published") or raw.get("published_at") or raw.get("time")),
        "url": _text(raw.get("url") or raw.get("link")),
    }


def _earnings_rows(summary: dict, ticker: str) -> list[dict]:
    matches = []
    earnings = (summary or {}).get("earnings") or {}
    for bucket in ("today", "tomorrow", "this_week", "coming_up"):
        for raw in earnings.get(bucket) or []:
            if _ticker(raw.get("ticker")) != ticker or not _is_upcoming(raw.get("date")):
                continue
            matches.append({
                "ticker": ticker,
                "date": _text(raw.get("date")),
                "time": _text(raw.get("time_label") or raw.get("time")) or "TBD",
                "eps_est": raw.get("eps_est"),
                "revenue_est": raw.get("rev_est") or raw.get("revenue_est"),
                "source": _text(raw.get("source")),
                "bucket": bucket,
            })
    return matches


def build_terminal_intelligence(ticker: str, stock: dict | None, intel_summary: dict | None,
                                ai_configured: bool = False) -> dict:
    """Build the fast, provider-free portion of the Terminal intelligence UI."""
    ticker = _ticker(ticker)
    stock = stock or {}
    summary = intel_summary or {}

    news = []
    seen = set()
    for raw in _rows(stock.get("news_headlines")) + _rows(summary.get("market_news") or summary.get("news")):
        row = _news_row(raw, ticker)
        if not row or (row["ticker"] and row["ticker"] != ticker):
            continue
        key = row["headline"].lower()
        if key in seen:
            continue
        seen.add(key)
        news.append(row)
        if len(news) == 12:
            break

    earnings = _earnings_rows(summary, ticker)
    if not earnings and _is_upcoming(stock.get("earnings_date")):
        earnings.append({
            "ticker": ticker,
            "date": _text(stock.get("earnings_date")),
            "time": _text(stock.get("earnings_time")) or "TBD",
            "eps_est": None,
            "revenue_est": None,
            "source": "stock snapshot",
            "bucket": "snapshot",
        })

    events = [
        {"type": "earnings", "date": row["date"], "label": "Earnings", "source": row["source"]}
        for row in earnings if row.get("date")
    ]

    overview = {
        "price": stock.get("current_price"),
        "change_pct": stock.get("gap_pct"),
        "bias": _text(stock.get("trade_bias")) or "Neutral",
        "grade": _text(stock.get("swing_grade")),
        "setup": _text(stock.get("swing_setup_type") or stock.get("setup_type")),
        "catalyst": _text(stock.get("catalyst_summary") or stock.get("catalyst_reason")),
        "relative_volume": stock.get("rel_volume"),
        "average_volume": stock.get("avg_volume"),
        "today_volume": stock.get("today_volume"),
        "vwap": stock.get("vwap"),
        "ema20": stock.get("ema_20_daily"),
        "ema50": stock.get("ema_50_daily"),
        "daily_trend": _text(stock.get("daily_trend")),
        "last_updated": _text(stock.get("last_updated")),
    }
    fundamentals = {
        "sector": _text(stock.get("company_sector") or stock.get("sector_name") or stock.get("sector_etf")),
        "industry": _text(stock.get("company_industry")),
        "relative_strength": stock.get("rs_score"),
        "trend": _text(stock.get("daily_trend")),
        "available_in_analyzer": True,
        "message": "Open the existing Fundamentals analyzer for source-labelled financial statements and scoring.",
    }
    why_moving = overview["catalyst"]
    if not why_moving and news:
        why_moving = news[0]["headline"]

    return {
        "ok": True,
        "ticker": ticker,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "overview": overview,
        "news": news,
        "earnings": earnings,
        "fundamentals": fundamentals,
        "events": events,
        "context": {
            "why_moving": why_moving,
            "news_count": len(news),
            "next_earnings": earnings[0] if earnings else None,
            "relative_volume": overview["relative_volume"],
            "insider_summary": None,
        },
        "ai": {
            "configured": bool(ai_configured),
            "message": "Ask Tradestaar AI about this ticker using verified app context."
            if ai_configured else "Tradestaar AI is not configured on this server.",
        },
    }


def build_insider_payload(ticker: str, rows: list[dict] | None, status: dict | None) -> dict:
    """Normalize corporate-insider rows and derive a factual, modest summary."""
    ticker = _ticker(ticker)
    normalized = []
    for raw in rows or []:
        if _ticker(raw.get("ticker")) != ticker:
            continue
        normalized.append({
            "ticker": ticker,
            "person": _text(raw.get("owner")) or "Unknown insider",
            "title": _text(raw.get("role")) or "Reporting owner",
            "kind": _text(raw.get("kind")) or "OTHER",
            "code": _text(raw.get("code")) or "—",
            "shares": raw.get("shares"),
            "price": raw.get("price"),
            "value": raw.get("value"),
            "transaction_date": _text(raw.get("trade_date")),
            "filing_date": _text(raw.get("filed_at")),
            "ownership_after": raw.get("ownership_after"),
            "sec_url": _text(raw.get("source_url")),
        })
    buys = [row for row in normalized if row["kind"] == "BUY"]
    sells = [row for row in normalized if row["kind"] == "SELL"]
    buy_value = sum(row["value"] for row in buys if isinstance(row.get("value"), (int, float)))
    sell_value = sum(row["value"] for row in sells if isinstance(row.get("value"), (int, float)))
    summary = None
    if normalized:
        summary = {
            "transactions": len(normalized),
            "buys": len(buys),
            "sells": len(sells),
            "reported_buy_value": buy_value,
            "reported_sell_value": sell_value,
            "label": f"{len(buys)} buys · {len(sells)} sells in recent SEC filings",
        }
    return {"ok": True, "ticker": ticker, "rows": normalized, "status": status or {}, "summary": summary}
