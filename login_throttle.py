"""Rate limiting for failed sign-in attempts.

The login page is public, the admin username is guessable, and nothing counted
attempts — an attacker could try passwords as fast as the network allowed,
indefinitely, against a known account.

The decision logic is pure functions over a list of failure timestamps, so the
policy can be tested without a database or a clock. Storage is a small table
created on demand, because two gunicorn workers serve this app and an
in-process counter would let an attacker alternate between them and double
their budget for free. It also survives the worker recycles that happen every
few hundred requests.

Locking is keyed on address and username together. Keying on username alone
lets anyone lock a real user out of their own account by guessing badly on
purpose; keying on address alone lets one NAT'd office lock out everyone
behind it.
"""
from __future__ import annotations

import time

# Five wrong passwords inside the window opens a lockout, and each further
# failure extends it, to a ceiling. The first step is short enough that a
# genuine user who mistyped twice is not meaningfully inconvenienced.
WINDOW_SECONDS = 15 * 60
FREE_ATTEMPTS = 5
BACKOFF_SECONDS = [60, 300, 900, 1800, 3600]
MAX_TRACKED = 50


def _recent(failures, now: float) -> list[float]:
    return [t for t in failures if (now - t) < WINDOW_SECONDS][-MAX_TRACKED:]


def lockout_seconds(failures, now: float | None = None) -> int:
    """Seconds the caller must wait. 0 means let them try.

    Counted from the most recent failure, so a locked-out attacker who keeps
    hammering keeps the lock alive rather than waiting it out while trying.
    """
    now = time.time() if now is None else now
    recent = _recent(list(failures or []), now)
    over = len(recent) - FREE_ATTEMPTS
    if over <= 0:
        return 0
    step = BACKOFF_SECONDS[min(over - 1, len(BACKOFF_SECONDS) - 1)]
    remaining = step - (now - max(recent))
    return int(remaining) + 1 if remaining > 0 else 0


def describe(seconds: int) -> str:
    """Wording for the login page. Deliberately says nothing about whether the
    username exists."""
    if seconds <= 0:
        return ""
    if seconds < 90:
        return f"Too many failed attempts. Try again in {seconds} seconds."
    return f"Too many failed attempts. Try again in {max(1, round(seconds / 60))} minutes."


# ─── Storage ──────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS login_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    failed_at REAL NOT NULL
)
"""
_INDEX = "CREATE INDEX IF NOT EXISTS idx_login_failures_scope ON login_failures(scope)"


def scope_key(ip: str, username: str) -> str:
    return f"{(ip or '?').strip()}|{(username or '').strip().lower()}"


def _ensure(conn) -> None:
    conn.execute(_DDL)
    conn.execute(_INDEX)


def recent_failures(conn, scope: str, now: float | None = None) -> list[float]:
    now = time.time() if now is None else now
    try:
        _ensure(conn)
        rows = conn.execute(
            "SELECT failed_at FROM login_failures WHERE scope = ? AND failed_at > ?"
            " ORDER BY failed_at",
            (scope, now - WINDOW_SECONDS),
        ).fetchall()
        return [float(r[0]) for r in rows]
    except Exception:
        # A throttle that cannot read its own table must not lock everyone out.
        return []


def record_failure(conn, scope: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    try:
        _ensure(conn)
        conn.execute("INSERT INTO login_failures(scope, failed_at) VALUES(?, ?)",
                     (scope, now))
        # Opportunistic cleanup so the table cannot grow without bound.
        conn.execute("DELETE FROM login_failures WHERE failed_at < ?",
                     (now - WINDOW_SECONDS * 4,))
        conn.commit()
    except Exception:
        pass


def clear(conn, scope: str) -> None:
    """Called on a successful sign-in, so one good password resets the count."""
    try:
        _ensure(conn)
        conn.execute("DELETE FROM login_failures WHERE scope = ?", (scope,))
        conn.commit()
    except Exception:
        pass
