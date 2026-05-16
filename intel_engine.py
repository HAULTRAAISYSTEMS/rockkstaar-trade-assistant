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
    "market_news": 600,    # 10 min  — news changes often
    "earnings":    21600,  # 6 hrs   — dates change rarely
    "splits":      43200,  # 12 hrs  — stable; long TTL protects rate limits
    "dividends":   43200,  # 12 hrs  — ex-dates don't change often
    "economic":    43200,  # 12 hrs  — stable; Finnhub econ is 1 call/day max
    "macro":       300,    # 5 min   — 10Y/DXY/VIX live feeds
}

# ── Finnhub rate-limit backoff ─────────────────────────────────────────────────
# When Finnhub returns HTTP 429 we freeze ALL Finnhub calls for 30 minutes.
# Every call site checks _fh_is_rate_limited() before touching urllib.

_fh_rl_lock  = threading.Lock()
_fh_rl_until = 0.0      # monotonic timestamp; 0 = not rate-limited
_FH_RL_SECS  = 1800     # 30-minute cooldown


def _fh_is_rate_limited() -> bool:
    return _time.monotonic() < _fh_rl_until


def _fh_set_rate_limited(secs: int = _FH_RL_SECS) -> None:
    global _fh_rl_until
    with _fh_rl_lock:
        _fh_rl_until = _time.monotonic() + secs
    logger.warning("Finnhub 429 — rate-limited, pausing all Finnhub calls for %d min", secs // 60)


def _fh_urlopen(url: str, timeout: int = 8):
    """urllib.request.urlopen wrapper that detects Finnhub 429 and sets the backoff."""
    import urllib.request
    import urllib.error
    if _fh_is_rate_limited():
        raise RuntimeError("Finnhub rate-limited — call skipped")
    try:
        return urllib.request.urlopen(url, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            _fh_set_rate_limited()
            raise RuntimeError(f"Finnhub 429 — rate limited for {_FH_RL_SECS // 60} min") from exc
        raise


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

_bg_refresh_lock = threading.Lock()
_bg_refreshing: bool = False


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


def trigger_background_refresh() -> None:
    """Start a daemon thread that populates all intel caches.
    No-op if a refresh is already running.
    Fetches econ first (fastest/static fallback) so partial data appears quickly.
    """
    global _bg_refreshing
    with _bg_refresh_lock:
        if _bg_refreshing:
            return
        _bg_refreshing = True

    def _run() -> None:
        global _bg_refreshing
        try:
            logger.info("intel bg-refresh: started (parallel)")

            def _call(fn, name):
                try:
                    fn()
                    logger.info("intel bg-refresh: %s complete", name)
                except Exception as exc:
                    logger.warning("intel bg-refresh/%s: %s", name, exc)

            tasks = [
                (fetch_economic_calendar, "econ"),
                (fetch_stock_splits,      "splits"),
                (fetch_earnings_calendar, "earnings"),
                (fetch_dividends,         "dividends"),
                (fetch_market_news,       "news"),
                (fetch_macro_environment, "macro"),
            ]
            with ThreadPoolExecutor(max_workers=5) as bg_ex:
                for fn, nm in tasks:
                    bg_ex.submit(_call, fn, nm)
            logger.info("intel bg-refresh: all complete")
        except Exception as exc:
            logger.warning("intel bg-refresh outer: %s", exc)
        finally:
            with _bg_refresh_lock:
                _bg_refreshing = False

    threading.Thread(target=_run, daemon=True, name="intel-bg-refresh").start()


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

# Major market tickers always included in news fetching — never dropped by the cap
_MAJOR_NEWS_TICKERS: list[str] = [
    "SPY", "QQQ", "NVDA", "TSLA", "AAPL", "MSFT", "AMZN",
    "META", "GOOGL", "AMD", "AVGO", "SMH",
]

# Mirrored from scanner.py — curated high-activity universe
SCANNER_UNIVERSE: list[str] = [
    "NVDA", "AMD", "TSLA", "AAPL", "META", "GOOGL", "AMZN", "MSFT",
    "PLTR", "SOFI", "IONQ", "RGTI", "QUBT", "JOBY", "ACHR", "RKLB",
    "LUNR", "OKLO", "SMR", "NNE",
    # Utilities / energy / power names — frequent earnings movers
    "CEG", "VST", "NRG", "GEV", "FSLR", "NEE", "SO",
    # Large-cap earnings movers
    "JPM", "BAC", "GS", "V", "MA", "HD", "WMT", "COST",
    "XOM", "CVX", "OXY", "LMT", "RTX",
]

# ── Manual earnings overrides ──────────────────────────────────────────────────
# Add entries here when APIs miss a known earnings date.
# These inject directly into the result regardless of what yfinance/Finnhub return.
# Format: ticker, date (YYYY-MM-DD), time_label (BMO/AMC/TBD), eps_est (float or None), source
EARNINGS_OVERRIDES: list[dict] = [
    {
        "ticker":     "CEG",
        "date":       "2026-05-11",
        "time_label": "BMO",
        "eps_est":    None,
        "source":     "manual override",
    },
    # Add more here as needed:
    # {"ticker": "XXXX", "date": "2026-MM-DD", "time_label": "BMO", "eps_est": None, "source": "manual override"},
]

# Static company names for the scanner universe — avoids slow .info API call
_COMPANY_NAMES: dict[str, str] = {
    "NVDA": "NVIDIA",              "AMD":  "Advanced Micro Devices",
    "TSLA": "Tesla",               "AAPL": "Apple",
    "META": "Meta Platforms",      "GOOGL": "Alphabet",
    "AMZN": "Amazon",              "MSFT": "Microsoft",
    "PLTR": "Palantir",            "SOFI": "SoFi Technologies",
    "IONQ": "IonQ",                "RGTI": "Rigetti Computing",
    "QUBT": "Quantum Computing",   "JOBY": "Joby Aviation",
    "ACHR": "Archer Aviation",     "RKLB": "Rocket Lab",
    "LUNR": "Intuitive Machines",  "OKLO": "Oklo",
    "SMR":  "NuScale Power",       "NNE":  "Nano Nuclear Energy",
    "CEG":  "Constellation Energy","VST":  "Vistra Energy",
    "NRG":  "NRG Energy",          "GEV":  "GE Vernova",
    "FSLR": "First Solar",         "NEE":  "NextEra Energy",
    "SO":   "Southern Company",    "JPM":  "JPMorgan Chase",
    "BAC":  "Bank of America",     "GS":   "Goldman Sachs",
    "V":    "Visa",                "MA":   "Mastercard",
    "HD":   "Home Depot",          "WMT":  "Walmart",
    "COST": "Costco",              "XOM":  "ExxonMobil",
    "CVX":  "Chevron",             "OXY":  "Occidental",
    "LMT":  "Lockheed Martin",     "RTX":  "RTX Corp",
}


def _company_name(ticker: str) -> str:
    return _COMPANY_NAMES.get(ticker.upper(), "")


def _get_watchlist_tickers() -> list[str]:
    try:
        from database import get_all_stock_data
        stocks = get_all_stock_data()
        return list({s["ticker"] for s in stocks if s.get("ticker")})
    except Exception as e:
        logger.warning("intel: failed to load watchlist tickers: %s", e)
        return []


def _merged_universe(extra: Optional[list[str]] = None) -> list[str]:
    """Universe for news fetching.
    Major market tickers always included first; remaining slots filled by watchlist
    then scanner universe. Hard-capped to protect Finnhub rate limits.
    """
    wl   = _get_watchlist_tickers()
    rest = list(dict.fromkeys(wl + SCANNER_UNIVERSE))
    if extra:
        rest = list(dict.fromkeys(rest + extra))
    # Major tickers always in — fill remaining slots up to 40
    remaining = [t for t in rest if t not in set(_MAJOR_NEWS_TICKERS)]
    combined  = list(dict.fromkeys(_MAJOR_NEWS_TICKERS + remaining))
    return combined[:40]


def _earnings_universe() -> list[str]:
    """Universe for earnings checks — larger cap, always includes override tickers."""
    wl = _get_watchlist_tickers()
    override_tickers = [ov["ticker"].upper() for ov in EARNINGS_OVERRIDES]
    base = list(dict.fromkeys(wl + override_tickers + SCANNER_UNIVERSE))
    return base[:60]   # earnings calendar calls are lighter than news fetches


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


# ── Macro Environment (10Y Yield / DXY / VIX / SPY / QQQ) ───────────────────

def fetch_macro_environment() -> dict:
    """Fetch 10Y Treasury, DXY, VIX, SPY/QQQ for the Intel market environment card.
    Cached 5 minutes. Returns an empty dict on failure so callers never crash.
    """
    cached = _cget("macro")
    if cached is not None:
        return cached

    try:
        from data_fetcher import compute_market_temperature
        mt = compute_market_temperature()
        result = {
            "yield_10y":         mt.get("yield_10y"),
            "yield_change_bps":  mt.get("yield_change_bps"),
            "yield_trend":       mt.get("yield_trend", "flat"),
            "yield_note":        mt.get("yield_note", "—"),
            "dxy_price":         mt.get("dxy_price"),
            "dxy_change_pct":    mt.get("dxy_change_pct"),
            "dxy_trend":         mt.get("dxy_trend", "flat"),
            "vix_level":         mt.get("vix_level"),
            "vix_direction":     mt.get("vix_direction", "flat"),
            "spy_price":         mt.get("spy_price"),
            "spy_pct_ema20":     mt.get("spy_pct_ema20"),
            "qqq_price":         mt.get("qqq_price"),
            "qqq_pct_ema20":     mt.get("qqq_pct_ema20"),
            "es_price":          mt.get("es_price"),
            "es_change_pct":     mt.get("es_change_pct"),
            "sectors":           mt.get("sectors", {}),
            "regime":            mt.get("regime"),
            "score":             mt.get("score"),
        }
        _cset("macro", result)
        logger.info(
            "intel/macro: yield=%.3f%%  dxy=%.2f  vix=%.1f",
            result["yield_10y"] or 0, result["dxy_price"] or 0, result["vix_level"] or 0,
        )
        return result
    except Exception as exc:
        logger.warning("fetch_macro_environment: %s", exc)
        return {}


# ── Earnings Calendar ─────────────────────────────────────────────────────────

def _earnings_from_finnhub(
    tickers: list[str],
    api_key: str,
    today: date,
    already_found: set,
) -> tuple[list[dict], list[str]]:
    """ONE bulk call to Finnhub /calendar/earnings (no symbol= filter).
    Returns (results, errors).  Filters locally by tickers_wanted to avoid
    N per-ticker requests that trigger 429s.
    """
    if _fh_is_rate_limited():
        return [], ["Finnhub rate-limited — earnings fallback skipped"]

    tickers_wanted = {t.upper() for t in tickers if t.upper() not in already_found}
    if not tickers_wanted:
        return [], []

    from_d = today.isoformat()
    to_d   = (today + timedelta(days=7)).isoformat()
    url = (
        f"https://finnhub.io/api/v1/calendar/earnings"
        f"?from={from_d}&to={to_d}&token={api_key}"
    )

    try:
        with _fh_urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except RuntimeError as exc:
        return [], [str(exc)]
    except Exception as exc:
        return [], [f"finnhub/earnings bulk: {exc}"]

    results: list[dict] = []
    for e in data.get("earningsCalendar", []):
        ticker = (e.get("symbol") or "").upper()
        if not ticker or ticker not in tickers_wanted:
            continue
        date_str = (e.get("date") or "")[:10]
        if not date_str:
            continue
        try:
            earn_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            continue
        days_away = (earn_date - today).days
        if days_away < 0 or days_away > 7:
            continue
        hour       = (e.get("hour") or "").lower()
        time_label = "BMO" if hour == "bmo" else "AMC" if hour == "amc" else "TBD"
        eps_est = None
        rev_est = None
        try:
            v = e.get("epsEstimate")
            if v: eps_est = round(float(v), 2)
        except Exception:
            pass
        try:
            v = e.get("revenueEstimate")
            if v: rev_est = int(v)
        except Exception:
            pass
        results.append({
            "ticker":       ticker,
            "company_name": _company_name(ticker),
            "date":         earn_date.isoformat(),
            "days_away":    days_away,
            "time_label":   time_label,
            "eps_est":      eps_est,
            "rev_est":      rev_est,
            "source":       "finnhub",
            "is_override":  False,
        })

    logger.info("intel/earnings finnhub bulk: %d found from %d wanted", len(results), len(tickers_wanted))
    return results, []


def fetch_earnings_calendar(tickers: Optional[list[str]] = None) -> dict:
    """
    Returns earnings buckets + debug metadata.
    Shape: {today, tomorrow, this_week, meta}.
    Each item: {ticker, date, date_label, time_label, on_watchlist, days_away,
                source, is_override}.

    Priority:
      1. EARNINGS_OVERRIDES  — manual entries, always injected
      2. yfinance calendar   — primary API
      3. Finnhub earnings    — fallback for tickers yfinance missed

    Cached for 6 hours.
    """
    cached = _cget("earnings")
    if cached is not None:
        return cached

    all_tickers = tickers if tickers is not None else _earnings_universe()
    wl_set      = set(_get_watchlist_tickers())
    today       = _today_et()

    buckets: dict[str, list] = {"today": [], "tomorrow": [], "this_week": []}
    meta: dict = {
        "tickers_checked":      len(all_tickers),
        "overrides_injected":   0,
        "yfinance_found":       0,
        "finnhub_found":        0,
        "earnings_errors":      [],
        "earnings_source_used": "overrides+yfinance",
    }

    def _bucket_item(item: dict) -> None:
        d = item["days_away"]
        if d == 0:
            buckets["today"].append(item)
        elif d == 1:
            buckets["tomorrow"].append(item)
        elif 2 <= d <= 7:
            buckets["this_week"].append(item)

    # ── 1. Manual overrides (always first) ──────────────────────────────────
    already_added: set[str] = set()
    for ov in EARNINGS_OVERRIDES:
        try:
            ov_date   = datetime.strptime(ov["date"][:10], "%Y-%m-%d").date()
            days_away = (ov_date - today).days
            if days_away < 0 or days_away > 7:
                continue
            _bucket_item({
                "ticker":       ov["ticker"].upper(),
                "company_name": _company_name(ov["ticker"].upper()),
                "date":         ov_date.isoformat(),
                "date_label":   _date_label(ov_date, today),
                "time_label":   ov.get("time_label", "TBD"),
                "on_watchlist": ov["ticker"].upper() in wl_set,
                "days_away":    days_away,
                "eps_est":      ov.get("eps_est"),
                "rev_est":      ov.get("rev_est"),
                "source":       ov.get("source", "manual override"),
                "is_override":  True,
            })
            already_added.add(ov["ticker"].upper())
            meta["overrides_injected"] += 1
        except Exception as exc:
            meta["earnings_errors"].append(f"override/{ov.get('ticker')}: {exc}")

    # ── 2. yfinance (primary API) ────────────────────────────────────────────
    yf_found:  set[str]  = set(already_added)
    yf_missed: list[str] = []

    def _fetch_cal(ticker: str) -> Optional[dict]:
        if ticker in already_added:
            return None
        try:
            import yfinance as yf
            tk = yf.Ticker(ticker)
            earn_date = None
            eps_est   = None
            rev_est   = None

            # ── Try .calendar (yfinance < 0.2.40, returns DataFrame or dict) ──
            try:
                cal = tk.calendar
                if cal is not None:
                    # DataFrame: index=field names, columns=consecutive dates
                    if hasattr(cal, "iloc"):
                        try:
                            cal = cal.iloc[:, 0].to_dict()
                        except Exception:
                            try:
                                cal = {k: (v[0] if isinstance(v, list) and v else v)
                                       for k, v in cal.to_dict(orient="list").items()}
                            except Exception:
                                cal = {}
                    if isinstance(cal, dict) and cal:
                        ed = (cal.get("Earnings Date") or cal.get("earningsDate")
                              or cal.get("Earnings Dates") or cal.get("earningsDates"))
                        if isinstance(ed, (list, tuple)):
                            ed = ed[0] if ed else None
                        if ed is not None:
                            try:
                                earn_date = ed.date() if hasattr(ed, "date") else \
                                            datetime.strptime(str(ed)[:10], "%Y-%m-%d").date()
                            except Exception:
                                pass
                        if earn_date is not None:
                            try:
                                ea = (cal.get("EPS Estimate") or cal.get("epsEstimate")
                                      or cal.get("Earnings Average") or cal.get("earningsAverage"))
                                if ea is not None:
                                    eps_est = round(float(ea), 2)
                            except Exception:
                                pass
                            try:
                                ra = (cal.get("Revenue Estimate") or cal.get("revenueEstimate")
                                      or cal.get("Revenue Average") or cal.get("revenueAverage"))
                                if ra is not None:
                                    rev_est = int(ra)
                            except Exception:
                                pass
            except Exception:
                pass

            # ── Fallback: .earnings_dates (yfinance 0.2.40+) ─────────────────
            if earn_date is None:
                try:
                    ed_df = tk.earnings_dates
                    if ed_df is not None and len(ed_df) > 0:
                        for idx in ed_df.index:
                            try:
                                d = idx.date() if hasattr(idx, "date") else \
                                    datetime.strptime(str(idx)[:10], "%Y-%m-%d").date()
                                if d >= today:
                                    earn_date = d
                                    break
                            except Exception:
                                continue
                except Exception:
                    pass

            if earn_date is None:
                return None
            days_away = (earn_date - today).days
            if days_away < 0 or days_away > 7:
                return None
            return {
                "ticker":       ticker,
                "company_name": _company_name(ticker),
                "date":         earn_date.isoformat(),
                "date_label":   _date_label(earn_date, today),
                "time_label":   "TBD",
                "on_watchlist": ticker in wl_set,
                "days_away":    days_away,
                "eps_est":      eps_est,
                "rev_est":      rev_est,
                "source":       "yfinance",
                "is_override":  False,
            }
        except Exception as e:
            logger.debug("intel/earnings yf %s: %s", ticker, e)
            return None

    pool = ThreadPoolExecutor(max_workers=8)
    futs = {pool.submit(_fetch_cal, t): t for t in all_tickers}
    pool.shutdown(wait=False)
    try:
        for fut in as_completed(futs, timeout=25):
            t = futs[fut]
            try:
                item = fut.result()
                if item:
                    _bucket_item(item)
                    yf_found.add(t)
                    meta["yfinance_found"] += 1
                elif t not in already_added:
                    yf_missed.append(t)
            except Exception:
                yf_missed.append(t)
    except Exception:
        pass

    # ── 3. Finnhub fallback for tickers yfinance missed ─────────────────────
    finnhub_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if finnhub_key and yf_missed:
        meta["earnings_source_used"] = "overrides+yfinance+finnhub"
        fh_items, fh_errors = _earnings_from_finnhub(yf_missed, finnhub_key, today, yf_found)
        meta["earnings_errors"].extend(fh_errors)
        for item in fh_items:
            item["date_label"]   = _date_label(datetime.strptime(item["date"], "%Y-%m-%d").date(), today)
            item["on_watchlist"] = item["ticker"] in wl_set
            _bucket_item(item)
            meta["finnhub_found"] += 1

    # Sort: overrides first, then watchlist, then by date
    for k in buckets:
        buckets[k].sort(key=lambda x: (
            not x.get("is_override"),
            not x.get("on_watchlist"),
            x["days_away"],
        ))

    meta["earnings_count"] = sum(len(v) for v in buckets.values())
    result = dict(buckets)
    result["meta"] = meta

    _cset("earnings", result)
    logger.info(
        "intel/earnings: today=%d tomorrow=%d week=%d "
        "(overrides=%d yf=%d fh=%d tickers=%d)",
        len(buckets["today"]), len(buckets["tomorrow"]), len(buckets["this_week"]),
        meta["overrides_injected"], meta["yfinance_found"],
        meta["finnhub_found"],      meta["tickers_checked"],
    )
    return result


def _apply_overrides_to_buckets(buckets: dict, today: date, wl_set: set) -> None:
    """Inject EARNINGS_OVERRIDES into buckets at query time.

    Called from get_intel_summary() so overrides always appear regardless of
    whether the cache was warm before the override was added, or the date math
    in the background refresh ran on a different calendar day.
    Skips any ticker already present in a bucket (avoids duplicates).
    """
    existing = {item["ticker"] for bucket in buckets.values() for item in bucket}
    for ov in EARNINGS_OVERRIDES:
        ticker = ov["ticker"].upper()
        if ticker in existing:
            continue
        try:
            ov_date   = datetime.strptime(ov["date"][:10], "%Y-%m-%d").date()
            days_away = (ov_date - today).days
            if days_away < 0 or days_away > 7:
                continue
            item = {
                "ticker":       ticker,
                "company_name": _company_name(ticker),
                "date":         ov_date.isoformat(),
                "date_label":   _date_label(ov_date, today),
                "time_label":   ov.get("time_label", "TBD"),
                "on_watchlist": ticker in wl_set,
                "days_away":    days_away,
                "eps_est":      ov.get("eps_est"),
                "rev_est":      ov.get("rev_est"),
                "source":       ov.get("source", "manual override"),
                "is_override":  True,
            }
            if days_away == 0:
                buckets["today"].insert(0, item)
            elif days_away == 1:
                buckets["tomorrow"].insert(0, item)
            elif 2 <= days_away <= 7:
                buckets["this_week"].insert(0, item)
        except Exception:
            pass


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
    if _fh_is_rate_limited():
        logger.warning("intel/splits: Finnhub rate-limited — skipping, using yfinance fallback")
        return []
    from_d = today.isoformat()
    to_d   = (today + timedelta(days=30)).isoformat()
    results: list[dict] = []
    for ticker in tickers:
        if _fh_is_rate_limited():
            logger.warning("intel/splits: Finnhub 429 mid-loop — stopping at %s", ticker)
            break
        try:
            url = (
                f"https://finnhub.io/api/v1/stock/split"
                f"?symbol={ticker}&from={from_d}&to={to_d}&token={api_key}"
            )
            with _fh_urlopen(url, timeout=4) as resp:
                data = json.loads(resp.read().decode())
            for s in (data or []):
                eff_str = (s.get("date") or "")[:10]
                try:
                    ed = datetime.strptime(eff_str, "%Y-%m-%d").date()
                except Exception:
                    continue
                days_away  = (ed - today).days
                if days_away < 0 or days_away > 30:
                    continue
                from_f     = s.get("fromFactor", 1) or 1
                to_f       = s.get("toFactor", 1) or 1
                split_type = "Forward" if to_f > from_f else "Reverse"
                ratio_str  = f"{int(from_f)}:{int(to_f)}"
                results.append({
                    "ticker":       ticker,
                    "company_name": _company_name(ticker),
                    "ratio":        ratio_str,
                    "type":         split_type,
                    "eff_date":     eff_str,
                    "days_away":    days_away,
                    "is_new":       0 <= days_away <= 7,
                    "status":       _split_status(days_away),
                    "source":       "finnhub",
                })
        except RuntimeError as exc:
            logger.warning("intel/splits finnhub %s: %s", ticker, exc)
            break   # rate-limited — stop calling Finnhub
        except Exception as e:
            logger.debug("intel/splits finnhub %s: %s", ticker, e)
    return results


def _splits_from_yfinance(tickers: list[str], today: date) -> list[dict]:
    lookback  = today - timedelta(days=7)
    lookahead = today + timedelta(days=30)
    results: list[dict] = []
    try:
        import yfinance as yf
        for ticker in tickers:
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
                        "ticker":       ticker,
                        "company_name": _company_name(ticker),
                        "ratio":        ratio_str,
                        "type":         split_type,
                        "eff_date":     sd.isoformat(),
                        "days_away":    days_away,
                        "is_new":       0 <= days_away <= 7,
                        "status":       _split_status(days_away),
                        "source":       "yfinance",
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


# ── Dividends ─────────────────────────────────────────────────────────────────

def _dividends_from_finnhub(
    tickers: list[str], api_key: str, today: date, wl_set: set,
) -> list[dict]:
    """Fetch ex-dividend dates via Finnhub /stock/dividend2 (parallel)."""
    if _fh_is_rate_limited():
        logger.warning("intel/dividends: Finnhub rate-limited — skipping, using yfinance fallback")
        return []
    from_d = today.isoformat()
    to_d   = (today + timedelta(days=30)).isoformat()
    results: list[dict] = []

    def _fetch_one(ticker: str) -> Optional[dict]:
        if _fh_is_rate_limited():
            return None
        try:
            url = (
                f"https://finnhub.io/api/v1/stock/dividend2"
                f"?symbol={ticker}&from={from_d}&to={to_d}&token={api_key}"
            )
            with _fh_urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            items = data.get("data", [])
            if not items:
                return None
            d       = items[0]
            ex_str  = (d.get("exDate") or "")[:10]
            if not ex_str:
                return None
            ex_date   = datetime.strptime(ex_str, "%Y-%m-%d").date()
            days_away = (ex_date - today).days
            if days_away < 0 or days_away > 30:
                return None
            pay_str = (d.get("payDate") or "")[:10] or None
            amount  = d.get("amount")
            return {
                "ticker":        ticker,
                "company_name":  _company_name(ticker),
                "ex_date":       ex_str,
                "ex_date_label": _date_label(ex_date, today),
                "payment_date":  pay_str,
                "days_away":     days_away,
                "div_amount":    round(float(amount), 4) if amount else None,
                "div_yield":     None,
                "frequency":     d.get("frequency", ""),
                "on_watchlist":  ticker in wl_set,
                "source":        "finnhub",
            }
        except RuntimeError:
            return None   # rate-limited mid-flight
        except Exception:
            return None

    pool = ThreadPoolExecutor(max_workers=6)
    futs = [pool.submit(_fetch_one, t) for t in tickers]
    pool.shutdown(wait=False)
    try:
        for fut in as_completed(futs, timeout=30):
            try:
                item = fut.result()
                if item:
                    results.append(item)
            except Exception:
                pass
    except Exception:
        pass
    return results


def _dividends_from_yfinance(tickers: list[str], today: date, wl_set: set) -> list[dict]:
    """Fallback: read exDividendDate + dividendYield from yfinance Ticker.info."""
    from datetime import timezone
    results: list[dict] = []

    def _fetch_one(ticker: str) -> Optional[dict]:
        try:
            import yfinance as yf
            info    = yf.Ticker(ticker).info
            ex_ts   = info.get("exDividendDate")
            if not ex_ts:
                return None
            ex_date   = datetime.fromtimestamp(int(ex_ts), tz=timezone.utc).date()
            days_away = (ex_date - today).days
            if days_away < 0 or days_away > 30:
                return None
            amount    = info.get("lastDividendValue") or info.get("dividendRate")
            div_yield = info.get("dividendYield")
            company   = _company_name(ticker) or info.get("shortName", "")
            return {
                "ticker":        ticker,
                "company_name":  company,
                "ex_date":       ex_date.isoformat(),
                "ex_date_label": _date_label(ex_date, today),
                "payment_date":  None,
                "days_away":     days_away,
                "div_amount":    round(float(amount), 4) if amount else None,
                "div_yield":     round(float(div_yield) * 100, 2) if div_yield else None,
                "on_watchlist":  ticker in wl_set,
                "source":        "yfinance",
            }
        except Exception:
            return None

    pool = ThreadPoolExecutor(max_workers=4)
    futs = [pool.submit(_fetch_one, t) for t in tickers]
    pool.shutdown(wait=False)
    try:
        for fut in as_completed(futs, timeout=30):
            try:
                item = fut.result()
                if item:
                    results.append(item)
            except Exception:
                pass
    except Exception:
        pass
    return results


def fetch_dividends(tickers: Optional[list[str]] = None) -> list[dict]:
    """
    Fetch upcoming ex-dividend dates for the next 30 days.
    Uses Finnhub if API key present; falls back to yfinance .info.
    Sorted: watchlist first, then by days_away.
    Cached for 6 hours.
    """
    cached = _cget("dividends")
    if cached is not None:
        return cached

    all_tickers = tickers if tickers is not None else _earnings_universe()
    wl_set      = set(_get_watchlist_tickers())
    today       = _today_et()
    results: list[dict] = []

    finnhub_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if finnhub_key:
        results = _dividends_from_finnhub(all_tickers, finnhub_key, today, wl_set)

    if not results:
        results = _dividends_from_yfinance(all_tickers, today, wl_set)

    results.sort(key=lambda x: (not x["on_watchlist"], x["days_away"]))
    _cset("dividends", results)
    logger.info("intel/dividends: %d items in next 30 days", len(results))
    return results


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
    if _fh_is_rate_limited():
        logger.warning("intel/econ: Finnhub rate-limited — using static fallback")
        return []
    try:
        today  = _today_et()
        from_d = today.isoformat()
        to_d   = (today + timedelta(days=14)).isoformat()
        url = (
            f"https://finnhub.io/api/v1/calendar/economic"
            f"?from={from_d}&to={to_d}&token={api_key}"
        )
        with _fh_urlopen(url, timeout=8) as resp:
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
                    day_lbl  = item.get("date_label", label)
                    time_lbl = item.get("time_label", "TBD")
                    src_note = " [manual override]" if item.get("is_override") else ""
                    msg = (
                        f"⚠️ <b>Earnings Risk: {ticker} reports {day_lbl} {time_lbl}{src_note}</b>\n"
                        f"Date: {item['date']}\n"
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

    # 4. Watchlist dividends with ex-date within 7 days
    try:
        for item in fetch_dividends():
            if not item.get("on_watchlist"):
                continue
            days = item.get("days_away", 99)
            if days < 0 or days > 7:
                continue
            ticker = item["ticker"]
            dk = _daily_key(ticker, "div_exdate")
            if should_send_alert(dk):
                ex_lbl = item.get("ex_date_label", item.get("ex_date", "—"))
                amt    = f"${item['div_amount']:.4f}" if item.get("div_amount") else "—"
                yld    = f"  ·  Yield: {item['div_yield']:.1f}%" if item.get("div_yield") else ""
                msg = (
                    f"💰 <b>Dividend Ex-Date: {ticker} — {ex_lbl}</b>\n"
                    f"Amount: {amt}{yld}\n"
                    f"<i>Must own shares before ex-date to receive dividend.</i>"
                )
                send_intel_alert(msg)
                sent.append({"type": "dividend", "ticker": ticker})
    except Exception as e:
        logger.warning("intel: dividend alert sweep failed: %s", e)

    # 5. High-impact economic events today
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

    # 6. VIX spike + 10Y yield spike
    try:
        macro = fetch_macro_environment()
        vix   = macro.get("vix_level")
        if vix and vix > 25:
            level = "EXTREME" if vix > 35 else "ELEVATED"
            dk = _daily_key("VIX", f"spike_{level}")
            if should_send_alert(dk):
                msg = (
                    f"⚠️ <b>VIX {level} — {vix:.1f}</b>\n"
                    f"Fear index {level.lower()}. Reduce size, use tighter stops.\n"
                    f"<i>Consider sitting out until VIX drops below 25.</i>"
                )
                send_intel_alert(msg)
                sent.append({"type": "vix_spike", "vix": vix})

        yield_10y   = macro.get("yield_10y")
        change_bps  = macro.get("yield_change_bps")
        if yield_10y and change_bps is not None and abs(change_bps) >= 10:
            direction = "SURGING" if change_bps > 0 else "DROPPING"
            dk = _daily_key("TNX", f"spike_{direction}")
            if should_send_alert(dk):
                note = (
                    "Growth/tech headwind — watch for sector rotation."
                    if change_bps > 0 else
                    "Bond rally — supportive for growth/tech."
                )
                msg = (
                    f"📈 <b>10Y Yield {direction} — {yield_10y:.3f}%</b>\n"
                    f"Daily change: {change_bps:+.1f} bps\n"
                    f"<i>{note}</i>"
                )
                send_intel_alert(msg)
                sent.append({"type": "yield_spike", "yield_10y": yield_10y, "change_bps": change_bps})
    except Exception as e:
        logger.warning("intel: vix/yield alert sweep failed: %s", e)

    if sent:
        logger.info("intel: sent %d Telegram alerts", len(sent))
    return sent


# ── Convenience rollup for /api/intel ────────────────────────────────────────

def get_intel_summary() -> dict:
    """Cache-first intel summary — always returns in < 1 second.

    Hot path (all caches warm): read from cache, return immediately.
    Cold path (any cache missing): return static/empty fallback immediately
    and fire a background refresh daemon to populate the caches.
    The frontend polls every 10 s until refreshing == False.
    """
    global _bg_refreshing

    c_news  = _cget("market_news")
    c_earn  = _cget("earnings")
    c_split = _cget("splits")
    c_div   = _cget("dividends")
    c_econ  = _cget("economic")
    c_macro = _cget("macro")

    is_cold = (c_earn is None) or (c_split is None) or (c_econ is None)
    errors: list[str] = []

    if is_cold:
        trigger_background_refresh()
        # Economic calendar has a free static fallback — use it immediately
        if c_econ is None:
            c_econ = _econ_static_fallback()
        errors.append(
            "Cache warming — background refresh started. "
            "Full data loads in ~30 s. Page will auto-refresh."
        )
        logger.info("intel_summary: cold start, bg refresh triggered")

    with _bg_refresh_lock:
        currently_refreshing = _bg_refreshing

    fh_limited = _fh_is_rate_limited()
    if fh_limited:
        remaining = max(0, int((_fh_rl_until - _time.monotonic()) / 60))
        errors.append(f"Finnhub rate-limited — using cached data ({remaining} min remaining)")
        logger.warning("get_intel_summary: Finnhub rate-limited, serving cache")

    # Extract earnings buckets and debug meta separately
    _earn       = c_earn or {}
    earn_buckets = {
        "today":     list(_earn.get("today",     [])),
        "tomorrow":  list(_earn.get("tomorrow",  [])),
        "this_week": list(_earn.get("this_week", [])),
    }
    earn_meta = _earn.get("meta", {})

    # Always re-inject overrides so they appear even with stale/empty cache.
    # Must use fresh today + wl_set (not the cached values from fetch time).
    today  = _today_et()
    wl_set = set(_get_watchlist_tickers())
    _apply_overrides_to_buckets(earn_buckets, today, wl_set)

    return {
        "ok":                 not is_cold,
        "rate_limited":       fh_limited,
        "errors":             errors,
        "last_updated":       _et_now().strftime("%I:%M %p ET"),
        "market_news":        c_news  or [],
        "news":               c_news  or [],   # alias — frontend checks both keys
        "earnings":           earn_buckets,
        "splits":             c_split or [],
        "dividends":          c_div   or [],
        "economic_events":    c_econ  or [],
        "market_environment": c_macro or {},
        "sector_heat": [
            {"ticker": k, "pct": v, "direction": "up" if (v or 0) >= 0 else "down"}
            for k, v in (c_macro or {}).get("sectors", {}).items()
        ],
        "earnings_debug": {
            "server_date":          today.isoformat(),
            "tickers_checked":      earn_meta.get("tickers_checked", 0),
            "earnings_source_used": earn_meta.get("earnings_source_used", "—"),
            "earnings_errors":      earn_meta.get("earnings_errors", []),
            "earnings_count":       sum(len(v) for v in earn_buckets.values()),
            "overrides_injected":   earn_meta.get("overrides_injected", 0),
            "yfinance_found":       earn_meta.get("yfinance_found", 0),
            "finnhub_found":        earn_meta.get("finnhub_found", 0),
            "splits_count":         len(c_split or []),
            "dividends_count":      len(c_div   or []),
        },
        "alerts_sent":     [],
        "from_cache":      not is_cold,
        "refreshing":      currently_refreshing,
    }
