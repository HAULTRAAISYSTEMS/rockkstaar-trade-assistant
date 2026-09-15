"""Rank watchlist research into a quiet, thesis-aware "What changed?" read.

This module deliberately contains no database or network access.  The command
centre supplies published research and the user's memory cards, and these pure
functions explain why an item is material.  That keeps the decision inspectable
and makes a missing provider fail closed instead of manufacturing a headline.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re


PRIMARY_HOSTS = ("sec.gov", "investor.", "investors.", "ir.")
PRIMARY_NAMES = ("sec", "investor relations", "company filing", "company release")
ESTABLISHED_NAMES = (
    "reuters", "associated press", "ap news", "bloomberg", "financial times",
    "wall street journal", "wsj", "cnbc", "marketwatch", "barron's",
)
FILING_CATALYSTS = {"10-K", "10-Q", "8-K", "20-F", "6-K", "FILING"}
DECISION_CATALYSTS = FILING_CATALYSTS | {"EARNINGS", "GUIDANCE", "BREAKING", "M&A"}


def _time(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _headline_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def source_quality(post: dict) -> dict:
    """Return a conservative source tier; unknown sources never rank upward."""
    name = str(post.get("source_name") or "").strip()
    source = name.lower()
    url = str(post.get("source_url") or "").lower()
    catalyst = str(post.get("catalyst_type") or "").upper()
    is_primary = (
        any(token in url for token in PRIMARY_HOSTS)
        or any(token == source or token in source for token in PRIMARY_NAMES)
        or ("sec.gov" in url and catalyst in FILING_CATALYSTS)
    )
    if is_primary:
        return {"rank": 3, "key": "primary", "label": "Primary source"}
    if any(token in source for token in ESTABLISHED_NAMES):
        return {"rank": 2, "key": "established", "label": "Established reporting"}
    return {"rank": 1, "key": "connected", "label": "Connected source"}


def _latest_theses(cards: list[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for card in sorted(cards or [], key=lambda row: _time(row.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
        ticker = str(card.get("ticker") or "").upper()
        if ticker and ticker not in result:
            result[ticker] = card
    return result


def _score(post: dict, quality: dict, thesis: dict | None) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    priority = str(post.get("priority") or "").title()
    catalyst = str(post.get("catalyst_type") or "").upper()
    if priority == "Critical":
        score += 4; reasons.append("critical priority")
    elif priority == "High":
        score += 3; reasons.append("high priority")
    elif priority == "Medium":
        score += 1
    if catalyst in FILING_CATALYSTS:
        score += 3; reasons.append("new filing")
    elif catalyst in {"EARNINGS", "GUIDANCE", "M&A"}:
        score += 3; reasons.append(catalyst.lower().replace("m&a", "company transaction"))
    elif catalyst == "BREAKING":
        score += 2; reasons.append("breaking development")
    if quality["rank"] == 3:
        score += 2; reasons.append("primary evidence")
    elif quality["rank"] == 2:
        score += 1
    if thesis:
        score += 2; reasons.append("matches saved thesis")
    if post.get("metrics"):
        score += 1; reasons.append("reported metrics")
    return score, reasons


def build_digest(posts: list[dict], cards: list[dict], *, reviewed_at="", now=None,
                 limit: int = 6) -> dict:
    """Build the new, material portion of a user's watchlist research feed.

    First use shows the last seven days.  Thereafter the explicit review time
    is the checkpoint. Items with no trustworthy timestamp are omitted because
    calling an undated story "new" would create exactly the alert noise this
    feature is intended to remove.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    checkpoint = _time(reviewed_at) or (now - timedelta(days=7))
    theses = _latest_theses(cards)
    seen: set[tuple[str, str]] = set()
    items = []

    for post in posts or []:
        ticker = str(post.get("ticker") or "").upper()
        headline = str(post.get("headline") or "").strip()
        published = _time(post.get("source_published_at") or post.get("published_at") or post.get("updated_at"))
        key = (ticker, _headline_key(headline))
        if not ticker or not headline or not published or published <= checkpoint or key in seen:
            continue
        seen.add(key)
        quality = source_quality(post)
        thesis = theses.get(ticker)
        score, reasons = _score(post, quality, thesis)
        if score < 5:
            continue
        impact = str((thesis or {}).get("thesis_impact") or "").lower()
        alert = bool(thesis and score >= 7)
        items.append({
            "id": post.get("id"), "ticker": ticker, "headline": headline,
            "company_name": post.get("company_name") or "",
            "source_name": post.get("source_name") or quality["label"],
            "source_url": post.get("source_url") or "",
            "published_at": published.isoformat(), "published_sort": published.timestamp(),
            "priority": post.get("priority") or "", "catalyst_type": post.get("catalyst_type") or "Research",
            "quality": quality, "score": score, "reasons": reasons,
            "why": post.get("tradestaar_take") or post.get("research_notes") or "",
            "thesis": thesis, "thesis_impact": impact, "alert": alert,
        })

    items.sort(key=lambda row: (-int(row["alert"]), -row["score"], -row["published_sort"]))
    material_count = len(items)
    return {
        "items": items[:max(1, min(int(limit), 12))],
        "material_count": material_count,
        "alert_count": sum(1 for row in items if row["alert"]),
        "reviewed_at": str(reviewed_at or ""),
        "checkpoint": checkpoint.isoformat(),
        "first_run": not bool(_time(reviewed_at)),
        "tracked_theses": len(theses),
    }
