"""calendar_alerts.py — what on the calendar is worth interrupting someone for.

The Earnings & Catalyst Calendar already knows what is coming. Until now it
only knew it while you were looking at it, which is the one time you do not
need telling. This turns the same data into notifications, through the
browser-push machinery that already exists: VAPID keys, encrypted per-user
subscriptions, quiet hours, a daily cap and dead-subscription pruning. None
of that is reimplemented here. This module decides only two things — which
events qualify for a given user, and whether now is the moment to say so.

FOUR TRIGGERS, each independently switchable per user:

  watchlist_earnings   a ticker on one of your watchlists reports
  macro_high_impact    NFP, CPI, FOMC — the releases the calendar flags HIGH
  large_cap_earnings   the market-wide slate, not only names you track
  earnings_results     a tracked company has reported, with the number
                       against the consensus the card was showing

THREE TIMINGS:

  evening_before   one digest the night before, for tomorrow
  morning_of       before the open, for today
  date_confirmed   when a date stops being provisional — an earnings date
                   arrives as TBD and later becomes BMO or AMC, and that
                   transition is the moment a plan can actually be made

IDEMPOTENCY. Every alert carries a key that is a pure function of what it is
about: "earn:AMD:2026-11-03:morning_of". The delivery table holds
UNIQUE(subscription_id, alert_key), so a second pass over the same calendar
sends nothing. That is what makes the caller's schedule unimportant — the
runner may fire every five minutes or once an hour, and each alert still
goes out exactly once. It is also why the timing windows below can be
generous: being inside the window twice is harmless.

WHAT THIS MODULE WILL NOT DO. It never invents an event. If the calendar is
empty because a feed is down — which it has been — this sends nothing rather
than a reassuring silence dressed up as "nothing scheduled". The two are
different and only the calendar itself can tell them apart.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

TRIGGERS = ("watchlist_earnings", "macro_high_impact",
            "large_cap_earnings", "earnings_results")
TIMINGS = ("evening_before", "morning_of", "date_confirmed")

# Defaults for a user who has never opened the settings. The market-wide
# slate is off: it is dozens of companies a day in earnings season, and a
# notification stream nobody reads is worse than none. Everything else is a
# bounded number of events about things the user chose to track.
DEFAULT_TRIGGERS = ("watchlist_earnings", "macro_high_impact", "earnings_results")
DEFAULT_TIMINGS = ("evening_before", "morning_of", "date_confirmed")

# Local-time windows. Wide on purpose: the dedupe key, not the clock, is what
# guarantees one alert, so a runner that fires at 19:05 and again at 19:35
# produces a single notification.
EVENING_WINDOW = (18, 22)   # 6pm–10pm, for tomorrow
MORNING_WINDOW = (6, 10)    # 6am–10am, before the 9:30 open


def _zone(name: str):
    try:
        return ZoneInfo(name or "America/New_York")
    except ZoneInfoNotFoundError:
        return timezone.utc


def parse_prefs(value, default: tuple) -> list[str]:
    """Stored preferences as a list, falling back to the default.

    NULL means "never chosen" and takes the default. An empty string means
    "chose nothing", which is a real choice and is honoured — the difference
    matters, because collapsing them would make turning everything off
    impossible.
    """
    if value is None:
        return list(default)
    return [p for p in str(value).split(",") if p.strip()]


def serialise_prefs(values, allowed: tuple) -> str:
    """Preferences for storage, filtered to what this build understands."""
    seen = []
    for item in values or []:
        key = str(item).strip()
        if key in allowed and key not in seen:
            seen.append(key)
    return ",".join(seen)


def event_key(kind: str, name: str, event_date) -> str:
    """A stable identity for a calendar event, independent of the alert."""
    day = event_date.isoformat() if hasattr(event_date, "isoformat") else str(event_date)[:10]
    return f"{kind}:{str(name).upper()}:{day}"


def alert_key(kind: str, name: str, event_date, timing: str) -> str:
    return f"{event_key(kind, name, event_date)}:{timing}"


def _due_timings(now_local: datetime, timings: list[str]) -> list[str]:
    """Which scheduled timings are live at this local hour."""
    due = []
    hour = now_local.hour
    if "evening_before" in timings and EVENING_WINDOW[0] <= hour < EVENING_WINDOW[1]:
        due.append("evening_before")
    if "morning_of" in timings and MORNING_WINDOW[0] <= hour < MORNING_WINDOW[1]:
        due.append("morning_of")
    return due


def _session_text(item: dict) -> str:
    """'before the open' / 'after the close', or an honest shrug."""
    label = str(item.get("time_label") or "").upper()
    return {"BMO": "before the open", "AMC": "after the close",
            "DMH": "during market hours"}.get(label, "time not announced")


def _fmt_eps(value) -> str:
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _earnings_rows(calendar: dict) -> list[dict]:
    rows = []
    for bucket in ("today", "tomorrow", "this_week", "coming_up"):
        for row in (calendar or {}).get(bucket) or []:
            if isinstance(row, dict) and row.get("ticker"):
                rows.append(row)
    return rows


def _on_date(rows: list[dict], target: date) -> list[dict]:
    out = []
    for row in rows:
        raw = str(row.get("date") or "")[:10]
        try:
            if datetime.strptime(raw, "%Y-%m-%d").date() == target:
                out.append(row)
        except ValueError:
            continue
    return out


def _macro_on_date(events: list[dict], target: date) -> list[dict]:
    out = []
    for ev in events or []:
        if str(ev.get("impact") or "").upper() != "HIGH":
            continue
        raw = str(ev.get("date") or "")[:10]
        try:
            if datetime.strptime(raw, "%Y-%m-%d").date() == target:
                out.append(ev)
        except ValueError:
            continue
    return out


def _digest_payload(timing: str, day: date, earnings: list[dict],
                    macro: list[dict], watch: set) -> dict | None:
    """One notification for a whole day, not one per company.

    Four separate buzzes for four companies reporting tomorrow is how a
    useful alert becomes one that gets switched off.
    """
    if not earnings and not macro:
        return None
    when = "Tomorrow" if timing == "evening_before" else "Today"

    mine = [r for r in earnings if str(r.get("ticker", "")).upper() in watch]
    others = [r for r in earnings if r not in mine]
    parts = []
    if mine:
        parts.append(", ".join(
            f"{r['ticker']} ({_session_text(r)})" for r in mine[:4]))
    if others:
        names = ", ".join(str(r["ticker"]) for r in others[:5])
        parts.append(f"also {names}" if mine else names)
    if macro:
        parts.append(" · ".join(str(e.get("title") or e.get("event") or "Macro release")
                                for e in macro[:3]))

    count = len(earnings) + len(macro)
    title = (f"{when}: {len(mine)} on your watchlist" if mine
             else f"{when}: {count} on the calendar")
    return {
        "title": title,
        "body": " — ".join(p for p in parts if p)[:240],
        "url": "/catalyst-calendar",
        "tag": f"calendar-{timing}-{day.isoformat()}",
    }


def build_alerts(*, calendar: dict, macro_events: list[dict], watchlist: set,
                 triggers: list[str], timings: list[str], now_local: datetime,
                 prior_state: dict) -> list[dict]:
    """Every alert this user should receive right now.

    Returns [{key, payload}]. Pure: no database, no network, no clock of its
    own — which is what makes the timing and dedupe rules testable without
    standing up a calendar.

    `prior_state` maps event_key -> {"time_label": str, "eps_actual": float}
    as last seen. It is how a confirmed date and a landed result are noticed;
    with no prior state nothing is reported as "changed", because a first
    sighting is not a change.
    """
    alerts: list[dict] = []
    watch = {str(t).upper() for t in watchlist or set()}
    rows = _earnings_rows(calendar)
    today = now_local.date()

    wants_watch = "watchlist_earnings" in triggers
    wants_large = "large_cap_earnings" in triggers
    wants_macro = "macro_high_impact" in triggers

    # ── Scheduled digests ────────────────────────────────────────────────
    for timing in _due_timings(now_local, timings):
        day = today + timedelta(days=1) if timing == "evening_before" else today
        day_rows = _on_date(rows, day)
        earnings = [r for r in day_rows
                    if (wants_watch and str(r.get("ticker", "")).upper() in watch)
                    or wants_large]
        macro = _macro_on_date(macro_events, day) if wants_macro else []
        payload = _digest_payload(timing, day, earnings, macro, watch)
        if payload:
            alerts.append({"key": f"digest:{timing}:{day.isoformat()}",
                           "payload": payload})

    # ── A date that stopped being provisional ────────────────────────────
    # Deliberately not gated on the time windows: the point of this one is
    # that it arrives when the company announces, not at a scheduled hour.
    if "date_confirmed" in timings and (wants_watch or wants_large):
        for row in rows:
            ticker = str(row.get("ticker") or "").upper()
            if not ticker:
                continue
            if ticker not in watch and not wants_large:
                continue
            label = str(row.get("time_label") or "").upper()
            if label in ("", "TBD"):
                continue
            key = event_key("earn", ticker, row.get("date"))
            was = (prior_state.get(key) or {}).get("time_label")
            if was is None or str(was).upper() not in ("", "TBD"):
                continue        # never seen before, or already known: no change
            alerts.append({
                "key": f"{key}:date_confirmed",
                "payload": {
                    "title": f"{ticker} confirmed its report time",
                    "body": (f"{row.get('date')} — {_session_text(row)}. "
                             f"Consensus {_fmt_eps(row.get('eps_est'))}."),
                    "url": f"/stock/{ticker}",
                    "tag": f"confirm-{ticker}",
                },
            })

    # ── A result landing ─────────────────────────────────────────────────
    if "earnings_results" in triggers:
        for row in rows:
            ticker = str(row.get("ticker") or "").upper()
            actual = row.get("eps_actual")
            if not ticker or actual is None:
                continue
            if ticker not in watch and not wants_large:
                continue
            key = event_key("earn", ticker, row.get("date"))
            if (prior_state.get(key) or {}).get("eps_actual") is not None:
                continue        # already reported last time we looked
            est = row.get("eps_est")
            verdict = ""
            try:
                if est is not None:
                    diff = float(actual) - float(est)
                    verdict = (" — beat" if diff > 0 else
                               " — missed" if diff < 0 else " — in line")
            except (TypeError, ValueError):
                verdict = ""
            alerts.append({
                "key": f"{key}:result",
                "payload": {
                    "title": f"{ticker} reported{verdict}",
                    "body": (f"EPS {_fmt_eps(actual)} against "
                             f"{_fmt_eps(est)} expected."),
                    "url": f"/stock/{ticker}",
                    "tag": f"result-{ticker}",
                },
            })

    return alerts


def current_state(calendar: dict) -> dict:
    """What the calendar says right now, in the shape prior_state is read in.

    Stored after each pass so the next one has something to compare against.
    """
    state = {}
    for row in _earnings_rows(calendar):
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        state[event_key("earn", ticker, row.get("date"))] = {
            "time_label": str(row.get("time_label") or "").upper(),
            "eps_actual": row.get("eps_actual"),
            "event_date": str(row.get("date") or "")[:10],
        }
    return state
