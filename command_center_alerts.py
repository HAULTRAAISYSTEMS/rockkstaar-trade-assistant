"""Command Center alert feed.

Turns two existing signal sources into rows for the Command Center's alert card:

  * insider Form 4 events that matched a user's ``INSIDER_ALERT_RULES``
  * upcoming earnings from the intelligence summary already rendered on /intel

Nothing here fetches. Callers pass in data they have already loaded, which keeps
the page render cheap and makes every builder testable without a database or a
network round trip. Persistence goes through the existing ``scanner_alerts``
table via an injected ``add_fn`` so this module stays import-light.
"""
from __future__ import annotations

from datetime import date, datetime

INSIDER_ALERT_TYPE = "insider"
EARNINGS_ALERT_TYPE = "earnings"

# A signal this strong in either direction is worth surfacing loudly.
_STRONG_SCORE = 60
_DEFAULT_EARNINGS_WINDOW = 7

# Nasdaq reports the slot as BMO / AMC / TBD. Spell it out: which side of the
# session a report lands on decides whether an overnight hold is exposed to it.
_SESSION_LABELS = {
    "BMO": "pre-market",
    "AMC": "after close",
    "TBD": "time TBD",
}


def session_label(row: dict) -> str:
    """Human-readable reporting slot, or '' when the source gave nothing."""
    raw = str(row.get("time_label") or row.get("session") or row.get("time") or "").strip()
    if not raw:
        return ""
    key = raw.upper()
    if key in _SESSION_LABELS:
        return _SESSION_LABELS[key]
    lowered = raw.lower()
    if any(token in lowered for token in ("before", "bmo", "pre")):
        return _SESSION_LABELS["BMO"]
    if any(token in lowered for token in ("after", "amc", "post")):
        return _SESSION_LABELS["AMC"]
    return _SESSION_LABELS["TBD"] if lowered in {"tbd", "unknown", "--"} else raw


def _as_date(value):
    text = str(value or "")[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _insider_severity(event: dict) -> str:
    score = (event.get("signal") or {}).get("score")
    try:
        score = int(score)
    except (TypeError, ValueError):
        return "medium"
    return "high" if abs(score) >= _STRONG_SCORE else "medium"


def build_insider_alerts(events: list[dict] | None) -> list[dict]:
    """Alert rows for Form 4 events that matched an enabled rule.

    Expects events from ``build_insider_dashboard``, which attaches
    ``alert_matches``. The message embeds owner and trade date so the same
    filing produces a stable string and can be de-duplicated on re-sync.
    """
    alerts = []
    for event in events or []:
        matches = event.get("alert_matches") or []
        if not matches:
            continue
        ticker = str(event.get("ticker") or "").upper()
        if not ticker:
            continue
        owner = str(event.get("owner") or "Reporting insider").strip()
        role = str(event.get("role") or "").strip()
        traded = str(event.get("trade_date") or "")[:10]
        who = f"{owner} ({role})" if role else owner
        severity = _insider_severity(event)
        for match in matches:
            detail = f"{match} - {who}"
            if traded:
                detail += f" on {traded}"
            alerts.append({
                "ticker": ticker,
                "alert_type": INSIDER_ALERT_TYPE,
                "message": detail,
                "severity": severity,
            })
    return alerts


def build_earnings_alerts(rows: list[dict] | None, within_days: int = _DEFAULT_EARNINGS_WINDOW,
                          today: date | None = None) -> list[dict]:
    """Alert rows for reports landing inside ``within_days``.

    Accepts the normalized earnings rows the /intel route already assembles.
    ``days_away`` is trusted when present and derived from ``date`` otherwise,
    because the two upstream schemas do not agree on which they supply.
    """
    today = today or datetime.now().date()
    alerts = []
    seen = set()
    for row in rows or []:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        when = _as_date(row.get("date") or row.get("earnings_date"))
        days = row.get("days_away")
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = (when - today).days if when else None
        if days is None or days < 0 or days > within_days:
            continue
        key = (ticker, when.isoformat() if when else str(days))
        if key in seen:
            continue
        seen.add(key)
        if days == 0:
            phrase = "reports today"
        elif days == 1:
            phrase = "reports tomorrow"
        else:
            phrase = f"reports in {days} days"
        session = session_label(row)
        # "AAPL reports tomorrow pre-market" reads better than appending the
        # slot after the date, and it is the part that changes a hold decision.
        detail = f"Earnings: {ticker} {phrase}"
        if session:
            detail += f", {session}"
        if when:
            detail += f" ({when.isoformat()})"
        alerts.append({
            "ticker": ticker,
            "alert_type": EARNINGS_ALERT_TYPE,
            "message": detail,
            "severity": "high" if days <= 1 else "medium",
        })
    return alerts


def dedupe_against(alerts: list[dict], existing: list[dict] | None) -> list[dict]:
    """Drop alerts already present in ``existing`` (same ticker/type/message)."""
    seen = {
        (str(row.get("ticker") or "").upper(), row.get("alert_type"), row.get("message"))
        for row in existing or []
    }
    fresh = []
    for alert in alerts:
        key = (alert["ticker"], alert["alert_type"], alert["message"])
        if key in seen:
            continue
        seen.add(key)
        fresh.append(alert)
    return fresh


def sync_alerts(alerts: list[dict], existing: list[dict] | None, add_fn) -> int:
    """Persist alerts that are not already stored. Returns how many were added.

    ``add_fn`` matches ``database.add_scanner_alert``. A failure on one row is
    swallowed so a single bad alert cannot break a page render.
    """
    added = 0
    for alert in dedupe_against(alerts, existing):
        try:
            add_fn(alert["ticker"], alert["alert_type"], alert["message"], alert["severity"])
            added += 1
        except Exception:
            continue
    return added
