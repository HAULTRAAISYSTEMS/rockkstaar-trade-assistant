"""Scheduled Live Research runner: market-wide discovery + priority ticker coverage.

All provider content crosses the Phase 6 draft-only ingestion boundary. This
module has no publication, public notification, or realtime announcement call.
"""
from __future__ import annotations
import argparse, json, os
from typing import Iterable
from database import get_db
from news_fetcher import fetch_headlines
from live_research_ingestion import finnhub_articles_to_items, ingest
from live_research_discovery import discover_market_news
import live_research_autopublish as autopublish

DEFAULT_TICKERS=("NVDA","META","AAPL","MSFT","AMZN","GOOGL","TSLA","AMD")

def _tickers_from_env():
    values=[x.strip().upper() for x in os.environ.get("LIVE_RESEARCH_TICKERS","").split(",") if x.strip()]
    return values or list(DEFAULT_TICKERS)
def _company_name(ticker): return ticker
def _admin_actor(conn):
    row=conn.execute("SELECT id, username, is_admin FROM users WHERE is_admin = 1 ORDER BY id ASC LIMIT 1").fetchone()
    if not row: raise RuntimeError("Live Research ingestion requires at least one admin user")
    return {"id":row["id"],"username":row["username"],"is_admin":bool(row["is_admin"])}
def _merge(total, summary, prefix=""):
    total["drafts_created"] += summary["created"]; total["duplicates"] += summary["duplicates"]; total["malformed_skipped"] += summary["skipped"]
    total["errors"].extend((prefix+e) for e in summary["errors"])

def run(tickers: Iterable[str]|None=None, *, discovery_fetchers=None):
    priority=[str(t).strip().upper() for t in (tickers or _tickers_from_env()) if str(t).strip()]
    conn=get_db()
    total={"priority_tickers":len(priority),"events_discovered":0,"tickers_resolved":0,"drafts_created":0,"duplicates":0,"low_importance_skipped":0,"stale_skipped":0,"malformed_skipped":0,"no_ticker_skipped":0,"provider_failures":[],"errors":[],"auto_published":0,"auto_publish_enabled":False}
    try:
        actor=_admin_actor(conn)
        try:
            items, stats=discover_market_news(fetchers=discovery_fetchers)
            total["events_discovered"] += stats["events_discovered"]; total["tickers_resolved"] += stats["tickers_resolved"]
            total["low_importance_skipped"] += stats["low_importance"]; total["stale_skipped"] += stats["stale"]; total["malformed_skipped"] += stats["malformed"]; total["no_ticker_skipped"] += stats.get("no_ticker",0)
            total["provider_failures"].extend(stats["provider_failures"])
            if stats.get("samples"): total["samples"]=stats["samples"]
            _merge(total, ingest(items, actor, conn), "discovery:")
        except Exception as exc: total["provider_failures"].append("discovery:"+type(exc).__name__)
        for ticker in priority:
            try:
                news=fetch_headlines(ticker); items=finnhub_articles_to_items(ticker,_company_name(ticker),news.articles)
                _merge(total, ingest(items,actor,conn),ticker+":")
            except Exception as exc: total["provider_failures"].append(ticker+":"+type(exc).__name__)
        try:
            gate=autopublish.auto_publish(conn, actor)
            total["auto_publish_enabled"]=gate["enabled"]; total["auto_published"]=gate["published"]
            total["errors"].extend("autopublish:"+e for e in gate["errors"])
        except Exception as exc: total["errors"].append("autopublish:"+type(exc).__name__)
        conn.commit(); return total
    finally: conn.close()

def main():
    parser=argparse.ArgumentParser(description="Discover market catalysts and create draft-only Live Research suggestions")
    parser.add_argument("--tickers",help="Comma-separated priority ticker override"); args=parser.parse_args()
    tickers=[x.strip().upper() for x in args.tickers.split(",") if x.strip()] if args.tickers else None
    result=run(tickers); print(json.dumps(result,sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
