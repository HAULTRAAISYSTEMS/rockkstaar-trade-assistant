"""Briefs explaining a flagged check, kept because a filing never changes.

Each brief costs a model call. The evidence behind it — the component lines
and the paragraphs from the filing — is fixed the moment the 10-Q is filed,
so the answer to "why did this flag" for one company, one quarter and one
check is written once and read forever after.

Not scoped to a user: the filing is public and two readers asking the same
question of the same quarter deserve the same answer, and the second one
should not be billed for it.
"""

from database import _USE_POSTGRES


VERSION = "0004_quarter_briefs"


def upgrade(conn) -> None:
    if _USE_POSTGRES:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quarter_briefs (
                id         SERIAL PRIMARY KEY,
                ticker     TEXT NOT NULL,
                period     TEXT NOT NULL,
                check_key  TEXT NOT NULL,
                payload    TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quarter_briefs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker     TEXT NOT NULL,
                period     TEXT NOT NULL,
                check_key  TEXT NOT NULL,
                payload    TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_quarter_briefs_unique "
        "ON quarter_briefs(ticker, period, check_key)"
    )
