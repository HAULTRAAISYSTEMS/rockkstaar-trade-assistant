"""Minimal migration runner built on Tradestaar's existing database wrapper."""

from __future__ import annotations

import importlib
from datetime import datetime, timezone

from database import get_db


MIGRATIONS = (
    "migrations.m0001_live_research_feed",
)


def _ensure_migration_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def applied_versions(conn) -> set[str]:
    _ensure_migration_table(conn)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row["version"] for row in rows}


def run_migrations(conn=None) -> list[str]:
    """Apply pending migrations in order and return the versions applied.

    When a connection is supplied, the caller owns its lifecycle.  This makes
    migrations testable against isolated databases and avoids hidden commits in
    callers that are already managing a transaction.
    """
    owns_connection = conn is None
    conn = conn or get_db()
    applied: list[str] = []
    try:
        done = applied_versions(conn)
        for module_name in MIGRATIONS:
            module = importlib.import_module(module_name)
            version = module.VERSION
            if version in done:
                continue
            module.upgrade(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(timezone.utc).isoformat()),
            )
            applied.append(version)
        conn.commit()
        return applied
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        if owns_connection:
            conn.close()
