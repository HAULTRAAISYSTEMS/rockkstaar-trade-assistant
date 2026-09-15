"""Personal research capture, company memory, and active-recall scheduling."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from database import get_db
from research_feed import normalize_ticker


IMPACTS = ("strengthened", "weakened", "neutral", "uncertain")
RATINGS = ("forgot", "partial", "remembered")
INTERVAL_DAYS = (0, 1, 3, 8, 21, 60)


class ResearchMemoryError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value, *, limit: int, required: bool = False) -> str:
    value = " ".join(str(value or "").split()).strip()
    if required and not value:
        raise ResearchMemoryError("Please complete every required field.")
    return value[:limit]


def _multiline(value, *, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _uid(user_id) -> int:
    user_id = int(user_id or 0)
    if user_id <= 0:
        raise ResearchMemoryError("Sign in to use Research Memory.")
    return user_id


def _ticker(value) -> str:
    try:
        return normalize_ticker(value)
    except Exception as exc:
        raise ResearchMemoryError("Enter a valid company ticker.") from exc


def _url(value) -> str:
    value = _text(value, limit=2000)
    if value and not value.lower().startswith(("https://", "http://")):
        raise ResearchMemoryError("The original source must use an http or https URL.")
    return value


def published_post(post_id: str, conn=None) -> dict | None:
    if not post_id:
        return None
    owns = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT id,ticker,company_name,headline,source_name,source_url,"
            "source_published_at,research_notes,tradestaar_take "
            "FROM research_posts WHERE id=? AND status='published'",
            (str(post_id),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        if owns:
            conn.close()


def create_card(user_id, data: dict, conn=None) -> str:
    user_id = _uid(user_id)
    owns = conn is None
    conn = conn or get_db()
    try:
        post_id = _text(data.get("research_post_id"), limit=80) or None
        source = published_post(post_id, conn=conn) if post_id else None
        if post_id and source is None:
            raise ResearchMemoryError("That Live Research item is no longer available.")
        if source:
            existing = conn.execute(
                "SELECT id FROM research_memory_cards WHERE user_id=? AND research_post_id=?",
                (user_id, post_id),
            ).fetchone()
            if existing:
                return existing["id"]

        ticker = _ticker((source or {}).get("ticker") or data.get("ticker"))
        headline = _text((source or {}).get("headline") or data.get("headline"), limit=300, required=True)
        why = _multiline(data.get("why_it_matters"), limit=4000)
        if not why:
            raise ResearchMemoryError("Write why this matters before saving it.")
        impact = _text(data.get("thesis_impact"), limit=20) or "uncertain"
        if impact not in IMPACTS:
            raise ResearchMemoryError("Choose a valid thesis impact.")
        question = _multiline(data.get("review_question"), limit=600)
        if not question:
            question = f"Without looking at your notes, what happened with {ticker}, and why did it matter to your thesis?"

        card_id = str(uuid4())
        timestamp = _now().isoformat()
        conn.execute(
            """INSERT INTO research_memory_cards
            (id,user_id,research_post_id,ticker,company_name,headline,source_name,source_url,
             source_published_at,why_it_matters,key_change,thesis_impact,disconfirming_evidence,
             notes,review_question,status,box,due_at,review_count,remembered_count,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                card_id, user_id, post_id, ticker,
                _text((source or {}).get("company_name") or data.get("company_name"), limit=160),
                headline,
                _text((source or {}).get("source_name") or data.get("source_name"), limit=160),
                _url((source or {}).get("source_url") or data.get("source_url")),
                _text((source or {}).get("source_published_at") or data.get("source_published_at"), limit=80),
                why,
                _multiline(data.get("key_change"), limit=2000), impact,
                _multiline(data.get("disconfirming_evidence"), limit=3000),
                _multiline(data.get("notes"), limit=8000), question,
                "active", 0, timestamp, 0, 0, timestamp, timestamp,
            ),
        )
        conn.commit()
        return card_id
    except Exception:
        conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def list_cards(user_id, *, ticker=None, status="active", limit=200, conn=None) -> list[dict]:
    user_id = _uid(user_id)
    owns = conn is None
    conn = conn or get_db()
    try:
        clauses = ["user_id=?", "status=?"]
        params: list = [user_id, status]
        if ticker:
            clauses.append("ticker=?")
            params.append(_ticker(ticker))
        params.append(max(1, min(int(limit), 500)))
        rows = conn.execute(
            f"SELECT * FROM research_memory_cards WHERE {' AND '.join(clauses)} "
            "ORDER BY updated_at DESC LIMIT ?", tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        if owns:
            conn.close()


def get_card(user_id, card_id, conn=None) -> dict | None:
    user_id = _uid(user_id)
    owns = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT * FROM research_memory_cards WHERE id=? AND user_id=?",
            (str(card_id), user_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        if owns:
            conn.close()


def due_card(user_id, conn=None) -> dict | None:
    user_id = _uid(user_id)
    owns = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT * FROM research_memory_cards WHERE user_id=? AND status='active' "
            "AND due_at<=? ORDER BY CASE WHEN last_rating='forgot' THEN 0 ELSE 1 END,due_at,created_at LIMIT 1",
            (user_id, _now().isoformat()),
        ).fetchone()
        return dict(row) if row else None
    finally:
        if owns:
            conn.close()


def rate_card(user_id, card_id, rating: str, conn=None) -> dict:
    user_id = _uid(user_id)
    rating = _text(rating, limit=20)
    if rating not in RATINGS:
        raise ResearchMemoryError("Choose Forgot, Partly remembered, or Remembered.")
    owns = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT * FROM research_memory_cards WHERE id=? AND user_id=? AND status='active'",
            (str(card_id), user_id),
        ).fetchone()
        if not row:
            raise ResearchMemoryError("Research card not found.")
        previous_box = max(0, int(row["box"] or 0))
        now = _now()
        if rating == "forgot":
            new_box, due = 1, now + timedelta(minutes=10)
        elif rating == "partial":
            new_box = max(1, previous_box)
            due = now + timedelta(days=1)
        else:
            new_box = min(previous_box + 1, len(INTERVAL_DAYS) - 1)
            due = now + timedelta(days=INTERVAL_DAYS[new_box])
        reviewed_at, due_at = now.isoformat(), due.isoformat()
        conn.execute(
            """UPDATE research_memory_cards SET box=?,due_at=?,review_count=review_count+1,
            remembered_count=remembered_count+?,last_rating=?,last_reviewed_at=?,updated_at=?
            WHERE id=? AND user_id=?""",
            (new_box, due_at, 1 if rating == "remembered" else 0, rating,
             reviewed_at, reviewed_at, str(card_id), user_id),
        )
        conn.execute(
            """INSERT INTO research_memory_reviews
            (id,card_id,user_id,rating,previous_box,new_box,reviewed_at,next_due_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (str(uuid4()), str(card_id), user_id, rating, previous_box, new_box, reviewed_at, due_at),
        )
        conn.commit()
        return {"rating": rating, "box": new_box, "due_at": due_at}
    except Exception:
        conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def archive_card(user_id, card_id, conn=None) -> None:
    user_id = _uid(user_id)
    owns = conn is None
    conn = conn or get_db()
    try:
        result = conn.execute(
            "UPDATE research_memory_cards SET status='archived',updated_at=? WHERE id=? AND user_id=?",
            (_now().isoformat(), str(card_id), user_id),
        )
        if getattr(result, "rowcount", 0) != 1:
            raise ResearchMemoryError("Research card not found.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def summary(user_id, cards=None) -> dict:
    cards = cards if cards is not None else list_cards(user_id)
    now = _now().isoformat()
    week_ago = (_now() - timedelta(days=7)).isoformat()
    tickers = sorted({card["ticker"] for card in cards})
    return {
        "total": len(cards),
        "due": sum(1 for card in cards if card["due_at"] <= now),
        "learned": sum(1 for card in cards if int(card["box"] or 0) >= 3),
        "this_week": sum(1 for card in cards if card["created_at"] >= week_ago),
        "tickers": tickers,
    }
