"""
data_fetcher.py - Live market data via yfinance.
Provides fetch_live_data() and fetch_news_headlines() for use in generate_stock_data().
Falls back gracefully if yfinance is unavailable or the fetch fails.
"""

from __future__ import annotations

import logging
from datetime import datetime, date

logger = logging.getLogger(__name__)

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False
    logger.warning("yfinance not installed — live data unavailable. Run: pip install yfinance")


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

    try:
        t = yf.Ticker(ticker)
        result: dict = {}

        # ------------------------------------------------------------------ #
        # 1. Fast info — current price, prev close, avg volume
        # ------------------------------------------------------------------ #
        fi = t.fast_info
        current_price = None
        prev_close    = None
        avg_volume    = None

        try:
            current_price = float(fi.last_price) if fi.last_price else None
        except Exception:
            pass

        try:
            prev_close = float(fi.previous_close) if fi.previous_close else None
        except Exception:
            pass

        try:
            avg_volume = int(fi.three_month_average_volume) if fi.three_month_average_volume else None
        except Exception:
            pass

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
            hist = t.history(period="5d", interval="1d")
            if not hist.empty:
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
            # Fallback: use fast_info previous_close if the history call failed entirely
            if prev_close and "prev_close" not in result:
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
        # ------------------------------------------------------------------ #
        try:
            # --- Phase from current ET time (set even if data fetch below fails) ---
            now_et   = _et_now()
            h, m     = now_et.hour, now_et.minute
            today_str = now_et.strftime("%Y-%m-%d")   # used for date filtering below

            if h < 9 or (h == 9 and m < 30):
                result["orb_phase"] = "pre_market"
            elif (h == 9 and m >= 30) or (h == 10 and m == 0):
                result["orb_phase"] = "forming"
            else:
                result["orb_phase"] = "locked"

            # --- Fetch 1-minute bars (pre + regular session) ---
            intra = t.history(period="1d", interval="1m", prepost=True)
            if not intra.empty:
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
        # ------------------------------------------------------------------ #
        try:
            cal = t.calendar
            earnings_date = None
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if ed:
                    # May be a list or a single value
                    if isinstance(ed, (list, tuple)):
                        ed = ed[0]
                    if hasattr(ed, "date"):
                        earnings_date = str(ed.date())
                    else:
                        earnings_date = str(ed)[:10]
            elif hasattr(cal, "columns"):
                # DataFrame format
                if "Earnings Date" in cal.columns:
                    ed = cal["Earnings Date"].iloc[0]
                    earnings_date = str(ed.date()) if hasattr(ed, "date") else str(ed)[:10]
            if earnings_date:
                result["earnings_date"] = earnings_date
        except Exception as e:
            logger.debug("Earnings date fetch failed for %s: %s", ticker, e)

        return result if result else None

    except Exception as e:
        logger.warning("fetch_live_data failed for %s: %s", ticker, e)
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
        t = yf.Ticker(ticker)
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
