"""
news_fetcher.py — Multi-source catalyst news fetcher with category parsing.

Source priority (first available API key wins):
  1. Finnhub company-news    env FINNHUB_API_KEY
  2. NewsAPI everything      env NEWS_API_KEY
  3. Polygon ticker-news     env POLYGON_API_KEY
  4. yfinance .news          (no key required — always last resort)

Usage:
    from news_fetcher import fetch_headlines, parse_catalyst_categories
    news = fetch_headlines("NVDA")
    # news.headlines  → list[str]
    # news.summary    → str
    # news.categories → list[str]  e.g. ["earnings_beat", "guidance_raise"]
    # news.freshness_minutes → int | None
    # news.source     → str

Category keys (used in scoring.py _CAT_WEIGHTS):
    earnings_beat, earnings_miss,
    analyst_upgrade, analyst_downgrade,
    partnership_deal, acquisition_merger,
    government_contract, product_launch,
    fda, sec_legal,
    guidance_raise, guidance_cut
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import NamedTuple

logger = logging.getLogger(__name__)

HEADLINE_REFRESH_MINUTES = 5   # minimum gap between headline refreshes during market hours


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class CatalystNews(NamedTuple):
    headlines:         list[str]
    summary:           str
    categories:        list[str]     # detected category keys
    freshness_minutes: int | None    # minutes since newest article (None = unknown)
    source:            str           # "finnhub" | "newsapi" | "polygon" | "yfinance" | "none"


# ---------------------------------------------------------------------------
# Category definitions
# ---------------------------------------------------------------------------
# Each entry: keywords (list), weight (int for scoring.py), label (display string)
# Keep weights in sync with _CAT_WEIGHTS in scoring.py.

CATALYST_CATEGORIES: dict[str, dict] = {
    "earnings_beat": {
        "keywords": [
            "beat estimates", "beat on earnings", "beat on revenue", "topped estimates",
            "exceeded expectations", "blew past estimates", "above consensus",
            "earnings beat", "revenue beat", "eps beat", "positive earnings surprise",
            "beat the street", "surpassed estimates", "quarterly beat", "trounced estimates",
        ],
        "weight": 4,
        "label":  "Earnings Beat",
    },
    "earnings_miss": {
        "keywords": [
            "missed estimates", "missed on earnings", "missed on revenue", "below estimates",
            "fell short", "disappointing results", "earnings miss", "revenue miss",
            "eps miss", "missed expectations", "below consensus", "negative surprise",
            "came in below", "failed to meet",
        ],
        "weight": 3,
        "label":  "Earnings Miss",
    },
    "analyst_upgrade": {
        "keywords": [
            "upgrade", "price target raised", "raises price target", "buy rating",
            "outperform", "overweight", "initiated buy", "reiterate buy", "strong buy",
            "added to conviction", "upgraded to buy", "lifted to outperform",
            "raises pt", "initiates with buy", "positive catalyst watch",
            "raises its price target", "boosts price target",
        ],
        "weight": 3,
        "label":  "Analyst Upgrade",
    },
    "analyst_downgrade": {
        "keywords": [
            "downgrade", "underperform", "underweight", "sell rating",
            "price target cut", "lowers price target", "reduced to sell",
            "cut to neutral", "removed from conviction", "downgraded to hold",
            "reduces pt", "lowers pt to", "cut to underperform",
            "cuts price target", "trims price target",
        ],
        "weight": 2,
        "label":  "Analyst Downgrade",
    },
    "partnership_deal": {
        "keywords": [
            "partnership", "strategic agreement", "collaboration agreement",
            "joint venture", "supply agreement", "licensing deal",
            "distribution agreement", "signs deal with", "signs agreement with",
            "new contract with", "multi-year agreement", "strategic alliance",
            "co-development", "commercialization agreement", "enters into agreement",
        ],
        "weight": 3,
        "label":  "Partnership/Deal",
    },
    "acquisition_merger": {
        "keywords": [
            "acquisition", "merger", "acquires", "buyout", "takeover",
            "merges with", "agreed to acquire", "going private", "merger agreement",
            "deal to buy", "to be acquired", "tender offer",
            "signed definitive agreement", "strategic combination", "to acquire",
        ],
        "weight": 5,
        "label":  "Acquisition/Merger",
    },
    "government_contract": {
        "keywords": [
            "government contract", "defense contract", "dod contract",
            "pentagon contract", "awarded contract", "federal contract",
            "u.s. army", "u.s. navy", "air force", "nasa contract",
            "military contract", "national security", "government award",
            "department of defense", "u.s. government", "department of energy",
        ],
        "weight": 4,
        "label":  "Gov't Contract",
    },
    "product_launch": {
        "keywords": [
            "product launch", "launches new", "unveiled", "new product announced",
            "commercial launch", "goes live", "product release",
            "new platform launched", "new service launched", "launches its",
            "first-in-class", "cleared for commercial", "product debut",
        ],
        "weight": 2,
        "label":  "Product Launch",
    },
    "fda": {
        "keywords": [
            "fda approved", "fda approval", "fda clearance", "fda grants",
            "fda accepts", "fda breakthrough", "nda approval", "pdufa",
            "regulatory approval", "ema approval", "510(k)", "fda cleared",
            "fast track designation", "priority review", "fda advisory",
            "bla approval", "sba approval",
        ],
        "weight": 5,
        "label":  "FDA/Regulatory",
    },
    "sec_legal": {
        "keywords": [
            "sec investigation", "sec charges", "sec subpoena", "doj investigation",
            "class action", "lawsuit filed", "indicted", "investigation launched",
            "securities fraud", "legal action", "regulatory fine", "penalty imposed",
            "consent order", "cease and desist", "whistleblower complaint",
            "criminal charges", "grand jury",
        ],
        "weight": 3,
        "label":  "SEC/Legal",
    },
    "guidance_raise": {
        "keywords": [
            "raises guidance", "raised guidance", "increased forecast",
            "raised outlook", "increases full-year", "upgraded guidance",
            "raised full-year", "guidance raised", "above prior guidance",
            "raised revenue guidance", "raised earnings guidance",
            "bullish full-year", "increased its outlook",
        ],
        "weight": 4,
        "label":  "Guidance Raise",
    },
    "guidance_cut": {
        "keywords": [
            "lowers guidance", "lowered guidance", "cut guidance",
            "reduced forecast", "decreases full-year", "guidance cut",
            "lowered full-year", "below prior guidance", "revised guidance lower",
            "cut revenue guidance", "cut earnings guidance",
            "cautious full-year", "lowered its outlook",
        ],
        "weight": 3,
        "label":  "Guidance Cut",
    },
}


# ---------------------------------------------------------------------------
# Category parsing
# ---------------------------------------------------------------------------

def parse_catalyst_categories(headlines: list[str]) -> list[str]:
    """
    Scan all headlines and return detected category keys.
    Scans the full combined text — each category can fire at most once.
    """
    full_text = " | ".join(headlines).lower()
    detected: list[str] = []
    for cat_key, cat_def in CATALYST_CATEGORIES.items():
        if any(kw in full_text for kw in cat_def["keywords"]):
            detected.append(cat_key)
    return detected


# ---------------------------------------------------------------------------
# Freshness helper
# ---------------------------------------------------------------------------

def _minutes_ago(ts: datetime | None) -> int | None:
    if ts is None:
        return None
    try:
        now = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0, int((now - ts).total_seconds() / 60))
    except Exception:
        return None


def freshness_label(minutes: int | None) -> str:
    """Human-readable freshness string for UI display."""
    if minutes is None:
        return ""
    if minutes < 2:
        return "Just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


# ---------------------------------------------------------------------------
# Source implementations
# ---------------------------------------------------------------------------

def _try_finnhub(ticker: str) -> CatalystNews | None:
    """Finnhub company-news (FINNHUB_API_KEY)."""
    api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import urllib.request
        from datetime import date, timedelta
        today  = date.today()
        from_d = (today - timedelta(days=3)).isoformat()
        to_d   = today.isoformat()
        url = (
            f"https://finnhub.io/api/v1/company-news"
            f"?symbol={ticker}&from={from_d}&to={to_d}&token={api_key}"
        )
        with urllib.request.urlopen(url, timeout=6) as resp:
            articles = json.loads(resp.read().decode())
        if not articles:
            return None
        articles.sort(key=lambda x: x.get("datetime", 0), reverse=True)
        headlines = [a["headline"] for a in articles[:5] if a.get("headline")]
        if not headlines:
            return None
        freshness = None
        ts = articles[0].get("datetime")
        if ts:
            freshness = _minutes_ago(datetime.fromtimestamp(ts, tz=timezone.utc))
        cats = parse_catalyst_categories(headlines)
        return CatalystNews(
            headlines=headlines, summary=headlines[0],
            categories=cats, freshness_minutes=freshness, source="finnhub",
        )
    except Exception as exc:
        logger.debug("Finnhub fetch failed for %s: %s", ticker, exc)
        return None


def _try_newsapi(ticker: str) -> CatalystNews | None:
    """NewsAPI everything endpoint (NEWS_API_KEY)."""
    api_key = os.environ.get("NEWS_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import urllib.request, urllib.parse
        query = urllib.parse.quote(f'"{ticker}" stock')
        url = (
            f"https://newsapi.org/v2/everything"
            f"?q={query}&sortBy=publishedAt&pageSize=5"
            f"&language=en&apiKey={api_key}"
        )
        with urllib.request.urlopen(url, timeout=6) as resp:
            data = json.loads(resp.read().decode())
        articles = data.get("articles", [])
        headlines = [a["title"] for a in articles if a.get("title")][:5]
        if not headlines:
            return None
        freshness = None
        pa = articles[0].get("publishedAt") if articles else None
        if pa:
            dt = datetime.fromisoformat(pa.replace("Z", "+00:00"))
            freshness = _minutes_ago(dt)
        cats = parse_catalyst_categories(headlines)
        return CatalystNews(
            headlines=headlines, summary=headlines[0],
            categories=cats, freshness_minutes=freshness, source="newsapi",
        )
    except Exception as exc:
        logger.debug("NewsAPI fetch failed for %s: %s", ticker, exc)
        return None


def _try_polygon(ticker: str) -> CatalystNews | None:
    """Polygon ticker-news endpoint (POLYGON_API_KEY)."""
    api_key = os.environ.get("POLYGON_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import urllib.request
        url = (
            f"https://api.polygon.io/v2/reference/news"
            f"?ticker={ticker}&order=desc&limit=5&sort=published_utc"
            f"&apiKey={api_key}"
        )
        with urllib.request.urlopen(url, timeout=6) as resp:
            data = json.loads(resp.read().decode())
        results = data.get("results", [])
        headlines = [r["title"] for r in results if r.get("title")][:5]
        if not headlines:
            return None
        freshness = None
        pu = results[0].get("published_utc") if results else None
        if pu:
            dt = datetime.fromisoformat(pu.replace("Z", "+00:00"))
            freshness = _minutes_ago(dt)
        cats = parse_catalyst_categories(headlines)
        return CatalystNews(
            headlines=headlines, summary=headlines[0],
            categories=cats, freshness_minutes=freshness, source="polygon",
        )
    except Exception as exc:
        logger.debug("Polygon fetch failed for %s: %s", ticker, exc)
        return None


def _try_yfinance(ticker: str) -> CatalystNews | None:
    """Fallback: yfinance .news (no key required)."""
    try:
        import yfinance as yf
        t    = yf.Ticker(ticker)
        news = t.news
        if not news:
            return None
        headlines = [item.get("title", "") for item in news[:5] if item.get("title")]
        if not headlines:
            return None
        freshness = None
        pt = news[0].get("providerPublishTime")
        if pt:
            dt = datetime.fromtimestamp(pt, tz=timezone.utc)
            freshness = _minutes_ago(dt)
        cats = parse_catalyst_categories(headlines)
        return CatalystNews(
            headlines=headlines, summary=headlines[0],
            categories=cats, freshness_minutes=freshness, source="yfinance",
        )
    except Exception as exc:
        logger.debug("yfinance news failed for %s: %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Empty fallback
# ---------------------------------------------------------------------------

_EMPTY = CatalystNews(
    headlines=["No headlines available — set FINNHUB_API_KEY, NEWS_API_KEY, or POLYGON_API_KEY for live news."],
    summary="No catalyst loaded. Connect a news API for live analysis.",
    categories=[],
    freshness_minutes=None,
    source="none",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_headlines(ticker: str) -> CatalystNews:
    """
    Fetch catalyst headlines using the best available source.
    Priority: Finnhub → NewsAPI → Polygon → yfinance → empty fallback.
    Never raises — always returns a CatalystNews.
    """
    for fn in (_try_finnhub, _try_newsapi, _try_polygon, _try_yfinance):
        result = fn(ticker)
        if result is not None:
            logger.debug("Headlines for %s via %s (%d items)", ticker, result.source, len(result.headlines))
            return result
    logger.debug("No news source available for %s", ticker)
    return _EMPTY


def needs_refresh(headlines_fetched_at: str | None) -> bool:
    """
    Return True if headlines should be re-fetched.
    Re-fetches when: never fetched, timestamp unparseable, or > HEADLINE_REFRESH_MINUTES old.
    """
    if not headlines_fetched_at:
        return True
    try:
        fetched = datetime.fromisoformat(headlines_fetched_at)
        age_min = (datetime.now() - fetched).total_seconds() / 60
        return age_min >= HEADLINE_REFRESH_MINUTES
    except Exception:
        return True
