"""Verified, user-scoped SMS preferences and idempotent delivery history."""

VERSION = "0006_sms_alerts"


def upgrade(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sms_alert_preferences (
            user_id INTEGER PRIMARY KEY,
            phone_ciphertext TEXT NOT NULL,
            phone_last4 TEXT NOT NULL,
            verified_at TEXT,
            enabled INTEGER NOT NULL DEFAULT 0,
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
        CREATE TABLE IF NOT EXISTS sms_alert_verifications (
            user_id INTEGER PRIMARY KEY,
            code_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            sent_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sms_alert_deliveries (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            post_id TEXT NOT NULL,
            phone_last4 TEXT NOT NULL,
            status TEXT NOT NULL,
            provider_message_id TEXT,
            error_code TEXT,
            attempted_at TEXT NOT NULL,
            UNIQUE(user_id, post_id),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(post_id) REFERENCES research_posts(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sms_deliveries_daily "
        "ON sms_alert_deliveries(user_id, attempted_at, status)"
    )
