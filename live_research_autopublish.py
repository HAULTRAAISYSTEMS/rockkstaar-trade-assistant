"""Stage-two auto-publication gate for Live Research (default OFF).

The Phase 6 ingestion boundary is deliberate: provider content enters the admin
review queue and a human approves it. This module does not remove that boundary.
It adds a narrow, opt-in bypass for the one class of item where the "verify
before publishing" step adds little -- primary-source regulatory filings, whose
content is a filed fact rather than an interpretation of one.

Everything here is inert unless LIVE_RESEARCH_AUTO_PUBLISH is truthy. With the
flag unset (the default), `select_publishable` returns nothing and the runner
behaves exactly as it did before this module existed.

Design notes
------------
* `priority_level()` in live_research_ingestion measures IMPORTANCE, not accuracy.
  A malformed M&A headline still scores Critical. So priority alone is never
  sufficient here -- it is combined with a primary-source requirement.
* Wire-service headlines (Reuters, Bloomberg, CNBC, ...) are intentionally NOT
  auto-publishable. They are journalism, and journalism gets reviewed.
* The gate is deny-by-default: every check must pass affirmatively. Any parsing
  failure, missing field, or unexpected value results in the item staying queued.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import research_feed as rf

# Statuses this module is willing to act on, and the terminal status it sets.
_SOURCE_STATUS = "incoming"
_PUBLISHED = "published"

# Hosts whose content is a filed primary-source fact rather than reporting.
# Deliberately short. Adding a wire service here defeats the purpose of the gate.
_PRIMARY_HOSTS = ("sec.gov",)

# Catalyst types that represent a discrete, verifiable regulatory event.
_PRIMARY_CATALYSTS = frozenset({"8-K", "10-Q", "10-K", "SEC FILING"})

_ELIGIBLE_PRIORITIES = frozenset({"Critical", "High"})

_TRUTHY = frozenset({"1", "true", "yes", "on", "enabled"})

_MARKER = "[auto-published:primary-source]"

# Matches the ingestion fingerprint marker so we can tell provider-ingested rows
# from hand-written admin posts.
_INGESTION_MARKER = re.compile(r"\[ingestion:[0-9a-f]{64}\]", re.IGNORECASE)


class AutoPublishError(RuntimeError):
    pass


def is_enabled() -> bool:
    """Master switch. Absent or unrecognised value means disabled."""
    return str(os.environ.get("LIVE_RESEARCH_AUTO_PUBLISH", "")).strip().lower() in _TRUTHY


def max_age_hours() -> int:
    """Reject anything older than this even if it otherwise qualifies."""
    try:
        value = int(os.environ.get("LIVE_RESEARCH_AUTO_PUBLISH_MAX_AGE_HOURS", "6"))
    except (TypeError, ValueError):
        return 6
    return value if 1 <= value <= 48 else 6


def publish_limit() -> int:
    """Hard ceiling per run, so a provider glitch cannot flood the feed."""
    try:
        value = int(os.environ.get("LIVE_RESEARCH_AUTO_PUBLISH_LIMIT", "10"))
    except (TypeError, ValueError):
        return 10
    return value if 1 <= value <= 50 else 10


def _host(url: str) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").lower()
    except Exception:
        return ""


def is_primary_host(url: str) -> bool:
    host = _host(url)
    if not host:
        return False
    return any(host == h or host.endswith("." + h) for h in _PRIMARY_HOSTS)


def _parse_ts(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _is_fresh(value, hours: int) -> bool:
    dt = _parse_ts(value)
    if dt is None:
        return False
    now = datetime.now(timezone.utc)
    # Reject future-dated rows (clock skew tolerance: 5 minutes).
    if dt > now + timedelta(minutes=5):
        return False
    return (now - dt) <= timedelta(hours=hours)


def evaluate(row, *, hours: int | None = None) -> tuple[bool, str]:
    """Decide one row. Returns (qualifies, reason). Deny by default.

    `row` is a research_posts record (sqlite3.Row or dict).
    """
    hours = max_age_hours() if hours is None else hours
    try:
        get = row.__getitem__ if hasattr(row, "keys") else (lambda k: row.get(k))

        def field(name, default=""):
            try:
                value = get(name)
            except (KeyError, IndexError, TypeError):
                return default
            return default if value is None else value

        if str(field("status")) != _SOURCE_STATUS:
            return False, "not_incoming"
        if str(field("priority")) not in _ELIGIBLE_PRIORITIES:
            return False, "priority_below_threshold"
        if str(field("catalyst_type")).upper() not in _PRIMARY_CATALYSTS:
            return False, "not_regulatory_catalyst"
        if not is_primary_host(field("source_url")):
            return False, "not_primary_source"
        if not _INGESTION_MARKER.search(str(field("research_notes"))):
            return False, "not_provider_ingested"

        ticker = rf.normalize_ticker(field("ticker"))
        if not ticker:
            return False, "unresolved_ticker"
        if not str(field("headline")).strip():
            return False, "missing_headline"
        if not str(field("source_name")).strip():
            return False, "missing_attribution"
        if str(field("take_origin")) == "ai":
            return False, "ai_generated"
        if bool(field("should_notify", False)):
            return False, "notification_flagged"

        published = field("source_published_at") or field("created_at")
        if not _is_fresh(published, hours):
            return False, "stale_or_undated"

        return True, "primary_source_filing"
    except Exception as exc:  # deny-by-default on any unexpected shape
        return False, "evaluation_error:" + type(exc).__name__


def select_publishable(conn, *, hours: int | None = None, limit: int | None = None) -> list[dict]:
    """Return incoming rows that qualify. Empty list when the flag is off."""
    if not is_enabled():
        return []
    limit = publish_limit() if limit is None else limit
    rows = conn.execute(
        "SELECT * FROM research_posts WHERE status = ? ORDER BY created_at ASC", (_SOURCE_STATUS,)
    ).fetchall()
    out = []
    for row in rows:
        ok, reason = evaluate(row, hours=hours)
        if ok:
            out.append({"id": row["id"], "ticker": row["ticker"], "reason": reason})
        if len(out) >= limit:
            break
    return out


def auto_publish(conn, actor: dict, *, hours: int | None = None, limit: int | None = None) -> dict:
    """Publish qualifying incoming rows. No-op unless explicitly enabled.

    Retains the admin-actor requirement: this runs as the configured admin and
    every row it touches is stamped so an auto-publication is distinguishable
    from a human approval in the audit trail.
    """
    summary = {"enabled": is_enabled(), "published": 0, "posts": [], "errors": []}
    if not summary["enabled"]:
        return summary
    rf._assert_admin(actor)
    candidates = select_publishable(conn, hours=hours, limit=limit)
    if not candidates:
        return summary
    now = rf._now()
    for candidate in candidates:
        try:
            conn.execute(
                "UPDATE research_posts SET status=?, published_at=?, reviewed_at=?, "
                "reviewed_by_user_id=?, updated_at=?, "
                "research_notes = research_notes || ? "
                "WHERE id = ? AND status = ?",
                (_PUBLISHED, now, now, int(actor["id"]), now,
                 "\n" + _MARKER, candidate["id"], _SOURCE_STATUS),
            )
            summary["published"] += 1
            summary["posts"].append({"id": candidate["id"], "ticker": candidate["ticker"]})
        except Exception as exc:
            summary["errors"].append(f"{candidate['id']}:{type(exc).__name__}")
    return summary
