"""Scheduled Live Research runner: market-wide discovery + priority ticker coverage.

All provider content crosses the Phase 6 draft-only ingestion boundary. This
module has no publication, public notification, or realtime announcement call.
"""
from __future__ import annotations
import argparse

import live_research_expiry, json, os
from typing import Iterable
from database import get_db
from news_fetcher import fetch_headlines
from live_research_ingestion import finnhub_articles_to_items, ingest
from live_research_discovery import discover_market_news
import live_research_autopublish as autopublish
import insider_alert_sync
import sms_alerts
import browser_push

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
    total={"priority_tickers":len(priority),"events_discovered":0,"tickers_resolved":0,"drafts_created":0,"duplicates":0,"low_importance_skipped":0,"stale_skipped":0,"malformed_skipped":0,"no_ticker_skipped":0,"provider_failures":[],"errors":[],"auto_published":0,"auto_publish_enabled":False,"insider_alerts_added":0,"earnings_alerts_added":0,"alert_sync_errors":[],"queue_incoming":None,"queue_newest":""}
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
        # Command Center alerts. Each source is interval-gated inside the sync,
        # so this is a cheap no-op on most 5-minute firings.
        try:
            total.update(insider_alert_sync.run(conn, priority))
        except Exception as exc: total["errors"].append("alert_sync:"+type(exc).__name__)
        # Retire items nobody reviewed in time, before measuring depth so the
        # number reported is what is actually still waiting. No-op unless
        # LIVE_RESEARCH_EXPIRE_DAYS is set.
        try:
            total.update(live_research_expiry.run(conn))
        except Exception as exc: total["errors"].append("expiry:"+type(exc).__name__)
        # Delivery is a separate, opt-in boundary. It sees published research
        # only and is a no-op unless SMS_ALERTS_ENABLED is explicitly enabled.
        try:
            total.update(sms_alerts.deliver_pending(conn))
        except Exception as exc:
            total["errors"].append("sms:"+type(exc).__name__)
        # Standards-based browser push has no paid provider. Subscriptions are
        # explicit per device and delivery uses the same thesis-aware boundary.
        try:
            total.update(browser_push.deliver_pending(conn))
        except Exception as exc:
            total["errors"].append("push:"+type(exc).__name__)
        # The earnings and catalyst calendar, as a second push source. Its own
        # counters rather than update(): a research push failing and a
        # calendar push failing are different problems, and a merged total
        # would hide which one did.
        try:
            cal = browser_push.deliver_calendar_alerts(conn)
            total["calendar_push_sent"] = cal.get("push_sent", 0)
            total["calendar_push_skipped"] = cal.get("push_skipped", 0)
            total["errors"].extend(cal.get("push_errors", []))
        except Exception as exc:
            total["errors"].append("calendar_push:" + type(exc).__name__)
        # Fundamentals changes — the page was always right, it just never
        # came to find you when a ratio crossed a line that matters.
        try:
            fund = browser_push.deliver_fundamentals_alerts(conn)
            total["fundamentals_push_sent"] = fund.get("push_sent", 0)
            total["fundamentals_checked"] = fund.get("tickers_checked", 0)
            total["errors"].extend(fund.get("push_errors", []))
        except Exception as exc:
            total["errors"].append("fundamentals_push:" + type(exc).__name__)
        # Queue depth. drafts_created is 0 on most firings by design - a story
        # creates a draft once, on the first run after it appears, and the next
        # 5-minute run correctly sees it as a duplicate. Reading a single run
        # therefore says nothing about whether ingestion is working. These two
        # numbers do: how much is waiting for review, and how recently anything
        # was added.
        try:
            row = conn.execute("SELECT COUNT(*) AS n, MAX(created_at) AS newest FROM research_posts WHERE status = ?", ("incoming",)).fetchone()
            total["queue_incoming"] = int(row["n"] or 0)
            total["queue_newest"] = str(row["newest"] or "")
        except Exception as exc: total["errors"].append("queue_depth:"+type(exc).__name__)
        conn.commit(); return total
    finally: conn.close()

def main():
    parser=argparse.ArgumentParser(description="Discover market catalysts and create draft-only Live Research suggestions")
    parser.add_argument("--tickers",help="Comma-separated priority ticker override"); args=parser.parse_args()
    tickers=[x.strip().upper() for x in args.tickers.split(",") if x.strip()] if args.tickers else None
    # Uptime first: this cron job runs outside the web service, so it is what
    # can tell the owner (Telegram) that the site is down — and back up.
    try:
        import uptime_watchdog
        print(json.dumps({"uptime": uptime_watchdog.run()}, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"uptime_error": type(exc).__name__}))
    result=run(tickers); print(json.dumps(result,sort_keys=True))
    # Market-wide SEC Form 4 feed (open-market buys/sells for every ticker),
    # then alerts for new buys on someone's watchlist or strong/cluster buys.
    try:
        import insider_feed
        from datetime import datetime as _dt, timezone as _tz
        from schwab import quotes_user_id
        started=_dt.now(_tz.utc).isoformat()
        feed=insider_feed.sync()
        conn=get_db()
        try:
            watch={}
            for r in conn.execute("SELECT w.user_id AS uid, ws.ticker AS t FROM watchlist_stocks ws JOIN watchlists w ON w.id=ws.watchlist_id").fetchall():
                if r["uid"] is not None: watch.setdefault(int(r["uid"]),set()).add(str(r["t"]).upper())
            fired=insider_feed.alert_new_buys(conn, since_iso=started, watch_by_user=watch, owner_id=quotes_user_id())
            conn.commit()
        finally: conn.close()
        print(json.dumps({"insider_feed": feed, "insider_alerts": len(fired)}, sort_keys=True, default=str))
    except Exception as exc:
        print(json.dumps({"insider_feed_error": type(exc).__name__}))
    # Weekly Research Memory recall: due questions to each user's devices,
    # and to Telegram for the owner. Self-gated on day/hour; once per day.
    try:
        import memory_edge
        from schwab import quotes_user_id
        owner=quotes_user_id(); conn=get_db()
        try: users=[int(r["user_id"]) for r in conn.execute("SELECT DISTINCT user_id FROM research_memory_cards WHERE status='active'").fetchall()]
        finally: conn.close()
        pings={str(u): memory_edge.recall_ping(u, telegram=None if u==owner else (lambda text: False)).get("reason","sent") for u in users}
        print(json.dumps({"recall": pings}, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"recall_error": type(exc).__name__}))
    # Notification inbox: fresh news on followed tickers and big market
    # headlines, earnings today/tomorrow and results, economic releases.
    try:
        import inbox_feeds
        print(json.dumps({"inbox_feeds": inbox_feeds.run()}, sort_keys=True, default=str))
    except Exception as exc:
        print(json.dumps({"inbox_feeds_error": type(exc).__name__}))
    return 0
def _run_with_deadline(seconds: int = 270) -> int:
    """Run main(), but never past `seconds`. The job fires every 5 minutes;
    a run hung on a network fetch used to sit for 35+ minutes holding a
    database transaction (and its locks) open, freezing the web app."""
    import signal

    def _expired(signum, frame):
        raise TimeoutError(f"live research run exceeded {seconds}s")
    signal.signal(signal.SIGALRM, _expired)
    signal.alarm(seconds)
    try:
        return main()
    except TimeoutError as exc:
        print(json.dumps({"run_timeout": str(exc)}))
        return 1
    finally:
        signal.alarm(0)

if __name__=="__main__": raise SystemExit(_run_with_deadline())
