"""Domain and persistence service for Tradestaar Live Research Feed.

Phase 1 intentionally contains no Flask routes or UI.  All publishing is an
explicit admin action at the service boundary; provider/AI suggestions can only
be persisted as drafts.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import uuid4

from database import get_db


CATEGORIES = (
    "Earnings", "Guidance", "Revenue", "EPS", "AI", "Partnership",
    "Acquisition", "SEC Filing", "Analyst", "Product", "Management",
    "Macro", "Breaking News",
)
SENTIMENTS = ("Bullish", "Neutral", "Bearish")
STATUSES = ("draft", "published")
TAKE_ORIGINS = ("manual", "ai", "provider")
METRIC_TYPES = ("revenue", "eps", "guidance", "other")
COMPARISONS = ("beat", "miss", "inline", "raised", "lowered", "not_applicable")
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,11}$")


class ResearchValidationError(ValueError):
    pass


class ResearchPermissionError(PermissionError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value) -> str:
    return str(value or "").strip()


def normalize_ticker(value) -> str:
    ticker = _text(value).upper()
    if not _TICKER_RE.fullmatch(ticker):
        raise ResearchValidationError("invalid ticker")
    return ticker


def validate_source_url(value) -> str:
    url = _text(value)
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ResearchValidationError("source_url must be an http(s) URL")
    return url


def _choice(value, allowed, field: str) -> str:
    value = _text(value)
    if value not in allowed:
        raise ResearchValidationError(f"invalid {field}")
    return value


def _required(value, field: str, max_length: int) -> str:
    value = _text(value)
    if not value:
        raise ResearchValidationError(f"{field} is required")
    if len(value) > max_length:
        raise ResearchValidationError(f"{field} is too long")
    return value


def validate_post(data: dict) -> dict:
    return {
        "ticker": normalize_ticker(data.get("ticker")),
        "company_name": _required(data.get("company_name"), "company_name", 200),
        "headline": _required(data.get("headline"), "headline", 500),
        "research_notes": _required(data.get("research_notes"), "research_notes", 20000),
        "category": _choice(data.get("category"), CATEGORIES, "category"),
        "sentiment": _choice(data.get("sentiment"), SENTIMENTS, "sentiment"),
        "source_name": _text(data.get("source_name"))[:200],
        "source_url": validate_source_url(data.get("source_url")),
        "tradestaar_take": _text(data.get("tradestaar_take"))[:10000],
        "take_origin": _choice(data.get("take_origin") or "manual", TAKE_ORIGINS, "take_origin"),
        "should_notify": 1 if bool(data.get("should_notify")) else 0,
    }


def validate_metric(data: dict, sort_order: int = 0) -> dict:
    metric_type = _choice(data.get("metric_type") or "other", METRIC_TYPES, "metric_type")
    comparison = _choice(data.get("comparison") or "not_applicable", COMPARISONS, "comparison")
    result = {
        "metric_type": metric_type,
        "label": _required(data.get("label"), "metric label", 200),
        "actual_value": data.get("actual_value"),
        "expected_value": data.get("expected_value"),
        "previous_value": data.get("previous_value"),
        "unit": _text(data.get("unit"))[:40],
        "period": _text(data.get("period"))[:100],
        "comparison": comparison,
        "notes": _text(data.get("notes"))[:2000],
        "sort_order": int(data.get("sort_order", sort_order)),
    }
    for field in ("actual_value", "expected_value", "previous_value"):
        value = result[field]
        if value in (None, ""):
            result[field] = None
            continue
        try:
            result[field] = float(value)
        except (TypeError, ValueError):
            raise ResearchValidationError(f"{field} must be numeric")
    return result


def _assert_admin(actor: dict | None) -> int:
    if not actor or not actor.get("is_admin"):
        raise ResearchPermissionError("administrator permission required")
    try:
        return int(actor["id"])
    except (KeyError, TypeError, ValueError):
        raise ResearchPermissionError("authenticated administrator required")


def create_draft(data: dict, actor: dict, metrics: list[dict] | None = None, conn=None) -> str:
    """Create a draft. Provider/AI-originated content is always forced to draft."""
    author_id = _assert_admin(actor)
    clean = validate_post(data)
    post_id = str(uuid4())
    timestamp = _now()
    owns = conn is None
    conn = conn or get_db()
    try:
        conn.execute(
            """INSERT INTO research_posts
            (id,ticker,company_name,headline,research_notes,category,sentiment,
             source_name,source_url,tradestaar_take,take_origin,status,should_notify,
             notification_status,author_user_id,created_at,updated_at,published_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (post_id, clean["ticker"], clean["company_name"], clean["headline"],
             clean["research_notes"], clean["category"], clean["sentiment"],
             clean["source_name"], clean["source_url"], clean["tradestaar_take"],
             clean["take_origin"], "draft", clean["should_notify"], "not_requested",
             author_id, timestamp, timestamp, None),
        )
        for index, raw in enumerate(metrics or []):
            metric = validate_metric(raw, index)
            conn.execute(
                """INSERT INTO research_metrics
                (id,post_id,metric_type,label,actual_value,expected_value,previous_value,
                 unit,period,comparison,notes,sort_order) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid4()), post_id, metric["metric_type"], metric["label"],
                 metric["actual_value"], metric["expected_value"], metric["previous_value"],
                 metric["unit"], metric["period"], metric["comparison"], metric["notes"],
                 metric["sort_order"]),
            )
        conn.commit()
        return post_id
    except Exception:
        conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def publish_post(post_id: str, actor: dict, conn=None) -> None:
    """Explicit admin-only publication boundary. Nothing calls this automatically."""
    _assert_admin(actor)
    owns = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute("SELECT id, status FROM research_posts WHERE id = ?", (post_id,)).fetchone()
        if not row:
            raise ResearchValidationError("research post not found")
        timestamp = _now()
        conn.execute(
            "UPDATE research_posts SET status='published', published_at=?, updated_at=? WHERE id=?",
            (timestamp, timestamp, post_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def list_published(*, ticker: str | None = None, category: str | None = None,
                   sentiment: str | None = None, limit: int = 50, conn=None) -> list[dict]:
    """Return published research only, newest first."""
    clauses = ["status = 'published'"]
    params: list = []
    if ticker:
        clauses.append("ticker = ?")
        params.append(normalize_ticker(ticker))
    if category:
        clauses.append("category = ?")
        params.append(_choice(category, CATEGORIES, "category"))
    if sentiment:
        clauses.append("sentiment = ?")
        params.append(_choice(sentiment, SENTIMENTS, "sentiment"))
    limit = max(1, min(int(limit), 100))
    params.append(limit)
    owns = conn is None
    conn = conn or get_db()
    try:
        rows = conn.execute(
            f"SELECT * FROM research_posts WHERE {' AND '.join(clauses)} "
            "ORDER BY published_at DESC LIMIT ?", tuple(params)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        if owns:
            conn.close()
