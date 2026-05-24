"""
opportunity_engine.py — Hidden Opportunity Research Engine

Scans for hidden investment opportunities before they hit major headlines.

Public API:
  scan_ticker(ticker, mkt_ctx, liq_ctx)  → full opportunity analysis
  run_opportunity_scan(tickers, ...)     → scan a list and rank results
  get_research_report(ticker, ...)       → structured investment research report
  build_opportunity_universe()           → curated universe of stocks to scan

Scoring:
  confluence_score (0-100): 10 factors × 10 pts each
  opportunity_grade: A+ (≥85) | A (≥75) | B+ (≥65) | B (≥55) | C (<55)

Action signals (never says "buy"):
  Accumulate | Watch | Wait for Pullback | Breakout Confirmed | Extended — Do Not Chase | Avoid

Data sources:
  - yfinance (.info + chart API)  → price, fundamentals
  - market_engine cache           → RS scores, sector alignment
  - liquidity_engine cache        → macro backdrop score

No fake predictions — all scores are evidence-based with explicit breakdown.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ── Fundamental data cache ─────────────────────────────────────────────────────
_fund_cache: dict[str, dict] = {}   # ticker → {data, fetched_at}
_fund_lock  = threading.Lock()
_FUND_TTL_H = 4   # hours

_fetch_active_set: set[str] = set()
_fetch_set_lock   = threading.Lock()

# Throttle concurrent yfinance requests — Yahoo Finance rate-limits heavy parallel usage
_yf_semaphore = threading.Semaphore(5)

# Known ETFs — don't score on fundamental factors (no revenue/EPS/margins)
_ETF_TICKERS = {
    "SPY","QQQ","IWM","SMH","XLK","XLE","XLF","XLI","XLP","XLU","XBI","ARKK",
    "GLD","TLT","GDX","SLV","DIA","IVV","VOO","EEM","HYG","TAN","ICLN","SOXX",
    "VTI","MDY","IJR","VIG","VYM","SCHD","SPDW","IEFA","AGG","BND","LQD",
}

# Fallback names for common ETFs when yfinance .info is rate-limited
_ETF_NAME_MAP = {
    "SPY":  "SPDR S&P 500 ETF",
    "QQQ":  "Invesco QQQ — Nasdaq 100",
    "IWM":  "iShares Russell 2000 ETF",
    "SMH":  "VanEck Semiconductor ETF",
    "SOXX": "iShares Semiconductor ETF",
    "XLK":  "Technology Select Sector SPDR",
    "XLE":  "Energy Select Sector SPDR",
    "XLF":  "Financial Select Sector SPDR",
    "XLI":  "Industrial Select Sector SPDR",
    "XLP":  "Consumer Staples Select SPDR",
    "XLU":  "Utilities Select Sector SPDR",
    "XBI":  "SPDR S&P Biotech ETF",
    "ARKK": "ARK Innovation ETF",
    "GLD":  "SPDR Gold Shares",
    "TLT":  "iShares 20+ Year Treasury Bond ETF",
    "GDX":  "VanEck Gold Miners ETF",
    "SLV":  "iShares Silver Trust",
    "DIA":  "SPDR Dow Jones Industrial ETF",
    "IVV":  "iShares Core S&P 500 ETF",
    "VOO":  "Vanguard S&P 500 ETF",
    "EEM":  "iShares MSCI Emerging Markets ETF",
    "HYG":  "iShares iBoxx High Yield Corporate Bond ETF",
    "TAN":  "Invesco Solar ETF",
    "ICLN": "iShares Global Clean Energy ETF",
    "VTI":  "Vanguard Total Stock Market ETF",
    "SCHD": "Schwab U.S. Dividend Equity ETF",
    "AGG":  "iShares Core U.S. Aggregate Bond ETF",
    "BND":  "Vanguard Total Bond Market ETF",
    "SPDW": "SPDR Portfolio Developed World ex-US ETF",
}

# ── Curated opportunity universe ──────────────────────────────────────────────

OPPORTUNITY_UNIVERSE = {
    # AI / Cloud Infrastructure
    "NVDA": {"theme": "AI Infrastructure", "mode": "both"},
    "AMD":  {"theme": "AI Infrastructure", "mode": "both"},
    "MSFT": {"theme": "AI / Cloud",        "mode": "invest"},
    "META": {"theme": "AI / Social",       "mode": "both"},
    "GOOGL":{"theme": "AI / Cloud",        "mode": "invest"},
    "PLTR": {"theme": "AI / Defense",      "mode": "both"},
    "AI":   {"theme": "AI Software",       "mode": "trade"},
    # Cybersecurity
    "CRWD": {"theme": "Cybersecurity",     "mode": "both"},
    "PANW": {"theme": "Cybersecurity",     "mode": "invest"},
    "DDOG": {"theme": "Cloud / Observ",   "mode": "both"},
    "NET":  {"theme": "Cloud Security",   "mode": "both"},
    "OKTA": {"theme": "Identity Security","mode": "invest"},
    "ZS":   {"theme": "Cybersecurity",    "mode": "invest"},
    # Semiconductors
    "AVGO": {"theme": "Semiconductors",   "mode": "invest"},
    "MRVL": {"theme": "AI Semis",         "mode": "both"},
    "MU":   {"theme": "Memory / HBM",     "mode": "both"},
    "TSM":  {"theme": "Foundry",          "mode": "invest"},
    "QCOM": {"theme": "Mobile / IoT",     "mode": "invest"},
    "AMAT": {"theme": "Semi Equipment",   "mode": "invest"},
    "KLAC": {"theme": "Semi Equipment",   "mode": "invest"},
    # Enterprise Software / Cloud
    "CRM":  {"theme": "Enterprise AI",    "mode": "invest"},
    "NOW":  {"theme": "Enterprise AI",    "mode": "invest"},
    "SNOW": {"theme": "Data Cloud",       "mode": "both"},
    "GTLB": {"theme": "DevOps",           "mode": "trade"},
    # Fintech / Crypto-adjacent
    "COIN": {"theme": "Crypto Exchange",  "mode": "trade"},
    "V":    {"theme": "Payments",         "mode": "invest"},
    "MA":   {"theme": "Payments",         "mode": "invest"},
    "SQ":   {"theme": "Fintech",          "mode": "both"},
    "HOOD": {"theme": "Retail Finance",   "mode": "trade"},
    # Energy
    "XOM":  {"theme": "Big Oil",          "mode": "invest"},
    "CVX":  {"theme": "Big Oil",          "mode": "invest"},
    "OXY":  {"theme": "Oil / Buffett",    "mode": "invest"},
    "SLB":  {"theme": "Oilfield Svcs",   "mode": "invest"},
    # Defense
    "LMT":  {"theme": "Defense",          "mode": "invest"},
    "RTX":  {"theme": "Defense / Aero",  "mode": "invest"},
    "NOC":  {"theme": "Defense",          "mode": "invest"},
    "LDOS": {"theme": "Defense IT",       "mode": "invest"},
    # Healthcare / Biotech
    "LLY":  {"theme": "GLP-1 / Pharma",  "mode": "invest"},
    "ABBV": {"theme": "Pharma",           "mode": "invest"},
    "MRNA": {"theme": "Biotech",          "mode": "trade"},
    # Consumer / Growth
    "AMZN": {"theme": "E-Commerce / AWS","mode": "invest"},
    "TSLA": {"theme": "EV / Energy",     "mode": "trade"},
    "NFLX": {"theme": "Streaming",       "mode": "invest"},
    "SHOP": {"theme": "E-Commerce",      "mode": "both"},
    # Sector ETFs (for flow analysis)
    "SMH":  {"theme": "Semis ETF",        "mode": "both"},
    "XLE":  {"theme": "Energy ETF",       "mode": "invest"},
    "IWM":  {"theme": "Small Cap ETF",    "mode": "trade"},
    "DELL": {"theme": "AI Infrastructure","mode": "both"},
    "HPE":  {"theme": "AI Servers",       "mode": "trade"},
    "SOUN": {"theme": "Voice AI",         "mode": "trade"},
}


# ── Fundamental data fetch ─────────────────────────────────────────────────────

def _fetch_fundamentals_bg(ticker: str) -> None:
    """Background thread: fetch yfinance fundamentals and cache."""
    try:
        try:
            import yfinance as yf
        except ImportError:
            return

        # Throttle concurrent requests — 50 simultaneous .info calls hit Yahoo Finance rate limits
        with _yf_semaphore:
            t_obj = yf.Ticker(ticker)

            # .info: full fundamentals — may fail under rate limiting
            info = {}
            try:
                info = t_obj.info or {}
            except Exception as exc:
                logger.debug("_fetch_fundamentals_bg %s .info: %s", ticker, exc)

            # fast_info: lightweight fallback for price/range/mktcap when .info fails
            fi_price = fi_high = fi_low = fi_mcap = None
            try:
                fast     = t_obj.fast_info
                fi_price = getattr(fast, "lastPrice",   None)
                fi_high  = getattr(fast, "yearHigh",    None)
                fi_low   = getattr(fast, "yearLow",     None)
                fi_mcap  = getattr(fast, "marketCap",   None)
            except Exception:
                pass

        # Resolve price — regularMarketPrice is None when market is closed
        current_price = (
            info.get("regularMarketPrice") or
            info.get("currentPrice") or
            fi_price or
            info.get("previousClose")
        )
        company = info.get("longName") or info.get("shortName")
        # Fallback: use hardcoded names for known ETFs so they always display correctly
        if not company and ticker in _ETF_NAME_MAP:
            company = _ETF_NAME_MAP[ticker]

        # Always proceed — OHLCV chart API runs after this and fills price/closes.
        # Only abort if yfinance is completely unavailable (ImportError handled above).

        data = {
            "ticker":            ticker,
            "quote_type":        info.get("quoteType", "EQUITY"),
            "company_name":      company or ticker,
            "sector":            info.get("sector", ""),
            "industry":          info.get("industry", ""),
            "aum":               info.get("totalAssets"),
            "market_cap":        info.get("marketCap") or fi_mcap,
            "pe_trailing":       info.get("trailingPE"),
            "pe_forward":        info.get("forwardPE"),
            "peg_ratio":         info.get("pegRatio"),
            "revenue_growth":    info.get("revenueGrowth"),
            "earnings_growth":   info.get("earningsGrowth"),
            "gross_margins":     info.get("grossMargins"),
            "operating_margins": info.get("operatingMargins"),
            "profit_margins":    info.get("profitMargins"),
            "free_cashflow":     info.get("freeCashflow"),
            "debt_to_equity":    info.get("debtToEquity"),
            "return_on_equity":  info.get("returnOnEquity"),
            "price_to_book":     info.get("priceToBook"),
            "price_to_sales":    info.get("priceToSalesTrailing12Months"),
            "eps_forward":       info.get("forwardEps"),
            "eps_trailing":      info.get("trailingEps"),
            "52w_high":          info.get("fiftyTwoWeekHigh") or fi_high,
            "52w_low":           info.get("fiftyTwoWeekLow")  or fi_low,
            "current_price":     current_price,
            "analyst_target":    info.get("targetMeanPrice"),
            "analyst_rec":       info.get("recommendationKey", ""),
            "num_analysts":      info.get("numberOfAnalystOpinions", 0),
            "short_float":       info.get("shortPercentOfFloat"),
            "institutional_pct": info.get("heldPercentInstitutions"),
            "insider_pct":       info.get("heldPercentInsiders"),
            "dividend_yield":    info.get("yield") or info.get("dividendYield"),
            "expense_ratio":     info.get("annualReportExpenseRatio") or info.get("totalExpenseRatio"),
            "business_summary":  (info.get("longBusinessSummary") or "")[:500],
            "fetched_at":        datetime.now(),
        }

        # Cache .info/fast_info data immediately — sync callers return without waiting for OHLCV
        with _fund_lock:
            _fund_cache[ticker] = data

        # Fetch OHLCV via chart API — bypasses yfinance crumb-token which fails on cloud IPs
        try:
            from data_fetcher import _fetch_ohlcv_via_chart_api
            hist = _fetch_ohlcv_via_chart_api(ticker, interval="1d", range_str="1y")
            if hist and hist.get("closes") and len(hist["closes"]) >= 5:
                with _fund_lock:
                    if ticker in _fund_cache:
                        _fund_cache[ticker]["closes"]  = hist["closes"]
                        _fund_cache[ticker]["volumes"] = hist["volumes"]
                        _fund_cache[ticker]["highs"]   = hist["highs"]
                        _fund_cache[ticker]["lows"]    = hist["lows"]
                        # Fill price and 52W range from OHLCV when .info/fast_info failed
                        if not _fund_cache[ticker].get("current_price"):
                            _fund_cache[ticker]["current_price"] = round(hist["closes"][-1], 2)
                        if not _fund_cache[ticker].get("52w_high"):
                            _fund_cache[ticker]["52w_high"] = round(max(hist["highs"]), 2)
                        if not _fund_cache[ticker].get("52w_low"):
                            _fund_cache[ticker]["52w_low"] = round(min(hist["lows"]), 2)
        except Exception as exc:
            logger.debug("_fetch_fundamentals_bg ohlcv %s: %s", ticker, exc)

    except Exception as exc:
        logger.debug("_fetch_fundamentals_bg %s: %s", ticker, exc)
    finally:
        with _fetch_set_lock:
            _fetch_active_set.discard(ticker)


def get_fundamentals(ticker: str) -> dict:
    """
    Return cached fundamentals for ticker. Triggers background fetch if stale/absent.
    Never blocks the caller.
    """
    t = ticker.upper().strip()
    now = datetime.now()
    with _fund_lock:
        cached = _fund_cache.get(t)
        if cached:
            age_h = (now - cached["fetched_at"]).total_seconds() / 3600
            if age_h < _FUND_TTL_H:
                return cached

    with _fetch_set_lock:
        if t not in _fetch_active_set:
            _fetch_active_set.add(t)
            threading.Thread(
                target=_fetch_fundamentals_bg, args=(t,),
                daemon=True, name=f"fund-{t}"
            ).start()

    return cached or {}


def get_fundamentals_sync(ticker: str, timeout: float = 15.0) -> dict:
    """
    Return fundamentals, waiting up to `timeout` seconds if not cached.
    Polls until price OR ohlcv closes are populated — the OHLCV chart-API
    fetch is the most reliable path on cloud hosts (no crumb-token needed).
    Only use from single-ticker report endpoint — not in bulk scan path.
    """
    import time as _time
    t = ticker.upper().strip()
    now = datetime.now()
    with _fund_lock:
        cached = _fund_cache.get(t)
        if cached:
            age_h = (now - cached["fetched_at"]).total_seconds() / 3600
            if age_h < _FUND_TTL_H and (cached.get("current_price") or cached.get("closes")):
                return cached

    # Trigger fetch if not already running
    with _fetch_set_lock:
        if t not in _fetch_active_set:
            _fetch_active_set.add(t)
            threading.Thread(
                target=_fetch_fundamentals_bg, args=(t,),
                daemon=True, name=f"fund-sync-{t}"
            ).start()

    # Poll until we have price or OHLCV closes — OHLCV chart-API fills both
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        with _fund_lock:
            cached = _fund_cache.get(t)
            if cached and (cached.get("current_price") or cached.get("closes")):
                return cached
        _time.sleep(0.25)

    with _fund_lock:
        return _fund_cache.get(t) or {}


# ── Technical data (reuse market_engine chart API) ─────────────────────────────

def _get_price_data(ticker: str) -> dict:
    """
    Get recent OHLCV. Tries institutional_engine first, falls back to fund cache OHLCV.
    """
    try:
        from institutional_engine import _get_ohlcv
        data = _get_ohlcv(ticker)
        if data and data.get("closes"):
            return data
    except Exception:
        pass
    # Fallback: OHLCV stored alongside fundamentals in _fetch_fundamentals_bg
    t = ticker.upper().strip()
    with _fund_lock:
        cached = _fund_cache.get(t)
        if cached and cached.get("closes"):
            return {
                "closes":  cached["closes"],
                "volumes": cached.get("volumes", []),
                "highs":   cached.get("highs",   []),
                "lows":    cached.get("lows",    []),
            }
    return {}


def _safe(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def _ema_val(prices: list[float], period: int) -> float | None:
    if not prices or len(prices) < period:
        return None
    mult = 2 / (period + 1)
    val  = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * mult + val * (1 - mult)
    return val


# ── Confluence scorer ─────────────────────────────────────────────────────────

def compute_confluence_score(
    ticker:   str,
    fund:     dict,
    ohlcv:    dict,
    mkt_ctx:  dict,
    liq_ctx:  dict,
) -> dict:
    """
    10-factor confluence score (0-100).

    Factors:
     1. Liquidity backdrop       0-10
     2. Sector strength          0-10
     3. Relative strength (RS)   0-10
     4. Revenue growth           0-10
     5. Earnings growth          0-10
     6. Profit margins           0-10
     7. Valuation                0-10
     8. Volume accumulation      0-10
     9. Technical setup          0-10
    10. Catalyst / theme         0-10

    Returns dict with score, breakdown, grade, action, reason.
    """
    breakdown  = {}
    total      = 0
    reasons    = []

    # ETF detection: skip fundamental factors (no revenue / EPS / margins / P/E)
    is_etf = (
        fund.get("quote_type") == "ETF" or
        ticker.upper() in _ETF_TICKERS
    )

    # ── 1. Liquidity backdrop ─────────────────────────────────────────────────
    liq_score = _safe(liq_ctx.get("score"), 50)
    if liq_score >= 65:
        pts = 10; reasons.append("Risk-on liquidity backdrop")
    elif liq_score >= 45:
        pts = 6
    else:
        pts = 2; reasons.append("Restrictive liquidity — headwind")
    breakdown["Liquidity Backdrop"] = pts
    total += pts

    # ── 2. Sector strength ────────────────────────────────────────────────────
    try:
        mflow = liq_ctx.get("money_flow", [])
        if is_etf:
            # For sector ETFs, their own money flow IS their sector strength
            own_flow = next((f for f in mflow if f["ticker"] == ticker.upper()), {})
            if own_flow:
                fs = _safe(own_flow.get("flow_score"), 50)
                pts = int(fs / 10)
                if fs >= 70:
                    reasons.append(f"{ticker} strong sector inflow")
            else:
                pts = 5
        else:
            from market_engine import get_sector_for_ticker
            sector_etf, _ = get_sector_for_ticker(ticker)
            leading = mkt_ctx.get("leading_sectors", [])
            if sector_etf and sector_etf in leading:
                pts = 10; reasons.append(f"Sector ETF {sector_etf} leading")
            elif sector_etf:
                etf_flow = next((f for f in mflow if f["ticker"] == sector_etf), {})
                fs = _safe(etf_flow.get("flow_score"), 50)
                pts = int(fs / 10) if etf_flow else 5
            else:
                pts = 4
    except Exception:
        pts = 5
    breakdown["Sector Strength"] = pts
    total += pts

    # ── 3. Relative Strength ──────────────────────────────────────────────────
    try:
        from market_engine import compute_rs_score_20d
        rs_score, rs_vs = compute_rs_score_20d(ticker)
    except Exception:
        rs_score, rs_vs = 50, 0.0

    if rs_score >= 85:    pts = 10; reasons.append(f"RS leader (+{rs_vs:.1f}% vs QQQ)")
    elif rs_score >= 70:  pts = 8
    elif rs_score >= 55:  pts = 5
    elif rs_score >= 40:  pts = 3
    else:                 pts = 0; reasons.append("RS laggard — underperforming market")
    breakdown["Relative Strength"] = pts
    total += pts

    # ── 4. Revenue growth ─────────────────────────────────────────────────────
    raw_rev = fund.get("revenue_growth")
    if is_etf or raw_rev is None:
        pts = 5  # N/A for ETFs or missing data — neutral
    else:
        rev_growth = _safe(raw_rev)
        if rev_growth >= 0.30:    pts = 10; reasons.append(f"Revenue +{rev_growth*100:.0f}% YoY")
        elif rev_growth >= 0.15:  pts = 8
        elif rev_growth >= 0.08:  pts = 6
        elif rev_growth >= 0:     pts = 4
        else:                     pts = 1; reasons.append("Revenue declining")
    breakdown["Revenue Growth"] = pts
    total += pts

    # ── 5. Earnings growth ────────────────────────────────────────────────────
    raw_earn = fund.get("earnings_growth")
    if is_etf or raw_earn is None:
        pts = 5
    else:
        earn_growth = _safe(raw_earn)
        if earn_growth >= 0.30:   pts = 10; reasons.append(f"EPS +{earn_growth*100:.0f}% YoY")
        elif earn_growth >= 0.15: pts = 8
        elif earn_growth >= 0.05: pts = 6
        elif earn_growth >= 0:    pts = 4
        else:                     pts = 1
    breakdown["Earnings Growth"] = pts
    total += pts

    # ── 6. Profit margins ─────────────────────────────────────────────────────
    raw_mg = fund.get("operating_margins")
    if is_etf or raw_mg is None:
        pts = 5
    else:
        margins = _safe(raw_mg)
        if margins >= 0.30:   pts = 10; reasons.append(f"{margins*100:.0f}% operating margins")
        elif margins >= 0.20: pts = 8
        elif margins >= 0.10: pts = 6
        elif margins >= 0:    pts = 4
        else:                 pts = 2
    breakdown["Profit Margins"] = pts
    total += pts

    # ── 7. Valuation ──────────────────────────────────────────────────────────
    peg  = fund.get("peg_ratio")
    pe_f = fund.get("pe_forward")
    if is_etf:
        pts = 5  # ETFs don't have meaningful P/E — neutral
    elif peg is not None:
        p = _safe(peg)
        if 0 < p <= 1.0:    pts = 10; reasons.append(f"PEG {p:.1f} — attractively valued")
        elif p <= 2.0:      pts = 7
        elif p <= 3.0:      pts = 4
        else:               pts = 2; reasons.append(f"PEG {p:.1f} — extended valuation")
    elif pe_f is not None:
        p = _safe(pe_f)
        if 0 < p <= 20:     pts = 8
        elif p <= 35:       pts = 6
        elif p <= 60:       pts = 4
        else:               pts = 2
    else:
        pts = 5
    breakdown["Valuation"] = pts
    total += pts

    # ── 8. Volume accumulation ────────────────────────────────────────────────
    closes  = ohlcv.get("closes",  [])
    volumes = ohlcv.get("volumes", [])
    if closes and volumes and len(closes) >= 20:
        avg_vol_20 = sum(volumes[-20:]) / 20
        avg_vol_5  = sum(volumes[-5:])  / 5
        # Up days vs down days on above-avg volume (last 20 days)
        acc_days = 0
        dist_days = 0
        for i in range(max(0, len(closes)-20), len(closes)):
            if volumes[i] > avg_vol_20 * 1.1:
                if closes[i] > closes[i-1]:
                    acc_days += 1
                elif closes[i] < closes[i-1]:
                    dist_days += 1
        rvol_ratio = avg_vol_5 / max(avg_vol_20, 1)
        if acc_days >= 5 and acc_days > dist_days * 1.5:
            pts = 10; reasons.append(f"Institutional accumulation ({acc_days} acc days)")
        elif acc_days > dist_days and rvol_ratio > 1.2:
            pts = 7
        elif acc_days > dist_days:
            pts = 5
        elif dist_days > acc_days * 1.5:
            pts = 1; reasons.append("Distribution pattern — institutional selling")
        else:
            pts = 4
    else:
        pts = 5
    breakdown["Volume Accumulation"] = pts
    total += pts

    # ── 9. Technical setup ────────────────────────────────────────────────────
    tech_pts = 0
    cp = _safe(fund.get("current_price"))
    if not cp and closes:
        cp = closes[-1]  # fallback: last OHLCV close when .info price is missing
    if closes and cp:
        ema20 = _ema_val(closes, min(20, len(closes)))
        ema50 = _ema_val(closes, min(50, len(closes)))
        high52 = _safe(fund.get("52w_high"))
        low52  = _safe(fund.get("52w_low"))

        # Above key EMAs
        if ema20 and cp > ema20:
            tech_pts += 3
        if ema50 and cp > ema50:
            tech_pts += 2

        # 52-week high proximity
        if high52 and high52 > 0:
            pct_from_high = (high52 - cp) / high52 * 100
            if pct_from_high <= 5:
                tech_pts += 5; reasons.append("Near 52-week high — breakout proximity")
            elif pct_from_high <= 15:
                tech_pts += 3
            elif pct_from_high <= 30:
                tech_pts += 1

        pts = min(10, tech_pts)
        if pts >= 8:
            reasons.append("Strong technical setup — above EMAs, near 52w high")
    else:
        pts = 5
    breakdown["Technical Setup"] = pts
    total += pts

    # ── 10. Catalyst / theme ──────────────────────────────────────────────────
    theme = OPPORTUNITY_UNIVERSE.get(ticker.upper(), {}).get("theme", "")
    rec   = (fund.get("analyst_rec") or "").lower()
    n_analysts = _safe(fund.get("num_analysts"), 0)
    analyst_target = fund.get("analyst_target")
    analyst_upside = 0
    if analyst_target and cp:
        analyst_upside = (analyst_target - cp) / max(cp, 1) * 100

    cat_pts = 0
    # High-demand themes
    if any(t in (theme or "").lower() for t in ["ai", "cyber", "defense", "hbm", "semi"]):
        cat_pts += 4; reasons.append(f"High-demand theme: {theme}")
    # Strong analyst consensus
    if rec in ("buy", "strong_buy") and n_analysts >= 5:
        cat_pts += 3; reasons.append(f"Analyst consensus BUY ({int(n_analysts)} analysts)")
    elif rec == "hold":
        cat_pts += 1
    # Analyst upside
    if analyst_upside > 20:
        cat_pts += 3; reasons.append(f"Analyst target +{analyst_upside:.0f}% upside")
    elif analyst_upside > 10:
        cat_pts += 2

    pts = min(10, cat_pts)
    breakdown["Catalyst / Theme"] = pts
    total += pts

    total = max(0, min(100, total))

    # ── Grade and action ──────────────────────────────────────────────────────
    if total >= 85:
        grade  = "A+"
        label  = "Hidden Gem"
    elif total >= 75:
        grade  = "A"
        label  = "Early Accumulation"
    elif total >= 65:
        grade  = "B+"
        label  = "Developing Leader"
    elif total >= 55:
        grade  = "B"
        label  = "Watchlist"
    else:
        grade  = "C"
        label  = "Avoid"

    # Action signal — never says "buy"
    cp    = _safe(fund.get("current_price"))
    ema20 = None
    if closes:
        ema20 = _ema_val(closes, min(20, len(closes)))
    high52 = _safe(fund.get("52w_high"))
    pct_from_high = (high52 - cp) / max(high52, 1) * 100 if high52 and cp else 50

    if grade == "C":
        action = "Avoid"
    elif grade in ("A+", "A") and ema20 and cp and cp < ema20 * 1.03:
        action = "Accumulate"
    elif grade in ("A+", "A") and pct_from_high <= 3:
        action = "Breakout Confirmed" if high52 and cp >= high52 * 0.98 else "Watch"
    elif grade == "B+" and ema20 and cp and cp > ema20 * 1.08:
        action = "Extended — Do Not Chase"
    elif grade in ("A+", "A") and ema20 and cp and cp > ema20 * 1.10:
        action = "Extended — Do Not Chase"
    elif grade in ("A+", "A", "B+"):
        action = "Watch"
    elif grade == "B":
        action = "Wait for Pullback"
    else:
        action = "Avoid"

    reason_str = "; ".join(reasons[:5])
    if not reason_str:
        reason_str = f"Score {total}/100 across 10 factors."

    return {
        "ticker":           ticker,
        "confluence_score": total,
        "grade":            grade,
        "grade_label":      label,
        "action":           action,
        "reason":           reason_str,
        "breakdown":        breakdown,
        "rs_score":         rs_score,
        "rs_vs_qqq":        round(rs_vs, 2),
        "theme":            theme,
        "revenue_growth":   fund.get("revenue_growth"),
        "earnings_growth":  fund.get("earnings_growth"),
        "analyst_upside":   round(analyst_upside, 1) if analyst_upside else None,
    }


# ── Investment research report ─────────────────────────────────────────────────

def build_research_report(
    ticker:  str,
    fund:    dict,
    ohlcv:   dict,
    mkt_ctx: dict,
    liq_ctx: dict,
    score_result: dict,
) -> dict:
    """
    Generate a structured investment research report for a single ticker.
    All data is evidence-based. No price predictions.
    """
    is_etf = (fund.get("quote_type") == "ETF" or ticker.upper() in _ETF_TICKERS)
    closes = ohlcv.get("closes", [])
    cp     = _safe(fund.get("current_price"))
    # Fallback: use last OHLCV close when .info didn't return a price
    if not cp and closes:
        cp = closes[-1]
    high52 = _safe(fund.get("52w_high"))
    low52  = _safe(fund.get("52w_low"))
    # Fallback: derive 52w range from OHLCV highs/lows when .info didn't return them
    if not high52 and ohlcv.get("highs"):
        high52 = max(ohlcv["highs"])
    if not low52 and ohlcv.get("lows"):
        low52 = min(ohlcv["lows"])
    # Round all prices to 2dp — numpy floats from OHLCV data carry many decimal places
    cp     = round(float(cp),     2) if cp     else None
    high52 = round(float(high52), 2) if high52 else None
    low52  = round(float(low52),  2) if low52  else None

    ema20 = _ema_val(closes, min(20, len(closes))) if closes else None
    ema50 = _ema_val(closes, min(50, len(closes))) if closes else None

    # Buy zone: EMA20 to -3% of EMA20 (ideal pullback entry)
    buy_zone_low  = round(ema20 * 0.97, 2) if ema20 else None
    buy_zone_high = round(ema20 * 1.01, 2) if ema20 else None

    # Add zone: slight pullback from current price
    add_zone_low  = round(ema50 * 0.98, 2) if ema50 else None
    add_zone_high = round(ema50 * 1.02, 2) if ema50 else None

    # Stop / invalidation: below EMA50 or 8% below entry (whichever is closer)
    stop_level    = round(ema50 * 0.96, 2) if ema50 else (round(cp * 0.92, 2) if cp else None)

    # Macro tailwind/headwind
    liq_status = liq_ctx.get("status", "NEUTRAL")
    yield_curve = liq_ctx.get("yield_curve", {})
    regime = mkt_ctx.get("regime", "NEUTRAL")
    theme  = OPPORTUNITY_UNIVERSE.get(ticker.upper(), {}).get("theme", "")

    macro_tail = []
    macro_head = []
    if liq_status == "RISK_ON":
        macro_tail.append("Expanding liquidity supports growth assets")
    elif liq_status == "RISK_OFF":
        macro_head.append("Contracting liquidity — headwind for high-multiple stocks")

    if yield_curve.get("inverted"):
        macro_head.append(f"Inverted yield curve ({yield_curve.get('spread','?')}%) — recession signal")
    elif yield_curve.get("steepening"):
        macro_tail.append("Steepening yield curve — risk appetite improving")

    if regime == "RISK_ON":
        macro_tail.append("Market in risk-on regime — favorable for longs")
    elif regime == "RISK_OFF":
        macro_head.append("Market risk-off — defensive posture required")

    if any(t in (theme or "").lower() for t in ["ai", "semi"]):
        macro_tail.append("AI infrastructure spending supercycle as multi-year tailwind")

    # Revenue / earnings narrative
    rev_g = fund.get("revenue_growth")
    earn_g= fund.get("earnings_growth")
    margins = fund.get("operating_margins")
    fcf   = fund.get("free_cashflow")
    debt_eq = fund.get("debt_to_equity")

    if is_etf:
        # Compute ETF performance metrics from OHLCV (always available via chart API)
        perf_1y = perf_6m = perf_3m = vol_ann = None
        if closes and len(closes) >= 2:
            try:
                import statistics as _stats
                if len(closes) >= 200:
                    perf_1y = (closes[-1] / closes[-200] - 1) * 100
                elif len(closes) >= 20:
                    perf_1y = (closes[-1] / closes[0] - 1) * 100
                if len(closes) >= 126:
                    perf_6m = (closes[-1] / closes[-126] - 1) * 100
                if len(closes) >= 63:
                    perf_3m = (closes[-1] / closes[-63] - 1) * 100
                if len(closes) >= 21:
                    rets = [closes[i] / closes[i-1] - 1 for i in range(-20, 0)]
                    vol_ann = _stats.stdev(rets) * (252 ** 0.5) * 100
            except Exception:
                pass

        def _pf(v):
            if v is None: return "N/A"
            return f"+{v:.1f}%" if v >= 0 else f"{v:.1f}%"

        rev_str  = _pf(perf_1y)
        earn_str = _pf(perf_6m)
        mg_str   = _pf(perf_3m)
        fcf_str  = f"${fund['aum']/1e9:.1f}B" if fund.get("aum") else "N/A"
        deq_str  = f"{vol_ann:.1f}%" if vol_ann is not None else "N/A"
    else:
        rev_str  = f"+{rev_g*100:.0f}% YoY"  if rev_g  and rev_g  > 0 else (f"{rev_g*100:.0f}% YoY" if rev_g  else "N/A")
        earn_str = f"+{earn_g*100:.0f}% YoY" if earn_g and earn_g > 0 else (f"{earn_g*100:.0f}% YoY" if earn_g else "N/A")
        mg_str   = f"{margins*100:.1f}%"      if margins else "N/A"
        fcf_str  = f"${fcf/1e9:.1f}B"         if fcf and fcf > 0 else ("Negative" if fcf else "N/A")
        deq_str  = f"{debt_eq:.1f}x"          if debt_eq else "N/A"

    # Analyst target
    target = fund.get("analyst_target")
    target_upside = score_result.get("analyst_upside")
    target_str = f"${target:.2f} ({target_upside:+.0f}%)" if target and target_upside else (f"${target:.2f}" if target else "N/A")

    # Risk level
    score = score_result.get("confluence_score", 50)
    if score >= 80:
        risk_level = "Moderate"
    elif score >= 65:
        risk_level = "Moderate-High"
    elif score >= 50:
        risk_level = "High"
    else:
        risk_level = "Very High / Avoid"

    # 6-12 month thesis
    thesis_parts = []
    if is_etf:
        name = fund.get("company_name", ticker)
        thesis_parts.append(f"{name} ({ticker}) is a sector ETF tracking the {theme} space.")
        aum = fund.get("aum")
        if aum and aum > 0:
            thesis_parts.append(f"AUM: ${aum/1e9:.1f}B.")
        rs_val = score_result.get("rs_score", 50)
        if rs_val and rs_val >= 70:
            thesis_parts.append(f"Showing relative strength (RS {rs_val}) vs benchmark.")
        if macro_tail:
            thesis_parts.append(f"Tailwind: {macro_tail[0]}.")
        if macro_head:
            thesis_parts.append(f"Risk: {macro_head[0]}.")
        thesis_parts.append(
            f"Technical status: {score_result.get('action', 'Watch')}. "
            f"Confluence score {score}/100 based on price/momentum factors."
        )
    else:
        if fund.get("company_name"):
            thesis_parts.append(f"{fund['company_name']} ({ticker})")
        if theme:
            thesis_parts.append(f"operates in the {theme} space")
        if rev_g and rev_g > 0.10:
            thesis_parts.append(f"with revenue growing {rev_g*100:.0f}% YoY")
        if earn_g and earn_g > 0.10:
            thesis_parts.append(f"and earnings accelerating {earn_g*100:.0f}% YoY")
        if macro_tail:
            thesis_parts.append(f". Macro tailwinds: {macro_tail[0]}")
        if macro_head:
            thesis_parts.append(f". Key risk: {macro_head[0]}")
        thesis_parts.append(
            f". Technical status: {score_result.get('action', 'Watch')}. "
            f"Confluence score {score}/100 ({score_result.get('grade_label','')})."
        )
    thesis = " ".join(thesis_parts) if thesis_parts else "Insufficient data for thesis generation."

    return {
        "ticker":           ticker,
        "company_name":     fund.get("company_name", ticker),
        "sector":           fund.get("sector", ""),
        "industry":         fund.get("industry", ""),
        "theme":            theme,
        "business_summary": fund.get("business_summary", ""),
        # Financials
        "revenue_growth":   rev_str,
        "earnings_growth":  earn_str,
        "operating_margins":mg_str,
        "free_cashflow":    fcf_str,
        "debt_to_equity":   deq_str,
        "is_etf":           is_etf,
        "aum":              f"${fund.get('aum',0)/1e9:.1f}B" if fund.get("aum") else None,
        "market_cap":       (f"${fund.get('aum',0)/1e9:.1f}B AUM" if fund.get("aum") else "ETF — N/A") if is_etf else (f"${fund.get('market_cap',0)/1e9:.1f}B" if fund.get("market_cap") else "N/A"),
        "pe_forward":       f"{fund.get('pe_forward','N/A')}x" if fund.get("pe_forward") else "N/A",
        "peg_ratio":        fund.get("peg_ratio"),
        # Macro
        "macro_tailwinds":  macro_tail,
        "macro_headwinds":  macro_head,
        "liq_status":       liq_ctx.get("status_label", ""),
        # Technical
        "current_price":    cp,
        "ema20":            round(ema20, 2) if ema20 else None,
        "ema50":            round(ema50, 2) if ema50 else None,
        "high_52w":         high52,
        "low_52w":          low52,
        "buy_zone":         f"${buy_zone_low} – ${buy_zone_high}" if buy_zone_low else "N/A",
        "add_zone":         f"${add_zone_low} – ${add_zone_high}" if add_zone_low else "N/A",
        "stop_level":       f"${stop_level}" if stop_level else "N/A",
        # Analyst
        "analyst_target":   target_str,
        "analyst_rec":      (fund.get("analyst_rec") or "").replace("_", " ").title(),
        "num_analysts":     fund.get("num_analysts", 0),
        # Scores
        "confluence_score": score,
        "grade":            score_result.get("grade"),
        "grade_label":      score_result.get("grade_label"),
        "action":           score_result.get("action"),
        "risk_level":       risk_level,
        "breakdown":        score_result.get("breakdown", {}),
        "reason":           score_result.get("reason", ""),
        # Thesis
        "thesis":           thesis,
    }


# ── Single ticker scan ─────────────────────────────────────────────────────────

def scan_ticker(ticker: str, mkt_ctx: dict, liq_ctx: dict) -> dict:
    """
    Run the full opportunity scan for a single ticker.
    Returns a complete result dict including score, grade, action, report.
    Non-blocking: returns stale/partial data if fundamentals are still loading.
    """
    t    = ticker.upper().strip()
    fund = get_fundamentals(t)
    ohlcv= _get_price_data(t)

    score_result = compute_confluence_score(t, fund, ohlcv, mkt_ctx, liq_ctx)
    report       = build_research_report(t, fund, ohlcv, mkt_ctx, liq_ctx, score_result)

    return {
        "ticker":            t,
        "company_name":      fund.get("company_name", t),
        "theme":             OPPORTUNITY_UNIVERSE.get(t, {}).get("theme", ""),
        "mode":              OPPORTUNITY_UNIVERSE.get(t, {}).get("mode", "both"),
        "confluence_score":  score_result["confluence_score"],
        "grade":             score_result["grade"],
        "grade_label":       score_result["grade_label"],
        "action":            score_result["action"],
        "reason":            score_result["reason"],
        "breakdown":         score_result["breakdown"],
        "rs_score":          score_result.get("rs_score"),
        "rs_vs_qqq":         score_result.get("rs_vs_qqq"),
        "revenue_growth":    fund.get("revenue_growth"),
        "earnings_growth":   fund.get("earnings_growth"),
        "current_price":     fund.get("current_price"),
        "52w_high":          fund.get("52w_high"),
        "analyst_rec":       fund.get("analyst_rec", ""),
        "analyst_upside":    score_result.get("analyst_upside"),
        "has_fundamentals":  bool(fund.get("company_name")),
        "report":            report,
    }


# ── Opportunity scanner ────────────────────────────────────────────────────────

def run_opportunity_scan(
    extra_tickers: list[str] | None = None,
    mkt_ctx:  dict | None = None,
    liq_ctx:  dict | None = None,
    mode:     str  = "both",   # "trade" | "invest" | "both"
) -> dict:
    """
    Scan the opportunity universe + any extra tickers.
    Returns ranked results with smart watchlists.
    """
    if mkt_ctx is None:
        try:
            from market_engine import get_market_context
            mkt_ctx = get_market_context()
        except Exception:
            mkt_ctx = {}

    if liq_ctx is None:
        from liquidity_engine import get_liquidity_status
        liq_ctx = get_liquidity_status()

    # Build scan universe
    universe = list(OPPORTUNITY_UNIVERSE.keys())
    if extra_tickers:
        for t in extra_tickers:
            t = t.upper().strip()
            if t and t not in universe:
                universe.append(t)

    results = []
    for t in universe:
        ticker_mode = OPPORTUNITY_UNIVERSE.get(t, {}).get("mode", "both")
        if mode != "both" and ticker_mode != "both" and ticker_mode != mode:
            continue
        try:
            r = scan_ticker(t, mkt_ctx, liq_ctx)
            results.append(r)
        except Exception as exc:
            logger.debug("scan_ticker %s: %s", t, exc)

    results.sort(key=lambda r: r["confluence_score"], reverse=True)

    # Categorize into watchlists
    hidden_gems     = [r for r in results if r["grade"] == "A+" and r["has_fundamentals"]]
    accumulate      = [r for r in results if r["action"] == "Accumulate"]
    breakouts       = [r for r in results if r["action"] == "Breakout Confirmed"]
    developing      = [r for r in results if r["grade"] in ("A", "B+") and r["action"] == "Watch"]
    invest_longs    = [r for r in results if r["mode"] in ("invest", "both") and r["grade"] in ("A+","A","B+")]

    # Accumulation signals: high RS + above EMA + volume expansion
    early_acc = [
        r for r in results
        if r.get("rs_score", 0) >= 70 and r["grade"] in ("A+","A","B+") and
        r["action"] in ("Accumulate", "Watch", "Breakout Confirmed")
    ]

    return {
        "results":          results,
        "total_scanned":    len(results),
        "hidden_gems":      hidden_gems[:10],
        "accumulate":       accumulate[:10],
        "breakouts":        breakouts[:10],
        "developing":       developing[:10],
        "early_acc":        early_acc[:10],
        "invest_watchlist": invest_longs[:15],
        "top_10":           results[:10],
        "liq_status":       liq_ctx.get("status", "NEUTRAL"),
        "regime":           mkt_ctx.get("regime", "NEUTRAL"),
        "scanned_at":       datetime.now().isoformat(),
    }


# ── Opportunity alerts ─────────────────────────────────────────────────────────

def generate_opportunity_alerts(results: list[dict], mkt_ctx: dict, liq_ctx: dict) -> list[dict]:
    """
    Generate specific opportunity alerts from scan results.
    """
    alerts = []

    for r in results:
        t     = r.get("ticker", "")
        score = r.get("confluence_score", 0)
        action= r.get("action", "")
        rs    = r.get("rs_score", 50)
        cp    = r.get("current_price")
        high52= r.get("52w_high")

        # Breakout alert
        if action == "Breakout Confirmed" and score >= 65:
            alerts.append({
                "type": "breakout", "severity": "high",
                "ticker": t, "score": score,
                "msg": f"{t}: Breakout confirmed — score {score}/100. {r.get('reason','')}"
            })

        # Hidden gem alert
        if r.get("grade") == "A+" and action == "Accumulate":
            alerts.append({
                "type": "hidden_gem", "severity": "high",
                "ticker": t, "score": score,
                "msg": f"{t}: Hidden Gem A+ — early accumulation opportunity. Score {score}/100."
            })

        # RS leader outperforming
        if rs >= 85 and score >= 65:
            alerts.append({
                "type": "rs_leader", "severity": "medium",
                "ticker": t, "score": score,
                "msg": f"{t}: RS leader (+{r.get('rs_vs_qqq',0):.1f}% vs QQQ) with strong confluence."
            })

        # 52-week high proximity
        if cp and high52 and high52 > 0:
            pct = (high52 - cp) / high52 * 100
            if pct <= 3 and score >= 60:
                alerts.append({
                    "type": "52w_high", "severity": "medium",
                    "ticker": t, "score": score,
                    "msg": f"{t}: Within {pct:.1f}% of 52-week high — potential breakout setup."
                })

    # Macro alerts from liquidity
    liq_alerts = liq_ctx.get("alerts", [])
    for a in liq_alerts:
        alerts.append({"type": "macro", "severity": "medium", "ticker": "MACRO", "msg": a.get("msg",""), "liq_type": a.get("type","")})

    return alerts
