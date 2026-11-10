"""Pre-trade gate: GO / WAIT / NO for one planned trade, plus position size.

Capital protection comes first. Any failed rule is a NO; two or more
warnings is a WAIT; otherwise GO. Every rule says why, with the numbers.

evaluate() is pure — it takes the indicators and the plan and returns the
verdict, so it is easy to test. gather() collects the indicators for a
ticker from what the app already has (the stock_data snapshot, daily and
intraday bars, the live quote feed, the earnings calendar, the market pulse).
"""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import date, datetime

logger = logging.getLogger(__name__)

PASS, WARN, FAIL, INFO = "pass", "warn", "fail", "info"


def _f(v):
    try:
        v = float(v)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _pct(a, b):
    return (a / b - 1.0) * 100.0 if a and b else None


# ── Indicators from bars ─────────────────────────────────────────────────────

def ema(values: list[float], period: int) -> float | None:
    vals = [v for v in values if v is not None]
    if len(vals) < period:
        return None
    k = 2.0 / (period + 1)
    out = sum(vals[:period]) / period
    for v in vals[period:]:
        out = v * k + out * (1 - k)
    return out


def atr(bars: list[dict], period: int = 14) -> float | None:
    if len(bars) < period + 1:
        return None
    trs = []
    for prev, cur in zip(bars, bars[1:]):
        trs.append(max(cur["high"] - cur["low"], abs(cur["high"] - prev["close"]), abs(cur["low"] - prev["close"])))
    return sum(trs[-period:]) / period


def session_vwap(bars: list[dict]) -> float | None:
    pv = vol = 0.0
    for b in bars:
        v = float(b.get("volume") or 0)
        if v <= 0:
            continue
        pv += (b["high"] + b["low"] + b["close"]) / 3.0 * v
        vol += v
    return pv / vol if vol else None


def days_until(when, today: date | None = None) -> int | None:
    if not when:
        return None
    today = today or date.today()
    try:
        d = datetime.fromisoformat(str(when)[:10]).date()
    except ValueError:
        return None
    return (d - today).days


# ── The rules ────────────────────────────────────────────────────────────────

def evaluate(ind: dict, plan: dict) -> dict:
    """Score one planned trade. ind = indicators, plan = entry/stop/target/account."""
    direction = "short" if str(plan.get("direction", "long")).lower() == "short" else "long"
    sign = 1 if direction == "long" else -1
    price = _f(ind.get("price"))
    entry = _f(plan.get("entry")) or price
    stop = _f(plan.get("stop"))
    target = _f(plan.get("target"))
    account = _f(plan.get("account_size"))
    risk_pct = _f(plan.get("risk_pct")) or 1.0
    max_pos_pct = _f(plan.get("max_position_pct")) or 25.0
    checks: list[dict] = []

    def add(key, label, status, detail):
        checks.append({"key": key, "label": label, "status": status, "detail": detail})

    # 1. Trend (daily EMAs)
    e20, e50 = _f(ind.get("ema20")), _f(ind.get("ema50"))
    if price and e20 and e50:
        above20, above50 = sign * (price - e20) > 0, sign * (price - e50) > 0
        stacked = sign * (e20 - e50) > 0
        side = "above" if direction == "long" else "below"
        if above20 and above50 and stacked:
            add("trend", "Daily trend", PASS, f"Price {side} EMA20 ({e20:.2f}) and EMA50 ({e50:.2f}), EMAs stacked in your direction.")
        elif above50:
            add("trend", "Daily trend", WARN, f"Price is {side} EMA50 ({e50:.2f}) but not cleanly {side} EMA20 ({e20:.2f}) — trend is soft.")
        else:
            add("trend", "Daily trend", FAIL, f"Price is on the wrong side of EMA50 ({e50:.2f}) for a {direction} — trading against the trend.")
    else:
        add("trend", "Daily trend", WARN, "Not enough daily history to read EMA20/EMA50.")

    # 2. VWAP (intraday control)
    vwap = _f(ind.get("vwap"))
    if price and vwap:
        gap = sign * _pct(price, vwap)
        if gap >= 0:
            add("vwap", "VWAP", PASS, f"Price {price:.2f} is on the right side of VWAP {vwap:.2f} ({gap:+.2f}%).")
        elif gap >= -0.3:
            add("vwap", "VWAP", WARN, f"Price is just through VWAP {vwap:.2f} ({gap:+.2f}%) — wait for it to reclaim.")
        else:
            add("vwap", "VWAP", FAIL, f"Price is {abs(gap):.2f}% on the wrong side of VWAP {vwap:.2f} — sellers/buyers against you control the session.")
    else:
        add("vwap", "VWAP", INFO, "No intraday VWAP yet (market closed or no session volume).")

    # 3. Relative volume
    rvol = _f(ind.get("rel_volume"))
    if rvol is not None:
        if rvol >= 1.5:
            add("rvol", "Relative volume", PASS, f"{rvol:.1f}× normal volume — real participation.")
        elif rvol >= 0.8:
            add("rvol", "Relative volume", WARN, f"{rvol:.1f}× normal volume — moves on light volume fail more often.")
        else:
            add("rvol", "Relative volume", FAIL, f"Only {rvol:.1f}× normal volume — no interest behind the move.")
    else:
        add("rvol", "Relative volume", INFO, "Relative volume unavailable.")

    # 4. Earnings risk
    days = ind.get("earnings_days")
    if ind.get("asset_type") == "ETF":
        add("earnings", "Earnings", PASS, "ETF — no single earnings event.")
    elif days is None:
        add("earnings", "Earnings", INFO, "No confirmed earnings date.")
    elif 0 <= days <= 3:
        add("earnings", "Earnings", FAIL, f"Earnings in {days} day{'s' if days != 1 else ''} ({ind.get('earnings_date')}) — a binary gap can blow through any stop.")
    elif 0 <= days <= 7:
        add("earnings", "Earnings", WARN, f"Earnings in {days} days ({ind.get('earnings_date')}) — size down or plan to be out before.")
    else:
        add("earnings", "Earnings", PASS, f"Next earnings {ind.get('earnings_date')} ({days} days out).")

    # 5. Market backdrop
    vix, spy_chg = _f(ind.get("vix")), _f(ind.get("spy_change_pct"))
    if vix is not None or spy_chg is not None:
        notes, status = [], PASS
        if vix is not None:
            notes.append(f"VIX {vix:.1f}")
            if vix >= 30:
                status = FAIL
            elif vix >= 22:
                status = WARN
        if spy_chg is not None:
            notes.append(f"SPY {spy_chg:+.2f}%")
            if sign * spy_chg <= -1.5:
                status = FAIL if status == FAIL else WARN
        text = ", ".join(notes)
        add("market", "Market backdrop", status,
            {PASS: f"{text} — backdrop is not fighting you.",
             WARN: f"{text} — choppy or against you; tighten size.",
             FAIL: f"{text} — high-fear tape; protect capital first."}[status])

    # 6. Plan: stop placement and reward/risk
    shares = risk_dollars = position_value = rr = None
    if entry and stop:
        per_share = sign * (entry - stop)
        if per_share <= 0:
            add("stop", "Stop placement", FAIL, f"Stop {stop:.2f} is on the wrong side of entry {entry:.2f} for a {direction}.")
        else:
            stop_pct = per_share / entry * 100
            a = _f(ind.get("atr"))
            atr_note = f" ({per_share / a:.1f}× ATR)" if a else ""
            if a and per_share < 0.5 * a:
                add("stop", "Stop placement", WARN, f"Stop is {stop_pct:.2f}% away{atr_note} — inside normal noise, likely to get shaken out.")
            elif stop_pct > 8:
                add("stop", "Stop placement", WARN, f"Stop is {stop_pct:.1f}% away{atr_note} — wide; size will be small.")
            else:
                add("stop", "Stop placement", PASS, f"Stop {stop:.2f} is {stop_pct:.2f}% from entry{atr_note}.")
            if target:
                reward = sign * (target - entry)
                rr = reward / per_share if reward > 0 else 0.0
                if rr >= 2:
                    add("rr", "Reward / risk", PASS, f"{rr:.1f} : 1 to target {target:.2f}.")
                elif rr >= 1.5:
                    add("rr", "Reward / risk", WARN, f"{rr:.1f} : 1 — below your 2 : 1 standard.")
                else:
                    add("rr", "Reward / risk", FAIL, f"{rr:.1f} : 1 — the trade does not pay for its risk.")
            else:
                add("rr", "Reward / risk", INFO, "Add a target to grade reward against risk.")
            # 7. Size
            if account:
                risk_dollars = account * risk_pct / 100.0
                shares = int(risk_dollars // per_share)
                position_value = shares * entry
                if shares < 1:
                    add("size", "Position size", FAIL, f"{risk_pct:g}% of ${account:,.0f} = ${risk_dollars:,.0f} risk can't cover one share at ${per_share:.2f} risk/share.")
                else:
                    cap = account * max_pos_pct / 100.0
                    if position_value > account:
                        add("size", "Position size", FAIL, f"{shares} shares = ${position_value:,.0f}, more than the whole account.")
                    elif position_value > cap:
                        capped = int(cap // entry)
                        add("size", "Position size", WARN, f"{shares} shares = ${position_value:,.0f} ({position_value / account * 100:.0f}% of account). Cap at {capped} shares to stay under {max_pos_pct:g}%.")
                    else:
                        add("size", "Position size", PASS, f"{shares} shares = ${position_value:,.0f} ({position_value / account * 100:.0f}% of account), risking ${shares * per_share:,.0f}.")
    else:
        add("stop", "Stop placement", FAIL, "No stop set. Every trade needs a stop before entry.")

    fails = sum(1 for c in checks if c["status"] == FAIL)
    warns = sum(1 for c in checks if c["status"] == WARN)
    passes = sum(1 for c in checks if c["status"] == PASS)
    graded = passes + warns + fails
    score = round(100 * (passes + 0.5 * warns) / graded) if graded else 0
    if fails:
        verdict, reason = "NO", next(c["detail"] for c in checks if c["status"] == FAIL)
    elif warns >= 2:
        verdict, reason = "WAIT", "Two or more rules are only partly met — wait for a cleaner setup or size down."
    else:
        verdict, reason = "GO", "Every rule that could be checked is met. Execute the plan and honor the stop."
    return {
        "verdict": verdict, "reason": reason, "score": score, "direction": direction,
        "checks": checks, "counts": {"pass": passes, "warn": warns, "fail": fails},
        "plan": {"entry": entry, "stop": stop, "target": target, "account_size": account,
                 "risk_pct": risk_pct, "shares": shares, "risk_dollars": round(risk_dollars, 2) if risk_dollars else None,
                 "position_value": round(position_value, 2) if position_value else None,
                 "reward_risk": round(rr, 2) if rr is not None else None},
    }


# ── Indicator gathering ─────────────────────────────────────────────────────

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 45


def _bars(ticker: str, interval: str, range_str: str) -> list[dict]:
    try:
        from market_data import fetch_chart_bars
        from terminal_intelligence import normalize_ohlcv_data
        data, _ = fetch_chart_bars(ticker, interval=interval, range_str=range_str)
        return normalize_ohlcv_data(data)
    except Exception as exc:
        logger.debug("gate bars %s %s: %s", ticker, interval, exc)
        return []


def gather(ticker: str) -> dict:
    """Indicators for one ticker from the app's own data. Cached briefly."""
    ticker = ticker.upper().strip()
    with _cache_lock:
        hit = _cache.get(ticker)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return dict(hit[1])
    snap = {}
    try:
        from database import get_stock_data
        snap = get_stock_data(ticker) or {}
    except Exception:
        snap = {}
    daily = _bars(ticker, "1d", "1y")
    intraday = _bars(ticker, "5m", "1d")
    closes = [b["close"] for b in daily]
    ind = {
        "ticker": ticker,
        "price": _f(snap.get("current_price")) or (closes[-1] if closes else None),
        "ema20": _f(snap.get("ema_20_daily")) or ema(closes, 20),
        "ema50": _f(snap.get("ema_50_daily")) or ema(closes, 50),
        "atr": atr(daily[-60:]) if daily else None,
        "vwap": _f(snap.get("vwap")) or session_vwap(intraday),
        "rel_volume": _f(snap.get("rel_volume")),
        "earnings_date": snap.get("earnings_date") or None,
        "asset_type": "EQUITY",
        "sources": [],
    }
    if ind["rel_volume"] is None and len(daily) >= 21:
        avg = sum(b["volume"] for b in daily[-21:-1]) / 20
        ind["rel_volume"] = round(daily[-1]["volume"] / avg, 2) if avg else None
    # Live price beats the snapshot.
    try:
        from market_engine import live_quotes
        q = (live_quotes([ticker]) or {}).get(ticker) or {}
        if _f(q.get("price")):
            ind["price"] = _f(q.get("price"))
            ind["sources"].append(q.get("source") or "live")
    except Exception:
        pass
    # Earnings from the calendar when the snapshot has none.
    if not ind["earnings_date"]:
        try:
            import intel_engine
            from terminal_intelligence import _earnings_rows
            rows = _earnings_rows(intel_engine.get_intel_summary() or {}, ticker)
            if rows:
                ind["earnings_date"] = rows[0].get("date")
        except Exception:
            pass
    ind["earnings_days"] = days_until(ind["earnings_date"])
    etfs = {"DIA", "IWM", "QQQ", "SPY", "VOO", "VTI", "SMH", "XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLP", "XLU", "XLB", "XLC", "XLRE", "SCHD", "TQQQ", "SQQQ"}
    if ticker in etfs:
        ind["asset_type"] = "ETF"
    # Market backdrop.
    try:
        from market_engine import fast_pulse_quotes
        pulse = fast_pulse_quotes() or {}
        ind["vix"] = _f((pulse.get("VIX") or {}).get("price"))
        ind["spy_change_pct"] = _f((pulse.get("SPY") or {}).get("change_pct"))
    except Exception:
        pass
    for k in ("price", "ema20", "ema50", "atr", "vwap"):
        if ind.get(k) is not None:
            ind[k] = round(ind[k], 4)
    with _cache_lock:
        _cache[ticker] = (time.time(), ind)
    return dict(ind)
