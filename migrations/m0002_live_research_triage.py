"""Add minimal workflow metadata for the admin research triage dashboard."""

from database import _USE_POSTGRES


VERSION = "0002_live_research_triage"


_COLUMNS = (
    ("priority", "TEXT NOT NULL DEFAULT 'Medium'"),
    ("catalyst_type", "TEXT NOT NULL DEFAULT 'BREAKING'"),
    ("source_published_at", "TEXT"),
    ("reviewed_at", "TEXT"),
    ("reviewed_by_user_id", "INTEGER"),
)


def _sqlite_columns(conn) -> set[str]:
    rows = conn.execute("PRAGMA table_info(research_posts)").fetchall()
    return {row["name"] if hasattr(row, "keys") else row[1] for row in rows}


def upgrade(conn) -> None:
    """Add columns without changing any existing draft or published status."""
    if _USE_POSTGRES:
        for name, definition in _COLUMNS:
            conn.execute(f"ALTER TABLE research_posts ADD COLUMN IF NOT EXISTS {name} {definition}")
    else:
        existing = _sqlite_columns(conn)
        for name, definition in _COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE research_posts ADD COLUMN {name} {definition}")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_research_posts_triage "
        "ON research_posts(status, priority, source_published_at, created_at)"
    )
