"""
intel_engine.py — Market Intelligence Engine for Rockkstaar Trade Assistant.

Provides four data feeds and a Telegram alert system:
  1. Market-moving news   — per-ticker headlines classified by impact
  2. Earnings calendar    — today / tomorrow / this week buckets
  3. Stock split tracker  — upcoming and recently announced splits
  4. Economic calendar    — high-impact macro events (CPI, FOMC, NFP …)

Data sources:
  News:       Finnhub / NewsAPI / Polygon / yfinance (via news_fetcher.py)
  Earnings:   yfinance Ticker.calendar
  Splits:     Finnhub splits endpoint → yfinance fallback
  Economic:   Finnhub calendar/economic endpoint → static fallback

Public API:
  fetch_market_news(tickers=None)      → list[dict]
  classify_news_impact(headline, cats) → (impact_str, reason_str)
  fetch_earnings_calendar(tickers=None)→ dict  {today, tomorrow, this_week}
  fetch_stock_splits(tickers=None)     → list[dict]
  fetch_economic_calendar()            → list[dict]
  should_send_alert(key, window_min)   → bool
  send_intel_alert(msg, priority)      → None
  check_and_send_intel_alerts()        → list[dict]
  get_intel_summary()                  → dict  (used by /api/intel)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, date
from typing import Optional

logger = logging.getLogger(__name__)


# ── ET helpers ────────────────────────────────────────────────────────────────

def _et_now() -> datetime:
    try:
        import zoneinfo
        return datetime.now(zoneinfo.ZoneInfo("America/New_York"))
    except Exception:
        from datetime import timezone
        return datetime.now(timezone(timedelta(hours=-4)))


def _today_et() -> date:
    return _et_now().date()


def _date_label(d: date, today: date) -> str:
    days = (d - today).days
    if days == 0:
        return "Today"
    if days == 1:
        return "Tomorrow"
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    mon_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{day_names[d.weekday()]} {mon_names[d.month - 1]} {d.day}"


def _format_freshness(minutes: Optional[int]) -> str:
    if minutes is None:
        return "—"
    if minutes < 2:
        return "Just now"
    if minutes < 60:
        return f"{minutes}m ago"
    h = minutes // 60
    if h < 24:
        return f"{h}h ago"
    return f"{h // 24}d ago"


# ── Cache ─────────────────────────────────────────────────────────────────────

_cache_lock = threading.Lock()
_cache: dict = {}

_CACHE_TTL: dict[str, int] = {
    "market_news": 900,    # 15 min — news changes often
    "earnings":    21600,  # 6 hrs  — dates change rarely
    "splits":      86400,  # 24 hrs — very stable
    "economic":    3600,   # 1 hr   — stable but refresh daily
}


def _cget(key: str):
    ns = key.split(":")[0]
    ttl = _CACHE_TTL.get(ns, 900)
    with _cache_lock:
        e = _cache.get(key)
        if e and (_time.monotonic() - e["ts"]) < ttl:
            return e["data"]
    return None


def _cset(key: str, data) -> None:
    with _cache_lock:
        _cache[key] = {"data": data, "ts": _time.monotonic()}


def clear_intel_cache() -> None:
    with _cache_lock:
        _cache.clear()


# ── Alert dedup ───────────────────────────────────────────────────────────────

_dedup_lock = threading.Lock()
_dedup: dict[str, float] = {}  # key → monotonic timestamp of last send


def should_send_alert(key: str, window_minutes: int = 1440) -> bool:
    """True if this key hasn't fired within window_minutes (default 24 h).
    Uses a date suffix for daily-scope keys so it resets at midnight automatically.
    """
    now = _time.monotonic()
    with _dedup_lock:
        last = _dedup.get(key, 0.0)
        if now - last < window_minutes * 60:
            return False
        _dedup[key] = now
    return True


def _daily_key(ticker: str, atype: str) -> str:
    """Key that naturally expires at midnight ET (date is embedded)."""
    return f"{ticker}:{atype}:{_today_et().isoformat()}"


def _window_key(ticker: str, atype: str, window_minutes: int = 15) -> str:
    """Key bucketed by time window (for scanner-type throttling)."""
    bucket = int(_time.time() / (window_minutes * 60))
    return f"{ticker}:{atype}:{bucket}"


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_intel_alert(msg: str, priority: str = "HIGH") -> None:
    """Send a Telegram message.  Requires TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID."""
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.debug("intel_alert skipped (no Telegram creds): %s", msg[:60])
        return
    try:
        import requests as _req
        _req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=6,
        )
        logger.info("intel_alert sent [%s]: %s", priority, msg[:80])
    except Exception as e:
        logger.warning("intel_alert send failed: %s", e)


# ── Impact classification ─────────────────────────────────────────────────────

_IMPACT_ORDER = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}

_CAT_IMPACT: dict[str, str] = {
    "acquisition_merger": "CRITICAL",
    "fda":                "CRITICAL",
    "earnings_beat":      "CRITICAL",
    "earnings_miss":      "HIGH",
    "guidance_raise":     "HIGH",
    "guidance_cut":       "HIGH",
    "analyst_upgrade":    "HIGH",
    "analyst_downgrade":  "MEDIUM",
    "government_contract":"HIGH",
    "partnership_deal":   "MEDIUM",
    "product_launch":     "LOW",
    "sec_legal":          "HIGH",
}

_CAT_REASON: dict[str, str] = {
    "acquisition_merger": "M&A activity — potential binary price move",
    "fda":                "FDA catalyst — binary event, size accordingly",
    "earnings_beat":      "Earnings beat — momentum fuel, watch continuation",
    "earnings_miss":      "Earnings miss — gap-down risk, avoid fresh entries",
    "guidance_raise":     "Guidance raised — fundamental strength confirmed",
    "guidance_cut":       "Guidance cut — forward expectations lowered",
    "analyst_upgrade":    "Analyst upgrade — institutional sentiment turning bullish",
    "analyst_downgrade":  "Analyst downgrade — institutional distribution risk",
    "government_contract":"Gov't contract — significant revenue catalyst",
    "partnership_deal":   "Partnership deal — business expansion signal",
    "product_launch":     "Product launch — watch for market reaction",
    "sec_legal":          "SEC/legal action — high uncertainty, reduce exposure",
}

# Additional keyword-level patterns not in news_fetcher categories
_EXTRA_PATTERNS: list[tuple[str, str, str]] = [
    ("ceo resign",              "CRITICAL", "CEO departure — leadership uncertainty, expect volatility"),
    ("ceo fired",               "CRITICAL", "CEO fired — leadership crisis, high volatility likely"),
    ("ceo replaced",            "HIGH",     "CEO transition — watch for new strategy guidance"),
    ("ceo step",                "HIGH",     "CEO stepping down — watch for successor announcement"),
    ("chapter 11",              "CRITICAL", "Bankruptcy filing — extreme downside risk"),
    ("bankruptcy",              "CRITICAL", "Bankruptcy risk — stay out until clarity"),
    ("ai deal",                 "HIGH",     "AI deal — high-growth narrative catalyst"),
    ("artificial intelligence deal", "HIGH","AI deal — high-growth narrative catalyst"),
    ("data center deal",        "HIGH",     "Data center expansion — AI infrastructure catalyst"),
    ("stock split",             "HIGH",     "Split announced — bullish sentiment signal"),
    ("share buyback",           "MEDIUM",   "Buyback — management confidence signal"),
    ("buyback program",         "MEDIUM",   "Buyback — capital return to shareholders"),
    ("special dividend",        "MEDIUM",   "Special dividend — one-time capital return"),
    ("major contract",          "HIGH",     "Large contract win — revenue catalyst"),
    ("billion-dollar contract", "HIGH",     "Billion-dollar contract — major revenue event"),
    ("strategic review",        "HIGH",     "Strategic review — potential M&A or restructuring"),
    ("going private",           "CRITICAL", "Going-private transaction — potential premium bid"),
    ("short seller",            "HIGH",     "Short seller report — expect sharp volatile move"),
    ("whistle",                 "HIGH",     "Whistleblower report — legal/regulatory risk"),
]


def classify_news_impact(headline: str, categories: list[str]) -> tuple[str, str]:
    """Return (impact_level, reason_string) for a headline + its categories."""
    hl_lower = headline.lower()
    best_impact = "LOW"
    best_reason = "General market news"

    for cat in categories:
        imp = _CAT_IMPACT.get(cat, "LOW")
        if _IMPACT_ORDER.get(imp, 0) > _IMPACT_ORDER.get(best_impact, 0):
            best_impact = imp
            best_reason = _CAT_REASON.get(cat, best_reason)

    for pattern, imp, reason in _EXTRA_PATTERNS:
        if pattern in hl_lower:
            if _IMPACT_ORDER.get(imp, 0) > _IMPACT_ORDER.get(best_impact, 0):
                best_impact = imp
                best_reason = reason

    return best_impact, best_reason


# ── Ticker universe ───────────────────────────────────────────────────────────

# Mirrored from scanner.py — curated high-activity universe
SCANNER_UNIVERSE: list[str] = [
    "NVDA", "AMD", "TSLA", "AAPL", "META", "GOOGL", "AMZN", "MSFT",
    "PLTR", "SOFI", "IONQ", "RGTI", "QUBT", "JOBY", "ACHR", "RKLB",
    "LUNR", "OKLO", "SMR", "NNE",
]


def _get_watchlist_tickers() -> list[str]:
    try:
        from database import get_all_stock_data
        stocks = get_all_stock_data()
        return list({s["ticker"] for s in stocks if s.get("ticker")})
    except Exception as e:
        logger.warning("intel: failed to load watchlist tickers: %s", e)
        return []


def _merged_universe(extra: Optional[list[str]] = None) -> list[str]:
    wl = _get_watchlist_tickers()
    base = list(dict.fromkeys(wl + SCANNER_UNIVERSE))
    if extra:
        base = list(dict.fromkeys(base + extra))
    return base[:30]   # hard cap to protect rate limits


# ── Market News ───────────────────────────────────────────────────────────────

def fetch_market_news(tickers: Optional[list[str]] = None) -> list[dict]:
    """
    Fetch and classify news for the watchlist + scanner universe.
    Returns MEDIUM+ impact items only, sorted CRITICAL → HIGH → MEDIUM.
    Cached for 15 minutes.
    """
    cached = _cget("market_news")
    if cached is not None:
        return cached

    all_tickers = tickers if tickers is not None else _merged_universe()
    wl_set = set(_get_watchlist_tickers())

    from news_fetcher import fetch_headlines as _fetch_hl

    results: list[dict] = []

    def _fetch_one(ticker: str) -> list[dict]:
        try:
            news = _fetch_hl(ticker)
            items = []
            for headline in news.headlines[:3]:
                impact, reason = classify_news_impact(headline, news.categories)
                if impact == "LOW":
                    continue
                items.append({
                    "ticker":       ticker,
                    "headline":     headline,
                    "source":       news.source.capitalize(),
                    "impact":       impact,
                    "reason":       reason,
                    "time":         _format_freshness(news.freshness_minutes),
                    "on_watchlist": ticker in wl_set,
                })
            return items
        except Exception as e:
            logger.debug("intel/news %s: %s", ticker, e)
            return []

    pool = ThreadPoolExecutor(max_workers=4)
    futs = [pool.submit(_fetch_one, t) for t in all_tickers]
    pool.shutdown(wait=False)  # don't block when as_completed times out
    try:
        for fut in as_completed(futs, timeout=20):
            try:
                results.extend(fut.result())
            except Exception:
                pass
    except Exception:
        pass

    # Sort by impact descending
    results.sort(key=lambda x: -_IMPACT_ORDER.get(x["impact"], 0))

    # Deduplicate by headline prefix (60 chars)
    seen: set[str] = set()
    unique: list[dict] = []
    for item in results:
        key = item["headline"][:60].lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)

    _cset("market_news", unique)
    logger.info("intel/news: %d items (MEDIUM+) from %d tickers", len(unique), len(all_tickers))
    return unique


# ── Earnings Calendar ─────────────────────────────────────────────────────────

def fetch_earnings_calendar(tickers: Optional[list[str]] = None) -> dict:
    """
    Returns {'today': [...], 'tomorrow': [...], 'this_week': [...]}.
    Each item: {ticker, date, date_label, time_label, on_watchlist, days_away}.
    Cached for 6 hours.
    """
    cached = _cget("earnings")
    if cached is not None:
        return cached

    all_tickers = tickers if tickers is not None else _merged_universe()
    wl_set      = set(_get_watchlist_tickers())
    today       = _today_et()

    buckets: dict[str, list] = {"today": [], "tomorrow": [], "this_week": []}

    def _fetch_cal(ticker: str) -> Optional[dict]:
        try:
            import yfinance as yf
            cal = yf.Ticker(ticker).calendar
            if cal is None:
                return None
            # yfinance returns dict or DataFrame depending on version
            if hasattr(cal, "to_dict"):
                cal = cal.iloc[:, 0].to_dict() if hasattr(cal, "iloc") else cal.to_dict()
            if not cal:
                return None
            ed = cal.get("Earnings Date") or cal.get("earningsDate")
            if ed is None:
                return None
            if isinstance(ed, (list, tuple)):
                ed = ed[0] if ed else None
            if ed is None:
                return None
            if hasattr(ed, "date"):
                earn_date = ed.date()
            else:
                earn_date = datetime.strptime(str(ed)[:10], "%Y-%m-%d").date()
            days_away = (earn_date - today).days
            if days_away < -1 or days_away > 7:
                return None
            return {
                "ticker":       ticker,
                "date":         earn_date.isoformat(),
                "date_label":   _date_label(earn_date, today),
                "time_label":   "TBD",
                "on_watchlist": ticker in wl_set,
                "days_away":    days_away,
            }
        except Exception as e:
            logger.debug("intel/earnings %s: %s", ticker, e)
            return None

    pool = ThreadPoolExecutor(max_workers=5)
    futs = [pool.submit(_fetch_cal, t) for t in all_tickers]
    pool.shutdown(wait=False)  # don't block when as_completed times out
    try:
        for fut in as_completed(futs, timeout=25):
            try:
                item = fut.result()
                if not item:
                    continue
                days = item["days_away"]
                if days == 0:
                    buckets["today"].append(item)
                elif days == 1:
                    buckets["tomorrow"].append(item)
                elif 2 <= days <= 7:
                    buckets["this_week"].append(item)
            except Exception:
                pass
    except Exception:
        pass

    for k in buckets:
        buckets[k].sort(key=lambda x: (not x["on_watchlist"], x["days_away"]))

    _cset("earnings", buckets)
    total = sum(len(v) for v in buckets.values())
    logger.info("intel/earnings: today=%d tomorrow=%d week=%d",
                len(buckets["today"]), len(buckets["tomorrow"]), len(buckets["this_week"]))
    return buckets


# ── Stock Split Tracker ───────────────────────────────────────────────────────

def fetch_stock_splits(tickers: Optional[list[str]] = None) -> list[dict]:
    """
    Returns upcoming + recently announced splits.
    Tries Finnhub splits endpoint first; falls back to yfinance historical data.
    Cached for 24 hours.
    """
    cached = _cget("splits")
    if cached is not None:
        return cached

    all_tickers = tickers if tickers is not None else _merged_universe()
    today       = _today_et()
    results: list[dict] = []

    finnhub_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if finnhub_key:
        results = _splits_from_finnhub(all_tickers, finnhub_key, today)

    if not results:
        results = _splits_from_yfinance(all_tickers, today)

    results.sort(key=lambda x: x.get("days_away", 0))
    _cset("splits", results)
    logger.info("intel/splits: %d upcoming/recent splits found", len(results))
    return results


def _splits_from_finnhub(tickers: list[str], api_key: str, today: date) -> list[dict]:
    import urllib.request
    from_d = today.isoformat()
    to_d   = (today + timedelta(days=60)).isoformat()
    results: list[dict] = []
    for ticker in tickers[:20]:
        try:
            url = (
                f"https://finnhub.io/api/v1/stock/split"
                f"?symbol={ticker}&from={from_d}&to={to_d}&token={api_key}"
            )
            with urllib.request.urlopen(url, timeout=4) as resp:
                data = json.loads(resp.read().decode())
            for s in (data or []):
                eff_str = (s.get("date") or "")[:10]
                try:
                    ed = datetime.strptime(eff_str, "%Y-%m-%d").date()
                except Exception:
                    continue
                days_away  = (ed - today).days
                from_f     = s.get("fromFactor", 1) or 1
                to_f       = s.get("toFactor", 1) or 1
                split_type = "Forward" if to_f > from_f else "Reverse"
                ratio_str  = f"{int(from_f)}:{int(to_f)}"
                results.append({
                    "ticker":    ticker,
                    "ratio":     ratio_str,
                    "type":      split_type,
                    "eff_date":  eff_str,
                    "days_away": days_away,
                    "is_new":    0 <= days_away <= 7,
                    "status":    _split_status(days_away),
                    "source":    "finnhub",
                })
        except Exception as e:
            logger.debug("intel/splits finnhub %s: %s", ticker, e)
    return results


def _splits_from_yfinance(tickers: list[str], today: date) -> list[dict]:
    lookback  = today - timedelta(days=30)
    lookahead = today + timedelta(days=60)
    results: list[dict] = []
    try:
        import yfinance as yf
        for ticker in tickers[:20]:
            try:
                splits = yf.Ticker(ticker).splits
                if splits is None or len(splits) == 0:
                    continue
                for ts, ratio in splits.items():
                    if hasattr(ts, "date"):
                        sd = ts.date()
                    else:
                        try:
                            sd = datetime.strptime(str(ts)[:10], "%Y-%m-%d").date()
                        except Exception:
                            continue
                    if sd < lookback or sd > lookahead:
                        continue
                    days_away  = (sd - today).days
                    split_type = "Forward" if ratio > 1 else "Reverse"
                    if ratio > 1:
                        ratio_str = f"{int(ratio)}:1"
                    elif ratio > 0:
                        ratio_str = f"1:{int(round(1 / ratio))}"
                    else:
                        ratio_str = "?"
                    results.append({
                        "ticker":    ticker,
                        "ratio":     ratio_str,
                        "type":      split_type,
                        "eff_date":  sd.isoformat(),
                        "days_away": days_away,
                        "is_new":    0 <= days_away <= 7,
                        "status":    _split_status(days_away),
                        "source":    "yfinance",
                    })
            except Exception:
                pass
    except Exception as e:
        logger.debug("intel/splits yfinance: %s", e)
    return results


def _split_status(days_away: int) -> str:
    if days_away < 0:
        return "Recent"
    if days_away == 0:
        return "Today!"
    if days_away <= 7:
        return f"In {days_away}d"
    return "Upcoming"


# ── Economic Calendar ─────────────────────────────────────────────────────────

_ECON_REASONS: dict[str, str] = {
    "cpi":               "CPI release — market moves hard on surprise. Reduce size.",
    "ppi":               "PPI release — producer inflation leads CPI. Watch trend.",
    "fomc":              "Fed decision — rate move possible. No new positions day-of.",
    "fed speak":         "Fed commentary — rate expectations can shift fast.",
    "federal reserve":   "Federal Reserve event — watch for policy signals.",
    "nfp":               "Non-Farm Payrolls — macro risk-on/off trigger. Wait for reaction.",
    "payroll":           "Jobs data — wait for first 15-min candle before trading.",
    "gdp":               "GDP release — broad economic health. Sector rotations likely.",
    "unemployment":      "Unemployment data — weekly jobs barometer.",
    "consumer price":    "Inflation data — direct FOMC input. Volatile open likely.",
    "producer price":    "Producer inflation — tracks pricing power upstream.",
    "consumer sentiment":"Consumer confidence — spending outlook, sector mover.",
    "retail sales":      "Retail sales — consumer spending direct read.",
    "pce":               "PCE inflation — Fed's preferred inflation gauge. High impact.",
    "ism":               "ISM survey — manufacturing/services health check.",
    "housing":           "Housing data — rate-sensitive sector indicator.",
    "default":           "High-impact event — reduce size, wait for market reaction.",
}


def _econ_reason(event_name: str) -> str:
    en = event_name.lower()
    for key, reason in _ECON_REASONS.items():
        if key in en:
            return reason
    return _ECON_REASONS["default"]


def fetch_economic_calendar() -> list[dict]:
    """
    Fetch high/medium-impact US economic events for the next 14 days.
    Uses Finnhub if FINNHUB_API_KEY is set, otherwise static fallback.
    Cached for 1 hour.
    """
    cached = _cget("economic")
    if cached is not None:
        return cached

    events = _econ_from_finnhub()
    if not events:
        events = _econ_static_fallback()

    _cset("economic", events)
    logger.info("intel/econ: %d events in next 14 days", len(events))
    return events


def _econ_from_finnhub() -> list[dict]:
    api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not api_key:
        return []
    try:
        import urllib.request
        today  = _today_et()
        from_d = today.isoformat()
        to_d   = (today + timedelta(days=14)).isoformat()
        url = (
            f"https://finnhub.io/api/v1/calendar/economic"
            f"?from={from_d}&to={to_d}&token={api_key}"
        )
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        raw = data.get("economicCalendar", [])
        events: list[dict] = []
        for e in raw:
            if (e.get("country") or "US").upper() != "US":
                continue
            impact = (e.get("impact") or "").lower()
            if impact not in ("high", "medium"):
                continue
            event_name = e.get("event") or "Economic Event"
            raw_time   = e.get("time") or ""
            date_str   = raw_time[:10]
            try:
                dt        = datetime.strptime(raw_time[:16], "%Y-%m-%d %H:%M")
                date_lbl  = f"{_month_abbr(dt.month)} {dt.day}"
                time_lbl  = _format_time_12h(dt.hour, dt.minute)
                days_away = (dt.date() - today).days
            except Exception:
                date_lbl  = date_str
                time_lbl  = "TBD"
                try:
                    days_away = (datetime.strptime(date_str, "%Y-%m-%d").date() - today).days
                except Exception:
                    days_away = 99
            events.append({
                "event":      event_name,
                "date":       date_str,
                "date_label": date_lbl,
                "time":       time_lbl,
                "impact":     impact.upper(),
                "reason":     _econ_reason(event_name),
                "days_away":  days_away,
                "is_today":   days_away == 0,
            })
        events.sort(key=lambda x: x["days_away"])
        return events
    except Exception as e:
        logger.warning("intel/econ finnhub: %s", e)
        return []


# Static fallback calendar — update each quarter with real dates
_STATIC_ECON: list[tuple] = [
    # (date_str, time_str, event_name, impact)
    ("2026-05-13", "8:30 AM ET",  "Consumer Price Index (CPI)",     "HIGH"),
    ("2026-05-13", "8:30 AM ET",  "Core CPI (MoM)",                 "HIGH"),
    ("2026-05-14", "8:30 AM ET",  "Producer Price Index (PPI)",     "HIGH"),
    ("2026-05-14", "8:30 AM ET",  "Core PPI (MoM)",                 "HIGH"),
    ("2026-05-15", "8:30 AM ET",  "Initial Jobless Claims",         "MEDIUM"),
    ("2026-05-15", "8:30 AM ET",  "Retail Sales (MoM)",             "HIGH"),
    ("2026-05-16", "10:00 AM ET", "Consumer Sentiment (Michigan)",  "MEDIUM"),
    ("2026-05-19", "TBD",         "Fed Speaker Events",             "MEDIUM"),
    ("2026-05-20", "8:30 AM ET",  "Housing Starts",                 "MEDIUM"),
    ("2026-05-22", "8:30 AM ET",  "Initial Jobless Claims",         "MEDIUM"),
    ("2026-05-29", "8:30 AM ET",  "GDP (Second Estimate)",          "HIGH"),
    ("2026-06-04", "8:30 AM ET",  "Non-Farm Payrolls (NFP)",        "HIGH"),
    ("2026-06-10", "8:30 AM ET",  "Consumer Price Index (CPI)",     "HIGH"),
    ("2026-06-17", "2:00 PM ET",  "FOMC Interest Rate Decision",    "HIGH"),
    ("2026-06-17", "2:30 PM ET",  "Fed Press Conference",           "HIGH"),
]


def _econ_static_fallback() -> list[dict]:
    today  = _today_et()
    events = []
    for date_str, time_str, event_name, impact in _STATIC_ECON:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            continue
        days_away = (d - today).days
        if days_away < 0 or days_away > 14:
            continue
        events.append({
            "event":      event_name,
            "date":       date_str,
            "date_label": f"{_month_abbr(d.month)} {d.day}",
            "time":       time_str,
            "impact":     impact,
            "reason":     _econ_reason(event_name),
            "days_away":  days_away,
            "is_today":   days_away == 0,
        })
    return events


def _month_abbr(m: int) -> str:
    return ["Jan","Feb","Mar","Apr","May","Jun",
            "Jul","Aug","Sep","Oct","Nov","Dec"][m - 1]


def _format_time_12h(hour: int, minute: int) -> str:
    suffix = "AM" if hour < 12 else "PM"
    h = hour % 12 or 12
    return f"{h}:{minute:02d} {suffix} ET"


# ── Intel Telegram Alerts ─────────────────────────────────────────────────────

def check_and_send_intel_alerts() -> list[dict]:
    """
    Evaluate all intel feeds and fire Telegram alerts for priority events.
    Call this from the background intel thread (every ~30 min market hours).
    Returns list of alerts sent for logging/debugging.

    Alert rules:
      CRITICAL news          → alert regardless of watchlist
      HIGH news on watchlist → alert
      Earnings today/tomorrow on watchlist → alert
      Split within 7 days    → alert
      HIGH econ event today  → one combined alert
    """
    sent: list[dict] = []
    wl_set = set(_get_watchlist_tickers())

    # 1. Market-moving news
    try:
        for item in fetch_market_news():
            impact  = item.get("impact", "LOW")
            ticker  = item.get("ticker", "")
            if impact == "CRITICAL":
                pass  # always alert
            elif impact == "HIGH" and ticker in wl_set:
                pass  # watchlist HIGH
            else:
                continue
            dk = _daily_key(ticker, f"news_{impact}")
            if should_send_alert(dk):
                emoji = "🚨" if impact == "CRITICAL" else "📣"
                msg = (
                    f"{emoji} <b>{impact} — {ticker}</b>\n"
                    f"{item['headline']}\n"
                    f"<i>{item['reason']}</i>"
                )
                send_intel_alert(msg, priority=impact)
                sent.append({"type": "news", "ticker": ticker, "impact": impact})
    except Exception as e:
        logger.warning("intel: news alert sweep failed: %s", e)

    # 2. Earnings on watchlist: today + tomorrow
    try:
        earnings = fetch_earnings_calendar()
        for bucket, label in [("today", "TODAY"), ("tomorrow", "TOMORROW")]:
            for item in earnings.get(bucket, []):
                if not item.get("on_watchlist"):
                    continue
                ticker = item["ticker"]
                dk = _daily_key(ticker, f"earnings_{bucket}")
                if should_send_alert(dk):
                    msg = (
                        f"📅 <b>EARNINGS {label} — {ticker}</b>\n"
                        f"Date: {item['date']} · Time: {item['time_label']}\n"
                        f"<i>Do not hold into earnings without a plan.</i>"
                    )
                    send_intel_alert(msg)
                    sent.append({"type": "earnings", "ticker": ticker, "bucket": bucket})
    except Exception as e:
        logger.warning("intel: earnings alert sweep failed: %s", e)

    # 3. Splits within 7 days
    try:
        for item in fetch_stock_splits():
            if not item.get("is_new"):
                continue
            ticker = item["ticker"]
            dk = _daily_key(ticker, "split_alert")
            if should_send_alert(dk):
                msg = (
                    f"📢 <b>SPLIT ALERT — {ticker}</b>\n"
                    f"{item['ratio']} {item['type']} split · Effective: {item['eff_date']}\n"
                    f"<i>Split within 7 days — watch for price adjustment and volume surge.</i>"
                )
                send_intel_alert(msg)
                sent.append({"type": "split", "ticker": ticker})
    except Exception as e:
        logger.warning("intel: splits alert sweep failed: %s", e)

    # 4. High-impact economic events today
    try:
        today_high = [e for e in fetch_economic_calendar()
                      if e.get("is_today") and e.get("impact") == "HIGH"]
        if today_high:
            dk = _daily_key("MACRO", "econ_today_high")
            if should_send_alert(dk):
                names = " | ".join(e["event"][:35] for e in today_high[:3])
                msg = (
                    f"⚠️ <b>HIGH-IMPACT MACRO DAY</b>\n"
                    f"{names}\n"
                    f"<i>Reduce position size. Wait for market reaction before entering.</i>"
                )
                send_intel_alert(msg)
                sent.append({"type": "economic", "events": [e["event"] for e in today_high]})
    except Exception as e:
        logger.warning("intel: econ alert sweep failed: %s", e)

    if sent:
        logger.info("intel: sent %d Telegram alerts", len(sent))
    return sent


# ── Convenience rollup for /api/intel ────────────────────────────────────────

def get_intel_summary() -> dict:
    """Full intel snapshot for the /api/intel endpoint.
    Always returns a complete dict — never raises.
    """
    errors: list[str] = []
    news:         list[dict] = []
    earnings:     dict       = {"today": [], "tomorrow": [], "this_week": []}
    splits:       list[dict] = []
    econ:         list[dict] = []
    alerts_sent:  list[dict] = []

    try:
        news = fetch_market_news()
    except Exception as e:
        errors.append(f"news: {e}")
        logger.error("intel_summary/news: %s", e)

    try:
        earnings = fetch_earnings_calendar()
    except Exception as e:
        errors.append(f"earnings: {e}")
        logger.error("intel_summary/earnings: %s", e)

    try:
        splits = fetch_stock_splits()
    except Exception as e:
        errors.append(f"splits: {e}")
        logger.error("intel_summary/splits: %s", e)

    try:
        econ = fetch_economic_calendar()
    except Exception as e:
        errors.append(f"econ: {e}")
        logger.error("intel_summary/econ: %s", e)

    try:
        alerts_sent = check_and_send_intel_alerts()
    except Exception as e:
        errors.append(f"alerts: {e}")
        logger.warning("intel_summary/alerts: %s", e)

    return {
        "ok":              len(errors) == 0,
        "errors":          errors,
        "last_updated":    _et_now().strftime("%I:%M %p ET"),
        "market_news":     news,
        "earnings":        earnings,
        "splits":          splits,
        "economic_events": econ,
        "alerts_sent":     alerts_sent,
    }
