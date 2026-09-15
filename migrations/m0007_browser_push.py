"""Encrypted browser push subscriptions and idempotent delivery history."""

VERSION = "0007_browser_push"


def upgrade(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS browser_push_preferences (
            user_id INTEGER PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 1,
            quiet_start INTEGER NOT NULL DEFAULT 21,
            quiet_end INTEGER NOT NULL DEFAULT 7,
            timezone TEXT NOT NULL DEFAULT 'America/New_York',
            daily_cap INTEGER NOT NULL DEFAULT 3,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS browser_push_subscriptions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            endpoint_hash TEXT NOT NULL UNIQUE,
            subscription_ciphertext TEXT NOT NULL,
            user_agent TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_success_at TEXT,
            last_error_code TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS browser_push_deliveries (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            subscription_id TEXT NOT NULL,
            post_id TEXT NOT NULL,
            status TEXT NOT NULL,
            error_code TEXT,
            attempted_at TEXT NOT NULL,
            UNIQUE(subscription_id, post_id),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(subscription_id) REFERENCES browser_push_subscriptions(id) ON DELETE CASCADE,
            FOREIGN KEY(post_id) REFERENCES research_posts(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_browser_push_daily "
        "ON browser_push_deliveries(user_id, attempted_at, status)"
    )
