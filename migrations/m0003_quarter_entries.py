"""Saved quarters from the seven-check drill.

Reading a filing takes twenty minutes. Losing it to a page refresh — or
having to retype it to compare this quarter against the last one — is what
stops the drill from becoming a habit.

Only the figures the reader typed are kept, never the grades. The grades are
derived, and a threshold that moves later should reflow every saved quarter
rather than leaving a museum of verdicts computed under old rules.
"""

from database import _USE_POSTGRES


VERSION = "0003_quarter_entries"


def upgrade(conn) -> None:
    if _USE_POSTGRES:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quarter_entries (
                id         SERIAL PRIMARY KEY,
                user_id    INTEGER NOT NULL DEFAULT 1,
                ticker     TEXT NOT NULL,
                period     TEXT NOT NULL DEFAULT '',
                figures    TEXT NOT NULL,
                note       TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quarter_entries (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL DEFAULT 1,
                ticker     TEXT NOT NULL,
                period     TEXT NOT NULL DEFAULT '',
                figures    TEXT NOT NULL,
                note       TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

    # One row per ticker and period per user: saving again after correcting a
    # figure is an edit of that quarter, not a second copy of it.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_quarter_entries_unique "
        "ON quarter_entries(user_id, ticker, period)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_quarter_entries_recent "
        "ON quarter_entries(user_id, updated_at)"
    )
