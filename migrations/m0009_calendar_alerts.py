"""Push notifications for the earnings and catalyst calendar.

The browser-push plumbing already existed — VAPID keys, encrypted per-user
subscriptions, quiet hours, a daily cap and dead-subscription pruning — and
fired for exactly one thing: a research item matching a saved thesis. This
adds the calendar as a second source.

It cannot reuse browser_push_deliveries for the idempotency record, which is
the obvious shortcut: that table's post_id carries a foreign key to
research_posts, so a synthetic key like "earn:AMD:2026-11-03:morning_of"
would be rejected. Hence a sibling table with the same shape and the same
UNIQUE(subscription_id, key) guarantee that makes a repeated run a no-op.

calendar_event_state is what lets "the date is now confirmed" be detected at
all. An earnings date arrives as TBD and later becomes BMO or AMC; without a
record of what the feed said last time, there is no change to notice — only
a current value that looks the same on every pass.
"""

VERSION = "0009_calendar_alerts"


def upgrade(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calendar_alert_deliveries (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            subscription_id TEXT NOT NULL,
            alert_key TEXT NOT NULL,
            status TEXT NOT NULL,
            error_code TEXT,
            attempted_at TEXT NOT NULL,
            UNIQUE(subscription_id, alert_key),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(subscription_id)
                REFERENCES browser_push_subscriptions(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_calendar_alert_daily "
        "ON calendar_alert_deliveries(user_id, attempted_at, status)"
    )
    # Global, not per user: whether a company has announced its session is a
    # fact about the company. Who gets told is the per-user part.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calendar_event_state (
            event_key TEXT PRIMARY KEY,
            event_date TEXT,
            time_label TEXT,
            eps_actual REAL,
            updated_at TEXT NOT NULL
        )
    """)

    # Which calendar alerts a user wants, stored on the existing preferences
    # row so quiet hours and the daily cap keep applying to all of them.
    for column, default in (("calendar_triggers", "TEXT"), ("calendar_timings", "TEXT")):
        try:
            conn.execute(
                f"ALTER TABLE browser_push_preferences ADD COLUMN {column} {default}"
            )
        except Exception:
            pass  # already present
