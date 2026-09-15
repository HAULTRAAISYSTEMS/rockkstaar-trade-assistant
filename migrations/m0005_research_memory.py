"""Personal research notes and their active-recall history."""

VERSION = "0005_research_memory"


def upgrade(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS research_memory_cards (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            research_post_id TEXT,
            ticker TEXT NOT NULL,
            company_name TEXT,
            headline TEXT NOT NULL,
            source_name TEXT,
            source_url TEXT,
            source_published_at TEXT,
            why_it_matters TEXT NOT NULL,
            key_change TEXT,
            thesis_impact TEXT NOT NULL DEFAULT 'uncertain',
            disconfirming_evidence TEXT,
            notes TEXT,
            review_question TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            box INTEGER NOT NULL DEFAULT 0,
            due_at TEXT NOT NULL,
            review_count INTEGER NOT NULL DEFAULT 0,
            remembered_count INTEGER NOT NULL DEFAULT 0,
            last_rating TEXT,
            last_reviewed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(research_post_id) REFERENCES research_posts(id) ON DELETE SET NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS research_memory_reviews (
            id TEXT PRIMARY KEY,
            card_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            rating TEXT NOT NULL,
            previous_box INTEGER NOT NULL,
            new_box INTEGER NOT NULL,
            reviewed_at TEXT NOT NULL,
            next_due_at TEXT NOT NULL,
            FOREIGN KEY(card_id) REFERENCES research_memory_cards(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_research_memory_source "
        "ON research_memory_cards(user_id, research_post_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_research_memory_due "
        "ON research_memory_cards(user_id, status, due_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_research_memory_ticker "
        "ON research_memory_cards(user_id, ticker, updated_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_research_memory_reviews_card "
        "ON research_memory_reviews(card_id, reviewed_at)"
    )
