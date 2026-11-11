"""Command-center pieces: market mood dial, spoken briefing, mini live board.

mood()            one 0-100 reading from VIX, SPY/QQQ, futures, sector
                  breadth and news tone → Risk-Off / Neutral / Risk-On, with
                  each input's contribution shown.
briefing_script() a ~60-second pre-market (or any-time) brief written to be
                  read aloud by the phone's own speech engine.
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


# ── Mood dial ────────────────────────────────────────────────────────────────

_TREND_SCORES = {"RISK_ON": 80, "NEUTRAL": 50, "CHOPPY": 45, "RISK_OFF": 20, "NO_TRADE": 15}


def mood(*, vix=None, spy=None, qqq=None, es=None, nq=None, breadth=None, news_tone=None,
         regime=None) -> dict:
    """breadth = share of sector ETFs up (0-1); news_tone = -100..100.

    regime = the market engine's trend regime (RISK_ON / NEUTRAL / RISK_OFF ...).
    It is one input here so the dial is the page's single verdict, instead of a
    "Risk Off" regime cell sitting next to a "Neutral" dial.
    """
    parts = []

    def add(name, score, weight, detail):
        parts.append({"name": name, "score": round(score), "weight": weight, "detail": detail})

    vix = _f(vix)
    if vix is not None:
        # 12 → 100 (calm), 20 → 55, 30 → 0 (fear)
        add("VIX", _clamp(100 - (vix - 12) * (100 / 18)), 0.30, f"VIX {vix:.1f}")
    idx = [x for x in (_f(spy), _f(qqq)) if x is not None]
    if idx:
        avg = sum(idx) / len(idx)
        add("Indexes", _clamp(50 + avg * 25), 0.25, "SPY/QQQ " + " / ".join(f"{x:+.2f}%" for x in idx))
    fut = [x for x in (_f(es), _f(nq)) if x is not None]
    if fut:
        avg = sum(fut) / len(fut)
        add("Futures", _clamp(50 + avg * 25), 0.15, "ES/NQ " + " / ".join(f"{x:+.2f}%" for x in fut))
    b = _f(breadth)
    if b is not None:
        add("Breadth", _clamp(b * 100), 0.20, f"{round(b * 100)}% of sectors up")
    r = str(regime or "").upper().replace("-", "_").replace(" ", "_")
    if r in _TREND_SCORES:
        add("Trend", _TREND_SCORES[r], 0.25, "Trend regime " + r.replace("_", " ").title())
    t = _f(news_tone)
    if t is not None:
        add("News tone", _clamp(50 + t / 2), 0.10, f"headline tone {t:+.0f}")
    if not parts:
        return {"known": False, "score": None, "label": "Unknown", "parts": []}
    total_w = sum(p["weight"] for p in parts)
    score = round(sum(p["score"] * p["weight"] for p in parts) / total_w)
    label = "Risk-On" if score >= 62 else "Risk-Off" if score <= 38 else "Neutral"
    note = {"Risk-On": "Buyers in control — press A+ setups, still honor stops.",
            "Neutral": "Mixed tape — be selective, smaller size, take profits faster.",
            "Risk-Off": "Defense first — cash is a position; only the cleanest setups."}[label]
    return {"known": True, "score": score, "label": label, "note": note,
            "parts": sorted(parts, key=lambda p: -p["weight"])}


def sector_breadth(quotes: dict) -> float | None:
    from market_engine import SECTOR_ETFS
    vals = [_f((quotes.get(s) or {}).get("change_pct")) for s in SECTOR_ETFS]
    vals = [v for v in vals if v is not None]
    return (sum(1 for v in vals if v > 0) / len(vals)) if len(vals) >= 5 else None


# ── Voice briefing ───────────────────────────────────────────────────────────

def _pct_words(v) -> str:
    v = _f(v)
    if v is None:
        return "flat"
    if abs(v) < 0.05:
        return "flat"
    return f"{'up' if v > 0 else 'down'} {abs(v):.1f} percent"


def briefing_script(*, now: datetime, pulse: dict, mood_reading: dict, movers: list[dict],
                    earnings: list[dict], events: list[dict], name: str = "") -> str:
    """A ~60-second script. Plain sentences, numbers spoken naturally."""
    hour = now.hour
    greet = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
    lines = [f"{greet}{', ' + name if name else ''}. Here's your Tradestaar briefing for {now.strftime('%A, %B')} {now.day}."]
    session = {"pre": "in pre-market", "post": "after hours", "overnight": "in the overnight session"}.get(pulse.get("session") or "", "")
    fut = []
    if pulse.get("es") is not None:
        fut.append(f"S and P futures are {_pct_words(pulse.get('es'))}")
    if pulse.get("nq") is not None:
        fut.append(f"Nasdaq futures {_pct_words(pulse.get('nq'))}")
    if fut:
        lines.append(", ".join(fut) + ".")
    idx = []
    if pulse.get("spy") is not None:
        idx.append(f"SPY is {_pct_words(pulse.get('spy'))}")
    if pulse.get("qqq") is not None:
        idx.append(f"QQQ {_pct_words(pulse.get('qqq'))}")
    if idx:
        lines.append(", ".join(idx) + (f" {session}" if session else "") + ".")
    if _f(pulse.get("vix")) is not None:
        lines.append(f"The VIX is at {float(pulse['vix']):.1f}.")
    if mood_reading.get("known"):
        lines.append(f"Market mood reads {mood_reading['label'].replace('-', ' ')}, {mood_reading['score']} out of 100. {mood_reading['note']}")
    big = [m for m in movers if _f(m.get("change_pct")) is not None and abs(float(m["change_pct"])) >= 1.5][:4]
    if big:
        said = [f"{' '.join(m['ticker'])} {_pct_words(m['change_pct'])}" for m in big]
        lines.append("On your watchlist: " + "; ".join(said) + ".")
    elif movers:
        lines.append("Your watchlist is quiet so far, with no moves over one and a half percent.")
    if earnings:
        names = ", ".join(" ".join(e["ticker"]) + (f" {e['when']}" if e.get("when") else "") for e in earnings[:4])
        lines.append(f"Earnings to watch: {names}.")
    if events:
        lines.append("On the calendar: " + "; ".join(f"{e['event']}{(' at ' + e['time']) if e.get('time') else ''}" for e in events[:3]) + ".")
    lines.append("Plan the trade, size it to your risk, and honor your stops. That's the briefing.")
    return " ".join(lines)
