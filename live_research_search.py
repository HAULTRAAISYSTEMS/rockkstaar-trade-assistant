"""Admin-only on-demand research search.

Fresh provider/SEC results are returned for review. Creating a result delegates to
Phase 6 create_suggestion(), which is draft-only and has no realtime/notification path.
"""
from __future__ import annotations
from datetime import datetime, timezone
import re
import requests

from database import get_db
from fundamentals_engine import _edgar_cik, _EDGAR_COMPANY_URL, _EDGAR_HEADERS
from live_research_discovery import classify, EVENT_CATEGORY
from live_research_ingestion import ProviderItem, create_suggestion
from news_fetcher import fetch_headlines

_TICKER = re.compile(r"^[A-Z][A-Z0-9.-]{0,5}$")


def resolve_company(query: str) -> tuple[str, str] | tuple[None, None]:
    q = str(query or "").strip()
    if not q:
        return None, None
    upper = q.upper()
    if _TICKER.fullmatch(upper):
        cik, name = _edgar_cik(upper)
        return (upper, name or upper) if cik else (None, None)
    # Reuse SEC's cached ticker/company directory loaded by _edgar_cik.
    import fundamentals_engine as fe
    _edgar_cik("AAPL")
    needle = q.casefold()
    matches = []
    for row in fe._edgar_tickers_cache.values():
        name = str(row.get("title") or "")
        if needle in name.casefold():
            matches.append((str(row.get("ticker") or "").upper(), name))
    if not matches:
        return None, None
    matches.sort(key=lambda x: (0 if x[1].casefold() == needle else 1, len(x[1])))
    return matches[0]


def _existing(conn, ticker: str, url: str):
    row = conn.execute("SELECT id,status,headline FROM research_posts WHERE ticker=? AND source_url=? ORDER BY updated_at DESC LIMIT 1", (ticker, url)).fetchone()
    return dict(row) if row else None


def _provider_items(ticker: str, company: str):
    news = fetch_headlines(ticker)
    out = []
    for row in news.articles or ():
        if not isinstance(row, dict):
            continue
        headline = str(row.get("headline") or "").strip(); summary = str(row.get("summary") or "").strip(); url = str(row.get("url") or "").strip()
        if not headline or not summary or not url:
            continue
        event = classify(headline, summary) or "breaking_news"
        category = EVENT_CATEGORY.get(event, "Earnings" if event == "earnings" else "Breaking News")
        out.append(ProviderItem("on-demand-news", str(row.get("id") or row.get("published_at") or url), ticker, company, headline,
            str(row.get("source") or news.source or "Provider"), url, category=category, facts=(summary,),
            published_at=str(row.get("published_at") or ""), source_kind="provider", metadata={"event_type":event}))
    return out


def _sec_items(ticker: str, company: str):
    cik, sec_name = _edgar_cik(ticker)
    if not cik: return []
    try:
        r = requests.get(_EDGAR_COMPANY_URL.format(cik=cik), headers=_EDGAR_HEADERS, timeout=12)
        if r.status_code != 200: return []
        recent = (r.json().get("filings") or {}).get("recent") or {}
    except Exception:
        return []
    forms=recent.get("form") or []; acc=recent.get("accessionNumber") or []; docs=recent.get("primaryDocument") or []; dates=recent.get("filingDate") or []
    out=[]
    for i, form in enumerate(forms[:30]):
        if form not in {"8-K","10-Q","10-K"}: continue
        accession=str(acc[i]).replace("-","") if i < len(acc) else ""; doc=str(docs[i]) if i < len(docs) else ""
        if not accession or not doc: continue
        url=f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{doc}"
        published=str(dates[i]) if i < len(dates) else ""
        headline=f"{sec_name or company} filed {form} with the SEC"
        out.append(ProviderItem("sec-edgar", str(acc[i]), ticker, sec_name or company, headline, "SEC EDGAR", url,
            category="SEC Filing", facts=(f"Official {form} filing submitted to the U.S. Securities and Exchange Commission.",),
            published_at=published, source_kind="sec", metadata={"event_type":"sec_filing","form":form}))
    return out[:8]


def search(query: str, conn=None) -> dict:
    ticker, company = resolve_company(query)
    if not ticker:
        return {"ticker":None,"company_name":None,"results":[],"errors":[],"message":"No matching public company/ticker found."}
    owns=conn is None; conn=conn or get_db(); errors=[]; items=[]
    try:
        try: items.extend(_provider_items(ticker, company))
        except Exception as exc: errors.append(f"provider:{type(exc).__name__}")
        try: items.extend(_sec_items(ticker, company))
        except Exception as exc: errors.append(f"sec:{type(exc).__name__}")
        # Primary/SEC first, then newest provider order as returned by existing stack.
        items.sort(key=lambda x: 0 if x.source_kind in {"primary","sec"} else 1)
        results=[]; seen=set()
        for item in items:
            if item.source_url in seen: continue
            seen.add(item.source_url); existing=_existing(conn,ticker,item.source_url)
            results.append({"ticker":ticker,"company_name":company,"headline":item.headline,"summary":item.facts[0],"source_name":item.source_name,
                "source_url":item.source_url,"published_at":item.published_at,"catalyst_type":item.metadata.get("event_type") or item.category,
                "category":item.category,"source_kind":item.source_kind,"provider":item.provider,"external_id":item.external_id,"existing":existing})
        return {"ticker":ticker,"company_name":company,"results":results,"errors":errors,"message":"" if results else "No fresh research results found."}
    finally:
        if owns: conn.close()


def create_draft_from_result(data: dict, actor: dict, conn=None) -> dict:
    owns=conn is None; conn=conn or get_db()
    try:
        ticker, company = resolve_company(data.get("ticker") or data.get("company_name") or "")
        if not ticker: raise ValueError("ticker/company could not be resolved")
        item=ProviderItem(str(data.get("provider") or "on-demand-news"),str(data.get("external_id") or data.get("source_url") or ""),ticker,company,
            str(data.get("headline") or ""),str(data.get("source_name") or ""),str(data.get("source_url") or ""),
            category=str(data.get("category") or "Breaking News"),facts=(str(data.get("summary") or ""),),published_at=str(data.get("published_at") or ""),
            source_kind=str(data.get("source_kind") or "provider"),metadata={"event_type":str(data.get("catalyst_type") or "")})
        existing=_existing(conn,ticker,item.source_url)
        if existing: return {"status":"existing","post":existing}
        result=create_suggestion(item,actor,conn); conn.commit(); return result
    except Exception:
        conn.rollback(); raise
    finally:
        if owns: conn.close()
