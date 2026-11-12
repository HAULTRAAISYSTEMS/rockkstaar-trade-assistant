"""Notification inbox: one place for everything the app alerts about.

Every alert the app already raises (scanner setups, Position Guardian,
insider buys, thesis changes, earnings/macro calendar, fundamentals changes,
recall pings, uptime) is recorded here at the moment it is decided, whether
or not a push, Telegram or SMS channel is configured. The bell on every page
and the Notifications panel on Today read from this table.

    add(kind, title, ...)        record one item (deduped by key, never raises)
    items(user_id, ...)          newest first, with an unread flag
    unread_count(user_id)
    mark_read(user_id)

user_id 0 means "for everyone" (market-wide and system items).
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

KINDS = {
    "news":         {"label": "News",         "icon": "📰"},
    "setup":        {"label": "Setups",       "icon": "⚡"},
    "position":     {"label": "Positions",    "icon": "🛡"},
    "insider":      {"label": "Insider",      "icon": "🟢"},
    "calendar":     {"label": "Earnings & Economy", "icon": "📅"},
    "research":     {"label": "Thesis",       "icon": "📌"},
    "fundamentals": {"label": "Fundamentals", "icon": "📊"},
    "memory":       {"label": "Memory",       "icon": "🧠"},
    "system":       {"label": "System",       "icon": "⚙️"},
}
SEVERITIES = ("info", "watch", "danger")
KEEP_DAYS = 30

_TAG = re.compile(r"<[^>]+>")
_last_prune = 0.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_tables(conn) -> None:
    from database import ensure_schema
    ensure_schema(conn, 'notify_inbox', ['notifications', 'idx_notifications_user_time'], _create_tables)


def _create_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 0,
            kind TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            title TEXT NOT NULL,
            body TEXT,
            url TEXT,
            ticker TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user_time ON notifications(user_id, created_at)")


def _clean(text, limit: int) -> str:
    text = _TAG.sub("", str(text or ""))
    return " ".join(text.split())[:limit]


def _et_day(now: datetime) -> str:
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return now.strftime("%Y-%m-%d")


def severity_from(value) -> str:
    v = str(value or "").lower()
    if v in ("high", "danger", "critical", "red"):
        return "danger"
    if v in ("medium", "watch", "warning", "warn"):
        return "watch"
    return "info"


def add(kind: str, title: str, body: str = "", *, url: str = "", ticker: str = "",
        severity: str = "info", user_id: int = 0, key: str | None = None,
        conn=None, now: datetime | None = None) -> bool:
    """Record one notification. Same key for the same user is stored once.

    Without a key, the same kind + title + body on the same ET day counts as
    one item, so two workers (or a retry) never double up. Never raises: an
    inbox problem must not stop the alert that called it.
    """
    try:
        now = now or _now()
        kind = kind if kind in KINDS else "system"
        title = _clean(title, 160) or KINDS[kind]["label"]
        body = _clean(body, 600)
        key = key or f"{kind}|{title}|{body}|{_et_day(now)}"
        uid = int(user_id or 0)
        nid = hashlib.sha1(f"{uid}|{key}".encode("utf-8")).hexdigest()[:24]
        owns = conn is None
        if owns:
            from database import get_db
            conn = get_db()
        try:
            ensure_tables(conn)
            cur = conn.execute(
                "INSERT INTO notifications (id,user_id,kind,severity,title,body,url,ticker,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT (id) DO NOTHING",
                (nid, uid, kind, severity if severity in SEVERITIES else severity_from(severity),
                 title, body, str(url or "")[:300], str(ticker or "").upper()[:12], now.isoformat()),
            )
            conn.commit()
            _maybe_prune(conn, now)
            return bool(getattr(cur, "rowcount", 1))
        finally:
            if owns:
                conn.close()
    except Exception as exc:
        logger.debug("inbox add failed: %s", exc)
        return False


def _maybe_prune(conn, now: datetime) -> None:
    global _last_prune
    if time.time() - _last_prune < 3600:
        return
    _last_prune = time.time()
    try:
        conn.execute("DELETE FROM notifications WHERE created_at < ?",
                      ((now - timedelta(days=KEEP_DAYS)).isoformat(),))
        conn.commit()
    except Exception as exc:
        logger.debug("inbox prune: %s", exc)


# ── Reading ──────────────────────────────────────────────────────────────────

def _read_key(user_id: int) -> str:
    return f"inbox_read_at:{int(user_id)}"


def read_at(user_id: int) -> str:
    try:
        from database import get_setting
        return get_setting(_read_key(user_id)) or ""
    except Exception:
        return ""


def mark_read(user_id: int, now: datetime | None = None) -> str:
    from database import set_setting
    stamp = (now or _now()).isoformat()
    set_setting(_read_key(user_id), stamp)
    return stamp


def _rows(conn, sql: str, args: tuple) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def items(user_id: int, *, kind: str | None = None, limit: int = 40, before: str | None = None,
          conn=None) -> dict:
    """{items, unread, read_at, more}. Items are newest first."""
    owns = conn is None
    if owns:
        from database import get_db
        conn = get_db()
    try:
        ensure_tables(conn)
        limit = max(1, min(int(limit or 40), 100))
        where, args = ["user_id IN (0, ?)"], [int(user_id)]
        if kind and kind in KINDS:
            where.append("kind = ?")
            args.append(kind)
        if before:
            where.append("created_at < ?")
            args.append(before)
        rows = _rows(conn, f"SELECT * FROM notifications WHERE {' AND '.join(where)} "
                           f"ORDER BY created_at DESC LIMIT ?", tuple(args) + (limit + 1,))
        seen = read_at(user_id)
        more = len(rows) > limit
        rows = rows[:limit]
        for r in rows:
            r["unread"] = (r.get("created_at") or "") > seen
            meta = KINDS.get(r.get("kind")) or KINDS["system"]
            r["icon"], r["kind_label"] = meta["icon"], meta["label"]
        return {"items": rows, "unread": unread_count(user_id, conn=conn, seen=seen),
                "read_at": seen, "more": more}
    finally:
        if owns:
            conn.close()


def unread_count(user_id: int, *, conn=None, seen: str | None = None) -> int:
    owns = conn is None
    if owns:
        from database import get_db
        conn = get_db()
    try:
        ensure_tables(conn)
        seen = read_at(user_id) if seen is None else seen
        row = conn.execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id IN (0, ?) AND created_at > ?",
                           (int(user_id), seen)).fetchone()
        return int(dict(row)["n"] if row else 0)
    except Exception as exc:
        logger.debug("inbox unread: %s", exc)
        return 0
    finally:
        if owns:
            conn.close()


def kinds() -> list[dict]:
    return [{"kind": k, **v} for k, v in KINDS.items()]
