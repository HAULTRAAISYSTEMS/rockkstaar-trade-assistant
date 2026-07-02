"""
data_fetcher.py - Live market data via Polygon.io (primary) + yfinance (fallback).
Provides fetch_live_data() and fetch_news_headlines() for use in generate_stock_data().

Data source priority:
  1. Polygon.io   — reliable cloud API, no rate-limit blocks on server IPs.
                    Requires POLYGON_API_KEY env var. Free tier = 15-min delayed
                    during market hours; Starter ($29/mo) = real-time.
  2. yfinance     — original source; kept as fallback when Polygon key is absent
                    or a Polygon call fails.
  3. Yahoo chart API — direct HTTP fallback that bypasses yfinance's crumb system.

If POLYGON_API_KEY is not set, behavior is identical to the original version.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from datetime import datetime, date, timedelta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Finnhub helpers  (quote data — used when Polygon key is absent)
# ---------------------------------------------------------------------------

_FINNHUB_KEY: str | None = os.environ.get("FINNHUB_API_KEY") or None


def _fetch_finnhub_quote(ticker: str) -> dict | None:
    """
    Fetch current quote from Finnhub /quote endpoint.
    Returns dict with current_price, prev_close, gap_pct, today high/low/open,
    or None on failure.  Free tier: 60 req/min — adequate for on-demand page loads.
    """
    if not _FINNHUB_KEY:
        return None
    try:
        import requests as _req
        r = _req.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": ticker, "token": _FINNHUB_KEY},
            timeout=8,
        )
        if r.status_code != 200:
            logger.debug("Finnhub quote %s → HTTP %s", ticker, r.status_code)
            return None
        d = r.json()
        current_price = d.get("c")
        if not current_price or float(current_price) <= 0:
            return None  # market closed / unknown symbol returns 0
        out: dict = {"current_price": round(float(current_price), 2)}
        pc = d.get("pc")
        if pc and float(pc) > 0:
            out["prev_close"] = round(float(pc), 2)
            out["gap_pct"]    = round(
                (out["current_price"] - out["prev_close"]) / out["prev_close"] * 100, 2
            )
        if d.get("h") and float(d["h"]) > 0:
            out["prev_day_high"] = round(float(d["h"]), 2)
        if d.get("l") and float(d["l"]) > 0:
            out["prev_day_low"] = round(float(d["l"]), 2)
        logger.info("Finnhub quote %s → price=%.2f gap=%s%%",
                    ticker, out["current_price"], out.get("gap_pct"))
        return out
    except Exception as _e:
        logger.debug("_fetch_finnhub_quote failed %s: %s", ticker, _e)
        return None


# ---------------------------------------------------------------------------
# Polygon.io helpers
# ---------------------------------------------------------------------------

_POLYGON_KEY: str | None = os.environ.get("POLYGON_API_KEY") or None
_POLYGON_BASE = "https://api.polygon.io"


def _polygon_get(path: str, params: dict | None = None) -> dict | None:
    """
    Make a GET request to the Polygon.io REST API.
    Returns the parsed JSON dict on HTTP 200, or None on any error.
    """
    if not _POLYGON_KEY:
        return None
    try:
        import requests as _req
        p = dict(params or {})
        p["apiKey"] = _POLYGON_KEY
        r = _req.get(f"{_POLYGON_BASE}{path}", params=p, timeout=8)
        if r.status_code == 200:
            return r.json()
        logger.debug("Polygon %s → HTTP %s", path, r.status_code)
    except Exception as _e:
        logger.debug("Polygon request failed %s: %s", path, _e)
    return None


def _fetch_polygon_snapshot(ticker: str) -> dict | None:
    """
    Fetch current price, prev close, today's volume, and avg volume from
    Polygon's snapshot endpoint.

    Returns a dict with any of:
        current_price, prev_close, avg_volume, rel_volume, gap_pct
    or None on failure.
    """
    data = _polygon_get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}")
    if not data:
        return None
    try:
        ticker_node = data.get("ticker") or {}
        day   = ticker_node.get("day") or {}
        prev  = ticker_node.get("prevDay") or {}

        current_price = ticker_node.get("lastTrade", {}).get("p") or day.get("c")
        if not current_price or float(current_price) <= 0:
            return None

        out: dict = {"current_price": round(float(current_price), 2)}

        pc = prev.get("c")
        if pc and float(pc) > 0:
            out["prev_close"] = round(float(pc), 2)
            out["gap_pct"]    = round((out["current_price"] - out["prev_close"]) / out["prev_close"] * 100, 2)

        # Volume — today's vs 30-day average (Polygon provides vw = VWAP, v = volume)
        today_vol = day.get("v")
        avg_vol   = ticker_node.get("prevDay", {}).get("v")  # best proxy available in snapshot
        if today_vol and float(today_vol) > 0:
            out["today_volume"] = int(float(today_vol))
            # Polygon snapshot doesn't expose 30-day avg directly; skip rel_volume here
            # (fetched separately in _fetch_polygon_prev_close or left to yfinance fallback)

        logger.info(
            "Polygon snapshot %s → price=%.2f prev_close=%s",
            ticker, out["current_price"], out.get("prev_close"),
        )
        return out
    except Exception as _e:
        logger.debug("_fetch_polygon_snapshot parse failed %s: %s", ticker, _e)
        return None


def _fetch_polygon_prev_close(ticker: str) -> dict | None:
    """
    Fetch previous trading day OHLCV from Polygon's /v2/aggs/ticker/{t}/prev endpoint.

    Returns dict with prev_close, prev_day_high, prev_day_low, prev_close_date,
    avg_volume (30-day), rel_volume — or None on failure.
    """
    data = _polygon_get(f"/v2/aggs/ticker/{ticker}/prev", {"adjusted": "true"})
    if not data:
        return None
    try:
        results = data.get("results") or []
        if not results:
            return None
        bar = results[0]
        pc  = bar.get("c")
        if not pc or float(pc) <= 0:
            return None
        out = {
            "prev_close":      round(float(pc), 2),
            "prev_day_high":   round(float(bar["h"]), 2),
            "prev_day_low":    round(float(bar["l"]), 2),
        }
        # Timestamp is milliseconds epoch → date string
        ts_ms = bar.get("t")
        if ts_ms:
            from datetime import timezone
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            out["prev_close_date"] = dt.strftime("%Y-%m-%d")
        logger.info("Polygon prev close %s → %s", ticker, out)
        return out
    except Exception as _e:
        logger.debug("_fetch_polygon_prev_close parse failed %s: %s", ticker, _e)
        return None


def _fetch_polygon_daily_bars(ticker: str, days: int = 252) -> dict | None:
    """
    Fetch up to *days* of daily OHLCV bars from Polygon for EMA/fib computation.

    Returns dict with keys: closes, highs, lows (all list[float]), or None.
    """
    from datetime import timezone
    to_date   = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    from_date = (datetime.now(tz=timezone.utc) - timedelta(days=days + 60)).strftime("%Y-%m-%d")
    data = _polygon_get(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}",
        {"adjusted": "true", "sort": "asc", "limit": days + 60},
    )
    if not data:
        return None
    try:
        results = data.get("results") or []
        if len(results) < 20:
            return None
        closes = [float(r["c"]) for r in results if r.get("c")]
        highs  = [float(r["h"]) for r in results if r.get("h")]
        lows   = [float(r["l"]) for r in results if r.get("l")]
        if len(closes) < 20:
            return None
        logger.info("Polygon daily bars %s → %d bars", ticker, len(closes))
        return {"closes": closes, "highs": highs, "lows": lows}
    except Exception as _e:
        logger.debug("_fetch_polygon_daily_bars parse failed %s: %s", ticker, _e)
        return None


def _fetch_polygon_intraday(ticker: str) -> dict | None:
    """
    Fetch today's 1-minute bars from Polygon for ORB, VWAP, and premarket range.

    Returns dict with keys: timestamps, opens, closes, highs, lows, volumes
    where timestamps are Unix seconds (ET-aware), or None on failure.

    Note: Polygon free tier returns 15-min delayed data during market hours.
    Upgrade to Starter ($29/mo) for real-time intraday bars.
    """
    now_et = _et_now()
    date_str = now_et.strftime("%Y-%m-%d")
    # Request from 4 AM to cover premarket
    from_ts = f"{date_str}T04:00:00"
    to_ts   = f"{date_str}T20:00:00"
    data = _polygon_get(
        f"/v2/aggs/ticker/{ticker}/range/1/minute/{date_str}/{date_str}",
        {"adjusted": "true", "sort": "asc", "limit": 1000},
    )
    if not data:
        return None
    try:
        results = data.get("results") or []
        if not results:
            return None
        # Convert ms timestamps → seconds
        timestamps = [int(r["t"] / 1000) for r in results]
        opens      = [float(r.get("o", 0)) for r in results]
        closes     = [float(r.get("c", 0)) for r in results]
        highs      = [float(r.get("h", 0)) for r in results]
        lows       = [float(r.get("l", 0)) for r in results]
        volumes    = [int(r.get("v", 0)) for r in results]
        logger.info("Polygon intraday %s → %d 1m bars", ticker, len(results))
        return {
            "timestamps": timestamps,
            "opens": opens, "closes": closes,
            "highs": highs, "lows": lows,
            "volumes": volumes,
        }
    except Exception as _e:
        logger.debug("_fetch_polygon_intraday parse failed %s: %s", ticker, _e)
        return None


def _yf_history_with_timeout(yf_ticker, timeout_s: int = 15, **kwargs):
    """
    Call yf_ticker.history(**kwargs) with a hard wall-clock timeout.

    yfinance's history() can hang indefinitely on cloud IPs when Yahoo Finance
    does not respond (rate-limit, crumb failure, network stall).  This wrapper
    runs the call in a daemon thread and abandons it after *timeout_s* seconds,
    returning an empty DataFrame so the caller can fall back to the chart API.

    Returns the DataFrame on success, an empty DataFrame on timeout/error.
    """
    try:
        import pandas as _pd
    except ImportError:
        return None

    result_box: list = [None]
    exc_box:    list = [None]

    def _call():
        try:
            result_box[0] = yf_ticker.history(**kwargs)
        except Exception as _e:
            exc_box[0] = _e

    t = threading.Thread(target=_call, daemon=True)
    t.start()
    t.join(timeout=timeout_s)

    if t.is_alive():
        logger.warning(
            "_yf_history_with_timeout: abandoned after %ds — kwargs=%s",
            timeout_s, kwargs,
        )
        return _pd.DataFrame()   # empty → triggers chart API fallback

    if exc_box[0]:
        raise exc_box[0]        # re-raise so callers' existing except blocks fire

    return result_box[0]


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

try:
    import yfinance as yf
    import requests as _requests
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False
    logger.warning("yfinance not installed — live data unavailable. Run: pip install yfinance")

# Browser-like session — prevents Yahoo Finance from blocking cloud/server IPs.
# Render, Railway, and other cloud hosts are commonly blocked without this.
_YF_SESSION: "_requests.Session | None" = None

def _get_yf_session():
    """Return a cached requests.Session with browser headers for yfinance."""
    global _YF_SESSION
    if _YF_SESSION is None and _YF_AVAILABLE:
        _YF_SESSION = _requests.Session()
        _YF_SESSION.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })
    return _YF_SESSION


def _fetch_ohlcv_via_chart_api(
    ticker: str,
    interval: str = "1d",
    range_str: str = "1y",
) -> dict | None:
    """
    Fetch OHLCV bars directly from Yahoo Finance's chart API.

    This bypasses yfinance's history() call (which requires a crumb token and
    fails on cloud IPs like Render).  The chart endpoint has no such requirement
    and returns the same daily bars needed for EMA and Fibonacci computation.

    Parameters
    ----------
    interval  : "1d" | "1h" | "15m" | "1m"
    range_str : "5d" | "30d" | "1y" | "2y"
                Use "1y" for daily EMAs/fibs (≥252 bars for 200 EMA).
                Use "30d" for hourly 4H-proxy bars.
                Use "5d" for 15m confirmation bars.

    Returns a dict:
        timestamps  list[int]   — Unix timestamps
        closes      list[float]
        opens       list[float]
        highs       list[float]
        lows        list[float]
        volumes     list[int]
    Or None on failure.
    """
    _CHART_URLS = [
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
        f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}",
    ]
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
    params = {"interval": interval, "range": range_str}

    for url in _CHART_URLS:
        try:
            import requests as _req
            r = _req.get(url, params=params, headers=headers, timeout=8)
            if r.status_code != 200:
                continue
            data        = r.json()
            result_node = data["chart"]["result"][0]
            timestamps  = result_node.get("timestamp", [])
            quote       = result_node["indicators"]["quote"][0]

            closes  = quote.get("close",  [])
            opens   = quote.get("open",   [])
            highs   = quote.get("high",   [])
            lows    = quote.get("low",    [])
            volumes = quote.get("volume", [])

            # Filter out bars with None close (incomplete bars, market holidays)
            valid = [
                (t, o, c, h, lo, v)
                for t, o, c, h, lo, v in zip(timestamps, opens, closes, highs, lows, volumes)
                if c is not None and c > 0
            ]
            if not valid:
                continue

            ts, ops, cls, hs, ls, vs = zip(*valid)
            return {
                "timestamps": list(ts),
                "closes":     [float(x) for x in cls],
                "opens":      [float(x) for x in ops],
                "highs":      [float(x) for x in hs],
                "lows":       [float(x) for x in ls],
                "volumes":    [int(x) if x else 0 for x in vs],
            }
        except Exception as _e:
            logger.debug(
                "_fetch_ohlcv_via_chart_api failed for %s interval=%s range=%s via %s: %s",
                ticker, interval, range_str, url, _e,
            )
            continue
    return None


def _fetch_price_via_chart_api(ticker: str) -> dict | None:
    """
    Fetch current price AND prev_close directly from Yahoo Finance's chart API.

    This endpoint does not require cookies/crumb and works from cloud IPs
    where yfinance's fast_info / history endpoints are blocked or rate-limited.

    Returns a dict with keys: current_price, prev_close (both floats > 0),
    or None on any error so callers can fall back further.
    """
    _CHART_URLS = [
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
        f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}",
    ]
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
    params = {"interval": "1d", "range": "5d"}
    for url in _CHART_URLS:
        try:
            import requests as _req
            r = _req.get(url, params=params, headers=headers, timeout=8)
            if r.status_code != 200:
                continue
            data = r.json()
            result_node = data["chart"]["result"][0]
            meta        = result_node["meta"]

            # Current price: regularMarketPrice is the live/last traded price
            price = meta.get("regularMarketPrice") or meta.get("chartPreviousClose")
            if not price or float(price) <= 0:
                continue

            out = {"current_price": round(float(price), 2)}

            # ── Pull OHLCV bars from the indicators node ─────────────────────
            # With range=5d we get ~5 complete trading days.  The last bar in
            # the series is the most recent completed session (prev trading day).
            try:
                quote      = result_node["indicators"]["quote"][0]
                timestamps = result_node.get("timestamp", [])
                closes  = quote.get("close",  [])
                highs   = quote.get("high",   [])
                lows    = quote.get("low",    [])

                # Filter to bars with a valid close price
                valid_bars = [
                    (t, c, h, lo)
                    for t, c, h, lo in zip(timestamps, closes, highs, lows)
                    if c is not None and c > 0
                ]

                if len(valid_bars) >= 2:
                    # Second-to-last bar = previous completed trading session
                    _, prev_c, prev_h, prev_lo = valid_bars[-2]
                    out["prev_close"]    = round(float(prev_c),  2)
                    out["prev_day_high"] = round(float(prev_h),  2)
                    out["prev_day_low"]  = round(float(prev_lo), 2)
                elif len(valid_bars) == 1:
                    _, prev_c, prev_h, prev_lo = valid_bars[0]
                    out["prev_close"]    = round(float(prev_c),  2)
                    out["prev_day_high"] = round(float(prev_h),  2)
                    out["prev_day_low"]  = round(float(prev_lo), 2)
            except Exception:
                # Fallback: use meta previousClose when bars unavailable
                prev = meta.get("previousClose") or meta.get("chartPreviousClose")
                if prev and float(prev) > 0:
                    out["prev_close"] = round(float(prev), 2)

            return out

        except Exception as _e:
            logger.debug("chart API fallback failed for %s via %s: %s", ticker, url, _e)
            continue
    return None


def _et_now() -> datetime:
    """Current time in US/Eastern — handles EST/EDT via zoneinfo (Python 3.9+)."""
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("America/New_York")
        return datetime.now(tz)
    except Exception:
        # Fallback: assume EDT (UTC-4). Accepts a 1-hour error in winter for ORB math.
        from datetime import timezone, timedelta
        return datetime.now(timezone(timedelta(hours=-4)))


def market_session_now() -> str:
    """
    Return the current US market session based on US/Eastern time.

    Sessions (weekdays only — weekends always return 'closed'):
      "pre_market"  — 04:00–09:29 ET  (pre-market trading, no ORB)
      "regular"     — 09:30–16:00 ET  (regular session, full signals active)
      "after_hours" — 16:01–20:00 ET  (after-hours trading, signals display-only)
      "closed"      — all other times (overnight / weekends)

    This is the single source of truth for whether live trading signals
    (TRIGGERED, EXECUTE) are currently actionable.
    """
    now = _et_now()
    # Weekends are always closed
    if now.weekday() >= 5:
        return "closed"
    h, m = now.hour, now.minute
    total_min = h * 60 + m
    if total_min < 4 * 60:            # before 04:00
        return "closed"
    if total_min < 9 * 60 + 30:      # 04:00–09:29
        return "pre_market"
    if total_min <= 16 * 60:          # 09:30–16:00
        return "regular"
    if total_min <= 20 * 60:          # 16:01–20:00
        return "after_hours"
    return "closed"                   # after 20:00


def orb_phase_now() -> str:
    """
    Return the current ORB phase based on US/Eastern time:
      "pre_market" — before 9:30 AM ET (no ORB data yet)
      "forming"    — 9:30–10:00 AM ET (ORB window open; show live partial levels)
      "locked"     — after 10:01 AM ET (ORB window closed; levels are final)
    """
    now = _et_now()
    h, m = now.hour, now.minute
    if h < 9 or (h == 9 and m < 30):
        return "pre_market"
    if (h == 9 and m >= 30) or (h == 10 and m == 0):
        return "forming"
    return "locked"


def fetch_live_data(ticker: str) -> dict | None:
    """
    Fetch live price, volume, and ORB data from Yahoo Finance.

    Returns a dict with any subset of these fields (only populated ones):
        current_price, prev_close, gap_pct,
        premarket_high, premarket_low,
        prev_day_high, prev_day_low,
        avg_volume, rel_volume,
        orb_high, orb_low,
        earnings_date

    Returns None if yfinance is unavailable or the fetch fails entirely.
    """
    # ------------------------------------------------------------------ #
    # 0. Polygon.io — primary source (used when POLYGON_API_KEY is set)
    #
    #    Fetches: current_price, prev_close, prev_day_high/low, gap_pct,
    #             intraday bars (ORB, VWAP, premarket, trend_structure).
    #    Falls through to yfinance when Polygon key is absent or call fails.
    # ------------------------------------------------------------------ #
    if _POLYGON_KEY:
        try:
            result: dict = {}

            # --- Price + prev close ---
            snap = _fetch_polygon_snapshot(ticker)
            prev = _fetch_polygon_prev_close(ticker)

            if snap and snap.get("current_price"):
                result["current_price"] = snap["current_price"]
                if snap.get("prev_close"):
                    result["prev_close"] = snap["prev_close"]
                if snap.get("gap_pct") is not None:
                    result["gap_pct"] = snap["gap_pct"]

            if prev:
                # prev close from /prev endpoint is more authoritative
                result["prev_close"]      = prev["prev_close"]
                result["prev_day_high"]   = prev["prev_day_high"]
                result["prev_day_low"]    = prev["prev_day_low"]
                if prev.get("prev_close_date"):
                    result["prev_close_date"] = prev["prev_close_date"]
                # Recompute gap with authoritative prev_close
                if result.get("current_price") and result["prev_close"] > 0:
                    result["gap_pct"] = round(
                        (result["current_price"] - result["prev_close"]) / result["prev_close"] * 100, 2
                    )

            # --- Intraday bars (ORB, VWAP, premarket) ---
            if result.get("current_price"):
                intra_bars = _fetch_polygon_intraday(ticker)
                if intra_bars:
                    from datetime import timezone as _tz
                    today_str = _et_now().strftime("%Y-%m-%d")
                    now_et    = _et_now()
                    h, m      = now_et.hour, now_et.minute

                    # ORB phase
                    if h < 9 or (h == 9 and m < 30):
                        result["orb_phase"] = "pre_market"
                    elif (h == 9 and m >= 30) or (h == 10 and m == 0):
                        result["orb_phase"] = "forming"
                    else:
                        result["orb_phase"] = "locked"

                    # Split bars by session using ET timestamps
                    import datetime as _dt
                    et_tz = None
                    try:
                        import zoneinfo
                        et_tz = zoneinfo.ZoneInfo("America/New_York")
                    except Exception:
                        pass

                    pm_highs = []
                    pm_lows  = []
                    orb_highs = []
                    orb_lows  = []
                    sess_closes  = []
                    sess_highs   = []
                    sess_lows    = []
                    sess_opens   = []
                    sess_volumes = []

                    for i, ts in enumerate(intra_bars["timestamps"]):
                        if et_tz:
                            bar_dt = _dt.datetime.fromtimestamp(ts, tz=et_tz)
                        else:
                            bar_dt = _dt.datetime.utcfromtimestamp(ts - 4 * 3600)
                        bh, bm = bar_dt.hour, bar_dt.minute
                        c  = intra_bars["closes"][i]
                        hv = intra_bars["highs"][i]
                        lv = intra_bars["lows"][i]
                        o  = intra_bars["opens"][i]
                        vol = intra_bars["volumes"][i]

                        # Premarket: 04:00–09:29
                        if (bh >= 4) and (bh < 9 or (bh == 9 and bm < 30)):
                            pm_highs.append(hv)
                            pm_lows.append(lv)

                        # ORB window: 09:30–10:00
                        if (bh == 9 and bm >= 30) or (bh == 10 and bm == 0):
                            orb_highs.append(hv)
                            orb_lows.append(lv)

                        # Regular session: 09:30+
                        if bh > 9 or (bh == 9 and bm >= 30):
                            sess_closes.append(c)
                            sess_highs.append(hv)
                            sess_lows.append(lv)
                            sess_opens.append(o)
                            sess_volumes.append(vol)

                    if pm_highs:
                        result["premarket_high"] = round(max(pm_highs), 2)
                        result["premarket_low"]  = round(min(pm_lows),  2)

                    if orb_highs and result.get("orb_phase") in ("forming", "locked"):
                        result["orb_high"] = round(max(orb_highs), 2)
                        result["orb_low"]  = round(min(orb_lows),  2)

                    if sess_closes:
                        # VWAP
                        tp_sum  = sum((h + l + c) / 3 * v
                                      for h, l, c, v in zip(sess_highs, sess_lows, sess_closes, sess_volumes))
                        vol_sum = sum(sess_volumes)
                        if vol_sum > 0:
                            result["vwap"] = round(tp_sum / vol_sum, 2)

                        # Trend structure (HH + HL on last 3 bars)
                        if len(sess_highs) >= 3:
                            higher_highs = sess_highs[-1] > sess_highs[-2] > sess_highs[-3]
                            higher_lows  = sess_lows[-1]  > sess_lows[-2]  > sess_lows[-3]
                        else:
                            higher_highs = higher_lows = False
                        result["higher_highs"]    = higher_highs
                        result["higher_lows"]     = higher_lows
                        result["trend_structure"] = higher_highs and higher_lows

                        vwap_now = result.get("vwap")
                        cur_now  = result.get("current_price")
                        result["price_above_vwap"] = bool(vwap_now and cur_now and cur_now > vwap_now)

                        # Strong candle bodies
                        if len(sess_closes) >= 3:
                            _last3_c = sess_closes[-3:]
                            _last3_o = sess_opens[-3:]
                            _last3_h = sess_highs[-3:]
                            _last3_l = sess_lows[-3:]
                            bodies = [abs(c - o) for c, o in zip(_last3_c, _last3_o)]
                            ranges = [h - l for h, l in zip(_last3_h, _last3_l)]
                            valid  = [r for r in ranges if r > 0.001]
                            if len(valid) >= 2:
                                ratios = [b / r for b, r in zip(bodies, ranges) if r > 0.001]
                                result["strong_candle_bodies"] = all(r > 0.5 for r in ratios)
                            else:
                                result["strong_candle_bodies"] = False
                        else:
                            result["strong_candle_bodies"] = False

                        # ORB hold + momentum breakout
                        orb_h = result.get("orb_high")
                        vwap  = result.get("vwap")
                        cur   = result.get("current_price")
                        if orb_h and cur and cur > orb_h:
                            candles_above = 0
                            for c in reversed(sess_closes):
                                if c > orb_h:
                                    candles_above += 1
                                else:
                                    break
                            result["candles_above_orb"] = candles_above
                            result["orb_hold"]          = candles_above >= 2
                        else:
                            result["candles_above_orb"] = 0
                            result["orb_hold"]          = False

                        if orb_h and vwap and cur and cur > orb_h and cur > vwap:
                            vol_inc = (
                                len(sess_volumes) >= 3
                                and sess_volumes[-2] > sess_volumes[-3]
                                and sess_volumes[-1] > sess_volumes[-2]
                            )
                            result["momentum_breakout"] = (
                                result["candles_above_orb"] >= 3 and vol_inc
                            )
                        else:
                            result["momentum_breakout"] = False

                # Earnings not available from Polygon on free tier — leave for yfinance path
                # or skip entirely (non-critical field)

            if result.get("current_price"):
                logger.info(
                    "fetch_live_data: Polygon SUCCESS %s price=%.2f",
                    ticker, result["current_price"],
                )
                return result

        except Exception as _poly_err:
            logger.warning(
                "fetch_live_data: Polygon path failed for %s (%s) — falling back to yfinance",
                ticker, _poly_err,
            )

    # ------------------------------------------------------------------ #
    # Fallback 1: Finnhub quote — works from Render IPs, no Polygon key needed
    # ------------------------------------------------------------------ #
    fh = _fetch_finnhub_quote(ticker)
    if fh and fh.get("current_price"):
        logger.info("fetch_live_data: Finnhub SUCCESS %s price=%.2f", ticker, fh["current_price"])
        return fh

    # ------------------------------------------------------------------ #
    # Fallback 2: yfinance + Yahoo chart API (original implementation)
    # ------------------------------------------------------------------ #

    if not _YF_AVAILABLE:
        return None

    # Quote types that never have earnings calendars or company fundamentals.
    # Requesting t.calendar for these produces a 404 from Yahoo Finance.
    _NO_FUNDAMENTALS_TYPES = frozenset({
        "ETF", "INDEX", "MUTUALFUND", "CRYPTOCURRENCY", "FUTURE", "FOREX", "CURRENCY",
    })

    def _f(val, cast=float, default=None):
        """Safely cast val; return default on None, zero (for prices), or error."""
        try:
            v = cast(val)
            return v if v == v else default   # NaN guard (NaN != NaN)
        except Exception:
            return default

    try:
        t = yf.Ticker(ticker, session=_get_yf_session())
        result: dict = {}

        # ------------------------------------------------------------------ #
        # 1. Fast info — current price, prev close, avg volume, quote type
        # ------------------------------------------------------------------ #
        fi = t.fast_info
        current_price = None
        prev_close    = None
        avg_volume    = None
        quote_type    = None   # "EQUITY", "ETF", "INDEX", etc.

        try:
            quote_type = str(fi.quote_type).upper() if fi.quote_type else None
        except Exception:
            pass

        try:
            v = _f(fi.last_price)
            current_price = v if v and v > 0 else None
        except Exception:
            pass

        try:
            v = _f(fi.previous_close)
            prev_close = v if v and v > 0 else None
        except Exception:
            pass

        try:
            v = _f(fi.three_month_average_volume, cast=int)
            avg_volume = v if v and v > 0 else None
        except Exception:
            pass

        # ── Direct API fallback — if fast_info returned no price (common on
        #    cloud hosts where Yahoo Finance blocks yfinance's endpoints), hit
        #    the Yahoo Finance chart API directly.  This endpoint does not
        #    require cookies / crumb and works reliably from server IPs. ──────
        if not current_price:
            _chart = _fetch_price_via_chart_api(ticker)
            if _chart:
                current_price = _chart.get("current_price")
                if not prev_close and _chart.get("prev_close"):
                    prev_close = _chart["prev_close"]
                # Inject prev_day_high/low from chart bars immediately into result
                # so section-2 history doesn't need to succeed to populate them.
                if _chart.get("prev_day_high") and "prev_day_high" not in result:
                    result["prev_day_high"] = _chart["prev_day_high"]
                if _chart.get("prev_day_low") and "prev_day_low" not in result:
                    result["prev_day_low"] = _chart["prev_day_low"]
                logger.info(
                    "fetch_live_data: chart API fallback for %s → "
                    "price=%.2f prev_close=%s prev_h=%s prev_l=%s",
                    ticker, current_price or 0, _chart.get("prev_close"),
                    _chart.get("prev_day_high"), _chart.get("prev_day_low"),
                )

        if current_price:
            result["current_price"] = round(current_price, 2)
        # prev_close is set in section 2 from daily history (more accurate than fast_info).
        # fast_info.previous_close is kept as a local variable for fallback only.
        if avg_volume:
            result["avg_volume"] = avg_volume

        # ------------------------------------------------------------------ #
        # 2. Daily history — official prev close, prev day high/low, today volume
        #
        #   prev_close sourced from the most recent completed trading day's Close
        #   field (hist row where date < today ET).  This is the official market
        #   close price — more reliable than fast_info.previous_close which can
        #   be cached or reflect after-hours moves.
        #
        #   prev_close_date records which trading date the close is from, enabling
        #   staleness detection and auto-refresh logic in the app layer.
        # ------------------------------------------------------------------ #
        try:
            hist = _yf_history_with_timeout(t, timeout_s=15, period="5d", interval="1d")
            if hist is not None and not hist.empty:
                # Normalize index to US/Eastern for accurate date comparison
                try:
                    hist.index = hist.index.tz_convert("America/New_York")
                except TypeError:
                    hist.index = hist.index.tz_localize("UTC").tz_convert("America/New_York")

                today_str = _et_now().strftime("%Y-%m-%d")

                # Previous trading day: most recent row strictly before today (ET)
                prev_rows = hist[hist.index.strftime("%Y-%m-%d") < today_str]
                if not prev_rows.empty:
                    prev_row = prev_rows.iloc[-1]
                    result["prev_close"]      = round(float(prev_row["Close"]), 2)
                    result["prev_close_date"] = prev_rows.index[-1].strftime("%Y-%m-%d")
                    result["prev_day_high"]   = round(float(prev_row["High"]),  2)
                    result["prev_day_low"]    = round(float(prev_row["Low"]),   2)

                    # Staleness warning — >4 days old means a holiday gap or fetch anomaly
                    from datetime import date as _date
                    days_old = (_date.today() - _date.fromisoformat(result["prev_close_date"])).days
                    if days_old > 4:
                        logger.warning(
                            "prev_close for %s is %d days old (%s) — possible stale data",
                            ticker, days_old, result["prev_close_date"],
                        )

                # Today's volume — use date-filtered row so we don't pick up yesterday
                today_rows = hist[hist.index.strftime("%Y-%m-%d") == today_str]
                if not today_rows.empty:
                    today_vol = int(today_rows.iloc[-1]["Volume"])
                    if today_vol and avg_volume and avg_volume > 0:
                        result["rel_volume"] = round(today_vol / avg_volume, 2)
                elif not hist.empty:
                    # Market hasn't opened yet — last bar is yesterday; still useful for rvol
                    today_vol = int(hist.iloc[-1]["Volume"])
                    if today_vol and avg_volume and avg_volume > 0:
                        result["rel_volume"] = round(today_vol / avg_volume, 2)

        except Exception as e:
            logger.debug("Daily history fetch failed for %s: %s", ticker, e)
            # Fallback chain for missing fields: fast_info → chart API OHLCV bars
            if "prev_close" not in result or "prev_day_high" not in result:
                _chart_fb = _fetch_price_via_chart_api(ticker)
                if _chart_fb:
                    if "prev_close" not in result:
                        if prev_close and prev_close > 0:
                            result["prev_close"] = round(prev_close, 2)
                        elif _chart_fb.get("prev_close"):
                            result["prev_close"] = _chart_fb["prev_close"]
                    if "prev_day_high" not in result and _chart_fb.get("prev_day_high"):
                        result["prev_day_high"] = _chart_fb["prev_day_high"]
                    if "prev_day_low" not in result and _chart_fb.get("prev_day_low"):
                        result["prev_day_low"] = _chart_fb["prev_day_low"]
                elif prev_close and prev_close > 0 and "prev_close" not in result:
                    result["prev_close"] = round(prev_close, 2)

        # ------------------------------------------------------------------ #
        # 3. Intraday bars — premarket range + ORB levels
        #
        #   Phase logic (US/Eastern):
        #     pre_market  < 09:30  → no ORB; clear levels
        #     forming   09:30–10:00 → ORB window open; show live partial high/low
        #     locked    > 10:00    → ORB window closed; levels are final
        #
        #   Data source: 1-minute bars, today only, US/Eastern date filter.
        #   ORB range:   9:30 AM bars through 10:00 AM bar (31 one-minute candles).
        #
        #   Skip entirely when the market is fully closed (weekends/overnight).
        #   Yahoo Finance returns no today-bars during closed hours and the 1m
        #   fetch can hang; skipping prevents that block.
        # ------------------------------------------------------------------ #
        try:
            # --- Phase from current ET time (set even if data fetch below fails) ---
            now_et    = _et_now()
            h, m      = now_et.hour, now_et.minute
            today_str = now_et.strftime("%Y-%m-%d")   # used for date filtering below

            if h < 9 or (h == 9 and m < 30):
                result["orb_phase"] = "pre_market"
            elif (h == 9 and m >= 30) or (h == 10 and m == 0):
                result["orb_phase"] = "forming"
            else:
                result["orb_phase"] = "locked"

            # --- Fetch 1-minute bars (pre + regular session) with timeout ---
            # Skip entirely when the market is fully closed (weekends/overnight).
            # Yahoo Finance returns no today-bars during closed hours, and the 1m
            # call can hang; avoiding it keeps dashboard loads fast on weekends.
            _mkt_session = market_session_now()
            if _mkt_session == "closed":
                logger.debug(
                    "fetch_live_data: skipping intraday 1m fetch for %s — market closed",
                    ticker,
                )
                intra = None   # sentinel — skip all processing below
            else:
                intra = _yf_history_with_timeout(
                    t, timeout_s=15, period="1d", interval="1m", prepost=True
                )
            if intra is not None and not intra.empty:
                # Ensure index is timezone-aware in US/Eastern
                try:
                    intra.index = intra.index.tz_convert("America/New_York")
                except TypeError:
                    # Index is timezone-naive — localize to UTC first
                    intra.index = intra.index.tz_localize("UTC").tz_convert("America/New_York")

                # ── Filter to TODAY only (ET date) ──────────────────────────
                # period="1d" usually returns only today, but with prepost=True
                # it may include yesterday's after-hours — strip them out.
                today_mask  = intra.index.strftime("%Y-%m-%d") == today_str
                intra_today = intra[today_mask]

                if not intra_today.empty:
                    # ── Premarket: 04:00–09:29 ET ──────────────────────────
                    pm_mask = (
                        (intra_today.index.hour >= 4) & (
                            (intra_today.index.hour < 9) |
                            ((intra_today.index.hour == 9) & (intra_today.index.minute < 30))
                        )
                    )
                    pm_bars = intra_today[pm_mask]
                    if not pm_bars.empty:
                        result["premarket_high"] = round(float(pm_bars["High"].max()), 2)
                        result["premarket_low"]  = round(float(pm_bars["Low"].min()),  2)

                    # ── ORB bars: 9:30–10:00 ET ─────────────────────────────
                    # Computed for both "forming" (partial) and "locked" (final).
                    # Not computed for "pre_market" — no regular-session bars exist yet.
                    if result["orb_phase"] in ("forming", "locked"):
                        orb_mask = (
                            ((intra_today.index.hour == 9) & (intra_today.index.minute >= 30)) |
                            ((intra_today.index.hour == 10) & (intra_today.index.minute == 0))
                        )
                        orb_bars = intra_today[orb_mask]
                        if not orb_bars.empty:
                            result["orb_high"] = round(float(orb_bars["High"].max()), 2)
                            result["orb_low"]  = round(float(orb_bars["Low"].min()),  2)

                    # ── Regular session bars: 9:30 ET onwards ───────────────
                    session_mask = (
                        (intra_today.index.hour > 9) |
                        ((intra_today.index.hour == 9) & (intra_today.index.minute >= 30))
                    )
                    session_bars = intra_today[session_mask]

                    if not session_bars.empty:
                        # ── VWAP (session cumulative) ────────────────────────
                        # VWAP = sum(typical_price * volume) / sum(volume)
                        # Typical price = (High + Low + Close) / 3
                        tp  = (session_bars["High"] + session_bars["Low"] + session_bars["Close"]) / 3
                        vol = session_bars["Volume"]
                        cum_vol = vol.cumsum().iloc[-1]
                        if cum_vol > 0:
                            vwap_val = float((tp * vol).cumsum().iloc[-1] / cum_vol)
                            result["vwap"] = round(vwap_val, 2)

                        # ── Trend structure — HH + HL (VWAP-independent) ─────
                        # higher_highs: each of last 3 session bar Highs > the one before
                        # higher_lows:  each of last 3 session bar Lows  > the one before
                        # trend_structure = HH AND HL only — does NOT require price > VWAP.
                        # This allows Momentum Runner detection even before VWAP reclaim.
                        # price_above_vwap is tracked separately as a participation signal.
                        if len(session_bars) >= 3:
                            _highs = session_bars["High"].values
                            _lows  = session_bars["Low"].values
                            higher_highs = bool(_highs[-1] > _highs[-2] > _highs[-3])
                            higher_lows  = bool(_lows[-1]  > _lows[-2]  > _lows[-3])
                        else:
                            higher_highs = False
                            higher_lows  = False

                        _vwap_now        = result.get("vwap")
                        _cur_now         = result.get("current_price")
                        price_above_vwap = bool(_vwap_now and _cur_now and _cur_now > _vwap_now)

                        result["higher_highs"]    = higher_highs
                        result["higher_lows"]     = higher_lows
                        result["trend_structure"] = higher_highs and higher_lows   # VWAP not required
                        result["price_above_vwap"] = price_above_vwap              # separate participation field

                        # ── Strong candle bodies — body > 50% of range on last 3 bars
                        # Strong bodies = conviction; filters out indecisive wick candles
                        if len(session_bars) >= 3:
                            _last3  = session_bars.iloc[-3:]
                            _bodies = abs(_last3["Close"] - _last3["Open"])
                            _ranges = _last3["High"] - _last3["Low"]
                            _valid  = _ranges > 0.001   # skip doji bars (zero range)
                            if _valid.sum() >= 2:
                                _ratio = _bodies[_valid] / _ranges[_valid]
                                result["strong_candle_bodies"] = bool((_ratio > 0.5).all())
                            else:
                                result["strong_candle_bodies"] = False
                        else:
                            result["strong_candle_bodies"] = False

                        # Pre-extract candle arrays once — used in both ORB blocks below
                        closes  = session_bars["Close"].values
                        volumes = session_bars["Volume"].values

                        # ── ORB Hold (price-only gate — no VWAP requirement) ──
                        # candles_above_orb and orb_hold fire whenever price > ORB high,
                        # regardless of VWAP position.  A stock holding above ORB high
                        # for 2+ candles is showing structural strength even if VWAP
                        # hasn't been reclaimed yet — this is the Momentum Runner signal.
                        orb_h = result.get("orb_high")
                        vwap  = result.get("vwap")
                        cur   = result.get("current_price")

                        if orb_h and cur and cur > orb_h:
                            candles_above = 0
                            for c in reversed(closes):
                                if c > orb_h:
                                    candles_above += 1
                                else:
                                    break
                            result["candles_above_orb"] = candles_above
                            result["orb_hold"]           = candles_above >= 2
                        else:
                            result["candles_above_orb"] = 0
                            result["orb_hold"]           = False

                        # ── Momentum Breakout (all 4 conditions — stricter) ──
                        # Breakout requires VWAP confirmation in addition to ORB hold.
                        # This is the full-conviction signal: 3+ candles, vol increasing,
                        # price above both ORB high AND VWAP.
                        if orb_h and vwap and cur and cur > orb_h and cur > vwap:
                            vol_increasing = (
                                len(volumes) >= 3
                                and volumes[-2] > volumes[-3]
                                and volumes[-1] > volumes[-2]
                            )
                            result["momentum_breakout"] = (
                                result["candles_above_orb"] >= 3 and vol_increasing
                            )
                        else:
                            result["momentum_breakout"] = False

        except Exception as e:
            logger.debug("Intraday fetch failed for %s: %s", ticker, e)
            # orb_phase is set before the fetch — preserve it; only log the fetch error

        # ------------------------------------------------------------------ #
        # 4. Gap % — recompute from live prices for accuracy
        # ------------------------------------------------------------------ #
        if result.get("current_price") and result.get("prev_close") and result["prev_close"] > 0:
            result["gap_pct"] = round(
                (result["current_price"] - result["prev_close"]) / result["prev_close"] * 100, 2
            )

        # ------------------------------------------------------------------ #
        # 5. Earnings date
        #    ETFs, indexes, and funds have no earnings calendar — skip the
        #    t.calendar call entirely to avoid Yahoo Finance 404 errors.
        #    For equities, suppress yfinance's internal logger during the call
        #    so a transient 404 doesn't spam the terminal; log one clean warning.
        # ------------------------------------------------------------------ #
        _is_non_equity = quote_type in _NO_FUNDAMENTALS_TYPES if quote_type else False

        if _is_non_equity:
            logger.debug("Skipping earnings calendar for %s (quote_type=%s)", ticker, quote_type)
        else:
            try:
                with _silence_yf():
                    cal = t.calendar
                earnings_date = None
                if isinstance(cal, dict):
                    ed = cal.get("Earnings Date")
                    if ed:
                        if isinstance(ed, (list, tuple)):
                            ed = ed[0]
                        if hasattr(ed, "date"):
                            earnings_date = str(ed.date())
                        else:
                            earnings_date = str(ed)[:10]
                elif hasattr(cal, "columns"):
                    if "Earnings Date" in cal.columns:
                        ed = cal["Earnings Date"].iloc[0]
                        earnings_date = str(ed.date()) if hasattr(ed, "date") else str(ed)[:10]
                if earnings_date:
                    result["earnings_date"] = earnings_date
            except Exception as e:
                # Log once at WARNING only for unexpected errors; 404s on known
                # tickers are silenced above via quote_type detection.
                err_str = str(e)
                if "404" in err_str or "No fundamentals" in err_str:
                    logger.warning(
                        "No earnings calendar for %s (likely ETF/fund not yet "
                        "detected via quote_type — consider adding to watchlist as equity only). "
                        "quote_type=%s", ticker, quote_type
                    )
                else:
                    logger.debug("Earnings date fetch failed for %s: %s", ticker, e)

        return result if result else None

    except Exception as e:
        logger.warning("fetch_live_data failed for %s: %s", ticker, e)
        return None


def swing_data_needs_refresh(fetched_at: str | None, minutes: int = 60) -> bool:
    """Return True if swing data is stale (older than *minutes*) or missing."""
    if not fetched_at:
        return True
    try:
        from datetime import datetime as _dt
        elapsed = (_dt.now() - _dt.fromisoformat(fetched_at)).total_seconds() / 60
        return elapsed >= minutes
    except Exception:
        return True


# ─────────────────────────────────────────────────────────────────────────────
# FIBONACCI ENGINE — Active Swing Detection
# ─────────────────────────────────────────────────────────────────────────────

def _compute_atr(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """Average True Range over the last `period` bars."""
    n = len(closes)
    if n < 2:
        return (highs[-1] - lows[-1]) if n >= 1 else 1.0
    trs = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
        trs.append(tr)
    w = min(len(trs), period)
    return sum(trs[-w:]) / w if w else 1.0


def _find_pivot_points(highs: list, lows: list, window: int = 3):
    """
    Return (pivot_highs, pivot_lows) as lists of (index, value).
    A pivot high at i: highs[i] >= all highs in [i-window, i+window].
    A pivot low  at i: lows[i]  <= all lows  in [i-window, i+window].
    """
    n = len(highs)
    ph, pl = [], []
    for i in range(window, n - window):
        lo, hi = max(0, i - window), i + window + 1
        if highs[i] >= max(highs[lo:hi]):
            ph.append((i, highs[i]))
        if lows[i] <= min(lows[lo:hi]):
            pl.append((i, lows[i]))
    return ph, pl


def _score_impulse_leg(
    start_i: int, end_i: int, leg_size: float,
    closes: list, highs: list, lows: list,
    volumes: list | None, n_total: int, atr: float,
) -> float:
    """
    Score an impulse leg from start_i to end_i on a 0-10 scale.
    start_i < end_i; leg_size = abs(high - low) for the leg.
    """
    if atr <= 0 or leg_size <= 0:
        return 0.0
    score = 0.0

    # 1. Magnitude vs ATR (0-3 pts) — bigger relative leg = higher quality
    m = leg_size / atr
    if   m >= 6:   score += 3.0
    elif m >= 4:   score += 2.5
    elif m >= 2.5: score += 2.0
    elif m >= 1.5: score += 1.5
    elif m >= 1.0: score += 1.0
    else:          score += 0.3

    # 2. Candle body quality during the leg (0-2 pts)
    end_clamp = min(end_i, len(closes) - 1)
    if end_clamp > start_i:
        bodies = []
        for i in range(start_i + 1, end_clamp + 1):
            rng = highs[i] - lows[i]
            if rng > 0:
                bodies.append(min(1.0, abs(closes[i] - closes[i - 1]) / rng))
        if bodies:
            avg_b = sum(bodies) / len(bodies)
            if   avg_b >= 0.55: score += 2.0
            elif avg_b >= 0.38: score += 1.2
            else:               score += 0.4

    # 3. Volume expansion during the leg (0-2 pts)
    if volumes and len(volumes) > end_clamp:
        lvols = volumes[start_i:end_clamp + 1]
        pvols = volumes[max(0, start_i - 10):start_i]
        if lvols and pvols:
            al = sum(lvols) / len(lvols)
            ap = sum(pvols) / len(pvols)
            if ap > 0:
                vr = al / ap
                if   vr >= 1.5: score += 2.0
                elif vr >= 1.2: score += 1.2
                else:           score += 0.4

    # 4. Recency (0-3 pts) — most recent endpoint of the leg
    bars_old = n_total - 1 - end_i
    if   bars_old <= 2:  score += 3.0
    elif bars_old <= 5:  score += 2.5
    elif bars_old <= 10: score += 2.0
    elif bars_old <= 20: score += 1.5
    elif bars_old <= 40: score += 0.7
    else:                score += 0.1

    return min(10.0, score)


def _find_active_impulse_leg(
    closes: list, highs: list, lows: list,
    volumes: list | None = None,
    window: int = 3,
) -> dict | None:
    """
    Detect the most recent high-quality impulsive leg suitable for Fibonacci anchoring.

    Returns a dict with keys:
        direction  "bullish" | "bearish"
        low_val    float — impulse low price
        high_val   float — impulse high price
        low_idx    int   — bar index of the impulse low
        high_idx   int   — bar index of the impulse high
        score      float — quality score 0-10
        atr_mult   float — leg size as multiple of ATR
    Returns None if no qualifying leg is found.
    """
    n = len(closes)
    if n < window * 2 + 5:
        return None

    atr = _compute_atr(highs, lows, closes, period=14)
    if atr <= 0:
        return None

    ph, pl = _find_pivot_points(highs, lows, window=window)
    if not ph or not pl:
        return None

    MIN_ATR_MULT = 1.2   # reject legs smaller than this many ATRs

    # An "active" swing has to mean what it says — a leg that's still
    # relevant to where the stock is actually trading now, not just the
    # biggest/cleanest move anywhere in the lookback history. Without a
    # recency check, a huge old rally from months ago can outscore a
    # smaller-but-current move on magnitude/quality alone (e.g. AMAT's fib
    # anchors stayed pinned to a leg ending near $447 even after price
    # rallied well past $600 — a >35% gap between the "active" high and the
    # real recent high).
    #
    # A hard per-candidate cutoff is too blunt though: if the very latest
    # high hasn't been pivot-confirmed yet (needs `window` bars of pullback
    # after it), rejecting every bullish candidate can flip the result to an
    # unrelated, low-confidence bearish leg — equally misleading in the
    # other direction. So: track the best candidate per direction, then
    # prefer whichever one is actually fresh (close to the real recent
    # extreme) over one that merely scores higher on magnitude/quality.
    RECENT_WINDOW    = 90
    FRESH_TOLERANCE  = 0.15   # within 15% of the real recent extreme counts as "fresh"
    recent_hi = max(highs[-RECENT_WINDOW:])
    recent_lo = min(lows[-RECENT_WINDOW:])

    best_bull: dict | None = None
    best_bear: dict | None = None

    # ── Bullish legs: pivot_low → pivot_high ──────────────────────────────────
    for j in range(len(pl) - 1, -1, -1):          # iterate recent pivot lows first
        li, lv = pl[j]
        # find the most recent pivot high AFTER this low
        for k in range(len(ph) - 1, -1, -1):
            hi, hv = ph[k]
            if hi <= li:
                continue
            leg_size = hv - lv
            if leg_size / atr < MIN_ATR_MULT:
                break  # all remaining ph[k] will be the same or worse — stop
            sc = _score_impulse_leg(li, hi, leg_size, closes, highs, lows, volumes, n, atr)
            candidate = {
                "direction": "bullish",
                "low_idx": li,  "low_val": lv,
                "high_idx": hi, "high_val": hv,
                "score": sc,    "atr_mult": leg_size / atr,
                "fresh": hv >= recent_hi * (1 - FRESH_TOLERANCE),
            }
            if best_bull is None or sc > best_bull["score"]:
                best_bull = candidate
            break  # take only the best qualifying high for this particular low

    # ── Bearish legs: pivot_high → pivot_low ─────────────────────────────────
    for j in range(len(ph) - 1, -1, -1):
        hi, hv = ph[j]
        for k in range(len(pl) - 1, -1, -1):
            li, lv = pl[k]
            if li <= hi:
                continue
            leg_size = hv - lv
            if leg_size / atr < MIN_ATR_MULT:
                break
            sc = _score_impulse_leg(hi, li, leg_size, closes, highs, lows, volumes, n, atr)
            candidate = {
                "direction": "bearish",
                "low_idx": li,  "low_val": lv,
                "high_idx": hi, "high_val": hv,
                "score": sc,    "atr_mult": leg_size / atr,
                # A bearish leg is "fresh" if it STARTED from a high that is
                # close to the recent peak — i.e. the rally that preceded the
                # selloff is still the dominant recent move.  Checking the low
                # against recent_lo was wrong: in an uptrend recent_lo is an
                # old accumulation low, making the threshold too permissive.
                "fresh": hv >= recent_hi * (1 - FRESH_TOLERANCE),
            }
            if best_bear is None or sc > best_bear["score"]:
                best_bear = candidate
            break

    # ── Pick the winner: freshness is a hard requirement, not just a tiebreaker ──
    # If neither direction has a pivot that's actually close to where price is
    # trading now (e.g. mid-rally with no confirmed pullback yet to anchor a
    # fresh pivot), there's no trustworthy "active" leg to show — defer to the
    # macro 20-bar fallback (computed by the caller) instead of guessing with a
    # stale leg, which is exactly how this bug surfaced in the first place.
    candidates = [c for c in (best_bull, best_bear) if c and c["score"] >= 2.0 and c["fresh"]]
    if not candidates:
        return None
    best = max(candidates, key=lambda c: c["score"])
    best.pop("fresh", None)
    return best


def fetch_swing_data(ticker: str) -> dict | None:
    """
    Fetch daily EMA, trend structure, and Fibonacci levels for swing analysis.

    Uses 200 days of daily bars from yfinance.
    Returns a dict with swing analysis fields, or None on failure.

    Fields returned:
        ema_20_daily, ema_50_daily, ema_200_daily   — EMA values
        pct_from_ema20, pct_from_ema50              — % distance from current price
        daily_trend     — "Bullish" | "Bullish Lean" | "Neutral" | "Bearish Lean" | "Bearish"
        daily_hh_hl     — True when daily higher highs + higher lows (last 5 bars)
        daily_lh_ll     — True when daily lower highs + lower lows
        fib_high, fib_low, fib_50, fib_618          — 20-bar swing Fibonacci levels
        swing_data_fetched_at                        — ISO timestamp of this fetch
    """
    if not _YF_AVAILABLE:
        return None

    def _ema(vals: list, period: int) -> float:
        """Compute full EMA series (SMA-seeded) and return the last value."""
        n = len(vals)
        if n < period:
            return float(vals[-1]) if n > 0 else 0.0
        k    = 2.0 / (period + 1)
        seed = sum(vals[:period]) / period
        e    = seed
        for v in vals[period:]:
            e = float(v) * k + e * (1.0 - k)
        return e

    try:
        closes = highs = lows = None
        _daily_source = "none"

        # ── Primary: Polygon.io daily bars (when API key is configured) ───────
        if _POLYGON_KEY:
            try:
                _poly_bars = _fetch_polygon_daily_bars(ticker, days=252)
                if _poly_bars and len(_poly_bars["closes"]) >= 20:
                    closes = _poly_bars["closes"]
                    highs  = _poly_bars["highs"]
                    lows   = _poly_bars["lows"]
                    _daily_source = "polygon"
                    logger.info(
                        "fetch_swing_data: Polygon daily bars for %s → %d bars",
                        ticker, len(closes),
                    )
            except Exception as _poly_err:
                logger.debug("fetch_swing_data: Polygon failed for %s: %s", ticker, _poly_err)

        # ── Fallback 1: yfinance history (200 trading days ≈ 10 months) ──────
        # Needs ≥200 bars for the 200 EMA; ≥20 for everything else.
        # Fails on cloud IPs when Yahoo Finance rejects the crumb token.
        if not closes:
            try:
                with _silence_yf():
                    hist = _yf_history_with_timeout(
                        yf.Ticker(ticker, session=_get_yf_session()),
                        timeout_s=20, period="200d", interval="1d",
                    )
                if hist is not None and not hist.empty and len(hist) >= 20:
                    try:
                        hist.index = hist.index.tz_convert("America/New_York")
                    except TypeError:
                        hist.index = hist.index.tz_localize("UTC").tz_convert("America/New_York")
                    closes = list(hist["Close"].astype(float))
                    highs  = list(hist["High"].astype(float))
                    lows   = list(hist["Low"].astype(float))
                    _daily_source = "yfinance"
            except Exception as _yf_err:
                logger.debug("fetch_swing_data: yfinance history failed for %s: %s", ticker, _yf_err)

        # ── Fallback 2: direct chart API (range=1y → ≥252 bars, no crumb needed) ─
        if not closes:
            _bars = _fetch_ohlcv_via_chart_api(ticker, interval="1d", range_str="1y")
            if _bars and len(_bars["closes"]) >= 20:
                closes = _bars["closes"]
                highs  = _bars["highs"]
                lows   = _bars["lows"]
                _daily_source = "chart_api"
                logger.info(
                    "fetch_swing_data: chart API daily fallback for %s → %d bars",
                    ticker, len(closes),
                )

        if not closes or len(closes) < 20:
            logger.warning(
                "fetch_swing_data: insufficient daily bars for %s "
                "(yfinance=%s, chart_api tried) — EMA/fib skipped",
                ticker, "empty" if closes is not None else "failed",
            )
            return None

        n   = len(closes)
        cur = closes[-1]

        result: dict = {}
        result["_daily_data_source"] = _daily_source   # debug field; not stored in DB

        # ── EMAs ────────────────────────────────────────────────────────────
        e20  = _ema(closes, 20)
        e50  = _ema(closes, 50)
        result["ema_20_daily"] = round(e20, 2)
        result["ema_50_daily"] = round(e50, 2)
        # 200 EMA: needs ≥200 bars; with chart API range=1y we typically get 252.
        result["ema_200_daily"] = round(_ema(closes, 200), 2) if n >= 200 else (
            round(_ema(closes, n), 2) if n >= 100 else None
        )

        result["pct_from_ema20"] = round((cur - e20) / e20 * 100, 2) if e20 else None
        result["pct_from_ema50"] = round((cur - e50) / e50 * 100, 2) if e50 else None

        # ── Daily trend ──────────────────────────────────────────────────────
        # EMA stack: price above/below the EMAs
        ema_bull = cur > e20 > e50
        ema_bear = cur < e20 < e50

        # Higher highs + higher lows (compare bar -1 vs bar -4 to smooth noise)
        if n >= 5:
            hh = highs[-1] > highs[-4]
            hl = lows[-1]  > lows[-4]
            lh = highs[-1] < highs[-4]
            ll = lows[-1]  < lows[-4]
        else:
            hh = hl = lh = ll = False

        result["daily_hh_hl"] = bool(hh and hl)
        result["daily_lh_ll"] = bool(lh and ll)

        if ema_bull and (hh and hl):
            result["daily_trend"] = "Bullish"
        elif ema_bear and (lh and ll):
            result["daily_trend"] = "Bearish"
        elif ema_bull or (hh and hl):
            result["daily_trend"] = "Bullish Lean"
        elif ema_bear or (lh and ll):
            result["daily_trend"] = "Bearish Lean"
        else:
            result["daily_trend"] = "Neutral"

        # ── Macro Fibonacci (20-bar simple swing high/low — institutional context) ─
        lb      = min(20, n)
        mac_hi  = float(max(highs[-lb:]))
        mac_lo  = float(min(lows[-lb:]))
        mac_rng = mac_hi - mac_lo
        result["macro_fib_high"] = round(mac_hi, 2)
        result["macro_fib_low"]  = round(mac_lo, 2)
        if mac_rng > 0:
            result["macro_fib_50"]  = round(mac_hi - 0.500 * mac_rng, 2)
            result["macro_fib_618"] = round(mac_hi - 0.618 * mac_rng, 2)
        else:
            result["macro_fib_50"]  = None
            result["macro_fib_618"] = None

        # ── Active Swing Fibonacci (pivot-based impulse leg detection) ────────
        # Detects the most recent significant directional leg so fib levels
        # align with the actual tradeable momentum move, not arbitrary lookback.
        _volumes: list | None = None
        try:
            if _daily_source == "yfinance" and hist is not None and not hist.empty:
                _volumes = list(hist["Volume"].astype(float))
            elif _daily_source == "chart_api" and _bars and _bars.get("volumes"):
                _volumes = _bars["volumes"]
        except Exception:
            pass

        _active_leg = _find_active_impulse_leg(closes, highs, lows, _volumes, window=3)

        if _active_leg and _active_leg["atr_mult"] >= 1.2:
            a_hi  = round(_active_leg["high_val"], 2)
            a_lo  = round(_active_leg["low_val"],  2)
            a_rng = a_hi - a_lo
            result["fib_high"]       = a_hi
            result["fib_low"]        = a_lo
            result["fib_direction"]  = _active_leg["direction"]
            result["fib_mode"]       = "active"
            result["fib_confidence"] = round(_active_leg["score"], 1)
            if a_rng > 0:
                result["fib_236"] = round(a_hi - 0.236 * a_rng, 2)
                result["fib_382"] = round(a_hi - 0.382 * a_rng, 2)
                result["fib_50"]  = round(a_hi - 0.500 * a_rng, 2)
                result["fib_618"] = round(a_hi - 0.618 * a_rng, 2)
                result["fib_65"]  = round(a_hi - 0.650 * a_rng, 2)
                result["fib_705"] = round(a_hi - 0.705 * a_rng, 2)
                result["fib_786"] = round(a_hi - 0.786 * a_rng, 2)
            else:
                for _k in ("fib_236", "fib_382", "fib_50", "fib_618", "fib_65", "fib_705", "fib_786"):
                    result[_k] = None
        else:
            # Fallback: use macro fib (20-bar range)
            result["fib_high"]       = result["macro_fib_high"]
            result["fib_low"]        = result["macro_fib_low"]
            result["fib_direction"]  = "bullish" if closes[-1] > (mac_hi + mac_lo) / 2 else "bearish"
            result["fib_mode"]       = "macro"
            result["fib_confidence"] = 3.0
            if mac_rng > 0:
                result["fib_236"] = round(mac_hi - 0.236 * mac_rng, 2)
                result["fib_382"] = round(mac_hi - 0.382 * mac_rng, 2)
                result["fib_50"]  = result["macro_fib_50"]
                result["fib_618"] = result["macro_fib_618"]
                result["fib_65"]  = round(mac_hi - 0.650 * mac_rng, 2)
                result["fib_705"] = round(mac_hi - 0.705 * mac_rng, 2)
                result["fib_786"] = round(mac_hi - 0.786 * mac_rng, 2)
            else:
                for _k in ("fib_236", "fib_382", "fib_50", "fib_618", "fib_65", "fib_705", "fib_786"):
                    result[_k] = None

        # ── 4H trend (derived from 1h bars — yfinance has no native 4h interval) ──
        # Uses regular-session 1h bars.  EMA stack + HH/HL on last ~80 1h bars
        # gives the same structural read as a 4H chart without requiring resampling.
        try:
            hist_1h = None
            try:
                with _silence_yf():
                    hist_1h = _yf_history_with_timeout(
                        yf.Ticker(ticker, session=_get_yf_session()),
                        timeout_s=15, period="30d", interval="60m",
                    )
                if hist_1h is not None and hist_1h.empty:
                    hist_1h = None
            except Exception:
                hist_1h = None

            # Chart API fallback for 1h bars
            _h1_closes = _h1_highs = _h1_lows = None
            if hist_1h is not None and not hist_1h.empty and len(hist_1h) >= 20:
                try:
                    hist_1h.index = hist_1h.index.tz_convert("America/New_York")
                except TypeError:
                    hist_1h.index = hist_1h.index.tz_localize("UTC").tz_convert("America/New_York")
                # Filter to regular session (09:30–15:59 ET)
                h1_mask = (
                    ((hist_1h.index.hour > 9) | ((hist_1h.index.hour == 9) & (hist_1h.index.minute >= 30))) &
                    (hist_1h.index.hour < 16)
                )
                _h1 = hist_1h[h1_mask]
                if len(_h1) >= 20:
                    _h1_closes = list(_h1["Close"].astype(float))
                    _h1_highs  = list(_h1["High"].astype(float))
                    _h1_lows   = list(_h1["Low"].astype(float))
            else:
                _bars_1h = _fetch_ohlcv_via_chart_api(ticker, interval="1h", range_str="30d")
                if _bars_1h and len(_bars_1h["closes"]) >= 20:
                    _h1_closes = _bars_1h["closes"]
                    _h1_highs  = _bars_1h["highs"]
                    _h1_lows   = _bars_1h["lows"]
                    logger.debug(
                        "fetch_swing_data: chart API 1h fallback for %s → %d bars",
                        ticker, len(_h1_closes),
                    )

            # _h1_closes/_h1_highs/_h1_lows are already filtered/list — use directly
            if _h1_closes and len(_h1_closes) >= 20:
                n_h1  = len(_h1_closes)
                h1_cur = _h1_closes[-1]

                h4_e20 = _ema(_h1_closes, 20)
                h4_e50 = _ema(_h1_closes, 50) if n_h1 >= 50 else None
                result["h4_ema20"] = round(h4_e20, 2)
                result["h4_ema50"] = round(h4_e50, 2) if h4_e50 else None

                h4_bull = (h1_cur > h4_e20 > h4_e50) if h4_e50 else (h1_cur > h4_e20)
                h4_bear = (h1_cur < h4_e20 < h4_e50) if h4_e50 else (h1_cur < h4_e20)

                if n_h1 >= 8:
                    h4_hh = _h1_highs[-1] > _h1_highs[-5]
                    h4_hl = _h1_lows[-1]  > _h1_lows[-5]
                    h4_lh = _h1_highs[-1] < _h1_highs[-5]
                    h4_ll = _h1_lows[-1]  < _h1_lows[-5]
                else:
                    h4_hh = h4_hl = h4_lh = h4_ll = False

                result["h4_hh_hl"] = bool(h4_hh and h4_hl)

                if   h4_bull and h4_hh and h4_hl:  result["h4_trend"] = "Bullish"
                elif h4_bear and h4_lh and h4_ll:  result["h4_trend"] = "Bearish"
                elif h4_bull or (h4_hh and h4_hl): result["h4_trend"] = "Bullish Lean"
                elif h4_bear or (h4_lh and h4_ll): result["h4_trend"] = "Bearish Lean"
                else:                               result["h4_trend"] = "Neutral"

                # ── H4 Fibonacci (active swing on 1h bars, window=2) ─────────
                _h4_leg = _find_active_impulse_leg(
                    _h1_closes, _h1_highs, _h1_lows, window=2
                )
                if _h4_leg and _h4_leg["atr_mult"] >= 1.0:
                    h4a_hi  = round(_h4_leg["high_val"], 2)
                    h4a_lo  = round(_h4_leg["low_val"],  2)
                    h4a_rng = h4a_hi - h4a_lo
                    result["h4_fib_high"] = h4a_hi
                    result["h4_fib_low"]  = h4a_lo
                    if h4a_rng > 0:
                        result["h4_fib_50"]  = round(h4a_hi - 0.500 * h4a_rng, 2)
                        result["h4_fib_618"] = round(h4a_hi - 0.618 * h4a_rng, 2)
                    else:
                        result["h4_fib_50"] = result["h4_fib_618"] = None
                else:
                    h4_lb = min(20, n_h1)
                    h4_hi = float(max(_h1_highs[-h4_lb:]))
                    h4_lo = float(min(_h1_lows[-h4_lb:]))
                    h4_rng = h4_hi - h4_lo
                    result["h4_fib_high"] = round(h4_hi, 2)
                    result["h4_fib_low"]  = round(h4_lo, 2)
                    if h4_rng > 0:
                        result["h4_fib_50"]  = round(h4_hi - 0.500 * h4_rng, 2)
                        result["h4_fib_618"] = round(h4_hi - 0.618 * h4_rng, 2)
                    else:
                        result["h4_fib_50"] = result["h4_fib_618"] = None

        except Exception as e:
            logger.debug("4H data fetch failed for %s: %s", ticker, e)

        result.setdefault("h4_trend",    "Neutral")
        result.setdefault("h4_ema20",    None)
        result.setdefault("h4_ema50",    None)
        result.setdefault("h4_hh_hl",    False)
        result.setdefault("h4_fib_high", None)
        result.setdefault("h4_fib_low",  None)
        result.setdefault("h4_fib_50",   None)
        result.setdefault("h4_fib_618",  None)

        # ── 15m confirmation signals ──────────────────────────────────────────
        # m15_confirmation scores 0 (none), 1 (developing), or 2 (confirmed).
        # Signals checked: 15m higher low on last 3 bars, strong bullish body on last bar.
        try:
            _m15_lows = _m15_closes = _m15_highs = _m15_opens = None
            try:
                with _silence_yf():
                    hist_15m = _yf_history_with_timeout(
                        yf.Ticker(ticker, session=_get_yf_session()),
                        timeout_s=10, period="5d", interval="15m",
                    )
                if hist_15m is not None and not hist_15m.empty and len(hist_15m) >= 6:
                    try:
                        hist_15m.index = hist_15m.index.tz_convert("America/New_York")
                    except TypeError:
                        hist_15m.index = hist_15m.index.tz_localize("UTC").tz_convert("America/New_York")
                    m15_mask = (
                        ((hist_15m.index.hour > 9) | ((hist_15m.index.hour == 9) & (hist_15m.index.minute >= 30))) &
                        (hist_15m.index.hour < 16)
                    )
                    m15 = hist_15m[m15_mask]
                    if len(m15) >= 6:
                        _m15_closes = list(m15["Close"].astype(float))
                        _m15_opens  = list(m15["Open"].astype(float))
                        _m15_highs  = list(m15["High"].astype(float))
                        _m15_lows   = list(m15["Low"].astype(float))
            except Exception:
                pass

            if not _m15_lows:
                _bars_15m = _fetch_ohlcv_via_chart_api(ticker, interval="15m", range_str="5d")
                if _bars_15m and len(_bars_15m["closes"]) >= 6:
                    _m15_closes = _bars_15m["closes"]
                    _m15_opens  = _bars_15m["opens"]
                    _m15_highs  = _bars_15m["highs"]
                    _m15_lows   = _bars_15m["lows"]

            if _m15_lows and len(_m15_lows) >= 6:
                body = abs(_m15_closes[-1] - _m15_opens[-1])
                rng  = _m15_highs[-1] - _m15_lows[-1]
                higher_low    = bool(len(_m15_lows) >= 3 and _m15_lows[-1] > _m15_lows[-3])
                strong_candle = bool(rng > 0 and body / rng > 0.50 and _m15_closes[-1] > _m15_opens[-1])
                result["m15_higher_low"]   = higher_low
                result["m15_confirmation"] = int(higher_low) + int(strong_candle)

        except Exception as e:
            logger.debug("15m confirmation fetch failed for %s: %s", ticker, e)

        result.setdefault("m15_higher_low",   False)
        result.setdefault("m15_confirmation", 0)

        result["swing_data_fetched_at"] = datetime.now().isoformat()
        return result

    except Exception as exc:
        logger.debug("fetch_swing_data failed for %s: %s", ticker, exc)
        return None


def fetch_news_headlines(ticker: str) -> tuple[str, list[str]]:
    """
    Attempt to pull recent news headlines for an unknown ticker via yfinance.
    Returns (catalyst_summary, headlines_list).
    Falls back to placeholder strings if unavailable.
    """
    if not _YF_AVAILABLE:
        return (
            "No catalyst loaded. Install yfinance and connect a news source.",
            ["No headlines available."],
        )

    try:
        t = yf.Ticker(ticker, session=_get_yf_session())
        news = t.news  # list of dicts with 'title', 'publisher', etc.
        if news:
            headlines = [item.get("title", "") for item in news[:5] if item.get("title")]
            summary = headlines[0] if headlines else "Recent news activity — see headlines."
            return summary, headlines
    except Exception as e:
        logger.debug("News fetch failed for %s: %s", ticker, e)

    return (
        "No catalyst loaded. Connect a news API for full analysis.",
        ["No headlines available."],
    )


# ─────────────────────────────────────────────────────────────────────────────
# OPTIONS CONTRACT SELECTOR
# ─────────────────────────────────────────────────────────────────────────────

import math as _math


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + _math.erf(x / _math.sqrt(2.0)))


def _bsm_greeks(S: float, K: float, T: float, sigma: float,
                is_call: bool, r: float = 0.045) -> tuple[float, float]:
    """
    Black-Scholes delta and daily theta.
    Returns (delta, theta_per_day).
    delta: 0..1 for calls, -1..0 for puts.
    theta_per_day: dollar decay per day per contract (negative).
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        intrinsic_call = S > K
        delta = 1.0 if (is_call and intrinsic_call) else (
                -1.0 if (not is_call and not intrinsic_call) else 0.0)
        return delta, 0.0

    sqrt_T     = _math.sqrt(T)
    d1         = (_math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2         = d1 - sigma * sqrt_T
    pdf_d1     = _math.exp(-0.5 * d1 ** 2) / _math.sqrt(2 * _math.pi)

    delta = _norm_cdf(d1) if is_call else (_norm_cdf(d1) - 1.0)

    # Theta (annualised) → convert to per-day
    theta_annual = (
        -S * pdf_d1 * sigma / (2.0 * sqrt_T)
        + (-r * K * _math.exp(-r * T) * _norm_cdf(d2)  if is_call
           else  r * K * _math.exp(-r * T) * _norm_cdf(-d2))
    )
    theta_per_day = theta_annual / 365.0

    return round(delta, 3), round(theta_per_day, 4)


import time as _time_module


def _is_rate_limit_error(exc: Exception) -> bool:
    """Return True if the exception looks like a Yahoo Finance 429 / rate-limit."""
    msg = str(exc).lower()
    return any(k in msg for k in (
        "too many requests", "rate limit", "429", "ratelimit",
        "try after", "throttl",
    ))


def fetch_option_contracts(ticker: str,
                           current_price: float | None = None,
                           trade_mode: str = "SWING TRADE") -> dict:
    """
    Fetch and filter option contracts for the given ticker.

    Returns:
        {
          "price": float | None,
          "calls": [...],
          "puts":  [...],
          "best_day": contract | None,
          "best_swing": contract | None,
          "error": str | None,          # human-readable error (None = success)
          "rate_limited": bool,         # True when Yahoo returned a 429
          "partial": bool,              # True when some expirations failed mid-fetch
        }

    Calls/Puts/All filtering is ALWAYS done client-side from the returned data —
    this function always returns both lists regardless of trade_mode.

    Rate-limit protection:
      - 0.4 s sleep between consecutive option_chain() calls
      - Per-expiration 429 is caught; we stop fetching more expirations and
        return whatever partial data was collected with rate_limited=True
      - Outer 429 (e.g., on yf_t.options) also returns rate_limited=True
    """
    if not _YF_AVAILABLE:
        return {"error": "yfinance not installed", "calls": [], "puts": [],
                "price": None, "best_day": None, "best_swing": None,
                "rate_limited": False, "partial": False}

    try:
        # Option chain calls require yfinance's own session (curl_cffi-based).
        # Do NOT pass our custom requests.Session — it breaks option_chain().
        yf_t = yf.Ticker(ticker)
        logger.info("options  ticker=%s  stage=upstream_call  trade_mode=%s",
                    ticker, trade_mode)

        # ── Current price ────────────────────────────────────────────
        price = current_price
        if not price:
            try:
                price = yf_t.fast_info.last_price or 0.0
            except Exception:
                price = 0.0

        if not price:
            return {"error": "Could not determine current price", "calls": [], "puts": [],
                    "price": None, "best_day": None, "best_swing": None,
                    "rate_limited": False, "partial": False}

        # ── Expiration selection ──────────────────────────────────────
        try:
            all_exps = yf_t.options
        except Exception as _exp_exc:
            if _is_rate_limit_error(_exp_exc):
                logger.warning("options  ticker=%s  stage=get_expirations  RATE LIMITED: %s",
                               ticker, _exp_exc)
                return {"error": "Options source temporarily rate-limited",
                        "calls": [], "puts": [], "price": price,
                        "best_day": None, "best_swing": None,
                        "rate_limited": True, "partial": False}
            raise

        if not all_exps:
            return {"error": "No option expirations available for this ticker",
                    "calls": [], "puts": [], "price": price,
                    "best_day": None, "best_swing": None,
                    "rate_limited": False, "partial": False}

        today = date.today()

        day_exps   = []
        swing_exps = []
        for exp in all_exps:
            exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
            dte = (exp_date - today).days
            if 0 <= dte <= 14:
                day_exps.append(exp)
            elif 15 <= dte <= 60:
                swing_exps.append(exp)

        # Always fetch both day and swing expirations so client-side filter works
        exps_to_fetch = list(dict.fromkeys(day_exps[:3] + swing_exps[:4]))
        if not exps_to_fetch:
            exps_to_fetch = list(all_exps[:3])

        logger.info("options  ticker=%s  expirations_to_fetch=%s", ticker, exps_to_fetch)

        # ── Pull chains with inter-call delay ─────────────────────────
        raw_calls:   list[dict] = []
        raw_puts:    list[dict] = []
        rate_limited = False
        partial      = False

        for idx, exp in enumerate(exps_to_fetch):
            # Small delay between calls — prevents bursting Yahoo's rate limiter
            if idx > 0:
                _time_module.sleep(0.4)

            try:
                chain    = yf_t.option_chain(exp)
                exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                dte      = max(0, (exp_date - today).days)
                T        = max(dte / 365.0, 1 / 365.0)

                for row_iter, is_call in ((chain.calls.iterrows(), True),
                                          (chain.puts.iterrows(),  False)):
                    for _, row in row_iter:
                        strike = float(row.get("strike") or 0)
                        if not strike:
                            continue
                        if abs(strike - price) / price > 0.15:
                            continue

                        bid  = float(row.get("bid") or 0)
                        ask  = float(row.get("ask") or 0)
                        if ask <= 0:
                            continue

                        mid        = (bid + ask) / 2.0
                        spread     = ask - bid
                        spread_pct = (spread / mid * 100) if mid > 0 else 999

                        vol = int(row.get("volume") or 0)
                        oi  = int(row.get("openInterest") or 0)
                        iv  = float(row.get("impliedVolatility") or 0)

                        if spread_pct > 25:
                            continue
                        if oi < 50:
                            continue
                        if iv <= 0:
                            continue

                        delta, theta = _bsm_greeks(price, strike, T, iv, is_call)

                        labels = []
                        if vol >= 200 and oi >= 500:
                            labels.append("Good Liquidity")
                        if dte <= 7:
                            labels.append("High Theta Risk")
                        if spread_pct > 15:
                            labels.append("Wide Spread")

                        contract = {
                            "strike":        strike,
                            "expiration":    exp,
                            "dte":           dte,
                            "option_type":   "CALL" if is_call else "PUT",
                            "delta":         delta,
                            "theta":         theta,
                            "bid":           round(bid,    2),
                            "ask":           round(ask,    2),
                            "mid":           round(mid,    2),
                            "spread":        round(spread, 2),
                            "spread_pct":    round(spread_pct, 1),
                            "volume":        vol,
                            "open_interest": oi,
                            "in_the_money":  bool(row.get("inTheMoney", False)),
                            "iv":            round(iv * 100, 1),
                            "labels":        labels,
                            "score_day":     0.0,
                            "score_swing":   0.0,
                        }

                        abs_d        = abs(delta)
                        liq_vol      = _math.log1p(vol)
                        liq_oi       = _math.log1p(oi)
                        spread_score = max(0.0, 1.0 - spread_pct / 25.0)

                        day_delta_score = max(0.0, 1.0 - abs(abs_d - 0.50) / 0.30)
                        contract["score_day"] = round(
                            day_delta_score * 0.35 + liq_vol * 0.40 + spread_score * 0.25, 4)

                        swing_delta_score = max(0.0, 1.0 - abs(abs_d - 0.45) / 0.25)
                        dte_ok = 1.0 if 21 <= dte <= 45 else max(0.0, 1.0 - abs(dte - 33) / 28.0)
                        contract["score_swing"] = round(
                            swing_delta_score * 0.30 + liq_oi * 0.35 +
                            spread_score * 0.20 + dte_ok * 0.15, 4)

                        if is_call:
                            raw_calls.append(contract)
                        else:
                            raw_puts.append(contract)

                logger.info("options  ticker=%s  exp=%s  calls=%d  puts=%d",
                            ticker, exp, len(raw_calls), len(raw_puts))

            except Exception as _exp_err:
                if _is_rate_limit_error(_exp_err):
                    logger.warning(
                        "options  ticker=%s  exp=%s  RATE LIMITED — stopping after %d/%d expirations",
                        ticker, exp, idx, len(exps_to_fetch),
                    )
                    rate_limited = True
                    if idx > 0:
                        partial = True   # we got some data before the limit hit
                    break
                else:
                    logger.warning("options  ticker=%s  exp=%s  fetch_error=%s",
                                   ticker, exp, _exp_err)
                    partial = True
                    continue

        # ── Sort ──────────────────────────────────────────────────────
        raw_calls.sort(key=lambda c: c["strike"])
        raw_puts.sort(key=lambda c:  c["strike"])

        # ── Best contract selection ───────────────────────────────────
        def _best_day(lst):
            cands = [c for c in lst if 0.35 <= abs(c["delta"]) <= 0.70 and c["dte"] <= 14]
            return max(cands, key=lambda c: c["score_day"], default=None)

        def _best_swing(lst):
            cands = [c for c in lst if 0.30 <= abs(c["delta"]) <= 0.65 and 21 <= c["dte"] <= 60]
            return max(cands, key=lambda c: c["score_swing"], default=None)

        best_day   = _best_day(raw_calls)   or _best_day(raw_puts)
        best_swing = _best_swing(raw_calls) or _best_swing(raw_puts)

        if best_day:
            best_day["best_tag"] = "Best Day Trade"
        if best_swing and best_swing is not best_day:
            best_swing["best_tag"] = "Best Swing Trade"

        # Rate-limited mid-fetch with no data at all → treat as full rate limit
        if rate_limited and not raw_calls and not raw_puts:
            return {"error": "Options source temporarily rate-limited",
                    "calls": [], "puts": [], "price": round(price, 2),
                    "best_day": None, "best_swing": None,
                    "rate_limited": True, "partial": False}

        logger.info(
            "options  ticker=%s  done  calls=%d  puts=%d  "
            "rate_limited=%s  partial=%s",
            ticker, len(raw_calls), len(raw_puts), rate_limited, partial,
        )
        return {
            "price":       round(price, 2),
            "calls":       raw_calls,
            "puts":        raw_puts,
            "best_day":    best_day,
            "best_swing":  best_swing,
            "error":       None,
            "rate_limited": rate_limited,
            "partial":     partial,
        }

    except Exception as exc:
        if _is_rate_limit_error(exc):
            logger.warning("options  ticker=%s  RATE LIMITED (outer): %s", ticker, exc)
            return {"error": "Options source temporarily rate-limited",
                    "calls": [], "puts": [], "price": current_price,
                    "best_day": None, "best_swing": None,
                    "rate_limited": True, "partial": False}
        logger.warning("options  ticker=%s  unexpected error: %s", ticker, exc)
        return {"error": str(exc), "calls": [], "puts": [],
                "price": current_price, "best_day": None, "best_swing": None,
                "rate_limited": False, "partial": False}


def compute_market_temperature() -> dict:
    """
    Compute market regime from SPY, QQQ, VIX daily and intraday data.

    Scoring:
      SPY above/below EMA20:   ±1
      QQQ above/below EMA20:   ±1
      SPY above/below VWAP:    ±1  (intraday only — None if market closed)
      QQQ above/below VWAP:    ±1
      SPY day change >+0.5%:   +1 / <-1%: -1
      VIX <18: +1, 18-22: 0, 22-28: -1, 28-35: -2, >35: -4
      VIX rising (>20): -1 / falling: +1

    Regimes:
      VIX > 35          → NO TRADE DAY
      score >= 3        → RISK ON
      score 1-2         → NEUTRAL
      score -1 to 0     → CAUTION / CHOP
      score <= -2       → RISK OFF
    """
    _UNKNOWN: dict = {
        "regime": "UNKNOWN", "label": "Unknown", "css": "mt-unknown",
        "reason": "Market data unavailable", "action_msg": "—",
        "longs_ok": None, "shorts_ok": None, "reduce_size": False,
        "score": None, "meter_score": 50, "error": True,
        "spy_price": None, "spy_pct_ema20": None, "spy_vs_vwap": None,
        "qqq_price": None, "qqq_pct_ema20": None, "qqq_vs_vwap": None,
        "vix_level": None, "vix_direction": None,
        "es_price": None, "es_change_pct": None, "es_above_vwap": None,
        "sectors": {}, "mode_desc": "—",
        "action_msg": "—", "decision_cmd": "—", "risk_pct_rec": None,
        "size_multiplier": None, "size_zone": "unknown", "why": "Market data unavailable.",
    }

    try:
        import threading as _thr

        # Parallel fetch — 5 API calls simultaneously
        _results: dict = {}

        def _fetch(key, ticker, interval, range_str):
            try:
                _results[key] = _fetch_ohlcv_via_chart_api(
                    ticker, interval=interval, range_str=range_str
                )
            except Exception:
                _results[key] = None

        _tasks = [
            ("spy_d", "SPY",  "1d", "6mo"),
            ("qqq_d", "QQQ",  "1d", "6mo"),
            ("vix_d", "^VIX", "1d", "1mo"),
            ("spy_h", "SPY",  "1h", "5d"),
            ("qqq_h", "QQQ",  "1h", "5d"),
            ("es_d",   "ES=F",      "1d", "5d"),
            ("es_h",   "ES=F",      "1h", "5d"),
            ("xlk_d",  "XLK",       "1d", "5d"),
            ("xly_d",  "XLY",       "1d", "5d"),
            ("xlf_d",  "XLF",       "1d", "5d"),
            ("xle_d",  "XLE",       "1d", "5d"),
            ("xlv_d",  "XLV",       "1d", "5d"),
            ("xli_d",  "XLI",       "1d", "5d"),
            ("xlu_d",  "XLU",       "1d", "5d"),
            ("xlb_d",  "XLB",       "1d", "5d"),
            ("xlre_d", "XLRE",      "1d", "5d"),
            ("xlc_d",  "XLC",       "1d", "5d"),
            ("smh_d",  "SMH",       "1d", "5d"),
            ("iwm_d",  "IWM",       "1d", "5d"),
            ("tnx_d",  "^TNX",      "1d", "5d"),
            ("dxy_d",  "DX-Y.NYB",  "1d", "5d"),
        ]
        _threads = [_thr.Thread(target=_fetch, args=a, daemon=True) for a in _tasks]
        for _t in _threads:
            _t.start()
        for _t in _threads:
            _t.join(timeout=18)

        spy_d = _results.get("spy_d")
        qqq_d = _results.get("qqq_d")
        vix_d = _results.get("vix_d")
        spy_h = _results.get("spy_h")
        qqq_h = _results.get("qqq_h")
        es_d  = _results.get("es_d")
        es_h  = _results.get("es_h")

        if not spy_d or not qqq_d:
            return {**_UNKNOWN, "reason": "SPY/QQQ data unavailable"}

        def _ema(closes, period):
            n = len(closes)
            if n < period:
                return closes[-1]
            k   = 2.0 / (period + 1)
            ema = sum(closes[:period]) / period
            for c in closes[period:]:
                ema = c * k + ema * (1.0 - k)
            return ema

        spy_cls = spy_d["closes"]
        qqq_cls = qqq_d["closes"]

        spy_price     = spy_cls[-1]
        spy_prev      = spy_cls[-2] if len(spy_cls) >= 2 else spy_price
        spy_ema20     = _ema(spy_cls, 20)
        spy_pct_ema20 = (spy_price - spy_ema20) / spy_ema20 * 100
        spy_day_chg   = (spy_price - spy_prev) / spy_prev * 100

        qqq_price     = qqq_cls[-1]
        qqq_ema20     = _ema(qqq_cls, 20)
        qqq_pct_ema20 = (qqq_price - qqq_ema20) / qqq_ema20 * 100

        # VIX level and 5-day direction
        vix_level     = None
        vix_direction = "flat"
        if vix_d and len(vix_d["closes"]) >= 2:
            vix_level = vix_d["closes"][-1]
            vix_ref   = vix_d["closes"][-5] if len(vix_d["closes"]) >= 5 else vix_d["closes"][0]
            if vix_level > vix_ref * 1.05:
                vix_direction = "rising"
            elif vix_level < vix_ref * 0.95:
                vix_direction = "falling"

        # VWAP from today's hourly bars (None when market is closed)
        def _today_vwap(intra_data):
            if not intra_data:
                return None
            try:
                import zoneinfo as _zi
                from datetime import timezone as _tz
                today_et = _et_now().strftime("%Y-%m-%d")
                bars = [
                    (c, h, lo, v)
                    for ts, _, c, h, lo, v in zip(
                        intra_data["timestamps"], intra_data["opens"],
                        intra_data["closes"],     intra_data["highs"],
                        intra_data["lows"],        intra_data["volumes"],
                    )
                    if datetime.fromtimestamp(ts, tz=_tz.utc)
                       .astimezone(_zi.ZoneInfo("America/New_York"))
                       .strftime("%Y-%m-%d") == today_et
                ]
                if not bars:
                    return None
                tpv = sum((h + lo + c) / 3.0 * v for c, h, lo, v in bars)
                tv  = sum(v for _, _, _, v in bars)
                return tpv / tv if tv > 0 else None
            except Exception:
                return None

        spy_vwap    = _today_vwap(spy_h)
        qqq_vwap    = _today_vwap(qqq_h)
        spy_vs_vwap = (spy_price - spy_vwap) / spy_vwap * 100 if spy_vwap else None
        qqq_vs_vwap = (qqq_price - qqq_vwap) / qqq_vwap * 100 if qqq_vwap else None

        # ES futures
        es_price = es_change_pct = es_above_vwap = None
        if es_d and len(es_d.get("closes", [])) >= 2:
            try:
                _ep    = es_d["closes"][-1]
                _eprev = es_d["closes"][-2]
                _evwap = _today_vwap(es_h)
                es_price      = _ep
                es_change_pct = (_ep - _eprev) / _eprev * 100
                es_above_vwap = bool(_ep > _evwap) if _evwap is not None else None
            except Exception:
                pass

        # Sector ETF daily % changes (12 sectors)
        sectors: dict = {}
        _sector_list = [
            ("xlk_d","XLK"), ("xly_d","XLY"), ("xlf_d","XLF"), ("xle_d","XLE"),
            ("xlv_d","XLV"), ("xli_d","XLI"), ("xlu_d","XLU"), ("xlb_d","XLB"),
            ("xlre_d","XLRE"),("xlc_d","XLC"), ("smh_d","SMH"), ("iwm_d","IWM"),
        ]
        for _sk, _sn in _sector_list:
            _sd = _results.get(_sk)
            if _sd and len(_sd.get("closes", [])) >= 2:
                try:
                    _sp  = _sd["closes"][-1]
                    _spp = _sd["closes"][-2]
                    sectors[_sn] = round((_sp - _spp) / _spp * 100, 2)
                except Exception:
                    sectors[_sn] = None
            else:
                sectors[_sn] = None

        # 10Y Treasury yield (^TNX: value is already %, e.g. 4.25 = 4.25% yield)
        yield_10y        = None
        yield_change_bps = None
        yield_trend      = "flat"
        yield_note       = "—"
        _tnx = _results.get("tnx_d")
        if _tnx and len(_tnx.get("closes", [])) >= 2:
            try:
                yield_10y        = round(_tnx["closes"][-1], 3)
                _tnx_prev        = _tnx["closes"][-2]
                yield_change_bps = round((yield_10y - _tnx_prev) * 100, 1)
                if yield_change_bps > 10:
                    yield_trend = "rising fast"
                    yield_note  = "Pressure on growth/tech — rising rates headwind"
                elif yield_change_bps > 2:
                    yield_trend = "rising"
                    yield_note  = "Watch tech — yields creeping higher"
                elif yield_change_bps < -10:
                    yield_trend = "falling fast"
                    yield_note  = "Supportive for growth/tech — rates declining"
                elif yield_change_bps < -2:
                    yield_trend = "falling"
                    yield_note  = "Mild tailwind for growth/tech"
                else:
                    yield_trend = "flat"
                    yield_note  = "Neutral — no rate pressure"
            except Exception:
                pass

        # DXY (US Dollar Index)
        dxy_price      = None
        dxy_change_pct = None
        dxy_trend      = "flat"
        _dxy = _results.get("dxy_d")
        if _dxy and len(_dxy.get("closes", [])) >= 2:
            try:
                dxy_price      = round(_dxy["closes"][-1], 2)
                _dxy_prev      = _dxy["closes"][-2]
                dxy_change_pct = round((dxy_price - _dxy_prev) / _dxy_prev * 100, 2) if _dxy_prev else 0.0
                if dxy_change_pct > 0.3:
                    dxy_trend = "rising"
                elif dxy_change_pct < -0.3:
                    dxy_trend = "falling"
            except Exception:
                pass

        # ── Score ─────────────────────────────────────────────────────────────
        score   = 0
        factors = []

        if spy_pct_ema20 >= 0:
            score += 1
            factors.append(f"SPY +{spy_pct_ema20:.1f}% vs EMA20")
        else:
            score -= 1
            factors.append(f"SPY {spy_pct_ema20:.1f}% vs EMA20")

        if qqq_pct_ema20 >= 0:
            score += 1
            factors.append(f"QQQ +{qqq_pct_ema20:.1f}% vs EMA20")
        else:
            score -= 1
            factors.append(f"QQQ {qqq_pct_ema20:.1f}% vs EMA20")

        if spy_vs_vwap is not None:
            if spy_vs_vwap >= 0:
                score += 1
                factors.append("SPY above VWAP")
            else:
                score -= 1
                factors.append("SPY below VWAP")

        if qqq_vs_vwap is not None:
            if qqq_vs_vwap >= 0:
                score += 1
                factors.append("QQQ above VWAP")
            else:
                score -= 1
                factors.append("QQQ below VWAP")

        if spy_day_chg > 0.5:
            score += 1
            factors.append(f"SPY up {spy_day_chg:.1f}%")
        elif spy_day_chg < -1.0:
            score -= 1
            factors.append(f"SPY down {abs(spy_day_chg):.1f}%")

        vix_note = ""
        if vix_level is not None:
            if vix_level > 35:
                score -= 4
                vix_note = f"VIX {vix_level:.0f} — extreme fear"
            elif vix_level > 28:
                score -= 2
                vix_note = f"VIX {vix_level:.0f} — elevated"
            elif vix_level > 22:
                score -= 1
                vix_note = f"VIX {vix_level:.0f} — caution"
            else:
                score += 1
                vix_note = f"VIX {vix_level:.0f} — calm"
            if vix_direction == "rising" and vix_level > 20:
                score -= 1
                vix_note += " ↑"
            elif vix_direction == "falling":
                score += 1
                vix_note += " ↓"

        # ES futures contribution (primary driver)
        if es_price is not None and es_change_pct is not None:
            if es_change_pct > 0 and es_above_vwap:
                score += 1
                factors.append(f"ES +{es_change_pct:.1f}% above VWAP")
            elif es_change_pct < 0 and es_above_vwap is False:
                score -= 1
                factors.append(f"ES {es_change_pct:.1f}% below VWAP")

        # Sector strength contribution (scales with however many sectors have data)
        _sector_vals = [v for v in sectors.values() if v is not None]
        if _sector_vals:
            _total_s = len(_sector_vals)
            _green   = sum(1 for v in _sector_vals if v > 0)
            _thresh_up   = max(3, round(_total_s * 0.60))
            _thresh_down = max(1, round(_total_s * 0.25))
            if _green >= _thresh_up:
                score += 1
                _top = max(
                    ((k, v) for k, v in sectors.items() if v is not None),
                    key=lambda x: x[1],
                )
                factors.append(f"{_green}/{_total_s} sectors green, {_top[0]} leads")
            elif _green <= _thresh_down:
                score -= 1
                factors.append("sectors mostly red")

        # ── Regime ────────────────────────────────────────────────────────────
        if vix_level is not None and vix_level > 35:
            regime, label, css = "NO_TRADE", "NO TRADE DAY", "mt-no-trade"
            longs_ok, shorts_ok, reduce_size = False, False, True
            mode_desc = "Stay flat"
            reason_parts = ([vix_note] if vix_note else []) + [
                f for f in factors if "-" in f or "below" in f or "down" in f
            ][:2]
        elif score >= 3:
            regime, label, css = "RISK_ON", "ATTACK MODE", "mt-risk-on"
            longs_ok, shorts_ok, reduce_size = True, False, False
            mode_desc = "Momentum trades allowed"
            reason_parts = [
                f for f in factors if "+" in f or "above" in f or "up" in f
            ][:3] + ([vix_note] if vix_note else [])
        elif score >= 1:
            regime, label, css = "NEUTRAL", "NEUTRAL", "mt-neutral"
            longs_ok, shorts_ok, reduce_size = True, True, False
            mode_desc = "Be selective"
            reason_parts = factors[:3] + ([vix_note] if vix_note else [])
        elif score >= -1:
            regime, label, css = "CAUTION", "CAUTION / CHOP", "mt-caution"
            longs_ok, shorts_ok, reduce_size = False, False, True
            mode_desc = "Wait for clarity"
            reason_parts = factors[:3] + ([vix_note] if vix_note else [])
        else:
            regime, label, css = "RISK_OFF", "DEFENSIVE MODE", "mt-risk-off"
            longs_ok, shorts_ok, reduce_size = False, True, True
            mode_desc = "Reduce risk / wait"
            reason_parts = [
                f for f in factors if "-" in f or "below" in f or "down" in f
            ][:3] + ([vix_note] if vix_note else [])

        reason = " · ".join(p for p in reason_parts if p)

        # ── Meter score: map raw score [-8, +6] → [0, 100] ──────────────────
        # Clamp to [-8, +6] (14-point range), then scale linearly.
        # NO_TRADE forces the needle to the far-left extreme.
        if regime == "NO_TRADE":
            meter_score = 3
        else:
            _clamped    = max(-8, min(6, score))
            meter_score = int(round((_clamped + 8) / 14 * 100))
            meter_score = max(0, min(100, meter_score))

        # ── Action message ────────────────────────────────────────────────────
        _action_map = {
            "NO_TRADE": "No-trade conditions",
            "RISK_OFF":  "Shorts favored",
            "CAUTION":   "Avoid chasing — wait for clarity",
            "NEUTRAL":   "Selective — trade both sides carefully",
            "RISK_ON":   "Longs favored",
        }
        action_msg = _action_map.get(regime, "—")

        # ── Decision engine: zone from meter_score ────────────────────────────
        # 0-20 = Extreme Risk Off, 21-40 = Risk Off, 41-60 = Neutral,
        # 61-80 = Risk On, 81-100 = Extreme Risk On
        if meter_score <= 20:
            decision_cmd   = "DEFENSE MODE — Protect capital, avoid trades"
            risk_pct_rec   = 3
            size_multiplier = 0.25
            size_zone      = "extreme-off"
        elif meter_score <= 40:
            decision_cmd   = "CAUTION — Only A+ setups, reduce size"
            risk_pct_rec   = 5
            size_multiplier = 0.5
            size_zone      = "risk-off"
        elif meter_score <= 60:
            decision_cmd   = "SELECTIVE — Trade carefully, no chasing"
            risk_pct_rec   = 7
            size_multiplier = 0.8
            size_zone      = "neutral"
        elif meter_score <= 80:
            decision_cmd   = "STANDARD MODE — Normal trading allowed"
            risk_pct_rec   = 10
            size_multiplier = 1.0
            size_zone      = "risk-on"
        else:
            decision_cmd   = "ATTACK MODE — Momentum trades allowed"
            risk_pct_rec   = 15
            size_multiplier = 1.25
            size_zone      = "extreme-on"

        # ── Why: short human-readable one-liner from actual data ─────────────
        _why_parts = []
        _vwap_bullish = (
            (spy_vs_vwap is not None and spy_vs_vwap > 0) and
            (qqq_vs_vwap is not None and qqq_vs_vwap > 0)
        )
        _vwap_bearish = (
            (spy_vs_vwap is not None and spy_vs_vwap < 0) and
            (qqq_vs_vwap is not None and qqq_vs_vwap < 0)
        )
        _ema_bullish = spy_pct_ema20 > 0 and qqq_pct_ema20 > 0
        _ema_bearish = spy_pct_ema20 < 0 and qqq_pct_ema20 < 0

        if _vwap_bullish:
            _why_parts.append("SPY & QQQ above VWAP")
        elif _vwap_bearish:
            _why_parts.append("SPY & QQQ below VWAP")
        elif spy_vs_vwap is None:
            if _ema_bullish:
                _why_parts.append("SPY & QQQ above EMA20")
            elif _ema_bearish:
                _why_parts.append("SPY & QQQ below EMA20")

        if spy_vs_vwap is not None and _ema_bullish:
            _why_parts.append("above EMA20")
        elif spy_vs_vwap is not None and _ema_bearish:
            _why_parts.append("below EMA20")

        if vix_level is not None:
            if vix_level < 15:
                _why_parts.append(f"VIX {vix_level:.0f} (very low)")
            elif vix_level < 20:
                _why_parts.append(f"VIX {vix_level:.0f} (calm)")
            elif vix_level < 25:
                _why_parts.append(f"VIX {vix_level:.0f} (elevated)")
            else:
                _dir = f" {vix_direction}" if vix_direction else ""
                _why_parts.append(f"VIX {vix_level:.0f}{_dir} (high risk)")

        if meter_score <= 20:
            _why_suffix = "extreme risk-off conditions"
        elif meter_score <= 40:
            _why_suffix = "bearish conditions"
        elif meter_score <= 60:
            _why_suffix = "mixed/choppy conditions"
        elif meter_score <= 80:
            _why_suffix = "bullish conditions"
        else:
            _why_suffix = "strongly bullish conditions"

        why = (", ".join(_why_parts) + f" → {_why_suffix}") if _why_parts else f"Score {meter_score}/100 — {_why_suffix}"

        logger.info(
            "compute_market_temperature  regime=%s  score=%s  meter=%s  zone=%s  vix=%.1f  reason=%s",
            regime, score, meter_score, size_zone, vix_level or 0, reason,
        )
        return {
            "regime":          regime,
            "label":           label,
            "css":             css,
            "reason":          reason,
            "mode_desc":       mode_desc,
            "action_msg":      action_msg,
            "longs_ok":        longs_ok,
            "shorts_ok":       shorts_ok,
            "reduce_size":     reduce_size,
            "score":           score,
            "meter_score":     meter_score,
            "decision_cmd":    decision_cmd,
            "risk_pct_rec":    risk_pct_rec,
            "size_multiplier": size_multiplier,
            "size_zone":       size_zone,
            "why":             why,
            "spy_price":       round(spy_price, 2),
            "spy_pct_ema20":   round(spy_pct_ema20, 2),
            "spy_vs_vwap":     round(spy_vs_vwap, 2) if spy_vs_vwap is not None else None,
            "qqq_price":       round(qqq_price, 2),
            "qqq_pct_ema20":   round(qqq_pct_ema20, 2),
            "qqq_vs_vwap":     round(qqq_vs_vwap, 2) if qqq_vs_vwap is not None else None,
            "vix_level":         round(vix_level, 1) if vix_level is not None else None,
            "vix_direction":     vix_direction,
            "es_price":          round(es_price, 2) if es_price is not None else None,
            "es_change_pct":     round(es_change_pct, 2) if es_change_pct is not None else None,
            "es_above_vwap":     es_above_vwap,
            "yield_10y":         yield_10y,
            "yield_change_bps":  yield_change_bps,
            "yield_trend":       yield_trend,
            "yield_note":        yield_note,
            "dxy_price":         dxy_price,
            "dxy_change_pct":    dxy_change_pct,
            "dxy_trend":         dxy_trend,
            "sectors":           sectors,
            "error":             False,
        }

    except Exception as _e:
        logger.warning("compute_market_temperature failed: %s", _e)
        return {**_UNKNOWN, "reason": "Error computing market data"}


def fetch_market_context() -> dict:
    """
    Lightweight fetch of ES futures + sector ETF data.
    Cached externally (30 s). Returns data suitable for /api/market_context.
    """
    import threading as _thr

    _results: dict = {}

    def _fetch(key, ticker, interval, range_str):
        try:
            _results[key] = _fetch_ohlcv_via_chart_api(
                ticker, interval=interval, range_str=range_str
            )
        except Exception:
            _results[key] = None

    _tasks = [
        ("es_d",   "ES=F",      "1d", "5d"),
        ("es_h",   "ES=F",      "1h", "5d"),
        ("xlk_d",  "XLK",       "1d", "5d"),
        ("xly_d",  "XLY",       "1d", "5d"),
        ("xlf_d",  "XLF",       "1d", "5d"),
        ("xle_d",  "XLE",       "1d", "5d"),
        ("xlv_d",  "XLV",       "1d", "5d"),
        ("xli_d",  "XLI",       "1d", "5d"),
        ("xlu_d",  "XLU",       "1d", "5d"),
        ("xlb_d",  "XLB",       "1d", "5d"),
        ("xlre_d", "XLRE",      "1d", "5d"),
        ("xlc_d",  "XLC",       "1d", "5d"),
        ("smh_d",  "SMH",       "1d", "5d"),
        ("iwm_d",  "IWM",       "1d", "5d"),
        ("tnx_d",  "^TNX",      "1d", "5d"),
        ("dxy_d",  "DX-Y.NYB",  "1d", "5d"),
    ]
    _threads = [_thr.Thread(target=_fetch, args=a, daemon=True) for a in _tasks]
    for _t in _threads:
        _t.start()
    for _t in _threads:
        _t.join(timeout=18)

    # ES futures
    es: dict = {"price": None, "change_pct": None, "above_vwap": None, "error": True}
    es_d = _results.get("es_d")
    es_h = _results.get("es_h")
    if es_d and len(es_d.get("closes", [])) >= 2:
        try:
            _ep    = es_d["closes"][-1]
            _eprev = es_d["closes"][-2]
            # Intraday VWAP using today's hourly bars
            _evwap = None
            if es_h and es_h.get("timestamps"):
                try:
                    import zoneinfo as _zi
                    from datetime import timezone as _tz
                    _today_et = _et_now().strftime("%Y-%m-%d")
                    _bars = [
                        (c, h, lo, v)
                        for ts, _, c, h, lo, v in zip(
                            es_h["timestamps"], es_h["opens"],
                            es_h["closes"],     es_h["highs"],
                            es_h["lows"],        es_h["volumes"],
                        )
                        if datetime.fromtimestamp(ts, tz=_tz.utc)
                           .astimezone(_zi.ZoneInfo("America/New_York"))
                           .strftime("%Y-%m-%d") == _today_et
                    ]
                    if _bars:
                        _tpv = sum((h + lo + c) / 3.0 * v for c, h, lo, v in _bars)
                        _tv  = sum(v for _, _, _, v in _bars)
                        if _tv > 0:
                            _evwap = _tpv / _tv
                except Exception:
                    pass
            es = {
                "price":      round(_ep, 2),
                "change_pct": round((_ep - _eprev) / _eprev * 100, 2),
                "above_vwap": (bool(_ep > _evwap) if _evwap is not None else None),
                "error":      False,
            }
        except Exception:
            pass

    # Sector ETFs (12 sectors)
    sectors: dict = {}
    for key, name in [
        ("xlk_d","XLK"), ("xly_d","XLY"), ("xlf_d","XLF"), ("xle_d","XLE"),
        ("xlv_d","XLV"), ("xli_d","XLI"), ("xlu_d","XLU"), ("xlb_d","XLB"),
        ("xlre_d","XLRE"),("xlc_d","XLC"), ("smh_d","SMH"), ("iwm_d","IWM"),
    ]:
        d = _results.get(key)
        if d and len(d.get("closes", [])) >= 2:
            try:
                _p  = d["closes"][-1]
                _pp = d["closes"][-2]
                sectors[name] = round((_p - _pp) / _pp * 100, 2)
            except Exception:
                sectors[name] = None
        else:
            sectors[name] = None

    # 10Y Treasury yield
    yield_10y = None; yield_change_bps = None; yield_trend = "flat"; yield_note = "—"
    _tnx = _results.get("tnx_d")
    if _tnx and len(_tnx.get("closes", [])) >= 2:
        try:
            yield_10y        = round(_tnx["closes"][-1], 3)
            _tnx_prev        = _tnx["closes"][-2]
            yield_change_bps = round((yield_10y - _tnx_prev) * 100, 1)
            yield_trend = (
                "rising fast" if yield_change_bps > 10 else
                "rising"      if yield_change_bps > 2  else
                "falling fast"if yield_change_bps < -10 else
                "falling"     if yield_change_bps < -2 else "flat"
            )
            _note_map = {
                "rising fast": "Pressure on growth/tech — rising rates headwind",
                "rising":      "Watch tech — yields creeping higher",
                "falling fast":"Supportive for growth/tech — rates declining",
                "falling":     "Mild tailwind for growth/tech",
                "flat":        "Neutral — no rate pressure",
            }
            yield_note = _note_map[yield_trend]
        except Exception:
            pass

    # DXY
    dxy_price = None; dxy_change_pct = None; dxy_trend = "flat"
    _dxy = _results.get("dxy_d")
    if _dxy and len(_dxy.get("closes", [])) >= 2:
        try:
            dxy_price      = round(_dxy["closes"][-1], 2)
            _dxy_prev      = _dxy["closes"][-2]
            dxy_change_pct = round((dxy_price - _dxy_prev) / _dxy_prev * 100, 2) if _dxy_prev else 0.0
            dxy_trend = "rising" if dxy_change_pct > 0.3 else "falling" if dxy_change_pct < -0.3 else "flat"
        except Exception:
            pass

    # After-hours flag (rough ET check — 9:30–16:00 = regular session)
    after_hours = True
    try:
        _now_et = _et_now()
        _mins   = _now_et.hour * 60 + _now_et.minute
        after_hours = not (570 <= _mins < 960)   # 9:30–16:00
    except Exception:
        pass

    return {
        "es":              es,
        "sectors":         sectors,
        "after_hours":     after_hours,
        "yield_10y":       yield_10y,
        "yield_change_bps":yield_change_bps,
        "yield_trend":     yield_trend,
        "yield_note":      yield_note,
        "dxy_price":       dxy_price,
        "dxy_change_pct":  dxy_change_pct,
        "dxy_trend":       dxy_trend,
    }
