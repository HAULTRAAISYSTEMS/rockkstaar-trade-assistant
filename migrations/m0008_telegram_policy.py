"""Durable Telegram delivery identity and cross-worker recipient locks."""
VERSION = '0008_telegram_policy'


def upgrade(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS telegram_locks (
        recipient TEXT PRIMARY KEY, version INTEGER NOT NULL DEFAULT 0)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS telegram_deliveries (
        recipient TEXT NOT NULL, event_key TEXT NOT NULL,
        day TEXT NOT NULL, category TEXT NOT NULL,
        PRIMARY KEY (recipient, event_key))''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_telegram_daily ON telegram_deliveries (recipient, day, category)')
