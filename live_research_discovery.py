"""Market-wide catalyst discovery for Live Research.

Discovery happens before ticker-specific enrichment. Provider/SEC candidates are
normalized, filtered for recency/importance, ticker-resolved, then handed to the
existing Phase 6 draft-only ingestion boundary.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from live_research_ingestion import ProviderItem
from news_fetcher import CATALYST_CATEGORIES, parse_catalyst_categories

EVENT_CATEGORY = {
    "earnings_beat": "Earnings", "earnings_miss": "Earnings",
    "guidance_raise": "Guidance", "guidance_cut": "Guidance",
    "analyst_upgrade": "Analyst", "analyst_downgrade": "Analyst",
    "partnership_deal": "Partnership", "government_contract": "Partnership",
    "acquisition_merger": "Acquisition", "fda": "Regulatory", "sec_legal": "SEC Filing",
}
HIGH_VALUE_FORMS = {"8-K", "10-Q", "10-K"}
GENERIC_PATTERNS = (
    "stocks to watch", "top stocks", "why these stocks", "market roundup",
    "stock market today", "better buy", "which stock is better", "class action",
    "shareholder alert", "law firm", "investigation on behalf", "deadline reminder",
)
TICKER_RE = re.compile(r"\b(?:NASDAQ|NYSE|AMEX)\s*:\s*([A-Z][A-Z0-9.-]{0,5})\b|\$([A-Z][A-Z0-9.-]{0,5})\b")


def _now(): return datetime.now(timezone.utc)

def _parse_time(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text: return None
    try:
        dt = datetime.fromisoformat(text)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except ValueError:
        return None

def is_recent(value, hours=24):
    dt = _parse_time(value)
    return bool(dt and _now() - dt <= timedelta(hours=hours) and dt <= _now() + timedelta(minutes=5))

def classify(headline, summary=""):
    cats = parse_catalyst_categories([f"{headline} {summary}"])
    if cats: return cats[0]
    text = f"{headline} {summary}".lower()
    if any(k in text for k in ("quarterly results", "reports results", "earnings", "eps", "revenue")): return "earnings"
    if any(k in text for k in ("8-k", "10-q", "10-k", "material agreement")): return "sec_filing"
    return ""

def importance(event_type, headline):
    if any(p in headline.lower() for p in GENERIC_PATTERNS): return 0
    if event_type in EVENT_CATEGORY or event_type in {"earnings", "sec_filing"}: return 5
    return 0

def resolve_ticker(row):
    direct = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
    if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,5}", direct): return direct
    match = TICKER_RE.search(f"{row.get('headline','')} {row.get('summary','')}")
    return next((g for g in match.groups() if g), "") if match else ""

def normalize_news(row, provider="market-news", recency_hours=24):
    if not isinstance(row, dict): return None, "malformed"
    headline, summary = str(row.get("headline") or row.get("title") or "").strip(), str(row.get("summary") or row.get("description") or "").strip()
    url = str(row.get("url") or "").strip(); published = row.get("published_at") or row.get("datetime") or row.get("publishedAt")
    ticker = resolve_ticker(row); event_type = classify(headline, summary)
    if not headline or not summary or not url or not ticker: return None, "malformed"
    if not is_recent(published, recency_hours): return None, "stale"
    if importance(event_type, headline) <= 0: return None, "low_importance"
    source = str(row.get("source") or provider).strip()
    category = EVENT_CATEGORY.get(event_type, "Earnings" if event_type == "earnings" else "Breaking News")
    item = ProviderItem(provider, str(row.get("id") or url), ticker, str(row.get("company_name") or ticker), headline, source, url,
                        category=category, facts=(summary,), published_at=str(published), source_kind=str(row.get("source_kind") or "provider"),
                        metadata={"event_type": event_type, "importance": importance(event_type, headline)})
    return item, "accepted"

def event_key(item):
    period = str(item.metadata.get("period") or "").lower()
    day = (_parse_time(item.published_at) or _now()).date().isoformat()
    return "|".join((item.ticker, str(item.metadata.get("event_type") or item.category).lower(), period or day))

def prefer_primary(items):
    rank = {"primary": 3, "sec": 2, "provider": 1}
    chosen = {}
    for item in items:
        key = event_key(item); old = chosen.get(key)
        if old is None or rank.get(item.source_kind, 0) > rank.get(old.source_kind, 0): chosen[key] = item
    return list(chosen.values())

def fetch_finnhub_market_news():
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not key: return []
    url = "https://finnhub.io/api/v1/news?" + urlencode({"category":"general", "token":key})
    with urlopen(url, timeout=8) as resp: return json.loads(resp.read().decode())

def fetch_polygon_market_news(limit=100):
    key = os.environ.get("POLYGON_API_KEY", "").strip()
    if not key: return []
    url = "https://api.polygon.io/v2/reference/news?" + urlencode({"limit":limit, "order":"desc", "sort":"published_utc", "apiKey":key})
    with urlopen(url, timeout=8) as resp: data=json.loads(resp.read().decode())
    out=[]
    for row in data.get("results") or []:
        tickers=row.get("tickers") or []
        for ticker in tickers[:3]:
            out.append({"id":row.get("id"), "ticker":ticker, "headline":row.get("title"), "summary":row.get("description"), "url":row.get("article_url"), "published_at":row.get("published_utc"), "source":(row.get("publisher") or {}).get("name") or "Polygon"})
    return out

def discover_market_news(fetchers=None, recency_hours=None):
    hours = int(recency_hours or os.environ.get("LIVE_RESEARCH_RECENCY_HOURS", "24"))
    fetchers = fetchers or (("finnhub-market", fetch_finnhub_market_news), ("polygon-market", fetch_polygon_market_news))
    stats={"events_discovered":0,"tickers_resolved":0,"low_importance":0,"stale":0,"malformed":0,"provider_failures":[]}; items=[]
    for name, fetcher in fetchers:
        try: rows=fetcher() or []
        except Exception as exc:
            stats["provider_failures"].append(f"{name}:{type(exc).__name__}"); continue
        stats["events_discovered"] += len(rows)
        for row in rows:
            item, reason=normalize_news(row, name, hours)
            if item: items.append(item); stats["tickers_resolved"] += 1
            else: stats[reason] += 1
    preferred=prefer_primary(items); stats["source_duplicates"] = len(items)-len(preferred)
    return preferred, stats
