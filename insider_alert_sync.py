"""Unattended Command Center alert sync, driven by the scheduled runner.

The cron fires every 5 minutes, but a full Form 4 sweep costs roughly one CIK
directory fetch plus one submissions request and up to twenty filing documents
per ticker. Running that every 5 minutes would put tens of thousands of requests
a day on the SEC, so each source carries its own interval, persisted in the
``settings`` table and checked before any network call is made.

Insider filings are the time-sensitive half and default to 30 minutes. The
earnings calendar barely moves, so it defaults to 6 hours.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

INSIDER_LAST_RUN_KEY = "insider_alert_sync_last_run"
EARNINGS_LAST_RUN_KEY = "earnings_alert_sync_last_run"

DEFAULT_INSIDER_MINUTES = 30
DEFAULT_EARNINGS_MINUTES = 360

# Only look back far enough to catch filings published since the last sweep.
# The dashboard uses 30 days; the cron only needs what is new.
INSIDER_HISTORY_DAYS = 2


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return value if value > 0 else default


def insider_interval_minutes() -> int:
    return _int_env("INSIDER_ALERT_SYNC_MINUTES", DEFAULT_INSIDER_MINUTES)


def earnings_interval_minutes() -> int:
    return _int_env("EARNINGS_ALERT_SYNC_MINUTES", DEFAULT_EARNINGS_MINUTES)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_due(last_run, interval_minutes: int, now: datetime | None = None) -> bool:
    """True when ``interval_minutes`` have elapsed since ``last_run``.

    An unset or unparseable timestamp counts as due, so a fresh deployment
    syncs on its first run rather than waiting out an interval.
    """
    previous = _parse(last_run)
    if previous is None:
        return True
    return (now or _now()) - previous >= timedelta(minutes=max(1, interval_minutes))


def _admin_user_id(conn) -> int | None:
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE is_admin = 1 ORDER BY id ASC LIMIT 1"
        ).fetchone()
    except Exception:
        return None
    return int(row["id"]) if row else None


def sync_insider_alerts(conn, tickers, *, now=None, force: bool = False) -> dict:
    """Fetch recent Form 4 filings and persist any that match an enabled rule."""
    from database import get_insider_alert_rules, get_setting, set_setting, add_scanner_alert, get_scanner_alerts
    import command_center_alerts as cca
    from smart_money import build_insider_dashboard, fetch_sec_form4, resolve_alert_rules

    now = now or _now()
    if not force and not is_due(get_setting(INSIDER_LAST_RUN_KEY), insider_interval_minutes(), now):
        return {"ran": False, "reason": "interval", "added": 0}

    clean = [str(t).strip().upper() for t in (tickers or []) if str(t).strip()]
    if not clean:
        return {"ran": False, "reason": "no_tickers", "added": 0}

    rows, _status = fetch_sec_form4(clean, limit=None, history_days=INSIDER_HISTORY_DAYS)
    user_id = _admin_user_id(conn)
    stored = get_insider_alert_rules(user_id) if user_id else {}
    dashboard = build_insider_dashboard(rows, alert_rules=resolve_alert_rules(stored))
    added = cca.sync_alerts(
        cca.build_insider_alerts(dashboard.get("events")),
        get_scanner_alerts(limit=200),
        add_scanner_alert,
    )
    set_setting(INSIDER_LAST_RUN_KEY, now.isoformat())
    return {"ran": True, "added": added, "filings": len(rows)}


def sync_earnings_alerts(conn, *, now=None, force: bool = False) -> dict:
    """Persist alerts for reports landing inside the earnings window."""
    from database import get_setting, set_setting, add_scanner_alert, get_scanner_alerts
    import command_center_alerts as cca

    now = now or _now()
    if not force and not is_due(get_setting(EARNINGS_LAST_RUN_KEY), earnings_interval_minutes(), now):
        return {"ran": False, "reason": "interval", "added": 0}

    import intel_engine
    rows = intel_engine.fetch_earnings_radar(limit=25) or []
    added = cca.sync_alerts(
        cca.build_earnings_alerts(rows, today=now.date()),
        get_scanner_alerts(limit=200),
        add_scanner_alert,
    )
    set_setting(EARNINGS_LAST_RUN_KEY, now.isoformat())
    return {"ran": True, "added": added, "reports": len(rows)}


def run(conn, tickers, *, now=None, force: bool = False) -> dict:
    """Run both syncs. Neither failure is allowed to break the caller's run."""
    summary = {"insider_alerts_added": 0, "earnings_alerts_added": 0, "alert_sync_errors": []}
    for label, call in (
        ("insider", lambda: sync_insider_alerts(conn, tickers, now=now, force=force)),
        ("earnings", lambda: sync_earnings_alerts(conn, now=now, force=force)),
    ):
        try:
            result = call()
            summary[f"{label}_alerts_added"] = result.get("added", 0)
        except Exception as exc:
            summary["alert_sync_errors"].append(f"{label}:{type(exc).__name__}")
    return summary
