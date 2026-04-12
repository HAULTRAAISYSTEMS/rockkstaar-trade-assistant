"""
data_fetcher.py - Live market data via yfinance.
Provides fetch_live_data() and fetch_news_headlines() for use in generate_stock_data().
Falls back gracefully if yfinance is unavailable or the fetch fails.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from datetime import datetime, date

logger = logging.getLogger(__name__)


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

        # ── Primary: yfinance history (200 trading days ≈ 10 months) ─────────
        # Needs ≥200 bars for the 200 EMA; ≥20 for everything else.
        # Fails on cloud IPs when Yahoo Finance rejects the crumb token.
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

        # ── Fallback: direct chart API (range=1y → ≥252 bars, no crumb needed) ─
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

        # ── Fibonacci levels (20-bar swing high/low) ────────────────────────
        lb     = min(20, n)
        sw_hi  = float(max(highs[-lb:]))
        sw_lo  = float(min(lows[-lb:]))
        sw_rng = sw_hi - sw_lo

        result["fib_high"] = round(sw_hi, 2)
        result["fib_low"]  = round(sw_lo, 2)
        if sw_rng > 0:
            result["fib_50"]  = round(sw_hi - 0.500 * sw_rng, 2)
            result["fib_618"] = round(sw_hi - 0.618 * sw_rng, 2)
        else:
            result["fib_50"]  = None
            result["fib_618"] = None

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

        except Exception as e:
            logger.debug("4H data fetch failed for %s: %s", ticker, e)

        result.setdefault("h4_trend",  "Neutral")
        result.setdefault("h4_ema20",  None)
        result.setdefault("h4_ema50",  None)
        result.setdefault("h4_hh_hl",  False)

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
