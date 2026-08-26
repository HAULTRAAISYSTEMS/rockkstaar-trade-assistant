"""Persistence foundation for Tradestaar Live Research Feed."""

VERSION = "0001_live_research_feed"


def upgrade(conn) -> None:
    # Keep DDL portable between SQLite and PostgreSQL. IDs are supplied by the
    # service layer (UUID text), avoiding backend-specific autoincrement syntax.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS research_posts (
            id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            company_name TEXT NOT NULL,
            headline TEXT NOT NULL,
            research_notes TEXT NOT NULL,
            category TEXT NOT NULL,
            sentiment TEXT NOT NULL,
            source_name TEXT,
            source_url TEXT,
            tradestaar_take TEXT,
            take_origin TEXT NOT NULL DEFAULT 'manual',
            status TEXT NOT NULL DEFAULT 'draft',
            should_notify INTEGER NOT NULL DEFAULT 0,
            notification_status TEXT NOT NULL DEFAULT 'not_requested',
            author_user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            published_at TEXT,
            FOREIGN KEY(author_user_id) REFERENCES users(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS research_metrics (
            id TEXT PRIMARY KEY,
            post_id TEXT NOT NULL,
            metric_type TEXT NOT NULL,
            label TEXT NOT NULL,
            actual_value REAL,
            expected_value REAL,
            previous_value REAL,
            unit TEXT,
            period TEXT,
            comparison TEXT NOT NULL DEFAULT 'not_applicable',
            notes TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(post_id) REFERENCES research_posts(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS research_saved_posts (
            user_id INTEGER NOT NULL,
            post_id TEXT NOT NULL,
            saved_at TEXT NOT NULL,
            PRIMARY KEY (user_id, post_id),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(post_id) REFERENCES research_posts(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS research_alert_preferences (
            user_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, ticker),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_research_posts_published ON research_posts(status, published_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_research_posts_ticker ON research_posts(ticker, status, published_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_research_posts_category ON research_posts(category, status, published_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_research_posts_sentiment ON research_posts(sentiment, status, published_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_research_metrics_post ON research_metrics(post_id, sort_order)")
