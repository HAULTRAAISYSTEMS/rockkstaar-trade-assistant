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
        if minutes >= 20 * 60 or minutes < 4 * 60:
            session = "overnight"
        elif minutes < 9 * 60 + 30:
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
    result: dict[str, Any] = {
        "overnight": None, "premarket": None, "after_hours": None, "latest": None
    }
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


# How many rows the Fundamentals tab surfaces. Enough to explain a verdict,
# few enough that the tab stays a summary rather than a second scorecard — the
# analyzer is one click away and does the full job.
FUNDAMENTAL_HIGHLIGHTS = 4


def _highlight_rank(row: dict) -> tuple:
    """Order rows by how much they explain the verdict.

    Outright failures first, because they are what cost the points. Then
    partial credit, which is the interesting middle. Passes only fill the
    remaining space, and unscored rows never make it — an N/A explains
    nothing.
    """
    passed = row.get("passed")
    if passed is False:
        rank = 0
    elif passed == "partial":
        rank = 1
    elif passed is True:
        rank = 2
    else:
        rank = 3
    # Within a rank, the heavier rows first: two points lost matters more.
    return (rank, -int(row.get("points") or 0), str(row.get("label") or ""))


def summarize_scorecard(scored: dict | None) -> dict:
    """The Terminal's read on a company's fundamentals.

    The Fundamentals tab used to be a sentence and a link to another page,
    which is the least useful thing a tab can be — the reader is already
    looking at the company and the whole scorecard is already computed. This
    turns it into the verdict, where the points went, and the handful of rows
    that decided it, each carrying the concept behind it so the tab can teach
    rather than only report.

    Takes an already-scored card. Nothing here fetches: a Terminal panel that
    blocks on a cold EDGAR call is worse than one that says it has no data.
    """
    if not scored or scored.get("error"):
        return {"available": False}

    rows = [row for section in (scored.get("sections") or [])
            for row in (section.get("rows") or [])]
    if not rows:
        return {"available": False}

    try:
        import concepts as _concepts
    except Exception:
        _concepts = None

    highlights = []
    for row in sorted(rows, key=_highlight_rank)[:FUNDAMENTAL_HIGHLIGHTS]:
        if row.get("passed") is None:
            continue
        concept = _concepts.for_row(row.get("key", "")) if _concepts else None
        highlights.append({
            "key": row.get("key"),
            "label": row.get("label"),
            "value": row.get("value"),
            "working": row.get("working"),
            "passed": row.get("passed"),
            "earned": row.get("earned"),
            "points": row.get("points"),
            "concept": concept["slug"] if concept else "",
            "concept_name": concept["name"] if concept else "",
            "one_liner": concept["one_liner"] if concept else "",
        })

    return {
        "available": True,
        "verdict": _text(scored.get("verdict")),
        "verdict_class": _text(scored.get("verdict_class")),
        "verdict_reason": _text(scored.get("verdict_reason")),
        "earned": scored.get("total_earned"),
        "possible": scored.get("total_possible"),
        "currency": _text(scored.get("currency")) or "USD",
        "coverage_note": _text(scored.get("coverage_note")),
        "sections": [
            {"name": _text(section.get("name")),
             "earned": section.get("earned"),
             "possible": section.get("possible")}
            for section in (scored.get("sections") or [])
        ],
        "red_flags": [_text(flag.get("label"))
                      for flag in (scored.get("red_flags") or [])][:3],
        "highlights": highlights,
        "as_of": _text(scored.get("last_updated") or scored.get("fetched_at")),
    }


def build_terminal_intelligence(ticker: str, stock: dict | None, intel_summary: dict | None,
                                ai_configured: bool = False,
                                scored: dict | None = None) -> dict:
    """Build the fast, provider-free portion of the Terminal intelligence UI."""
    ticker = _ticker(ticker)
    stock = stock or {}
    summary = intel_summary or {}
    etf_tickers = {
        "DIA", "IWM", "QQQ", "SCHD", "SMH", "SPY", "VOO", "VTI", "VTV", "VUG",
        "VFH", "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
    }
    company_name = _text(stock.get("company_name") or stock.get("name"))
    asset_type = "ETF" if ticker in etf_tickers or " etf" in company_name.lower() else "EQUITY"

    # Two sources feed this. The per-ticker cache stores bare headline strings
    # with no source, date or link; the market news feed stores full records.
    # The old loop took the first of each headline it saw and the bare strings
    # came first, so a story that had a source and a link on one path arrived
    # with neither — five headlines you could not attribute or go and read.
    # Both are collected now and the richer record wins.
    def _detail(row: dict) -> int:
        return sum(1 for field in ("source", "published", "url") if row.get(field))

    news: list[dict] = []
    by_headline: dict[str, int] = {}
    for raw in _rows(stock.get("news_headlines")) + _rows(summary.get("market_news") or summary.get("news")):
        row = _news_row(raw, ticker)
        if not row or (row["ticker"] and row["ticker"] != ticker):
            continue
        key = row["headline"].lower()
        if key in by_headline:
            existing = news[by_headline[key]]
            if _detail(row) > _detail(existing):
                # Same story, better record. Keep its position in the list.
                news[by_headline[key]] = row
            continue
        if len(news) >= 12:
            continue
        by_headline[key] = len(news)
        news.append(row)

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
    earnings_note = (
        "Earnings do not apply to an ETF — it holds companies rather than being one."
        if asset_type == "ETF" else
        "No confirmed date yet. Companies usually announce two to four weeks ahead."
    ) if not earnings else ""

    scorecard = summarize_scorecard(scored)
    fundamentals = {
        "sector": _text(stock.get("company_sector") or stock.get("sector_name") or stock.get("sector_etf")),
        "industry": _text(stock.get("company_industry")),
        "relative_strength": stock.get("rs_score"),
        "trend": _text(stock.get("daily_trend")),
        "available_in_analyzer": True,
        "scorecard": scorecard,
        # The message is now the fallback rather than the whole panel.
        "message": (
            "Earnings and balance sheets are not applicable to an ETF."
            if asset_type == "ETF" else
            "This company has not been scored yet. Opening the analyzer runs it, "
            "and the result appears here afterwards."
        ) if not scorecard.get("available") else "",
    }
    why_moving = overview["catalyst"]
    if not why_moving and news:
        why_moving = news[0]["headline"]

    return {
        "ok": True,
        "ticker": ticker,
        "asset_type": asset_type,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "overview": overview,
        "news": news,
        "earnings": earnings,
        # The Terminal is where someone is looking at a company and wondering
        # what a results release even contains, so the tab points at the idea
        # rather than assuming it.
        "earnings_note": earnings_note,
        "earnings_concept": {
            "slug": "earnings-report",
            "blurb": "Read the release in four steps: revenue and EPS against "
                     "expectations, then guidance, then margins, then the call. "
                     "The surprise moves the stock, not the number.",
        },
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
            "label": _text(raw.get("label")) or _text(raw.get("kind")) or "UNSPECIFIED",
            "explanation": _text(raw.get("explanation")),
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
