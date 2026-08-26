"""Realtime helpers for Tradestaar Live Research Feed Phase 4.

The database remains the source of truth.  WebSocket messages only announce
that explicitly published research changed; clients reconcile through the
published-only REST feed.  This keeps drafts and provider/AI suggestions out
of the public realtime path.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

import research_feed_phase2 as svc

_LOCK = threading.Lock()
_CONDITION = threading.Condition(_LOCK)
_REVISION = 0
_LAST_EVENT = None


def published_cursor(post):
    """Return a stable incremental cursor for a published post."""
    return post.get("published_at") or post.get("updated_at") or ""


def list_incremental(*, since=None, user_id=None, watchlist_tickers=None, limit=50, conn=None):
    """Return published posts newer than *since*; drafts can never enter here."""
    posts = svc.list_published(
        watchlist_tickers=watchlist_tickers,
        user_id=user_id,
        limit=max(1, min(int(limit), 100)),
        conn=conn,
    )
    if since:
        posts = [p for p in posts if published_cursor(p) > str(since)]
    posts.sort(key=published_cursor)
    return posts


def announce_published(post_id, *, ticker=None):
    """Wake connected clients after an explicit admin publication."""
    global _REVISION, _LAST_EVENT
    with _CONDITION:
        _REVISION += 1
        _LAST_EVENT = {
            "type": "research.published",
            "post_id": str(post_id),
            "ticker": ticker,
            "revision": _REVISION,
            "server_time": datetime.now(timezone.utc).isoformat(),
        }
        _CONDITION.notify_all()
        return dict(_LAST_EVENT)


def wait_for_event(after_revision=0, timeout=25.0):
    """Block briefly for the next in-process publication event."""
    with _CONDITION:
        if _REVISION <= int(after_revision or 0):
            _CONDITION.wait(timeout=max(0.1, float(timeout)))
        if _LAST_EVENT and _LAST_EVENT["revision"] > int(after_revision or 0):
            return dict(_LAST_EVENT)
        return {
            "type": "research.heartbeat",
            "revision": _REVISION,
            "server_time": datetime.now(timezone.utc).isoformat(),
        }


def websocket_loop(ws, *, user_id):
    """Authenticated WebSocket loop. Payloads never contain draft content."""
    revision = 0
    ws.send(json.dumps({
        "type": "research.ready",
        "revision": revision,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }))
    while True:
        event = wait_for_event(revision, timeout=25.0)
        revision = max(revision, int(event.get("revision", revision)))
        ws.send(json.dumps(event))
        time.sleep(0.01)
