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
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
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
_news_state_lock = threading.Lock()
_news_refreshing = False
_news_retry_after = 0.0
_news_last_failure = ""

_CACHE_TTL: dict[str, int] = {
    "market_news": 600,    # 10 min  — news changes often
    "earnings":    3600,   # 1 hr    — refreshes more often to catch new dates
    "splits":      43200,  # 12 hrs  — stable; long TTL protects rate limits
    "dividends":   43200,  # 12 hrs  — ex-dates don't change often
    "economic":    43200,  # 12 hrs  — stable; Finnhub econ is 1 call/day max
    "macro":       300,    # 5 min   — 10Y/DXY/VIX live feeds
    "ndx":         86400,  # 24 hrs  — NDX constituents; rebalances ~quarterly
    "ndx_watch":   3600,   # 1 hr    — cached watch rollup (avoids hot-path DB hits)
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


# ── yfinance rate-limit backoff ────────────────────────────────────────────────
# Yahoo Finance (via the yfinance package) returns HTTP 401/429 when it
# blocks/rate-limits an IP. Unlike Finnhub, yfinance calls have no built-in
# timeout or backoff — a serial loop over the full ticker universe can hang
# the whole gthread worker long enough for Render's health check to fail and
# restart the dyno, which then repeats the same flood on boot (crash loop).
# This mirrors the Finnhub breaker: one 401/429 freezes ALL yfinance calls
# for 30 minutes so the loop aborts fast instead of burning through 60 tickers.

_yf_rl_lock  = threading.Lock()
_yf_rl_until = 0.0      # monotonic timestamp; 0 = not rate-limited
_YF_RL_SECS  = 1800     # 30-minute cooldown


def _yf_is_rate_limited() -> bool:
    return _time.monotonic() < _yf_rl_until


def _yf_set_rate_limited(secs: int = _YF_RL_SECS) -> None:
    global _yf_rl_until
    with _yf_rl_lock:
        _yf_rl_until = _time.monotonic() + secs
    logger.warning("yfinance 401/429 — Yahoo is blocking us, pausing all yfinance calls for %d min", secs // 60)


def _is_yf_rate_limit_error(exc: BaseException) -> bool:
    msg = str(exc)
    return "401" in msg or "429" in msg or "Too Many Requests" in msg or "Unauthorized" in msg


def _cget(key: str):
    ns = key.split(":")[0]
    ttl = _CACHE_TTL.get(ns, 900)
    with _cache_lock:
        e = _cache.get(key)
        if ns == "earnings" and e:
            data = e.get("data") or {}
            count = sum(len(data.get(bucket) or []) for bucket in ("today", "tomorrow", "this_week", "coming_up"))
            if count == 0:
                ttl = min(ttl, 300)
        if e and (_time.monotonic() - e["ts"]) < ttl:
            return e["data"]
    return None


def _cset(key: str, data) -> None:
    with _cache_lock:
        _cache[key] = {"data": data, "ts": _time.monotonic()}


def clear_intel_cache(namespace: str | None = None) -> None:
    """Clear every intel cache entry or only one feed namespace."""
    global _news_retry_after, _news_last_failure
    with _cache_lock:
        if namespace is None:
            _cache.clear()
        else:
            for key in list(_cache):
                if key.split(":")[0] == namespace:
                    _cache.pop(key, None)
    if namespace is None or namespace == "market_news":
        with _news_state_lock:
            _news_retry_after = 0.0
            _news_last_failure = ""


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
                (fetch_economic_calendar,  "econ"),
                (fetch_stock_splits,       "splits"),
                (fetch_earnings_calendar,  "earnings"),
                (fetch_dividends,          "dividends"),
                (fetch_market_news,        "news"),
                (fetch_macro_environment,  "macro"),
                (run_ndx_constituent_check, "ndx"),
            ]
            # Hard ceiling: never block the bg thread longer than 90 s.
            # Without this, a hung yfinance call keeps _bg_refreshing=True forever,
            # preventing any future refresh from starting.
            bg_ex = ThreadPoolExecutor(max_workers=3)
            futs = [bg_ex.submit(_call, fn, nm) for fn, nm in tasks]
            bg_ex.shutdown(wait=False)
            try:
                for fut in as_completed(futs, timeout=90):
                    pass
            except Exception:
                pass   # TimeoutError after 90 s — move on regardless
            logger.info("intel bg-refresh: all complete (or timed out)")
            try:
                import gc as _gc
                _gc.collect()
                logger.info("intel bg-refresh: gc.collect() done")
            except Exception:
                pass
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
    # Mega-cap tech
    "NVDA", "AMD", "TSLA", "AAPL", "META", "GOOGL", "AMZN", "MSFT",
    "ORCL", "CRM", "ADBE", "INTC", "QCOM", "AVGO", "MU", "AMAT",
    "NFLX", "UBER", "SNAP", "PINS", "SPOT",
    # High-momentum / speculative
    "PLTR", "SOFI", "IONQ", "RGTI", "QUBT", "JOBY", "ACHR", "RKLB",
    "LUNR", "OKLO", "SMR", "NNE", "ALAB", "ARM", "SMCI",
    # Utilities / energy / power
    "CEG", "VST", "NRG", "GEV", "FSLR", "NEE", "SO", "DUK", "SRE",
    # Financials
    "JPM", "BAC", "GS", "MS", "WFC", "C", "V", "MA", "AXP", "COF",
    "SCHW", "BLK", "BX",
    # Consumer / retail (AMZN and NFLX deduplicated — already in mega-cap)
    "HD", "WMT", "COST", "TGT", "NKE", "SBUX", "MCD",
    "DIS", "LOW", "TJX", "LULU", "GPS", "RL",
    # Healthcare / biotech
    "JNJ", "UNH", "PFE", "ABBV", "MRK", "LLY", "BMY", "GILD",
    "AMGN", "ISRG", "DXCM", "MRNA", "BNTX",
    # Energy / commodities
    "XOM", "CVX", "OXY", "COP", "SLB", "HAL",
    # Industrials / defense
    "LMT", "RTX", "GD", "NOC", "BA", "CAT", "DE", "HON", "MMM", "GE",
    # Fintech / other
    "COIN", "HOOD", "SQ", "PYPL", "SHOP",
]

# ── Manual earnings overrides ──────────────────────────────────────────────────
# Add entries here when APIs miss a known earnings date.
# These inject directly into the result regardless of what yfinance/Finnhub return.
# Format: ticker, date (YYYY-MM-DD), time_label (BMO/AMC/TBD), eps_est (float or None), source
EARNINGS_OVERRIDES: list[dict] = [
    # Add entries here when APIs miss a known earnings date.
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
    "MS":   "Morgan Stanley",      "WFC":  "Wells Fargo",
    "C":    "Citigroup",           "AXP":  "American Express",
    "V":    "Visa",                "MA":   "Mastercard",
    "HD":   "Home Depot",          "WMT":  "Walmart",
    "COST": "Costco",              "TGT":  "Target",
    "NKE":  "Nike",                "SBUX": "Starbucks",
    "MCD":  "McDonald's",          "DIS":  "Walt Disney",
    "LOW":  "Lowe's",              "LULU": "Lululemon",
    "XOM":  "ExxonMobil",         "CVX":  "Chevron",
    "OXY":  "Occidental",         "COP":  "ConocoPhillips",
    "LMT":  "Lockheed Martin",     "RTX":  "RTX Corp",
    "GD":   "General Dynamics",   "NOC":  "Northrop Grumman",
    "BA":   "Boeing",             "CAT":  "Caterpillar",
    "JNJ":  "Johnson & Johnson",  "UNH":  "UnitedHealth",
    "PFE":  "Pfizer",             "ABBV": "AbbVie",
    "LLY":  "Eli Lilly",          "MRK":  "Merck",
    "COIN": "Coinbase",           "HOOD": "Robinhood",
    "SHOP": "Shopify",            "PYPL": "PayPal",
    "ORCL": "Oracle",             "CRM":  "Salesforce",
    "ADBE": "Adobe",              "AVGO": "Broadcom",
    "NFLX": "Netflix",            "UBER": "Uber",
    "ARM":  "Arm Holdings",       "SMCI": "Super Micro",
    "ALAB": "Astera Labs",
    "ANET": "Arista Networks",     "FN":   "Fabrinet",
    "TSM":  "Taiwan Semiconductor","ISRG": "Intuitive Surgical",
    "EME":  "EMCOR Group",         "GOOG": "Alphabet",
}


def _company_name(ticker: str) -> str:
    return _COMPANY_NAMES.get(ticker.upper(), "")


# Static one-line "what does this company do" blurbs for the same universe
# as _COMPANY_NAMES. yfinance's .info endpoint (the only free source of a
# real description) has proven unreliable on this host — Yahoo intermittently
#401s/429s it even outside the bulk-fetch crash loop — so this is the
# primary source for known tickers, with yfinance only as best-effort for
# tickers outside this list.
_COMPANY_DESCRIPTIONS: dict[str, str] = {
    "NVDA": "Designs GPUs and AI accelerator chips that power data centers, gaming, and machine learning workloads.",
    "AMD":  "Designs CPUs and GPUs for PCs, servers, and data centers, competing directly with Intel and NVIDIA.",
    "TSLA": "Designs and manufactures electric vehicles, batteries, and solar energy products.",
    "AAPL": "Designs and sells the iPhone, Mac, and iPad, plus a growing services ecosystem including the App Store.",
    "META": "Owns Facebook, Instagram, and WhatsApp, and is investing heavily in AI and the metaverse.",
    "GOOGL": "Google's parent company — dominates search and online advertising while investing in cloud, AI, and Waymo self-driving cars.",
    "AMZN": "The largest US e-commerce retailer and owner of AWS, the leading cloud computing platform.",
    "MSFT": "Sells the Windows OS, Office productivity suite, and Azure cloud services, with major AI investments through OpenAI.",
    "PLTR": "Builds data analytics software used by government agencies and large enterprises for intelligence and operations.",
    "SOFI": "Offers online personal loans, student loan refinancing, banking, and investing services.",
    "IONQ": "Builds trapped-ion quantum computers and sells quantum computing access via the cloud.",
    "RGTI": "Designs and builds superconducting quantum computing chips and systems.",
    "QUBT": "Develops quantum and photonic computing hardware and software.",
    "JOBY": "Developing electric vertical takeoff and landing (eVTOL) aircraft for air taxi services.",
    "ACHR": "Developing electric vertical takeoff and landing (eVTOL) aircraft for urban air mobility.",
    "RKLB": "Designs, builds, and launches small satellites and rockets, and makes spacecraft components.",
    "LUNR": "Builds lunar landers and provides space exploration services for NASA and commercial clients.",
    "OKLO": "Developing small modular nuclear fission reactors for clean energy generation.",
    "SMR":  "Designs small modular nuclear reactors for utility-scale power generation.",
    "NNE":  "Develops portable microreactor technology for clean nuclear power.",
    "CEG":  "The largest producer of carbon-free electricity in the US, operating a major nuclear power fleet.",
    "VST":  "An integrated power company that generates and sells electricity across multiple US markets.",
    "NRG":  "Generates and sells electricity and provides energy management services to homes and businesses.",
    "GEV":  "Makes power generation equipment including gas turbines, wind turbines, and grid technology.",
    "FSLR": "Manufactures thin-film solar panels used in large-scale solar power installations.",
    "NEE":  "One of the largest utility holding companies, with major investments in wind and solar power.",
    "SO":   "A major regulated electric and gas utility serving the southeastern US.",
    "JPM":  "The largest US bank by assets, offering consumer banking, investment banking, and asset management.",
    "BAC":  "One of the largest US banks, offering consumer banking, wealth management, and investment banking.",
    "GS":   "A leading global investment bank providing trading, asset management, and advisory services.",
    "V":    "Operates the world's largest electronic payments network, processing card transactions globally.",
    "MA":   "Operates a global electronic payments network connecting consumers, merchants, and banks.",
    "HD":   "The largest home improvement retailer in the US, selling building materials and tools.",
    "WMT":  "The world's largest retailer, operating supercenters, discount stores, and a growing e-commerce business.",
    "COST": "Operates membership-based warehouse clubs selling groceries and general merchandise in bulk.",
    "XOM":  "One of the world's largest oil and gas companies, involved in exploration, production, and refining.",
    "CVX":  "A major integrated oil and gas company engaged in exploration, production, and refining.",
    "OXY":  "An oil and gas exploration and production company with growing carbon capture operations.",
    "LMT":  "A leading defense contractor making fighter jets, missiles, and aerospace systems.",
    "RTX":  "Aerospace and defense systems maker (formerly Raytheon) — jet engines, missiles, and avionics.",
}


def _company_description(ticker: str) -> str:
    return _COMPANY_DESCRIPTIONS.get(ticker.upper(), "")


def _wikipedia_description(name: str, max_chars: int = 320) -> Optional[str]:
    """
    Universal fallback for company descriptions — used for any ticker
    outside the curated static list (e.g. new tickers the user adds).
    Wikipedia's public REST API needs no key and isn't subject to the
    401/429 blocking we've seen from Yahoo, so it's the reliable catch-all.
    """
    if not name:
        return None
    import urllib.request
    import urllib.parse
    headers = {"User-Agent": "TradestaarElite/1.0 (contact: app)"}
    try:
        search_url = (
            "https://en.wikipedia.org/w/api.php?action=opensearch&format=json"
            f"&namespace=0&limit=1&search={urllib.parse.quote(name)}"
        )
        req = urllib.request.Request(search_url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        titles = data[1] if isinstance(data, list) and len(data) > 1 else []
        if not titles:
            return None
        title = titles[0]

        summary_url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title)
        req2 = urllib.request.Request(summary_url, headers=headers)
        with urllib.request.urlopen(req2, timeout=5) as resp:
            summary = json.loads(resp.read().decode())
        extract = (summary.get("extract") or "").strip()
        if not extract or summary.get("type") == "disambiguation":
            return None
        if len(extract) > max_chars:
            cut = extract[:max_chars]
            last_period = cut.rfind(". ")
            extract = cut[:last_period + 1] if last_period > 80 else cut.rstrip() + "…"
        return extract
    except Exception as e:
        logger.debug("intel/profile wikipedia %s: %s", name, e)
        return None


# ── Company profile (name / sector / industry / description) ─────────────────
# Company fundamentals barely change — cached in stock_data indefinitely.
# Pass force=True to bypass the cache and refetch.

_PROFILE_MAX_AGE_DAYS = 30


def fetch_company_profile(ticker: str, force: bool = False) -> dict:
    """
    Return {company_name, sector, industry, description, logo_url}.
    Tries Finnhub /stock/profile2 first (name, industry, logo), then
    yfinance for a description and sector fallback. Cached in the DB so
    repeat lookups (e.g. revisiting a stock page) don't re-hit either API.
    """
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return {}

    from database import get_company_profile, save_company_profile

    if not force:
        cached = get_company_profile(ticker)
        if cached and cached.get("fetched_at"):
            # A cached row with no description at all means an earlier fetch
            # failed before the Wikipedia fallback existed (or hit a dead
            # end). Re-run instead of serving that gap for up to 30 days —
            # the Wikipedia step below makes a hit far more likely now.
            if not cached.get("description"):
                cached = None
        if cached and cached.get("fetched_at"):
            try:
                age_days = (datetime.now() - datetime.fromisoformat(cached["fetched_at"])).days
            except Exception:
                age_days = 0
            if age_days < _PROFILE_MAX_AGE_DAYS:
                return cached

    profile: dict = {}

    finnhub_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if finnhub_key and not _fh_is_rate_limited():
        try:
            url = f"https://finnhub.io/api/v1/stock/profile2?symbol={ticker}&token={finnhub_key}"
            with _fh_urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            if data:
                profile["company_name"] = data.get("name")
                profile["industry"]     = data.get("finnhubIndustry")
                profile["logo_url"]     = data.get("logo")
        except RuntimeError as exc:
            logger.warning("intel/profile finnhub %s: %s", ticker, exc)
        except Exception as e:
            logger.debug("intel/profile finnhub %s: %s", ticker, e)

    # Static description first — instant and reliable. yfinance's .info
    # endpoint (the only free source of a real description) has proven
    # unreliable on this host (Yahoo intermittently 401s/429s it even
    # outside the bulk-fetch crash loop), so it's a best-effort enhancement
    # only, not the primary path.
    if not profile.get("description"):
        static_desc = _company_description(ticker)
        if static_desc:
            profile["description"] = static_desc

    if (not profile.get("description") or not profile.get("sector")) and not _yf_is_rate_limited():
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info or {}
            profile["company_name"] = profile.get("company_name") or info.get("longName") or info.get("shortName")
            profile["sector"]       = profile.get("sector") or info.get("sector")
            profile["industry"]     = profile.get("industry") or info.get("industry")
            profile["description"]  = profile.get("description") or info.get("longBusinessSummary")
            profile["logo_url"]     = profile.get("logo_url") or info.get("logo_url")
        except Exception as e:
            if _is_yf_rate_limit_error(e):
                _yf_set_rate_limited()
            logger.debug("intel/profile yfinance %s: %s", ticker, e)

    profile["company_name"] = profile.get("company_name") or _company_name(ticker) or ticker

    # Universal fallback — covers any ticker not in the curated static list
    # (e.g. new tickers the user adds) without depending on Yahoo at all.
    if not profile.get("description"):
        wiki_name = profile.get("company_name") or ticker
        wiki_desc = _wikipedia_description(wiki_name)
        if not wiki_desc and wiki_name != ticker:
            wiki_desc = _wikipedia_description(ticker)  # retry with bare ticker
        if wiki_desc:
            profile["description"] = wiki_desc

    if profile.get("description") or profile.get("sector") or profile.get("industry"):
        try:
            save_company_profile(ticker, profile)
        except Exception:
            logger.exception("intel/profile: failed to cache profile for %s", ticker)

    return profile


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
    """Per-symbol earnings checks: every watchlist and override ticker.

    The separate Nasdaq bulk feed supplies the market-wide large/mid-cap slate,
    so this list must not be capped or padded with scanner symbols.
    """
    wl = _get_watchlist_tickers()
    override_tickers = [ov["ticker"].upper() for ov in EARNINGS_OVERRIDES]
    return list(dict.fromkeys(wl + override_tickers))


# ── Market News ───────────────────────────────────────────────────────────────

def fetch_market_news(tickers: Optional[list[str]] = None) -> list[dict]:
    """
    Fetch and classify news for the watchlist + scanner universe.
    Returns verified provider headlines sorted by impact. LOW-impact headlines
    remain visible as ordinary coverage instead of producing a blank scanner.
    Cached for 15 minutes.
    """
    global _news_refreshing, _news_retry_after, _news_last_failure

    cached = _cget("market_news")
    if cached is not None:
        return cached
    with _news_state_lock:
        if _time.monotonic() < _news_retry_after:
            return []
        _news_refreshing = True

    wl_tickers = _get_watchlist_tickers()
    wl_set = set(wl_tickers)
    all_tickers = tickers if tickers is not None else _merged_universe()
    # News is latency-sensitive. Put personally tracked symbols first and cap
    # the fan-out so blocked providers cannot starve the entire feed on Render.
    all_tickers = list(dict.fromkeys(list(wl_tickers) + list(all_tickers)))[:18]

    from news_fetcher import fetch_headlines as _fetch_hl

    results: list[dict] = []

    def _fetch_one(ticker: str) -> list[dict]:
        try:
            news = _fetch_hl(ticker)
            if news.source == "none":
                return []
            articles = list(news.articles) or [
                {"headline": headline, "source": news.source.capitalize()}
                for headline in news.headlines
            ]
            items = []
            for article in articles[:3]:
                headline = str(article.get("headline") or "").strip()
                if not headline:
                    continue
                impact, reason = classify_news_impact(headline, news.categories)
                items.append({
                    "ticker":       ticker,
                    "headline":     headline,
                    "source":       article.get("source") or news.source.replace("_", " ").title(),
                    "url":          article.get("url") or "",
                    "image":        article.get("image") or "",
                    "summary":      article.get("summary") or "",
                    "published_at": article.get("published_at") or "",
                    "impact":       impact,
                    "reason":       reason,
                    "time":         _format_freshness(news.freshness_minutes),
                    "on_watchlist": ticker in wl_set,
                })
            return items
        except Exception as e:
            logger.debug("intel/news %s: %s", ticker, e)
            return []

    pool = ThreadPoolExecutor(max_workers=min(8, max(1, len(all_tickers))))
    futs = [pool.submit(_fetch_one, t) for t in all_tickers]
    try:
        for fut in as_completed(futs, timeout=16):
            try:
                results.extend(fut.result())
            except Exception:
                pass
    except Exception:
        pass
    finally:
        # Pending work is cancelled and running calls finish independently;
        # the request never waits beyond the global deadline.
        pool.shutdown(wait=False, cancel_futures=True)
        with _news_state_lock:
            _news_refreshing = False

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

    # Do not turn a provider timeout into a ten-minute cached empty feed. A
    # later request may retry while keyed/RSS services recover.
    if unique:
        _cset("market_news", unique)
        with _news_state_lock:
            _news_retry_after = 0.0
            _news_last_failure = ""
    else:
        with _news_state_lock:
            _news_retry_after = _time.monotonic() + 60
            _news_last_failure = (
                "No connected news source returned a usable story. "
                "The app will retry automatically in about one minute."
            )
    logger.info("intel/news: %d provider headlines from %d tickers", len(unique), len(all_tickers))
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
    # Keep enough runway to preserve the next known date for watchlist names.
    to_d   = (today + timedelta(days=120)).isoformat()
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
        if days_away < 0 or days_away > 120:
            continue
        hour       = (e.get("hour") or "").lower()
        time_label = "BMO" if hour == "bmo" else "AMC" if hour == "amc" else "TBD"
        eps_est = None
        rev_est = None
        try:
            v = e.get("epsEstimate")
            # A breakeven consensus of 0.00 is routine for pre-profit names —
            # exactly the ones where the estimate matters — and truthiness
            # dropped it, rendering an em dash as though nothing was published.
            if v is not None: eps_est = round(float(v), 2)
        except Exception:
            pass
        try:
            v = e.get("revenueEstimate")
            if v is not None: rev_est = int(v)
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

    buckets: dict[str, list] = {"today": [], "tomorrow": [], "this_week": [], "coming_up": []}
    meta: dict = {
        "tickers_checked":      len(all_tickers),
        "overrides_injected":   0,
        "yfinance_found":       0,
        "finnhub_found":        0,
        "nasdaq_found":         0,
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
        elif 8 <= d <= 21 or (item.get("on_watchlist") and d <= 120):
            buckets["coming_up"].append(item)

    # ── 1. Manual overrides (always first) ──────────────────────────────────
    already_added: set[str] = set()
    for ov in EARNINGS_OVERRIDES:
        try:
            ov_date   = datetime.strptime(ov["date"][:10], "%Y-%m-%d").date()
            days_away = (ov_date - today).days
            if days_away < 0 or days_away > 120:
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

    # ── 2a. Direct Yahoo chart API (primary — confirmed to work from cloud IPs) ──
    # Uses the same v8/finance/chart endpoint that data_fetcher.py uses for prices.
    # Returns earningsTimestamp from meta object — no crumb/cookie needed.
    # Falls back to quoteSummary, then yfinance if chart API has no earnings date.
    def _fetch_earnings_yahoo_api(ticker: str) -> Optional[dict]:
        """Fetch next earnings date via Yahoo Finance chart API (no crumb needed)."""
        if ticker in already_added:
            return None

        import urllib.request as _urlreq, json as _json, datetime as _dt

        _headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }

        earn_date = None
        eps_est = rev_est = None

        # ── Try 1: v8 chart API (same endpoint used for live prices — works on Render) ──
        for base_url in [
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}",
        ]:
            try:
                req = _urlreq.Request(
                    base_url + "?interval=1d&range=5d",
                    headers=_headers,
                )
                with _urlreq.urlopen(req, timeout=8) as resp:
                    data = _json.loads(resp.read())
                result = ((data.get("chart") or {}).get("result") or [{}])[0]
                meta   = result.get("meta") or {}

                # earningsTimestamp = next earnings as UTC Unix timestamp
                ts = meta.get("earningsTimestamp")
                if ts:
                    d = _dt.datetime.utcfromtimestamp(int(ts)).date()
                    if d >= today:
                        earn_date = d
                        break

                # earningsTimestampStart/End give a range — use Start
                ts_start = meta.get("earningsTimestampStart")
                if ts_start and not earn_date:
                    d = _dt.datetime.utcfromtimestamp(int(ts_start)).date()
                    if d >= today:
                        earn_date = d
                        break
            except Exception as _e:
                logger.debug("intel/earnings chart_api %s: %s", ticker, _e)
                continue

        # ── Try 2: v10 quoteSummary calendarEvents (second attempt) ─────────────
        if not earn_date:
            for base_url in [
                f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}",
                f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}",
            ]:
                try:
                    req = _urlreq.Request(
                        base_url + "?modules=calendarEvents",
                        headers=_headers,
                    )
                    with _urlreq.urlopen(req, timeout=8) as resp:
                        data = _json.loads(resp.read())
                    result = ((data.get("quoteSummary") or {}).get("result") or [{}])[0]
                    cal    = result.get("calendarEvents") or {}
                    for ed_obj in (cal.get("earningsDate") or []):
                        try:
                            raw_ts = ed_obj.get("raw")
                            if raw_ts:
                                d = _dt.datetime.utcfromtimestamp(int(raw_ts)).date()
                                if d >= today:
                                    earn_date = d
                                    break
                            fmt = (ed_obj.get("fmt") or "")[:10]
                            if fmt:
                                d = datetime.strptime(fmt, "%Y-%m-%d").date()
                                if d >= today:
                                    earn_date = d
                                    break
                        except Exception:
                            continue
                    if earn_date:
                        # Pull EPS/Rev estimates if available
                        try:
                            ea = (cal.get("earningsAverage") or {})
                            if ea.get("raw") is not None:
                                eps_est = round(float(ea["raw"]), 2)
                        except Exception:
                            pass
                        try:
                            ra = (cal.get("revenueAverage") or {})
                            if ra.get("raw") is not None:
                                rev_est = int(ra["raw"])
                        except Exception:
                            pass
                        break
                except Exception as _e:
                    logger.debug("intel/earnings quotesummary %s: %s", ticker, _e)
                    continue

        if not earn_date:
            return None

        days_away = (earn_date - today).days
        if days_away < 0 or days_away > 120:
            return None

        logger.info("intel/earnings yahoo_api %s → %s (%d days away)", ticker, earn_date, days_away)
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
            "source":       "yahoo_chart_api",
            "is_override":  False,
        }

    # Run direct Yahoo API fetch first (parallel, 8 workers)
    yahoo_api_found:  set[str]  = set(already_added)
    yahoo_api_missed: list[str] = []

    pool_ya = ThreadPoolExecutor(max_workers=3)
    futs_ya = {pool_ya.submit(_fetch_earnings_yahoo_api, t): t
               for t in all_tickers if t not in already_added}
    pool_ya.shutdown(wait=False)
    try:
        for fut in as_completed(futs_ya, timeout=25):
            t = futs_ya[fut]
            try:
                item = fut.result()
                if item:
                    _bucket_item(item)
                    yahoo_api_found.add(t)
                    meta["yfinance_found"] += 1   # reuse counter so debug shows totals
                elif t not in already_added:
                    yahoo_api_missed.append(t)
            except Exception:
                yahoo_api_missed.append(t)
    except Exception:
        pass

    meta["earnings_source_used"] = "overrides+yahoo_api+yfinance"

    # ── 2b. yfinance fallback for tickers the direct API missed ──────────────
    yf_found:  set[str]  = set(yahoo_api_found)
    yf_missed: list[str] = []

    def _fetch_cal(ticker: str) -> Optional[dict]:
        # Skip tickers already found via yahoo_api or overrides
        if ticker in yahoo_api_found:
            return None
        if ticker in already_added:
            return None
        if _yf_is_rate_limited():
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
            if days_away < 0 or days_away > 120:
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
            if _is_yf_rate_limit_error(e):
                _yf_set_rate_limited()
            logger.debug("intel/earnings yf %s: %s", ticker, e)
            return None

    # Only run yfinance on tickers the yahoo_api missed
    _yf_targets = [] if _yf_is_rate_limited() else [t for t in yahoo_api_missed if t not in yahoo_api_found]
    pool = ThreadPoolExecutor(max_workers=3)
    futs = {pool.submit(_fetch_cal, t): t for t in _yf_targets}
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
                elif t not in yahoo_api_found:
                    yf_missed.append(t)
            except Exception:
                yf_missed.append(t)
    except Exception:
        pass

    # ── 3. Finnhub fallback for tickers both yahoo_api AND yfinance missed ─────
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

    # ── 4. Nasdaq market-wide calendar (free, no key) ─────────────────────────
    # Always merge it. The per-symbol feeds above protect watchlist coverage;
    # Nasdaq supplies the broad large/mid-cap earnings slate for the next week.
    present = {
        item.get("ticker")
        for bucket in buckets.values()
        for item in bucket
        if item.get("ticker")
    }
    nasdaq_items = _earnings_from_nasdaq(today, wl_set, present)
    nasdaq_found = 0
    for item in nasdaq_items:
        _bucket_item(item)
        nasdaq_found += 1
    meta["nasdaq_found"] = nasdaq_found
    if nasdaq_found:
        meta["earnings_source_used"] = meta.get("earnings_source_used", "") + "+nasdaq_marketwide"
        logger.info("intel/earnings nasdaq market-wide: %d large/mid-cap items added", nasdaq_found)

    # Sort: overrides first, then watchlist, then by date
    for k in ("today", "tomorrow", "this_week", "coming_up"):
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
            if days_away < 0 or days_away > 21:
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
            elif 8 <= days_away <= 21 or (ticker in wl_set and days_away <= 120):
                buckets.setdefault("coming_up", []).insert(0, item)
        except Exception:
            pass


# ── Stock Split Tracker ───────────────────────────────────────────────────────

# ── Nasdaq public calendar API (no key needed) ───────────────────────────────

def _nasdaq_cal(endpoint: str, date_str: str) -> list[dict]:
    """Single GET to Nasdaq calendar API. Returns row list or [] on any error."""
    import requests as _requests
    url = f"https://api.nasdaq.com/api/calendar/{endpoint}?date={date_str}"
    hdrs = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"),
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin":          "https://www.nasdaq.com",
        "Referer":         f"https://www.nasdaq.com/market-activity/{endpoint}",
    }
    try:
        response = _requests.get(url, headers=hdrs, timeout=8)
        response.raise_for_status()
        data = response.json()
        return (data.get("data") or {}).get("rows") or []
    except Exception as exc:
        logger.debug("intel/nasdaq/%s %s: %s", endpoint, date_str, exc)
        return []


def _parse_market_cap(value) -> Optional[int]:
    """Normalize Nasdaq market-cap strings such as '$42.7B' to raw dollars."""
    if value in (None, "", "--", "N/A"):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    raw = str(value).strip().upper().replace("$", "").replace(",", "")
    multiplier = 1
    if raw.endswith("T"):
        multiplier, raw = 1_000_000_000_000, raw[:-1]
    elif raw.endswith("B"):
        multiplier, raw = 1_000_000_000, raw[:-1]
    elif raw.endswith("M"):
        multiplier, raw = 1_000_000, raw[:-1]
    try:
        return int(float(raw) * multiplier)
    except (TypeError, ValueError):
        return None


def _market_cap_tier(market_cap: Optional[int]) -> str:
    if market_cap is None:
        return ""
    if market_cap >= 10_000_000_000:
        return "Large Cap"
    if market_cap >= 2_000_000_000:
        return "Mid Cap"
    return "Small Cap"


def _earnings_from_nasdaq(today: date, wl_set: set, already_added: set) -> list[dict]:
    """
    Pull market-wide large/mid-cap earnings for today through the next 7 days.
    Watchlist symbols are retained regardless of market cap. Per-symbol feeds
    provide watchlist dates beyond this market-wide window.
    """
    date_strs = [(today + timedelta(days=i)).isoformat() for i in range(8)]
    results: list[dict] = []

    def _fetch_day(ds: str) -> list[dict]:
        return _nasdaq_cal("earnings", ds)

    pool = ThreadPoolExecutor(max_workers=4)
    futs = {pool.submit(_fetch_day, ds): ds for ds in date_strs}
    try:
        for fut in as_completed(futs, timeout=20):
            ds = futs[fut]
            d  = datetime.strptime(ds, "%Y-%m-%d").date()
            try:
                rows = fut.result() or []
            except Exception:
                rows = []
            for row in rows:
                ticker = (row.get("symbol") or "").strip().upper()
                if not ticker or ticker in already_added:
                    continue
                market_cap = _parse_market_cap(
                    row.get("marketCap") or row.get("marketcap") or row.get("market_cap")
                )
                cap_tier = _market_cap_tier(market_cap)
                if ticker not in wl_set and cap_tier not in ("Large Cap", "Mid Cap"):
                    continue
                time_raw   = (row.get("time") or "").lower()
                time_label = ("BMO" if any(k in time_raw for k in ("before", "bmo", "pre"))
                              else "AMC" if any(k in time_raw for k in ("after", "amc", "post"))
                              else "TBD")
                try:
                    eps_raw = row.get("epsForecast") or row.get("epsEstimate")
                    eps_est = float(eps_raw) if eps_raw not in (None, "--", "", "N/A") else None
                except Exception:
                    eps_est = None
                days_away = (d - today).days
                results.append({
                    "ticker":       ticker,
                    "company_name": row.get("name") or row.get("companyName") or _company_name(ticker),
                    "date":         d.isoformat(),
                    "date_label":   _date_label(d, today),
                    "time_label":   time_label,
                    "on_watchlist": ticker in wl_set,
                    "days_away":    days_away,
                    "eps_est":      eps_est,
                    "market_cap":   market_cap,
                    "cap_tier":     cap_tier,
                    "source":       "nasdaq",
                    "is_override":  False,
                })
                already_added.add(ticker)
    except FuturesTimeoutError:
        pending = sum(not fut.done() for fut in futs)
        logger.warning(
            "intel/earnings nasdaq: timed out with %d day request(s) pending; keeping %d partial results",
            pending, len(results),
        )
    finally:
        for fut in futs:
            if not fut.done():
                fut.cancel()
        pool.shutdown(wait=False, cancel_futures=True)
    return results


def fetch_earnings_radar(limit: int = 12) -> list[dict]:
    """Return a bounded, request-scoped earnings slate for the Intel card.

    The general earnings refresh also checks symbols one by one and can take
    much longer.  The radar only needs the next seven calendar days, so this
    direct Nasdaq request gives the browser a result in the same HTTP request
    and does not depend on a process-local background worker.
    """
    today = _today_et()
    watchlist = set(_get_watchlist_tickers())
    rows = _earnings_from_nasdaq(today, watchlist, set())

    buckets = {"today": [], "tomorrow": [], "this_week": [], "coming_up": []}
    _apply_overrides_to_buckets(buckets, today, watchlist)
    seen = {row.get("ticker") for row in rows}
    for bucket in buckets.values():
        for row in bucket:
            if row.get("ticker") not in seen and 0 <= int(row.get("days_away", 99)) <= 7:
                rows.append(row)
                seen.add(row.get("ticker"))

    return select_radar_rows(rows, limit)


def _radar_sort_key(row: dict):
    return (
        int(row.get("days_away", 99)),
        not bool(row.get("on_watchlist")),
        -(row.get("market_cap") or 0),
        row.get("ticker") or "",
    )


def select_radar_rows(rows: list[dict], limit: int = 12) -> list[dict]:
    """Order the radar so watchlist names can never be crowded off the card.

    Sorting by date alone meant a held ticker reporting on Friday lost its slot
    to every mega-cap reporting Monday. In earnings season that pushed the
    user's own positions off the card entirely - the one week the card matters
    most. Watchlist rows are now reserved first, then the market-wide slate
    fills whatever room is left, and the whole card is re-sorted by date so it
    still reads as a calendar.
    """
    limit = max(1, int(limit))
    ordered = sorted(rows or [], key=_radar_sort_key)
    watch = [row for row in ordered if row.get("on_watchlist")]
    market = [row for row in ordered if not row.get("on_watchlist")]
    # Watchlist names never take more than half the card, so a large watchlist
    # cannot hide the market-wide prints that move the tape.
    reserved = watch[:max(1, limit // 2)] if market else watch[:limit]
    selected = reserved + market[:limit - len(reserved)]
    if len(selected) < limit:
        remaining = [row for row in ordered if row not in selected]
        selected += remaining[:limit - len(selected)]
    return sorted(selected, key=_radar_sort_key)


def _splits_from_nasdaq(today: date) -> list[dict]:
    """Pull upcoming splits from Nasdaq calendar for the next 14 days (no key)."""
    date_strs = [(today + timedelta(days=i)).isoformat() for i in range(15)]
    results: list[dict] = []
    seen: set = set()

    def _fetch_day(ds: str) -> list[dict]:
        return _nasdaq_cal("splits", ds)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(_fetch_day, ds): ds for ds in date_strs}
        for fut in as_completed(futs, timeout=25):
            ds = futs[fut]
            d  = datetime.strptime(ds, "%Y-%m-%d").date()
            try:
                rows = fut.result() or []
            except Exception:
                rows = []
            for row in rows:
                ticker = (row.get("symbol") or "").strip().upper()
                if not ticker or ticker in seen:
                    continue
                ratio = (row.get("ratio") or row.get("splitRatio") or
                         f"{row.get('newShares','?')}:{row.get('oldShares','?')}")
                try:
                    parts = str(ratio).replace(" ", "").split(":")
                    to_f  = float(parts[0]) if parts else 1
                    fr_f  = float(parts[1]) if len(parts) > 1 else 1
                    split_type = "Forward" if to_f > fr_f else "Reverse"
                except Exception:
                    split_type = "Forward"
                days_away = (d - today).days
                results.append({
                    "ticker":       ticker,
                    "company_name": row.get("companyName") or _company_name(ticker),
                    "ratio":        ratio,
                    "type":         split_type,
                    "eff_date":     d.isoformat(),
                    "days_away":    days_away,
                    "is_new":       0 <= days_away <= 7,
                    "status":       _split_status(days_away),
                    "source":       "nasdaq",
                })
                seen.add(ticker)
    return results


def _dividends_from_nasdaq(today: date, wl_set: set) -> list[dict]:
    """Pull upcoming ex-dividend dates from Nasdaq for the next 14 days (no key)."""
    date_strs = [(today + timedelta(days=i)).isoformat() for i in range(15)]
    results: list[dict] = []
    seen: set = set()

    def _fetch_day(ds: str) -> list[dict]:
        return _nasdaq_cal("dividends", ds)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(_fetch_day, ds): ds for ds in date_strs}
        for fut in as_completed(futs, timeout=25):
            ds = futs[fut]
            d  = datetime.strptime(ds, "%Y-%m-%d").date()
            try:
                rows = fut.result() or []
            except Exception:
                rows = []
            for row in rows:
                ticker = (row.get("symbol") or "").strip().upper()
                if not ticker or ticker in seen:
                    continue
                # Filter to watchlist — otherwise hundreds of tickers show up
                if wl_set and ticker not in wl_set:
                    continue
                try:
                    amt_raw = (row.get("dividend_Rate") or row.get("amount")
                               or row.get("dividendRate") or row.get("indicated_Annual_Dividend"))
                    amount = float(amt_raw) if amt_raw not in (None, "--", "", "N/A") else None
                except Exception:
                    amount = None
                pay_date = (row.get("payment_Date") or row.get("paymentDate")
                            or row.get("payDate") or "—")
                days_away = (d - today).days
                results.append({
                    "ticker":       ticker,
                    "company_name": row.get("companyName") or _company_name(ticker),
                    "ex_date":      d.isoformat(),
                    "ex_date_label": _date_label(d, today),
                    "pay_date":     pay_date,
                    "amount":       amount,
                    "on_watchlist": ticker in wl_set,
                    "days_away":    days_away,
                    "source":       "nasdaq",
                })
                seen.add(ticker)
    return results


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

    if not results:
        results = _splits_from_nasdaq(today)

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
    """Parallelised with per-ticker timeout so a hung connection never blocks the
    background refresh thread.  Each ticker gets 8 s; total capped at 20 s."""
    if _yf_is_rate_limited():
        logger.warning("intel/splits: yfinance rate-limited — skipping entirely")
        return []
    lookback  = today - timedelta(days=7)
    lookahead = today + timedelta(days=30)
    results: list[dict] = []

    def _fetch_one(ticker: str) -> list[dict]:
        if _yf_is_rate_limited():
            return []
        try:
            import yfinance as yf
            splits = yf.Ticker(ticker).splits
            if splits is None or len(splits) == 0:
                return []
            items: list[dict] = []
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
                # int() truncated, so a 3-for-2 split (1.5) announced itself
                # as "1:1" — a Forward split at a ratio meaning no split at all.
                # A fraction keeps 3:2 and 5:4 intact and still renders 10:1.
                if ratio > 0:
                    from fractions import Fraction
                    frac = Fraction(ratio).limit_denominator(50)
                    ratio_str = f"{frac.numerator}:{frac.denominator}"
                else:
                    ratio_str = "?"
                items.append({
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
            return items
        except Exception as e:
            if _is_yf_rate_limit_error(e):
                _yf_set_rate_limited()
                logger.warning("intel/splits yfinance %s: rate-limited", ticker)
            return []

    pool = ThreadPoolExecutor(max_workers=3)
    futs = {pool.submit(_fetch_one, t): t for t in tickers}
    pool.shutdown(wait=False)
    try:
        for fut in as_completed(futs, timeout=20):
            try:
                results.extend(fut.result() or [])
            except Exception:
                pass
    except Exception:
        pass   # TimeoutError after 20 s — return what we have
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

    pool = ThreadPoolExecutor(max_workers=3)
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


def normalize_dividend_yield(info: dict) -> Optional[float]:
    """Annual dividend yield as a percentage, e.g. 1.52 for 1.52%.

    info["dividendYield"] cannot be trusted on its own. Yahoo returned it as
    a fraction (0.0152) for years and switched to a percentage (1.52) during
    2025, and which one arrives depends on the yfinance version and on what
    the endpoint happens to be serving that day. The old code multiplied by
    100 unconditionally, which turns a 1.5% yield into 152% the moment the
    upstream convention flips - and the intel card prints it to one decimal
    beside a ticker, so it reads as a real number.

    The dividend rate and the share price are unambiguous, so the yield is
    computed from those wherever both are present. The ambiguous field is a
    last resort, disambiguated by magnitude: no listed equity yields 100%,
    so a value above 1 is already a percentage.
    """
    def _num(*keys):
        for key in keys:
            try:
                value = float(info.get(key))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return None

    rate = _num("dividendRate", "trailingAnnualDividendRate")
    price = _num("currentPrice", "regularMarketPrice", "previousClose")
    if rate and price:
        return round(rate / price * 100, 2)

    # trailingAnnualDividendYield has always been a fraction.
    fraction = _num("trailingAnnualDividendYield")
    if fraction:
        return round(fraction * 100, 2)

    raw = _num("dividendYield")
    if raw is None:
        return None
    return round(raw if raw > 1 else raw * 100, 2)


def _dividends_from_yfinance(tickers: list[str], today: date, wl_set: set) -> list[dict]:
    """Fallback: read exDividendDate + dividendYield from yfinance Ticker.info."""
    from datetime import timezone
    results: list[dict] = []

    if _yf_is_rate_limited():
        logger.warning("intel/dividends: yfinance rate-limited — skipping entirely")
        return []

    def _fetch_one(ticker: str) -> Optional[dict]:
        if _yf_is_rate_limited():
            return None
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
            div_yield = normalize_dividend_yield(info)
            company   = _company_name(ticker) or info.get("shortName", "")
            return {
                "ticker":        ticker,
                "company_name":  company,
                "ex_date":       ex_date.isoformat(),
                "ex_date_label": _date_label(ex_date, today),
                "payment_date":  None,
                "days_away":     days_away,
                "div_amount":    round(float(amount), 4) if amount else None,
                "div_yield":     div_yield,
                "on_watchlist":  ticker in wl_set,
                "source":        "yfinance",
            }
        except Exception as e:
            if _is_yf_rate_limit_error(e):
                _yf_set_rate_limited()
            return None

    pool = ThreadPoolExecutor(max_workers=3)
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

    if not results:
        results = _dividends_from_nasdaq(today, wl_set)

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
    "press conference":  "Fed press conference — the tone often moves more than the decision itself.",
    "projections":       "The dot plot — where the committee sees rates going. Moves the long end.",
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
                time_lbl  = _format_time_12h(dt.hour, dt.minute, tz_label="")
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
                # The provider does not document the timezone of its time
                # field, so the card must not claim one. Consumers use this to
                # mark the time rather than dressing it as Eastern.
                "time_zone":  "",
                # Consensus / prior — carried through from the raw Finnhub payload
                # (these were previously dropped). None when Finnhub omits them.
                "estimate":   e.get("estimate"),
                "previous":   e.get("prev"),
                "actual":     e.get("actual"),
                "unit":       e.get("unit") or "",
            })
        events.sort(key=lambda x: x["days_away"])
        return events
    except Exception as e:
        logger.warning("intel/econ finnhub: %s", e)
        return []


# Static fallback calendar.
#
# Finnhub's economic-calendar endpoint is on a premium plan, so in practice
# this list IS the macro calendar, not a fallback. It ran out on 2026-09-16 —
# twelve days after it was last read — and the card would simply have gone
# empty with nothing to say why.
#
# Every date below is taken from the issuing agency's own published schedule:
# BLS for payrolls, CPI and PPI; Census for retail sales; BEA for GDP and
# personal income; the Federal Reserve for FOMC. All times are Eastern, which
# is what these agencies publish in. The previous list had CPI on 2026-09-10 —
# that is the PPI date; August CPI is released on the 11th.
#
# BLS, Census and BEA publish about a year ahead, so this reaches the end of
# 2026; the Fed publishes two years ahead, so FOMC reaches the end of 2027.
# STATIC_ECON_HORIZON below is derived from the last row, and the calendar
# page says the schedule ends rather than showing an empty card.
_STATIC_ECON: list[tuple] = [
    # (date_str, time_str, event_name, impact)
    # ── September 2026 ──
    ("2026-09-04", "8:30 AM ET",  "Non-Farm Payrolls (NFP)",           "HIGH"),
    ("2026-09-04", "8:30 AM ET",  "Unemployment Rate",                 "HIGH"),
    ("2026-09-10", "8:30 AM ET",  "Producer Price Index (PPI)",        "HIGH"),
    ("2026-09-11", "8:30 AM ET",  "Consumer Price Index (CPI)",        "HIGH"),
    ("2026-09-11", "8:30 AM ET",  "Core CPI (MoM)",                    "HIGH"),
    ("2026-09-16", "8:30 AM ET",  "Retail Sales (MoM)",                "HIGH"),
    ("2026-09-16", "2:00 PM ET",  "FOMC Interest Rate Decision",       "HIGH"),
    ("2026-09-16", "2:00 PM ET",  "FOMC Economic Projections",         "HIGH"),
    ("2026-09-16", "2:30 PM ET",  "Fed Press Conference",              "HIGH"),
    ("2026-09-30", "8:30 AM ET",  "GDP (Third Estimate, Q2)",          "MEDIUM"),
    ("2026-09-30", "8:30 AM ET",  "PCE Price Index",                   "HIGH"),
    ("2026-09-30", "8:30 AM ET",  "Core PCE (MoM)",                    "HIGH"),
    # ── October 2026 ──
    ("2026-10-02", "8:30 AM ET",  "Non-Farm Payrolls (NFP)",           "HIGH"),
    ("2026-10-02", "8:30 AM ET",  "Unemployment Rate",                 "HIGH"),
    ("2026-10-14", "8:30 AM ET",  "Consumer Price Index (CPI)",        "HIGH"),
    ("2026-10-14", "8:30 AM ET",  "Core CPI (MoM)",                    "HIGH"),
    ("2026-10-15", "8:30 AM ET",  "Producer Price Index (PPI)",        "HIGH"),
    ("2026-10-15", "8:30 AM ET",  "Retail Sales (MoM)",                "HIGH"),
    ("2026-10-28", "2:00 PM ET",  "FOMC Interest Rate Decision",       "HIGH"),
    ("2026-10-28", "2:30 PM ET",  "Fed Press Conference",              "HIGH"),
    ("2026-10-29", "8:30 AM ET",  "GDP (Advance Estimate, Q3)",        "HIGH"),
    ("2026-10-29", "8:30 AM ET",  "PCE Price Index",                   "HIGH"),
    ("2026-10-29", "8:30 AM ET",  "Core PCE (MoM)",                    "HIGH"),
    # ── November 2026 ──
    ("2026-11-06", "8:30 AM ET",  "Non-Farm Payrolls (NFP)",           "HIGH"),
    ("2026-11-06", "8:30 AM ET",  "Unemployment Rate",                 "HIGH"),
    ("2026-11-10", "8:30 AM ET",  "Consumer Price Index (CPI)",        "HIGH"),
    ("2026-11-10", "8:30 AM ET",  "Core CPI (MoM)",                    "HIGH"),
    ("2026-11-13", "8:30 AM ET",  "Producer Price Index (PPI)",        "HIGH"),
    ("2026-11-17", "8:30 AM ET",  "Retail Sales (MoM)",                "HIGH"),
    ("2026-11-25", "8:30 AM ET",  "GDP (Second Estimate, Q3)",         "MEDIUM"),
    ("2026-11-25", "8:30 AM ET",  "PCE Price Index",                   "HIGH"),
    ("2026-11-25", "8:30 AM ET",  "Core PCE (MoM)",                    "HIGH"),
    # ── December 2026 ──
    ("2026-12-04", "8:30 AM ET",  "Non-Farm Payrolls (NFP)",           "HIGH"),
    ("2026-12-04", "8:30 AM ET",  "Unemployment Rate",                 "HIGH"),
    ("2026-12-09", "2:00 PM ET",  "FOMC Interest Rate Decision",       "HIGH"),
    ("2026-12-09", "2:00 PM ET",  "FOMC Economic Projections",         "HIGH"),
    ("2026-12-09", "2:30 PM ET",  "Fed Press Conference",              "HIGH"),
    ("2026-12-10", "8:30 AM ET",  "Consumer Price Index (CPI)",        "HIGH"),
    ("2026-12-10", "8:30 AM ET",  "Core CPI (MoM)",                    "HIGH"),
    ("2026-12-15", "8:30 AM ET",  "Producer Price Index (PPI)",        "HIGH"),
    ("2026-12-16", "8:30 AM ET",  "Retail Sales (MoM)",                "HIGH"),
    ("2026-12-23", "8:30 AM ET",  "GDP (Third Estimate, Q3)",          "MEDIUM"),
    ("2026-12-23", "8:30 AM ET",  "PCE Price Index",                   "HIGH"),
    ("2026-12-23", "8:30 AM ET",  "Core PCE (MoM)",                    "HIGH"),
    # ── FOMC through 2027. The Fed publishes two years out; the statistical
    #    agencies do not, so these stand alone until the 2027 schedules post.
    ("2027-01-27", "2:00 PM ET",  "FOMC Interest Rate Decision",       "HIGH"),
    ("2027-01-27", "2:30 PM ET",  "Fed Press Conference",              "HIGH"),
    ("2027-03-17", "2:00 PM ET",  "FOMC Interest Rate Decision",       "HIGH"),
    ("2027-03-17", "2:00 PM ET",  "FOMC Economic Projections",         "HIGH"),
    ("2027-03-17", "2:30 PM ET",  "Fed Press Conference",              "HIGH"),
    ("2027-04-28", "2:00 PM ET",  "FOMC Interest Rate Decision",       "HIGH"),
    ("2027-04-28", "2:30 PM ET",  "Fed Press Conference",              "HIGH"),
    ("2027-06-09", "2:00 PM ET",  "FOMC Interest Rate Decision",       "HIGH"),
    ("2027-06-09", "2:00 PM ET",  "FOMC Economic Projections",         "HIGH"),
    ("2027-06-09", "2:30 PM ET",  "Fed Press Conference",              "HIGH"),
    ("2027-07-28", "2:00 PM ET",  "FOMC Interest Rate Decision",       "HIGH"),
    ("2027-07-28", "2:30 PM ET",  "Fed Press Conference",              "HIGH"),
    ("2027-09-15", "2:00 PM ET",  "FOMC Interest Rate Decision",       "HIGH"),
    ("2027-09-15", "2:00 PM ET",  "FOMC Economic Projections",         "HIGH"),
    ("2027-09-15", "2:30 PM ET",  "Fed Press Conference",              "HIGH"),
    ("2027-10-27", "2:00 PM ET",  "FOMC Interest Rate Decision",       "HIGH"),
    ("2027-10-27", "2:30 PM ET",  "Fed Press Conference",              "HIGH"),
    ("2027-12-08", "2:00 PM ET",  "FOMC Interest Rate Decision",       "HIGH"),
    ("2027-12-08", "2:00 PM ET",  "FOMC Economic Projections",         "HIGH"),
    ("2027-12-08", "2:30 PM ET",  "Fed Press Conference",              "HIGH"),
]

# The last date this schedule knows about. A hardcoded calendar always runs
# out; the only question is whether it says so or goes quietly blank.
STATIC_ECON_HORIZON: str = max(row[0] for row in _STATIC_ECON)

# The statistical agencies stop well before the Fed does, so past this date
# the calendar is FOMC meetings and nothing else - which would read as a
# quiet couple of months rather than a gap in the data.
STATIC_ECON_FULL_THROUGH: str = max(
    row[0] for row in _STATIC_ECON
    if "FOMC" not in row[2] and "Fed Press" not in row[2])


def static_econ_coverage(today=None) -> dict:
    """How far the built-in schedule still reaches, for the page to report."""
    today = today or _today_et()
    return {
        "horizon": STATIC_ECON_HORIZON,
        "full_through": STATIC_ECON_FULL_THROUGH,
        "days_of_full_coverage": (
            datetime.strptime(STATIC_ECON_FULL_THROUGH, "%Y-%m-%d").date() - today).days,
        "exhausted": today.isoformat() > STATIC_ECON_HORIZON,
    }


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
            "time_zone":  "ET",
            "impact":     impact,
            "reason":     _econ_reason(event_name),
            "days_away":  days_away,
            "is_today":   days_away == 0,
            # Static fallback has no consensus/prior data — leave None (→ shown as —)
            "estimate":   None,
            "previous":   None,
            "actual":     None,
            "unit":       "",
        })
    return events


def _month_abbr(m: int) -> str:
    return ["Jan","Feb","Mar","Apr","May","Jun",
            "Jul","Aug","Sep","Oct","Nov","Dec"][m - 1]


def _format_time_12h(hour: int, minute: int, tz_label: str = "ET") -> str:
    """A clock time, with a timezone label only where one is known.

    This stamped " ET" on every macro event, including the ones parsed
    straight out of the Finnhub payload — whose timezone Finnhub does not
    document. If that field is UTC, every release on the card was labelled
    Eastern and was four or five hours wrong, which for an 8:30 print is the
    difference between before the open and after lunch. The static fallback
    times below are hand-entered Eastern and keep the label; the provider's
    are shown as supplied until the convention is confirmed.
    """
    suffix = "AM" if hour < 12 else "PM"
    h = hour % 12 or 12
    return f"{h}:{minute:02d} {suffix}{' ' + tz_label if tz_label else ''}"


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

    # Every feed must independently re-trigger its refresh when its own TTL
    # expires. Previously news could remain None forever while longer-lived
    # earnings/economic caches were still warm.
    with _news_state_lock:
        news_in_cooldown = c_news is None and _time.monotonic() < _news_retry_after
        news_is_refreshing = _news_refreshing
        news_failure = _news_last_failure
    news_refresh_requested = c_news is None and not news_in_cooldown and not news_failure
    is_cold = (
        (c_news is None and not news_in_cooldown)
        or (c_earn is None)
        or (c_split is None)
        or (c_econ is None)
    )
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

    from news_fetcher import news_source_status
    news_status = news_source_status()
    news_status_refreshing = news_is_refreshing or news_refresh_requested
    news_status.update({
        "count": len(c_news or []),
        "refreshing": news_status_refreshing,
        "empty": not bool(c_news),
        "last_error": news_failure,
    })
    if not c_news:
        if news_status_refreshing:
            news_status["message"] = "News refresh is running. This page will update when provider responses arrive."
        elif news_failure:
            news_status["message"] = news_failure
        elif news_status["configured"]:
            news_status["message"] = "Connected news providers returned no headlines. Refresh the feed or check provider limits."
        else:
            news_status["message"] = "Free news fallbacks returned no headlines. Add FINNHUB_API_KEY, NEWS_API_KEY, or POLYGON_API_KEY on Render for reliable coverage."

    # Extract earnings buckets and debug meta separately
    _earn       = c_earn or {}
    earn_buckets = {
        "today":      list(_earn.get("today",      [])),
        "tomorrow":   list(_earn.get("tomorrow",   [])),
        "this_week":  list(_earn.get("this_week",  [])),
        "coming_up":  list(_earn.get("coming_up",  [])),
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
        "news_status":        news_status,
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
            "nasdaq_found":         earn_meta.get("nasdaq_found", 0),
            "splits_count":         len(c_split or []),
            "dividends_count":      len(c_div   or []),
        },
        "ndx_watch":       get_ndx_watch_data(),
        "alerts_sent":     [],
        "from_cache":      not is_cold,
        "refreshing":      currently_refreshing,
    }


# ── Nasdaq-100 Constituent Tracker ────────────────────────────────────────────

def _ndx_parse_invesco_csv(raw: str) -> list:
    """Parse Invesco QQQ holdings CSV.  Handles preamble rows before the header."""
    import csv, io
    lines = raw.splitlines()
    csv_start = 0
    for i, line in enumerate(lines):
        lower = line.lower()
        if "ticker" in lower or "symbol" in lower:
            csv_start = i
            break
    csv_text = "\n".join(lines[csv_start:])
    results = []
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            ticker = (
                row.get("Ticker") or row.get("ticker") or
                row.get("Symbol") or row.get("symbol") or ""
            ).strip().upper()
            if not ticker or len(ticker) > 6:
                continue
            if not all(c.isalpha() or c in (".", "-") for c in ticker):
                continue
            name = (
                row.get("Name") or row.get("name") or
                row.get("Security Name") or ""
            ).strip()
            weight_raw = (
                row.get("Weight") or row.get("weight") or
                row.get("% Weight") or row.get("Weightings") or "0"
            ).strip().replace("%", "").replace(",", "")
            try:
                weight = float(weight_raw)
            except (ValueError, TypeError):
                weight = 0.0
            results.append({"ticker": ticker, "company_name": name, "weight_pct": weight})
    except Exception as exc:
        logger.debug("intel/ndx csv parse: %s", exc)
    results.sort(key=lambda x: x["weight_pct"], reverse=True)
    return results


def _ndx_from_invesco() -> list:
    """Fetch QQQ holdings CSV from Invesco.com."""
    import urllib.request
    url = (
        "https://www.invesco.com/us/financial-products/etfs/holdings/main/holdings/0"
        "?audienceType=Investor&action=download&ticker=QQQ"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept":   "text/csv,application/csv,text/plain,*/*",
        "Referer":  "https://www.invesco.com/us/financial-products/etfs/product-detail?t=QQQ",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        if "<html" in raw[:200].lower():
            logger.debug("intel/ndx invesco returned HTML, not CSV")
            return []
        result = _ndx_parse_invesco_csv(raw)
        logger.info("intel/ndx invesco: parsed %d constituents", len(result))
        return result
    except Exception as exc:
        logger.debug("intel/ndx invesco fetch: %s", exc)
        return []


def fetch_ndx_constituents() -> list:
    """Fetch the current Nasdaq-100 constituent list from Invesco QQQ holdings CSV.
    Returns [{ticker, weight_pct, company_name}] sorted by weight desc.
    Cached for 24 hours.
    """
    cached = _cget("ndx")
    if cached is not None:
        return cached
    result = _ndx_from_invesco()
    if result:
        _cset("ndx", result)
        logger.info("intel/ndx: %d constituents fetched and cached", len(result))
    else:
        logger.warning("intel/ndx: fetch failed — no data available")
    return result


def run_ndx_constituent_check() -> dict:
    """Fetch current NDX constituents, diff against last DB snapshot, persist changes.
    Returns {added, removed, snapshot_date, total}.
    Safe to call repeatedly — only writes a new snapshot when date changes.
    """
    try:
        from database import (
            get_latest_ndx_snapshot, save_ndx_snapshot,
            save_ndx_change, add_scanner_alert,
        )
    except Exception as exc:
        logger.warning("intel/ndx: db import failed: %s", exc)
        return {"added": [], "removed": [], "snapshot_date": "", "total": 0}

    today_str = _today_et().isoformat()
    constituents = fetch_ndx_constituents()
    if not constituents:
        return {"added": [], "removed": [], "snapshot_date": today_str, "total": 0, "error": "fetch_failed"}

    current_tickers = {c["ticker"] for c in constituents}
    ticker_info     = {c["ticker"]: c for c in constituents}
    prev_date, prev_tickers = get_latest_ndx_snapshot()

    added   = []
    removed = []

    if prev_tickers:
        added   = sorted(current_tickers - prev_tickers)
        removed = sorted(prev_tickers   - current_tickers)

        for ticker in added:
            info = ticker_info.get(ticker, {})
            save_ndx_change(ticker, "added", today_str, info.get("company_name", ""))
            dk = _daily_key(ticker, "ndx_added")
            if should_send_alert(dk):
                name_part = f" ({info['company_name']})" if info.get("company_name") else ""
                add_scanner_alert(
                    ticker,
                    "NDX_ADDED",
                    f"Added to Nasdaq-100: {ticker}{name_part}",
                    severity="high",
                )
                logger.info("intel/ndx: ADDED  ticker=%s", ticker)

        for ticker in removed:
            save_ndx_change(ticker, "removed", today_str, "")
            dk = _daily_key(ticker, "ndx_removed")
            if should_send_alert(dk):
                add_scanner_alert(
                    ticker,
                    "NDX_REMOVED",
                    f"Removed from Nasdaq-100: {ticker}",
                    severity="medium",
                )
                logger.info("intel/ndx: REMOVED  ticker=%s", ticker)

    if prev_date != today_str:
        save_ndx_snapshot(constituents, today_str)

    # Bust the ndx_watch cache so get_intel_summary() picks up fresh DB data
    with _cache_lock:
        _cache.pop("ndx_watch", None)

    return {
        "added":         added,
        "removed":       removed,
        "snapshot_date": today_str,
        "total":         len(current_tickers),
    }


def get_ndx_watch_data() -> dict:
    """Return NDX 100 Watch data for the Intel page.

    Hot-path safe: result is cached in-process for 1 h so that the two DB
    queries (changes + top-holdings) are NOT run on every /api/intel call.
    The background refresh calls _populate_ndx_watch_cache() which updates
    the cache after the constituent check completes.
    """
    cached = _cget("ndx_watch")
    if cached is not None:
        return cached
    result = _build_ndx_watch_payload()
    _cset("ndx_watch", result)
    return result


def _build_ndx_watch_payload() -> dict:
    """Fetch NDX watch data from DB using a single connection, return payload dict."""
    recent_changes: list = []
    top_holdings:   list = []
    try:
        from database import get_db as _get_db
        conn = _get_db()
        try:
            # Recent constituent changes
            rows = conn.execute(
                "SELECT * FROM ndx_changes ORDER BY id DESC LIMIT 20"
            ).fetchall()
            recent_changes = [dict(r) for r in rows]

            # Top holdings from most recent snapshot (single query)
            snap_row = conn.execute(
                "SELECT snapshot_date FROM ndx_constituents "
                "ORDER BY snapshot_date DESC LIMIT 1"
            ).fetchone()
            if snap_row:
                snap_date = snap_row["snapshot_date"]
                h_rows = conn.execute(
                    "SELECT ticker, weight_pct, company_name "
                    "FROM ndx_constituents WHERE snapshot_date = ? "
                    "ORDER BY weight_pct DESC LIMIT 15",
                    (snap_date,),
                ).fetchall()
                top_holdings = [dict(r) for r in h_rows]
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("intel/ndx _build_watch_payload: %s", exc)

    cached_constituents = _cget("ndx") or []
    total = len(cached_constituents) if cached_constituents else len(top_holdings)
    return {
        "recent_changes": recent_changes,
        "top_holdings":   top_holdings,
        "total_members":  total,
        "last_checked":   _et_now().strftime("%m/%d %I:%M %p ET") if cached_constituents else "—",
    }
