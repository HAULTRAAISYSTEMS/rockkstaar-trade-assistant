"""Scheduled production runner for Tradestaar Live Research.

Reuses the app's existing news provider stack and Phase 6 ingestion service.
Every provider item is persisted as an ADMIN-REVIEWABLE DRAFT only. This module
contains no publish, realtime announcement, or notification call.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Iterable

from database import get_db
from news_fetcher import fetch_headlines
from live_research_ingestion import finnhub_articles_to_items, ingest

DEFAULT_TICKERS = ("NVDA", "META", "AAPL", "MSFT", "AMZN", "GOOGL", "TSLA", "AMD")


def _tickers_from_env() -> list[str]:
    raw = os.environ.get("LIVE_RESEARCH_TICKERS", "")
    values = [x.strip().upper() for x in raw.split(",") if x.strip()]
    return values or list(DEFAULT_TICKERS)


def _company_name(ticker: str) -> str:
    # Do not fabricate a company name. The ticker is a truthful fallback label.
    return ticker


def _admin_actor(conn) -> dict:
    row = conn.execute(
        "SELECT id, username, is_admin FROM users WHERE is_admin = 1 ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if not row:
        raise RuntimeError("Live Research ingestion requires at least one admin user")
    return {"id": row["id"], "username": row["username"], "is_admin": bool(row["is_admin"])}


def run(tickers: Iterable[str] | None = None) -> dict:
    tickers = [str(t).strip().upper() for t in (tickers or _tickers_from_env()) if str(t).strip()]
    conn = get_db()
    try:
        actor = _admin_actor(conn)
        total = {"tickers": len(tickers), "created": 0, "duplicates": 0, "skipped": 0, "errors": []}
        for ticker in tickers:
            try:
                news = fetch_headlines(ticker)
                # Phase 6's adapter is intentionally strict: articles without a real
                # source URL + summary are skipped rather than guessed or fabricated.
                items = finnhub_articles_to_items(ticker, _company_name(ticker), news.articles)
                summary = ingest(items, actor, conn)
                total["created"] += summary["created"]
                total["duplicates"] += summary["duplicates"]
                total["skipped"] += summary["skipped"]
                total["errors"].extend(f"{ticker}:{err}" for err in summary["errors"])
            except Exception as exc:
                total["skipped"] += 1
                total["errors"].append(f"{ticker}:{type(exc).__name__}")
        conn.commit()
        return total
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create draft-only Live Research suggestions")
    parser.add_argument("--tickers", help="Comma-separated ticker override")
    args = parser.parse_args()
    tickers = [x.strip().upper() for x in args.tickers.split(",") if x.strip()] if args.tickers else None
    result = run(tickers)
    print(json.dumps(result, sort_keys=True))
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
