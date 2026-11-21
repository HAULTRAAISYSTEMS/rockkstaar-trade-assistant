"""Game Plan: the Rockkstaar market-environment check, run on live data.

Mirrors the trading engine's gate (SPY/QQQ structure, VIX zone, DXY
direction) and returns one verdict with the reasons and today's rules:

    RISK-ON   SPY & QQQ bullish, VIX < 20, DXY flat or falling
    CAUTION   mixed signals, or VIX 20–25            → only A+ setups (22/30+)
    RISK-OFF  SPY/QQQ bearish, VIX > 25, or DXY surging → WAIT unless 27+/30

Inputs are daily closes (oldest → newest). Pure functions; the route in
app.py supplies the closes and caches the result.
"""
from __future__ import annotations


def ema(values: list[float], period: int) -> float | None:
    if not values or len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def structure(closes: list[float]) -> dict:
    """Trend read for one index: price vs 20/50 EMA plus the losing/winning streak."""
    if not closes or len(closes) < 21:
        return {"available": False}
    px = closes[-1]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    if e50 is not None and px > e20 > e50:
        trend = "bullish"
    elif e50 is not None and px < e20 < e50:
        trend = "bearish"
    elif e50 is None:
        trend = "bullish" if px > e20 else "bearish"
    else:
        trend = "neutral"
    streak = 0
    for i in range(len(closes) - 1, 0, -1):
        up = closes[i] > closes[i - 1]
        if streak == 0:
            streak = 1 if up else -1
        elif (streak > 0) == up:
            streak += 1 if up else -1
        else:
            break
    hi = max(closes[-60:])
    chg = (px / closes[-2] - 1) * 100 if closes[-2] else None
    return {"available": True, "price": round(px, 2), "trend": trend,
            "ema20": round(e20, 2) if e20 else None, "ema50": round(e50, 2) if e50 else None,
            "day_pct": round(chg, 2) if chg is not None else None,
            "streak": streak, "off_high_pct": round((px / hi - 1) * 100, 1) if hi else None,
            "low_10d": round(min(closes[-10:]), 2), "high_10d": round(max(closes[-10:]), 2)}


def vix_zone(level: float | None) -> dict:
    if level is None:
        return {"zone": "unknown", "label": "VIX unavailable"}
    if level < 15:
        return {"zone": "low", "label": "Low fear"}
    if level < 20:
        return {"zone": "caution", "label": "Caution zone"}
    if level < 25:
        return {"zone": "elevated", "label": "Elevated fear"}
    if level < 30:
        return {"zone": "high", "label": "High fear"}
    return {"zone": "extreme", "label": "Extreme volatility"}


def dxy_read(closes: list[float]) -> dict:
    if not closes or len(closes) < 6:
        return {"available": False, "direction": "unknown"}
    d1 = (closes[-1] / closes[-2] - 1) * 100
    d5 = (closes[-1] / closes[-6] - 1) * 100
    if d1 > 0.5 or d5 > 1.5:
        direction = "surging"
    elif d1 > 0.15 or d5 > 0.6:
        direction = "rising"
    elif d1 < -0.15 or d5 < -0.6:
        direction = "falling"
    else:
        direction = "flat"
    return {"available": True, "level": round(closes[-1], 2), "day_pct": round(d1, 2),
            "week_pct": round(d5, 2), "direction": direction}


RULES = {
    "RISK-ON": "Green light. A and B setups at key levels, with a confirmation candle.",
    "CAUTION": "Only A+ setups — 22/30 or better. Smaller size, tighter stops.",
    "RISK-OFF": "Stand aside. No new longs unless a setup scores 27+/30. Protect capital.",
}


def build_game_plan(spy: list[float], qqq: list[float], vix: list[float], dxy: list[float]) -> dict:
    s, q = structure(spy), structure(qqq)
    vix_level = vix[-1] if vix else None
    vix_day = ((vix[-1] / vix[-2] - 1) * 100) if vix and len(vix) >= 2 and vix[-2] else None
    vz = vix_zone(vix_level)
    dx = dxy_read(dxy)

    trends = [x["trend"] for x in (s, q) if x.get("available")]
    reasons: list[str] = []
    if not trends or vix_level is None:
        verdict = "UNKNOWN"
        reasons.append("Not enough market data to run the check right now.")
    else:
        bearish = "bearish" in trends
        all_bull = len(trends) == 2 and all(t == "bullish" for t in trends)
        if bearish or vix_level > 25 or dx["direction"] == "surging":
            verdict = "RISK-OFF"
        elif all_bull and vix_level < 20 and dx["direction"] in ("flat", "falling", "unknown"):
            verdict = "RISK-ON"
        else:
            verdict = "CAUTION"
        for name, x in (("SPY", s), ("QQQ", q)):
            if not x.get("available"):
                continue
            where = {"bullish": "above", "bearish": "below", "neutral": "between"}[x["trend"]]
            streak = x["streak"]
            run = (f", {abs(streak)} {'up' if streak > 0 else 'down'} days in a row" if abs(streak) >= 3 else "")
            reasons.append(f"{name} {x['trend']} — {where} its 20/50-day averages{run}.")
        reasons.append(f"VIX {vix_level:.1f} — {vz['label'].lower()}.")
        if dx.get("available"):
            reasons.append(f"Dollar {dx['direction']} ({dx['week_pct']:+.1f}% this week).")

    levels = []
    if s.get("available"):
        if s["ema20"]:
            levels.append({"label": "Reclaim 20-day" if s["price"] < s["ema20"] else "Hold 20-day", "price": s["ema20"]})
        levels.append({"label": "10-day low", "price": s["low_10d"]})
        if s["ema50"]:
            levels.append({"label": "50-day", "price": s["ema50"]})
    return {"ok": verdict != "UNKNOWN", "verdict": verdict, "rule": RULES.get(verdict, ""),
            "reasons": reasons, "spy": s, "qqq": q,
            "vix": {"level": round(vix_level, 2) if vix_level is not None else None,
                    "day_pct": round(vix_day, 2) if vix_day is not None else None, **vz},
            "dxy": dx, "levels": levels}
