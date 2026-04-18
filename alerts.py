"""
alerts.py — Central alert generation for Rockkstaar Trade Assistant.

Alert triggers (evaluated in priority order per stock):
  1. READY — LEVEL HOLDS     (green — strongest signal)
  2. A+ Swing Setup           (swing_score >= 8, not WAIT)
  3. PRE-CONFIRMATION         (setup forming, not yet confirmed)
  4. Zone entry               (price entering EMA zone — heads-up only)
  5. TREND CONTINUATION       (score >= 6 — breakout in progress)

Each alert has: ticker, message, alert_type, severity, timestamp.

Non-spammy: the same (ticker, alert_type) fires at most once per
DEDUP_MINS minutes — use clear_alerts() to reset between sessions.

Public API:
  generate_alerts(stocks)  → list[dict]   scan annotated stocks, push new alerts
  get_alerts(limit)        → list[dict]   most-recent-first for JSON / template
  get_alert_count()        → int          badge count
  clear_alerts()                          reset queue + dedup tracker
"""

import threading
from datetime import datetime, timedelta
from dataclasses import dataclass, field as _field


def _et_now() -> datetime:
    try:
        import zoneinfo
        return datetime.now(zoneinfo.ZoneInfo("America/New_York"))
    except Exception:
        from datetime import timezone
        return datetime.now(timezone(timedelta(hours=-4)))

# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------

@dataclass
class _Alert:
    ticker:     str
    message:    str
    alert_type: str    # "ready" | "aplus" | "pre_confirm" | "zone" | "continuation"
    severity:   str    # "high" | "medium" | "low"
    timestamp:  str    # "YYYY-MM-DD HH:MM"


# ---------------------------------------------------------------------------
# Module-level state  (thread-safe)
# ---------------------------------------------------------------------------

_lock:        threading.Lock = threading.Lock()
_queue:       list           = []       # list[_Alert]
_last_fired:  dict           = {}       # {(ticker, alert_type): datetime}

MAX_ALERTS  = 50
DEDUP_MINS  = 30     # suppress identical (ticker, type) within this window


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _should_fire(ticker: str, atype: str) -> bool:
    """Return True if this (ticker, alert_type) hasn't fired within DEDUP_MINS."""
    key  = (ticker, atype)
    now  = datetime.now()
    last = _last_fired.get(key)
    if last and (now - last).total_seconds() < DEDUP_MINS * 60:
        return False
    _last_fired[key] = now
    return True


def _push(a: _Alert) -> None:
    """Append alert to queue, evicting oldest if at capacity."""
    with _lock:
        _queue.append(a)
        if len(_queue) > MAX_ALERTS:
            _queue.pop(0)


def _level_hint(setup_type: str) -> str:
    """Short level description extracted from swing_setup_type."""
    t = setup_type or ""
    if "20 EMA"      in t: return " — approaching 20 EMA"
    if "50 EMA"      in t: return " — approaching 50 EMA"
    if "61.8"        in t: return " — near 61.8% fib"
    if "50%"         in t: return " — near 50% fib"
    if "Order Block" in t: return " — demand zone retest"
    if "Breakout"    in t: return " — breakout retest"
    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_alerts(stocks: list) -> list:
    """
    Scan a list of annotated stock dicts and fire alerts for actionable events.

    Returns a list of newly created alert dicts (may be empty).
    Call this after annotating + ranking stocks — e.g. inside _dashboard_inner().

    Alert priority per stock (first match wins for "ready" and "aplus"):
      1. READY — LEVEL HOLDS  → high severity
      2. A+ Setup (score ≥ 8) → high severity
      3. PRE-CONFIRMATION     → medium severity
      4. Zone entry (EMA)     → medium/low severity (background only)
      5. TREND CONTINUATION   → medium severity (score ≥ 6 gate)
    """
    new_alerts = []
    ts = _et_now().strftime("%I:%M %p").lstrip("0") + " ET"

    for s in stocks:
        if s.get("trade_bias") == "Avoid":
            continue

        ticker = s.get("ticker") or ""
        if not ticker:
            continue

        status     = s.get("swing_status") or ""
        score      = s.get("swing_score")  or 0
        setup_type = s.get("swing_setup_type") or ""
        rr         = s.get("risk_reward")
        pct20      = s.get("pct_from_ema20")
        pct50      = s.get("pct_from_ema50")

        # Build R:R suffix (only show when R:R is meaningful)
        rr_str = f" | R:R {rr:.1f}:1" if (rr and rr >= 1.0) else ""

        # ── 1. READY — LEVEL HOLDS ────────────────────────────────────────────
        if status == "READY — LEVEL HOLDS":
            if _should_fire(ticker, "ready"):
                msg = f"{ticker} READY — Level holds{rr_str}"
                a = _Alert(ticker, msg, "ready", "high", ts)
                _push(a)
                new_alerts.append(_alert_to_dict(a))
            # Don't double-fire "aplus" for the same event
            continue

        # ── 2. A+ Setup  (score >= 8, not suppressed by WAIT) ────────────────
        if score >= 8 and status != "WAIT":
            if _should_fire(ticker, "aplus"):
                msg = f"{ticker} A+ Swing Setup — score {score}/10{rr_str}"
                a = _Alert(ticker, msg, "aplus", "high", ts)
                _push(a)
                new_alerts.append(_alert_to_dict(a))

        # ── 3. PRE-CONFIRMATION ───────────────────────────────────────────────
        if status == "PRE-CONFIRMATION":
            if _should_fire(ticker, "pre_confirm"):
                hint = _level_hint(setup_type)
                msg  = f"{ticker} upgraded to PRE-CONFIRMATION{hint}"
                a = _Alert(ticker, msg, "pre_confirm", "medium", ts)
                _push(a)
                new_alerts.append(_alert_to_dict(a))

        # ── 4. Zone entry (price newly near EMA — low-priority heads-up) ─────
        if status not in ("READY — LEVEL HOLDS", "PRE-CONFIRMATION"):
            if pct20 is not None and abs(pct20) <= 1.5:
                if _should_fire(ticker, "zone_ema20"):
                    msg = f"{ticker} entering 20 EMA zone — watch for pullback entry"
                    a = _Alert(ticker, msg, "zone", "medium", ts)
                    _push(a)
                    new_alerts.append(_alert_to_dict(a))
            elif pct50 is not None and abs(pct50) <= 2.0:
                if _should_fire(ticker, "zone_ema50"):
                    msg = f"{ticker} entering 50 EMA zone — deeper pullback level"
                    a = _Alert(ticker, msg, "zone", "low", ts)
                    _push(a)
                    new_alerts.append(_alert_to_dict(a))

        # ── 5. TREND CONTINUATION  (score >= 6 gate avoids noise) ────────────
        if status == "TREND CONTINUATION" and score >= 6:
            if _should_fire(ticker, "continuation"):
                msg = f"{ticker} TREND CONTINUATION — breakout in progress{rr_str}"
                a = _Alert(ticker, msg, "continuation", "medium", ts)
                _push(a)
                new_alerts.append(_alert_to_dict(a))

    return new_alerts


def _alert_to_dict(a: _Alert) -> dict:
    return {
        "ticker":     a.ticker,
        "message":    a.message,
        "alert_type": a.alert_type,
        "severity":   a.severity,
        "timestamp":  a.timestamp,
    }


def get_alerts(limit: int = 20) -> list:
    """Return recent alerts as dicts, most-recent first."""
    with _lock:
        recent = list(_queue[-limit:])
    recent.reverse()
    return [_alert_to_dict(a) for a in recent]


def get_alert_count() -> int:
    """Return the total number of pending alerts."""
    with _lock:
        return len(_queue)


def clear_alerts() -> None:
    """Dismiss all pending alerts and reset the dedup tracker."""
    with _lock:
        _queue.clear()
        _last_fired.clear()
