"""
news_fetcher.py — Multi-source catalyst news fetcher with category parsing.

Source priority (first available API key wins):
  1. Finnhub company-news    env FINNHUB_API_KEY
  2. NewsAPI everything      env NEWS_API_KEY
  3. Polygon ticker-news     env POLYGON_API_KEY
  4. Yahoo Finance search JSON (no key required)
  5. Google / Yahoo / Seeking Alpha RSS (no key required)
  6. yfinance .news          (no key required — always last resort)

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

import contextlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Log which API keys are present at import time so Render logs show immediately
# which sources are available without needing a per-ticker fetch.
def _log_key_status() -> None:
    keys = {
        "FINNHUB_API_KEY":  bool(os.environ.get("FINNHUB_API_KEY", "").strip()),
        "NEWS_API_KEY":     bool(os.environ.get("NEWS_API_KEY",     "").strip()),
        "POLYGON_API_KEY":  bool(os.environ.get("POLYGON_API_KEY",  "").strip()),
    }
    present  = [k for k, v in keys.items() if v]
    missing  = [k for k, v in keys.items() if not v]
    if present:
        logger.info("news_fetcher  API keys present: %s", ", ".join(present))
    if missing:
        logger.info("news_fetcher  API keys missing (will use yfinance fallback): %s", ", ".join(missing))

_log_key_status()


def news_source_status() -> dict:
    """Describe source availability without exposing credentials."""
    configured = [
        label
        for env_name, label in (
            ("FINNHUB_API_KEY", "Finnhub"),
            ("NEWS_API_KEY", "NewsAPI"),
            ("POLYGON_API_KEY", "Polygon"),
        )
        if os.environ.get(env_name, "").strip()
    ]
    return {
        "configured": bool(configured),
        "configured_sources": configured,
        "fallback_sources": ["Yahoo Finance", "Yahoo RSS", "Seeking Alpha RSS"],
        "message": (
            "Connected news sources: " + ", ".join(configured) + "."
            if configured
            else "No keyed news provider is configured; using free Yahoo and RSS fallbacks."
        ),
    }


@contextlib.contextmanager
def _silence_yf():
    """Temporarily raise yfinance's logger to ERROR to suppress known 404 noise."""
    yf_log = logging.getLogger("yfinance")
    old = yf_log.level
    yf_log.setLevel(logging.ERROR)
    try:
        yield
    finally:
        yf_log.setLevel(old)

HEADLINE_REFRESH_MINUTES = 5   # minimum gap between headline refreshes during market hours


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CatalystNews:
    headlines:         list[str]
    summary:           str
    categories:        list[str]     # detected category keys
    freshness_minutes: int | None    # minutes since newest article (None = unknown)
    source:            str           # "finnhub" | "newsapi" | "polygon" | "yfinance" | "none"
    articles:          tuple[dict, ...] = ()  # normalized provider metadata for story cards


def _article(
    title: object,
    *,
    source: object = "",
    url: object = "",
    image: object = "",
    summary: object = "",
    published_at: object = "",
) -> dict:
    """Normalize provider fields without inventing unavailable metadata."""
    return {
        "headline": str(title or "").strip(),
        "source": str(source or "").strip(),
        "url": str(url or "").strip(),
        "image": str(image or "").strip(),
        "summary": str(summary or "").strip(),
        "published_at": str(published_at or "").strip(),
    }


def _nested_url(value: object) -> str:
    """Extract a URL from either a string or Yahoo's nested URL objects."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("url") or value.get("href") or "")
    return ""


def _thumbnail_url(value: object) -> str:
    """Choose the largest supplied thumbnail resolution."""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    resolutions = value.get("resolutions") or []
    if isinstance(resolutions, list):
        candidates = [row for row in resolutions if isinstance(row, dict) and row.get("url")]
        if candidates:
            return str(max(candidates, key=lambda row: row.get("width", 0)).get("url") or "")
    return str(value.get("url") or "")


def _display_image(source: object, thumbnail: object) -> str:
    """Keep real publisher art but suppress Yahoo's repeated brand placeholder."""
    publisher = str(source or "").strip().lower()
    if publisher in {"yahoo", "yahoo finance"}:
        return ""
    return _thumbnail_url(thumbnail)


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
        with urllib.request.urlopen(url, timeout=4) as resp:
            articles = json.loads(resp.read().decode())
        if not articles:
            logger.info("Finnhub  ticker=%s  0 articles returned", ticker)
            return None
        articles.sort(key=lambda x: x.get("datetime", 0), reverse=True)
        rich_articles = tuple(
            _article(
                a.get("headline", ""),
                source=a.get("source") or "Finnhub",
                url=a.get("url", ""),
                image=a.get("image", ""),
                summary=a.get("summary", ""),
                published_at=(
                    datetime.fromtimestamp(a["datetime"], tz=timezone.utc).isoformat()
                    if a.get("datetime") else ""
                ),
            )
            for a in articles[:5]
            if a.get("headline")
        )
        headlines = [a["headline"] for a in rich_articles]
        if not headlines:
            return None
        freshness = None
        ts = articles[0].get("datetime")
        if ts:
            freshness = _minutes_ago(datetime.fromtimestamp(ts, tz=timezone.utc))
        cats = parse_catalyst_categories(headlines)
        logger.info("Finnhub  ticker=%s  headlines=%d  categories=%s", ticker, len(headlines), cats)
        return CatalystNews(
            headlines=headlines, summary=headlines[0],
            categories=cats, freshness_minutes=freshness, source="finnhub",
            articles=rich_articles,
        )
    except Exception as exc:
        logger.warning("Finnhub fetch failed for %s: %s", ticker, exc)
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
        with urllib.request.urlopen(url, timeout=4) as resp:
            data = json.loads(resp.read().decode())
        articles = data.get("articles", [])
        rich_articles = tuple(
            _article(
                a.get("title", ""),
                source=(a.get("source") or {}).get("name") or "NewsAPI",
                url=a.get("url", ""),
                image=a.get("urlToImage", ""),
                summary=a.get("description", ""),
                published_at=a.get("publishedAt", ""),
            )
            for a in articles[:5]
            if a.get("title")
        )
        headlines = [a["headline"] for a in rich_articles]
        if not headlines:
            logger.info("NewsAPI  ticker=%s  0 headlines returned  status=%s", ticker, data.get("status"))
            return None
        freshness = None
        pa = articles[0].get("publishedAt") if articles else None
        if pa:
            try:
                dt = datetime.fromisoformat(pa.replace("Z", "+00:00"))
                freshness = _minutes_ago(dt)
            except Exception as _date_exc:
                logger.warning("NewsAPI  ticker=%s  bad publishedAt format=%s: %s", ticker, pa, _date_exc)
        cats = parse_catalyst_categories(headlines)
        logger.info("NewsAPI  ticker=%s  headlines=%d  categories=%s", ticker, len(headlines), cats)
        return CatalystNews(
            headlines=headlines, summary=headlines[0],
            categories=cats, freshness_minutes=freshness, source="newsapi",
            articles=rich_articles,
        )
    except Exception as exc:
        logger.warning("NewsAPI fetch failed for %s: %s", ticker, exc)
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
        with urllib.request.urlopen(url, timeout=4) as resp:
            data = json.loads(resp.read().decode())
        results = data.get("results", [])
        rich_articles = tuple(
            _article(
                r.get("title", ""),
                source=(r.get("publisher") or {}).get("name") or "Polygon",
                url=r.get("article_url", ""),
                image=r.get("image_url", ""),
                summary=r.get("description", ""),
                published_at=r.get("published_utc", ""),
            )
            for r in results[:5]
            if r.get("title")
        )
        headlines = [a["headline"] for a in rich_articles]
        if not headlines:
            logger.info("Polygon  ticker=%s  0 headlines returned", ticker)
            return None
        freshness = None
        pu = results[0].get("published_utc") if results else None
        if pu:
            try:
                dt = datetime.fromisoformat(pu.replace("Z", "+00:00"))
                freshness = _minutes_ago(dt)
            except Exception as _date_exc:
                logger.warning("Polygon  ticker=%s  bad published_utc format=%s: %s", ticker, pu, _date_exc)
        cats = parse_catalyst_categories(headlines)
        logger.info("Polygon  ticker=%s  headlines=%d  categories=%s", ticker, len(headlines), cats)
        return CatalystNews(
            headlines=headlines, summary=headlines[0],
            categories=cats, freshness_minutes=freshness, source="polygon",
            articles=rich_articles,
        )
    except Exception as exc:
        logger.warning("Polygon fetch failed for %s: %s", ticker, exc)
        return None


def _try_yahoo_search(ticker: str) -> CatalystNews | None:
    """Bounded Yahoo Finance search endpoint with article thumbnails.

    This avoids yfinance's internal request stack, which can hang when Yahoo
    rate-limits a hosted IP. The endpoint itself remains optional: a 401/429 or
    schema change simply falls through to the RSS providers.
    """
    try:
        import urllib.parse
        import urllib.request

        query = urllib.parse.urlencode({
            "q": ticker,
            "quotesCount": 1,
            "newsCount": 8,
            "enableFuzzyQuery": "false",
        })
        req = urllib.request.Request(
            f"https://query1.finance.yahoo.com/v1/finance/search?{query}",
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; TradestaarElite/1.0)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))

        rich_articles = []
        freshness_dt = None
        for item in payload.get("news", [])[:8]:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            published_at = ""
            published = item.get("providerPublishTime")
            if published:
                try:
                    published_dt = datetime.fromtimestamp(int(published), tz=timezone.utc)
                    published_at = published_dt.isoformat()
                    freshness_dt = freshness_dt or published_dt
                except (TypeError, ValueError, OSError):
                    pass
            publisher = item.get("publisher") or "Yahoo Finance"
            rich_articles.append(_article(
                item.get("title"),
                source=publisher,
                url=item.get("link", ""),
                image=_display_image(publisher, item.get("thumbnail")),
                summary=item.get("summary") or item.get("description") or "",
                published_at=published_at,
            ))
            if len(rich_articles) >= 5:
                break

        headlines = [article["headline"] for article in rich_articles]
        if not headlines:
            return None
        cats = parse_catalyst_categories(headlines)
        return CatalystNews(
            headlines=headlines,
            summary=headlines[0],
            categories=cats,
            freshness_minutes=_minutes_ago(freshness_dt),
            source="yahoo_search",
            articles=tuple(rich_articles),
        )
    except Exception as exc:
        logger.debug("Yahoo search failed for %s: %s", ticker, exc)
        return None


def _try_yfinance(ticker: str) -> CatalystNews | None:
    """Fallback: yfinance .news (no key required).

    yfinance changed its news payload structure around v0.2.50:
      Old format: {"title": "...", "providerPublishTime": <unix int>, ...}
      New format: {"id": "...", "content": {"title": "...", "pubDate": "...", ...}}

    We check both paths so the code is compatible with any installed version.
    """
    try:
        import yfinance as yf
        with _silence_yf():
            t    = yf.Ticker(ticker)
            news = t.news
        if not news:
            logger.info("yfinance  ticker=%s  news list is empty", ticker)
            return None

        rich_articles = []
        freshness_dt = None

        for item in news[:10]:  # scan up to 10 to get 5 real titles
            if not isinstance(item, dict):
                continue

            # New format: content nested dict
            content = item.get("content")
            if isinstance(content, dict):
                title = content.get("title", "").strip()
                provider = content.get("provider") or {}
                pub = content.get("pubDate") or content.get("displayTime")
                publisher = (provider.get("displayName") if isinstance(provider, dict) else provider) or "Yahoo Finance"
                article = _article(
                    title,
                    source=publisher,
                    url=_nested_url(content.get("canonicalUrl") or content.get("clickThroughUrl")),
                    image=_display_image(publisher, content.get("thumbnail")),
                    summary=content.get("summary") or content.get("description") or "",
                    published_at=pub or "",
                )
                if title and not freshness_dt:
                    if pub:
                        try:
                            freshness_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                        except Exception:
                            pass
            else:
                # Old format: flat dict
                title = item.get("title", "").strip()
                pt = item.get("providerPublishTime")
                published_at = ""
                if pt:
                    try:
                        published_at = datetime.fromtimestamp(int(pt), tz=timezone.utc).isoformat()
                    except Exception:
                        pass
                publisher = item.get("publisher") or "Yahoo Finance"
                article = _article(
                    title,
                    source=publisher,
                    url=item.get("link", ""),
                    image=_display_image(publisher, item.get("thumbnail")),
                    summary=item.get("summary") or item.get("description") or "",
                    published_at=published_at,
                )
                if title and not freshness_dt:
                    if pt:
                        try:
                            freshness_dt = datetime.fromtimestamp(int(pt), tz=timezone.utc)
                        except Exception:
                            pass

            if title:
                rich_articles.append(article)
            if len(rich_articles) >= 5:
                break

        headlines = [a["headline"] for a in rich_articles]
        if not headlines:
            logger.warning(
                "yfinance  ticker=%s  news items present (%d) but no titles extracted — "
                "structure may have changed again. First item keys: %s",
                ticker, len(news), list(news[0].keys()) if news else [],
            )
            return None

        freshness = _minutes_ago(freshness_dt)
        cats = parse_catalyst_categories(headlines)
        logger.info("yfinance  ticker=%s  headlines=%d  categories=%s", ticker, len(headlines), cats)
        return CatalystNews(
            headlines=headlines, summary=headlines[0],
            categories=cats, freshness_minutes=freshness, source="yfinance",
            articles=tuple(rich_articles),
        )
    except Exception as exc:
        logger.warning("yfinance news failed for %s: %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Benzinga / MarketWatch RSS fallback (no API key needed)
# ---------------------------------------------------------------------------

def _try_rss(ticker: str) -> CatalystNews | None:
    """
    Fetch news via Yahoo Finance RSS (no API key, different IP path than the YF API).
    Falls back to MarketWatch RSS if Yahoo RSS returns nothing.
    This is a last-resort free fallback for when all keyed sources fail.
    """
    import urllib.parse as _urlparse
    import urllib.request as _urlreq
    from email.utils import parsedate_to_datetime
    import html
    import re
    import xml.etree.ElementTree as ET

    _RSS_SOURCES = [
        # Google News is generally reachable from hosted servers even when
        # finance-specific Yahoo endpoints rate-limit the same IP.
        (
            "https://news.google.com/rss/search?"
            + _urlparse.urlencode({
                "q": f"{ticker} stock when:3d",
                "hl": "en-US",
                "gl": "US",
                "ceid": "US:en",
            }),
            "google_news",
        ),
        # Yahoo Finance RSS — different endpoint from the blocked v8 API
        (f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
         "yahoo_rss"),
        # Seeking Alpha ticker RSS (free, limited but often works)
        (f"https://seekingalpha.com/symbol/{ticker}.xml",
         "seeking_alpha"),
    ]

    _hdrs = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"),
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }

    for url, source_name in _RSS_SOURCES:
        try:
            req  = _urlreq.Request(url, headers=_hdrs)
            with _urlreq.urlopen(req, timeout=4) as resp:
                raw = resp.read().decode("utf-8", errors="replace")

            root = ET.fromstring(raw)
            nodes = root.findall(".//item") or root.findall(".//{*}entry")
            rich_articles = []
            freshness_dt = None
            for node in nodes[:8]:
                def _find(name: str):
                    found = node.find(name)
                    return found if found is not None else node.find(f"{{*}}{name}")

                def _text(name: str) -> str:
                    found = _find(name)
                    return (found.text or "").strip() if found is not None else ""

                title = html.unescape(re.sub(r"<[^>]+>", "", _text("title"))).strip()
                if not title or len(title) <= 10:
                    continue
                link_node = _find("link")
                link = _text("link") or (link_node.get("href", "") if link_node is not None else "")
                description = html.unescape(re.sub(r"<[^>]+>", " ", _text("description") or _text("summary")))
                description = re.sub(r"\s+", " ", description).strip()
                image = ""
                for child in node.iter():
                    tag = child.tag.lower() if isinstance(child.tag, str) else ""
                    if tag.endswith(("thumbnail", "content", "enclosure")) and child.get("url"):
                        mime = (child.get("type") or "").lower()
                        if "image" in mime or tag.endswith("thumbnail"):
                            image = child.get("url", "")
                            break
                published_at = _text("pubDate") or _text("published") or _text("updated")
                if published_at and freshness_dt is None:
                    try:
                        freshness_dt = parsedate_to_datetime(published_at)
                    except (TypeError, ValueError, OverflowError):
                        try:
                            freshness_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                        except (TypeError, ValueError):
                            pass
                provider = _text("source")
                default_provider = {
                    "google_news": "Google News",
                    "yahoo_rss": "Yahoo Finance",
                    "seeking_alpha": "Seeking Alpha",
                }.get(source_name, source_name.replace("_", " ").title())
                rich_articles.append(_article(
                    title,
                    source=provider or default_provider,
                    url=link,
                    image=image,
                    summary=description,
                    published_at=published_at,
                ))
                if len(rich_articles) >= 5:
                    break

            headlines = [a["headline"] for a in rich_articles]

            if headlines:
                cats = parse_catalyst_categories(headlines)
                logger.info("rss  ticker=%s  source=%s  headlines=%d", ticker, source_name, len(headlines))
                return CatalystNews(
                    headlines=headlines,
                    summary=headlines[0],
                    categories=cats,
                    freshness_minutes=_minutes_ago(freshness_dt),
                    source=source_name,
                    articles=tuple(rich_articles),
                )
        except Exception as exc:
            logger.debug("rss  ticker=%s  source=%s  error: %s", ticker, source_name, exc)

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
    articles=(),
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_headlines(ticker: str) -> CatalystNews:
    """
    Fetch catalyst headlines using the best available source.
    Priority: Finnhub → NewsAPI → Polygon → Yahoo search → RSS → yfinance.
    Never raises — always returns a CatalystNews.
    """
    # RSS is attempted before yfinance because it is a bounded HTTP request;
    # yfinance can hang behind Yahoo rate limits on hosted environments.
    for fn in (
        _try_finnhub,
        _try_newsapi,
        _try_polygon,
        _try_yahoo_search,
        _try_rss,
        _try_yfinance,
    ):
        result = fn(ticker)
        if result is not None:
            logger.info("fetch_headlines  ticker=%s  source=%s  headlines=%d",
                        ticker, result.source, len(result.headlines))
            return result
    logger.warning("fetch_headlines  ticker=%s  ALL sources failed — returning empty fallback", ticker)
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
        now = datetime.now(fetched.tzinfo) if fetched.tzinfo else datetime.now()
        age_min = (now - fetched).total_seconds() / 60
        return age_min >= HEADLINE_REFRESH_MINUTES
    except Exception:
        return True
