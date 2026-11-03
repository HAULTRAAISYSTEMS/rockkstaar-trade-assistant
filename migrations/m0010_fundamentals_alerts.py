"""Fundamentals-change notifications: last-seen snapshot and delivery record.

The snapshot is global rather than per user — whether Intel's return on
equity crossed zero is a fact about Intel, not about who is watching. Who
gets told is the per-user part, and that lives in the delivery table.

Storing the snapshot at all is what makes a change detectable. Without a
previous reading there is only a current value, which looks identical on
every pass, and the app would be back to showing the number correctly and
telling nobody it moved.
"""

VERSION = "0010_fundamentals_alerts"


def upgrade(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fundamentals_state (
            ticker TEXT PRIMARY KEY,
            snapshot_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fundamentals_alert_deliveries (
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
        "CREATE INDEX IF NOT EXISTS idx_fundamentals_alert_daily "
        "ON fundamentals_alert_deliveries(user_id, attempted_at, status)"
    )
    try:
        conn.execute("ALTER TABLE browser_push_preferences "
                     "ADD COLUMN fundamentals_triggers TEXT")
    except Exception:
        pass
