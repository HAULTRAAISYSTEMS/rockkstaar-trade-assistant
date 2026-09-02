"""Retire Live Research items nobody reviewed in time.

The queue only ever grew. Discovery adds catalysts every five minutes, review
is manual, and nothing removed an item that was never looked at — so the
backlog reached 823 items, which is not a queue anyone works through. It is
also not a real backlog: a catalyst is a reason to act now, and a headline
sitting unreviewed for a week has already told you whatever it was going to.

Expired items move to 'rejected', not deleted. The row, its ticker, its source
URL and its fingerprint all stay, so the dedupe layer still recognises the
story if it resurfaces and nothing is lost to an audit. reviewed_at is stamped
so an expired item is distinguishable from one a person actually rejected:
reviewed_by_user_id stays null, because nobody looked at it.

Off unless LIVE_RESEARCH_EXPIRE_DAYS is set to a positive number, because
draining a queue is the kind of thing that should be someone's decision rather
than a side effect of a deploy.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

_STATUS_FROM = "incoming"
_STATUS_TO = "rejected"


def expiry_days(env=None) -> int:
    """0 (off) unless the environment names a positive number of days."""
    raw = (env or os.environ).get("LIVE_RESEARCH_EXPIRE_DAYS", "")
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        return 0
    return days if days > 0 else 0


def cutoff(days: int, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def run(conn, *, days: int | None = None, now: datetime | None = None,
        batch: int = 500) -> dict:
    """Expire stale incoming items. Returns a summary for the run log.

    Capped per firing so a first run against a long backlog cannot hold the
    database for the whole sweep; the cron comes back in five minutes and takes
    the next batch.
    """
    days = expiry_days() if days is None else days
    summary = {"expired": 0, "expire_days": days, "expire_remaining": 0}
    if days <= 0:
        return summary

    before = cutoff(days, now)
    try:
        rows = conn.execute(
            "SELECT id FROM research_posts WHERE status = ? AND created_at < ?"
            " ORDER BY created_at LIMIT ?",
            (_STATUS_FROM, before, batch)).fetchall()
        ids = [str(r["id"]) for r in rows]
        stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")
        for post_id in ids:
            conn.execute(
                "UPDATE research_posts SET status = ?, reviewed_at = ?,"
                " updated_at = ? WHERE id = ? AND status = ?",
                (_STATUS_TO, stamp, stamp, post_id, _STATUS_FROM))
        summary["expired"] = len(ids)
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM research_posts WHERE status = ? AND created_at < ?",
            (_STATUS_FROM, before)).fetchone()
        summary["expire_remaining"] = int(row["n"] or 0)
    except Exception as exc:
        summary["expire_error"] = type(exc).__name__
    return summary
