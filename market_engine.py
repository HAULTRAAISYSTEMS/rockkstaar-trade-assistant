"""
market_engine.py — Market Regime, Sector Rotation & Relative Strength Engine

Provides market-wide intelligence for the Rockkstaar Trade Assistant:

  get_market_context()           → cached regime + sector strengths + VIX
  get_sector_for_ticker(ticker)  → (etf_symbol, sector_name)
  rs_score_intraday(stock_gap, qqq_gap) → int 1-100 (quick intraday RS)
  compute_rs_score_20d(ticker)   → int 1-100 (20-day RS vs QQQ, slower)

Market Regime:
  RISK_ON    — QQQ & SPY above 20 EMA, trending up, VIX calm
  NEUTRAL    — mixed signals
  CAUTION    — near EMA, elevated VIX, choppy
  RISK_OFF   — below EMAs, bearish structure
  NO_TRADE   — VIX extreme (>30)

Sector ETFs tracked:
  XLK, SMH, XLF, XLE, XLY, XLC, XLB, XLI, XLV, XLU, XLRE, IWM

All data cached with 60-minute TTL.
Data source: yfinance + Yahoo Finance chart API (no paid key required).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_YF_AVAILABLE = False
try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    pass

# ── Cache ──────────────────────────────────────────────────────────────────────

_cache_lock      = threading.Lock()
_market_cache:   dict = {}
_market_cache_at: Optional[datetime] = None
_CACHE_TTL_MIN   = 60          # refresh market context every hour

_rs_cache: dict  = {}          # {ticker: (score, vs_qqq, fetched_at)}
_RS_TTL_MIN      = 120         # 2-hour RS cache (intraday drift is slow)

# ── Sector registry ────────────────────────────────────────────────────────────

SECTOR_ETFS: dict[str, str] = {
    "XLK":  "Technology",
    "SMH":  "Semiconductors",
    "XLF":  "Financials",
    "XLE":  "Energy",
    "XLY":  "Consumer Disc",
    "XLC":  "Communication",
    "XLB":  "Materials",
    "XLI":  "Industrials",
    "XLV":  "Healthcare",
    "XLU":  "Utilities",
    "XLRE": "Real Estate",
    "IWM":  "Small Cap",
}

# Prebuilt ticker → sector ETF mapping (avoids extra yfinance calls)
_TICKER_SECTOR: dict[str, str] = {
    # Technology / AI / Software
    "NVDA": "XLK", "AMD":  "XLK", "MSFT": "XLK", "AAPL": "XLK",
    "META": "XLK", "GOOGL":"XLK", "GOOG": "XLK", "ORCL": "XLK",
    "CRM":  "XLK", "NOW":  "XLK", "ADBE": "XLK", "INTC": "SMH",
    "QCOM": "SMH", "TXN":  "SMH", "AVGO": "SMH", "MU":   "SMH",
    "MRVL": "SMH", "AMAT": "SMH", "LRCX": "SMH", "KLAC": "SMH",
    "ASML": "SMH", "ONTO": "SMH", "ACLS": "SMH",
    # AI / Cloud / Cybersecurity
    "PLTR": "XLK", "AI":   "XLK", "SOUN": "XLK", "BBAI": "XLK",
    "PATH": "XLK", "CRWD": "XLK", "PANW": "XLK", "ZS":   "XLK",
    "DDOG": "XLK", "NET":  "XLK", "SNOW": "XLK", "OKTA": "XLK",
    "GTLB": "XLK", "RDDT": "XLC", "PINS": "XLC",
    # Consumer / E-Commerce
    "AMZN": "XLY", "TSLA": "XLY", "NFLX": "XLC", "HD":   "XLY",
    "MCD":  "XLY", "SBUX": "XLY", "NKE":  "XLY", "TGT":  "XLY",
    "WMT":  "XLY", "COST": "XLY",
    # Financials / Crypto
    "JPM":  "XLF", "GS":   "XLF", "MS":   "XLF", "BAC":  "XLF",
    "WFC":  "XLF", "V":    "XLF", "MA":   "XLF", "COIN": "XLF",
    "HOOD": "XLF", "PYPL": "XLF", "SQ":   "XLF",
    # Healthcare / Biotech
    "LLY":  "XLV", "UNH":  "XLV", "JNJ":  "XLV", "ABBV": "XLV",
    "MRK":  "XLV", "PFE":  "XLV", "MRNA": "XLV", "BNTX": "XLV",
    # Energy
    "XOM":  "XLE", "CVX":  "XLE", "COP":  "XLE", "SLB":  "XLE",
    # Communication
    "GOOGL":"XLC", "META": "XLC", "DIS":  "XLC", "T":    "XLC",
}

# yfinance sector → ETF symbol
_YF_SECTOR_MAP: dict[str, str] = {
    "Technology":             "XLK",
    "Communication Services": "XLC",
    "Consumer Cyclical":      "XLY",
    "Financial Services":     "XLF",
    "Healthcare":             "XLV",
    "Energy":                 "XLE",
    "Basic Materials":        "XLB",
    "Industrials":            "XLI",
    "Utilities":              "XLU",
    "Real Estate":            "XLRE",
    "Consumer Defensive":     "XLY",
    "Technology (Semis)":     "SMH",
}


# ── Public: ticker → sector ────────────────────────────────────────────────────

def get_sector_for_ticker(ticker: str) -> tuple[str, str]:
    """
    Return (etf_symbol, sector_name) for a ticker.
    Uses built-in map first; falls back to yfinance info.
    Returns ("", "") if unknown.
    """
    t = (ticker or "").upper().strip()
    etf = _TICKER_SECTOR.get(t)
    if etf:
        return etf, SECTOR_ETFS.get(etf, "")

    if _YF_AVAILABLE:
        try:
            info   = yf.Ticker(t).info
            sector = info.get("sector", "")
            etf    = _YF_SECTOR_MAP.get(sector, "")
            if etf:
                _TICKER_SECTOR[t] = etf   # cache for this session
                return etf, SECTOR_ETFS.get(etf, sector)
        except Exception:
            pass

    return "", ""


# ── Intraday RS (fast — no API call) ─────────────────────────────────────────

def rs_score_intraday(stock_gap_pct: float | None, qqq_1d_pct: float | None) -> int:
    """
    Fast intraday RS score based on today's gap vs QQQ's move.

    Returns 1-100 where:
      >80 = RS leader (outperforming strongly)
      >65 = strong RS
      ~50 = in-line with market
      <35 = weak RS
      <20 = laggard
    """
    sg = stock_gap_pct or 0.0
    qg = qqq_1d_pct    or 0.0
    out_pct = sg - qg
    raw = 50 + out_pct * 7.5
    return max(1, min(100, round(raw)))


def rs_label(score: int) -> str:
    """Human-readable RS strength label."""
    if score >= 85:
        return "RS Leader"
    if score >= 70:
        return "Strong RS"
    if score >= 55:
        return "Above Avg"
    if score >= 40:
        return "Neutral RS"
    if score >= 25:
        return "Weak RS"
    return "RS Laggard"


def rs_css_class(score: int) -> str:
    """CSS class for the RS badge."""
    if score >= 80:
        return "rs-leader"
    if score >= 65:
        return "rs-strong"
    if score >= 45:
        return "rs-avg"
    if score >= 30:
        return "rs-weak"
    return "rs-laggard"


# ── Low-level price helpers ────────────────────────────────────────────────────

def _fetch_prices_chart(tickers: list[str], period: str = "60d") -> dict[str, list[float]]:
    """
    Fetch daily close prices via Yahoo Finance chart API.
    Returns {ticker: [close_prices]} (may be empty list on failure).
    Avoids yfinance's crumb/cookie requirement — works on cloud IPs.
    """
    result: dict[str, list[float]] = {t: [] for t in tickers}
    if not tickers:
        return result

    try:
        import requests as _req
    except ImportError:
        return result

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }

    for ticker in tickers:
        for base in (
            "https://query1.finance.yahoo.com/v8/finance/chart",
            "https://query2.finance.yahoo.com/v8/finance/chart",
        ):
            try:
                url = f"{base}/{ticker}"
                r   = _req.get(
                    url,
                    params={"interval": "1d", "range": period},
                    headers=headers,
                    timeout=10,
                )
                if r.status_code != 200:
                    continue
                node = r.json()["chart"]["result"][0]
                closes = node["indicators"]["quote"][0].get("close", [])
                prices = [float(c) for c in closes if c is not None and c > 0]
                if prices:
                    result[ticker] = prices
                    break
            except Exception as _e:
                logger.debug("market_engine chart fetch %s: %s", ticker, _e)
                continue

    return result


def _pct_change(prices: list[float], lookback: int) -> Optional[float]:
    """% change over last `lookback` bars. None if insufficient data."""
    if not prices or len(prices) < lookback + 1:
        return None
    base = prices[-lookback - 1]
    if base == 0:
        return None
    return (prices[-1] - base) / base * 100


def _ema(prices: list[float], period: int) -> Optional[float]:
    """Exponential moving average. Returns the last EMA value."""
    if not prices or len(prices) < period:
        return None
    mult = 2 / (period + 1)
    ema  = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * mult + ema * (1 - mult)
    return ema


# ── Market regime ──────────────────────────────────────────────────────────────

def _compute_regime(
    qqq: list[float],
    spy: list[float],
    vix: list[float],
) -> dict:
    """Derive market regime from index prices."""
    if not qqq or not spy:
        return _empty_regime()

    qqq_last  = qqq[-1]
    spy_last  = spy[-1]
    q_ema20   = _ema(qqq, min(20, len(qqq)))
    q_ema50   = _ema(qqq, min(50, len(qqq)))
    s_ema20   = _ema(spy, min(20, len(spy)))
    s_ema50   = _ema(spy, min(50, len(spy)))
    q_1d      = _pct_change(qqq, 1)
    s_1d      = _pct_change(spy, 1)
    q_5d      = _pct_change(qqq, 5)
    q_20d     = _pct_change(qqq, 20)
    vix_last  = vix[-1] if vix else None

    def _trend(last, ema20, ema50, chg):
        if ema20 and ema50 and last > ema20 > ema50 and (chg or 0) > 0:
            return "Bullish"
        if ema20 and ema50 and last < ema20 < ema50 and (chg or 0) < 0:
            return "Bearish"
        if ema20 and last > ema20:
            return "Above 20 EMA"
        if ema20 and last < ema20:
            return "Below 20 EMA"
        return "Neutral"

    qqq_trend = _trend(qqq_last, q_ema20, q_ema50, q_1d)
    spy_trend = _trend(spy_last, s_ema20, s_ema50, s_1d)

    vix_extreme  = vix_last is not None and vix_last > 30
    vix_elevated = vix_last is not None and vix_last > 20
    both_bull = "Bullish" in qqq_trend and "Bullish" in spy_trend
    both_bear = "Bearish" in qqq_trend and "Bearish" in spy_trend
    both_above = "Above" in qqq_trend or both_bull

    if vix_extreme:
        regime, label = "NO_TRADE", "No Trade — VIX Extreme"
        signal = f"VIX {vix_last:.0f} — too volatile for new entries"
    elif both_bull and not vix_elevated and (q_5d or 0) > 0:
        regime, label = "RISK_ON", "Risk-On"
        signal = "QQQ & SPY trending bullish — momentum environment"
    elif both_bear or ((q_1d or 0) < -1.0 and (s_1d or 0) < -0.8):
        regime, label = "RISK_OFF", "Risk-Off"
        signal = "Indices below EMAs — defensive posture required"
    elif vix_elevated or (not both_above and not both_bull):
        regime, label = "CAUTION", "Caution — Choppy"
        signal = f"Mixed signals{f', VIX {vix_last:.0f}' if vix_last else ''} — reduce size"
    elif both_above:
        regime, label = "RISK_ON", "Risk-On"
        signal = "Indices holding above 20 EMA — favorable for longs"
    else:
        regime, label = "NEUTRAL", "Neutral"
        signal = "Mixed market signals — be selective"

    return {
        "regime":       regime,
        "regime_label": label,
        "qqq_trend":    qqq_trend,
        "spy_trend":    spy_trend,
        "vix_level":    round(vix_last, 1) if vix_last else None,
        "qqq_1d_pct":   round(q_1d,  2) if q_1d  is not None else None,
        "spy_1d_pct":   round(s_1d,  2) if s_1d  is not None else None,
        "qqq_5d_pct":   round(q_5d,  2) if q_5d  is not None else None,
        "qqq_20d_pct":  round(q_20d, 2) if q_20d is not None else None,
        "signal":       signal,
        "longs_ok":     regime not in ("RISK_OFF", "NO_TRADE"),
        "shorts_ok":    regime not in ("RISK_ON",),
        "reduce_size":  regime == "CAUTION",
        "no_trade":     regime == "NO_TRADE",
    }


def _empty_regime() -> dict:
    return {
        "regime":       "NEUTRAL",
        "regime_label": "Neutral",
        "qqq_trend":    "Unknown",
        "spy_trend":    "Unknown",
        "vix_level":    None,
        "qqq_1d_pct":   None,
        "spy_1d_pct":   None,
        "qqq_5d_pct":   None,
        "qqq_20d_pct":  None,
        "signal":       "Market data unavailable",
        "longs_ok":     True,
        "shorts_ok":    True,
        "reduce_size":  False,
        "no_trade":     False,
    }


# ── Sector strengths ───────────────────────────────────────────────────────────

def _compute_sectors(prices: dict[str, list[float]]) -> list[dict]:
    """Rank sector ETFs by 1-day and 5-day performance."""
    sectors = []
    for etf, name in SECTOR_ETFS.items():
        px = prices.get(etf, [])
        if not px:
            continue
        chg_1d = _pct_change(px, 1)
        chg_5d = _pct_change(px, 5)
        ema20  = _ema(px, min(20, len(px)))
        last   = px[-1]
        above  = bool(ema20 and last > ema20)

        if chg_1d is None:
            continue

        if chg_1d >= 1.5 or (chg_1d >= 0.5 and above and (chg_5d or 0) > 2):
            strength = "leading"
        elif chg_1d >= 0.3:
            strength = "strong"
        elif chg_1d <= -1.5 or (chg_1d <= -0.5 and not above):
            strength = "weak"
        elif chg_1d <= -0.3:
            strength = "lagging"
        else:
            strength = "neutral"

        sectors.append({
            "etf":        etf,
            "name":       name,
            "price":      round(last, 2),
            "change_1d":  round(chg_1d, 2),
            "change_5d":  round(chg_5d, 2) if chg_5d is not None else None,
            "above_ema20": above,
            "strength":   strength,
            "css":        _sector_css(strength),
        })

    sectors.sort(key=lambda x: x["change_1d"], reverse=True)
    for i, s in enumerate(sectors):
        s["rank"] = i + 1
    return sectors


def _sector_css(strength: str) -> str:
    return {
        "leading":  "sector-leading",
        "strong":   "sector-strong",
        "neutral":  "sector-neutral",
        "lagging":  "sector-lagging",
        "weak":     "sector-weak",
    }.get(strength, "sector-neutral")


# ── Context builder ────────────────────────────────────────────────────────────

def _build_context() -> dict:
    """Full market context fetch. Called at most once per _CACHE_TTL_MIN."""
    all_tickers = ["QQQ", "SPY", "^VIX"] + list(SECTOR_ETFS.keys())
    prices = _fetch_prices_chart(all_tickers, period="60d")

    qqq = prices.get("QQQ", [])
    spy = prices.get("SPY", [])
    vix = prices.get("^VIX", [])

    regime  = _compute_regime(qqq, spy, vix)
    sectors = _compute_sectors(prices)

    leading = [s["etf"] for s in sectors if s["strength"] in ("leading", "strong")][:4]
    weak    = [s["etf"] for s in sectors if s["strength"] in ("weak", "lagging")][:3]

    return {
        **regime,
        "sectors":         sectors,
        "leading_sectors": leading,
        "weak_sectors":    weak,
        "top_sector":      sectors[0] if sectors else None,
        "bottom_sector":   sectors[-1] if sectors else None,
        "qqq_price":       round(qqq[-1], 2) if qqq else None,
        "spy_price":       round(spy[-1], 2) if spy else None,
        "_qqq_prices":     qqq,     # kept for compute_rs_score_20d
        "fetched_at":      datetime.now().isoformat(),
    }


def _empty_context() -> dict:
    return {
        **_empty_regime(),
        "sectors":         [],
        "leading_sectors": [],
        "weak_sectors":    [],
        "top_sector":      None,
        "bottom_sector":   None,
        "qqq_price":       None,
        "spy_price":       None,
        "_qqq_prices":     [],
        "fetched_at":      None,
    }


def get_market_context() -> dict:
    """
    Return cached market context. Thread-safe. Refreshes every _CACHE_TTL_MIN.
    Always returns a valid dict — never raises.
    """
    global _market_cache, _market_cache_at

    now = datetime.now()
    with _cache_lock:
        if (
            _market_cache_at is not None
            and (now - _market_cache_at).total_seconds() < _CACHE_TTL_MIN * 60
            and _market_cache
        ):
            return dict(_market_cache)

    try:
        ctx = _build_context()
    except Exception as e:
        logger.warning("market_engine._build_context failed: %s", e)
        ctx = _empty_context()

    with _cache_lock:
        _market_cache    = ctx
        _market_cache_at = now

    return dict(ctx)


def refresh_market_context_bg() -> None:
    """Kick off a background refresh without blocking the caller."""
    import threading as _th
    _th.Thread(target=get_market_context, daemon=True, name="mkt-ctx-refresh").start()


# ── 20-day RS score (slower — fetches per-ticker data) ───────────────────────

def compute_rs_score_20d(ticker: str, period_days: int = 20) -> tuple[int, float]:
    """
    Compute Relative Strength score vs QQQ over `period_days` days.

    Returns (rs_score: int 1-100, rs_vs_qqq_pct: float).
    Uses cached QQQ prices when available.

    Scoring:
      100 = extreme outperformer
      75  = strong RS leader
      50  = in-line with QQQ
      25  = weak RS
      1   = extreme laggard
    """
    t = (ticker or "").upper().strip()
    if not t:
        return 50, 0.0

    # Check cache
    with _cache_lock:
        cached = _rs_cache.get(t)
        if cached:
            score, vs_qqq, at = cached
            if (datetime.now() - at).total_seconds() < _RS_TTL_MIN * 60:
                return score, vs_qqq

    try:
        # Try to reuse QQQ prices from market context cache
        qqq_prices: list[float] = []
        with _cache_lock:
            if _market_cache:
                qqq_prices = _market_cache.get("_qqq_prices", [])

        # Fetch stock prices (and QQQ if not cached)
        need = [t] if qqq_prices else [t, "QQQ"]
        prices = _fetch_prices_chart(need, period=f"{period_days + 15}d")

        stock_px = prices.get(t, [])
        if not qqq_prices:
            qqq_prices = prices.get("QQQ", [])

        n = min(len(stock_px), len(qqq_prices), period_days + 1)
        if n < 5:
            return 50, 0.0

        stock_ret = (stock_px[-1]  - stock_px[-n])  / stock_px[-n]  * 100
        qqq_ret   = (qqq_prices[-1] - qqq_prices[-n]) / qqq_prices[-n] * 100
        vs_qqq    = round(stock_ret - qqq_ret, 2)

        # Map ±20% outperformance → 1-100 scale
        raw   = 50 + vs_qqq * 2.5
        score = max(1, min(100, round(raw)))

        with _cache_lock:
            _rs_cache[t] = (score, vs_qqq, datetime.now())

        return score, vs_qqq

    except Exception as e:
        logger.debug("compute_rs_score_20d %s: %s", ticker, e)
        return 50, 0.0


# ── Sector alignment helper ────────────────────────────────────────────────────

def is_sector_leading(ticker_etf: str, ctx: dict | None = None) -> bool:
    """Return True if the ticker's sector ETF is in the current leading sectors."""
    if not ticker_etf:
        return False
    c = ctx or get_market_context()
    return ticker_etf in (c.get("leading_sectors") or [])


# ── Market temperature dict (compatible with existing _get_market_temperature) ─

def market_temperature_from_context(ctx: dict) -> dict:
    """
    Return a market temperature dict in the same shape used by the existing
    compute_trade_coach() / compute_no_trade_assessment() functions.
    Pass the result of get_market_context() as `ctx`.
    """
    regime = ctx.get("regime", "NEUTRAL")
    signal = ctx.get("signal", "")
    vix    = ctx.get("vix_level")

    _regime_map = {
        "RISK_ON":  "RISK_ON",
        "NEUTRAL":  "NEUTRAL",
        "CAUTION":  "CAUTION",
        "RISK_OFF": "RISK_OFF",
        "NO_TRADE": "NO_TRADE",
    }

    _decision_map = {
        "RISK_ON":  "Buy — Market Bullish",
        "NEUTRAL":  "Selective — Be Choosy",
        "CAUTION":  "Reduce Size — Choppy",
        "RISK_OFF": "Avoid Longs — Risk Off",
        "NO_TRADE": "No Trade — VIX Extreme",
    }

    return {
        "regime":        _regime_map.get(regime, "NEUTRAL"),
        "longs_ok":      ctx.get("longs_ok", True),
        "shorts_ok":     ctx.get("shorts_ok", True),
        "reduce_size":   ctx.get("reduce_size", False),
        "decision_cmd":  _decision_map.get(regime, "Monitor Market"),
        "vix":           vix,
        "signal":        signal,
    }
