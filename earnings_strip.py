"""Earnings strip: one slim line on the Command Center for YOUR reports.

Only companies on your watchlist, only when they report within a few
trading days, at most five. Each item knows where it stands:

  soon      reports in 1-3 days            plain
  today     reports today, not yet         gold pulse
  live      inside its release window      red LIVE dot
            (before the open 4:00-9:30 ET, or after the close 4:00-8:00 PM ET)
  reported  the actual EPS is in           beat / miss / in line vs the estimate

The beat/miss read uses only the calendar's own estimate and actual; when
either is missing the item shows the date and nothing is guessed.
"""
from __future__ import annotations

from datetime import date, datetime

WINDOW_DAYS = 3
LIMIT = 5
_SESSION = {"bmo": "before open", "amc": "after close", "dmh": "during market"}
_RANK = {"live": 0, "reported": 1, "today": 2, "soon": 3}


def _num(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _when(days: int, iso: str) -> str:
    if days == 0:
        return "Today"
    if days == 1:
        return "Tomorrow"
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%a %b %-d")
    except ValueError:
        return f"In {days} days"


def _state(days: int, code: str, actual, now_et: datetime) -> str:
    if days > 0:
        return "soon"
    if actual is not None:
        return "reported"
    mins = now_et.hour * 60 + now_et.minute
    if code == "bmo" and 240 <= mins < 570:
        return "live"
    if code == "amc" and 960 <= mins < 1200:
        return "live"
    return "today"


def _result(actual, est):
    if actual is None or est is None:
        return None, None
    if actual > est + 0.005:
        verdict, label = "beat", "Beat"
    elif actual < est - 0.005:
        verdict, label = "miss", "Missed"
    else:
        verdict, label = "inline", "In line"
    return verdict, f"{label} · EPS ${actual:,.2f} vs ${est:,.2f}"


def build_strip(rows, now_et: datetime, window: int = WINDOW_DAYS, limit: int = LIMIT) -> list[dict]:
    """Strip items from the catalyst calendar rows (see app._build_catalyst_calendar)."""
    out, seen = [], set()
    for r in rows or []:
        if r.get("kind") != "EARNINGS" or not r.get("on_watchlist"):
            continue
        t = str(r.get("ticker") or "").upper()
        try:
            days = int(r.get("days_away"))
        except (TypeError, ValueError):
            continue
        if not t or t in seen or days < 0 or days > window:
            continue
        seen.add(t)
        code = str(r.get("time_code") or "").lower()
        actual, est = _num(r.get("eps_actual")), _num(r.get("eps_est"))
        state = _state(days, code, actual, now_et)
        verdict, result = _result(actual, est) if state == "reported" else (None, None)
        out.append({
            "ticker": t, "days": days, "date": r.get("date"),
            "when": _when(days, r.get("date") or ""), "session": _SESSION.get(code, ""),
            "state": state, "verdict": verdict, "result": result,
            "eps_est": est, "provisional": bool(r.get("date_provisional")),
        })
    out.sort(key=lambda e: (_RANK[e["state"]], e["days"], 0 if e["session"] == "before open" else 1, e["ticker"]))
    return out[:limit]
