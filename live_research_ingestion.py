"""Provider-assisted Live Research ingestion (Phase 6).

This module turns already-retrieved provider/primary-source facts into admin-reviewable
research suggestions. It has deliberately NO publication, realtime-announcement, or
notification dependency. Every suggestion is persisted through research_feed.create_draft.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlparse

import research_feed as rf


class IngestionError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderItem:
    provider: str
    external_id: str
    ticker: str
    company_name: str
    headline: str
    source_name: str
    source_url: str
    category: str = "Breaking News"
    sentiment: str = "Neutral"
    facts: tuple[str, ...] = ()
    metrics: tuple[dict, ...] = ()
    published_at: str = ""
    source_kind: str = "provider"  # primary | sec | provider
    metadata: dict = field(default_factory=dict)


def _clean_text(value, limit=20000):
    return str(value or "").strip()[:limit]


def _source_host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def is_primary_source(item: ProviderItem) -> bool:
    host = _source_host(item.source_url)
    return item.source_kind in {"primary", "sec"} or host.endswith("sec.gov") or "investor" in host or host.startswith("ir.")


def normalize_item(raw: ProviderItem | dict) -> ProviderItem:
    if isinstance(raw, ProviderItem):
        item = raw
    elif isinstance(raw, dict):
        item = ProviderItem(
            provider=_clean_text(raw.get("provider"), 80),
            external_id=_clean_text(raw.get("external_id"), 300),
            ticker=rf.normalize_ticker(raw.get("ticker")),
            company_name=_clean_text(raw.get("company_name"), 200),
            headline=_clean_text(raw.get("headline"), 500),
            source_name=_clean_text(raw.get("source_name"), 200),
            source_url=rf.validate_source_url(raw.get("source_url")),
            category=_clean_text(raw.get("category") or "Breaking News", 80),
            sentiment=_clean_text(raw.get("sentiment") or "Neutral", 40),
            facts=tuple(_clean_text(x, 2000) for x in (raw.get("facts") or ()) if _clean_text(x)),
            metrics=tuple(raw.get("metrics") or ()),
            published_at=_clean_text(raw.get("published_at"), 100),
            source_kind=_clean_text(raw.get("source_kind") or "provider", 40),
            metadata=dict(raw.get("metadata") or {}),
        )
    else:
        raise IngestionError("provider item must be an object")
    if not item.provider or not item.external_id:
        raise IngestionError("provider and external_id are required")
    if not item.company_name or not item.headline or not item.source_name or not item.source_url:
        raise IngestionError("company, headline and attributed source are required")
    rf._choice(item.category, rf.CATEGORIES, "category")
    rf._choice(item.sentiment, rf.SENTIMENTS, "sentiment")
    rf.validate_source_url(item.source_url)
    if not item.facts:
        raise IngestionError("at least one verified fact is required")
    for i, metric in enumerate(item.metrics):
        rf.validate_metric(metric, i)
    return item


def fingerprint(item: ProviderItem | dict) -> str:
    item = normalize_item(item)
    # Prefer stable provider identity; include source URL so provider ID collisions cannot merge sources.
    raw = "|".join((item.provider.lower(), item.external_id, item.source_url)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _marker(fp: str) -> str:
    return f"[ingestion:{fp}]"


def already_ingested(item: ProviderItem | dict, conn) -> bool:
    fp = fingerprint(item)
    row = conn.execute("SELECT id FROM research_posts WHERE research_notes LIKE ? LIMIT 1", (f"%{_marker(fp)}%",)).fetchone()
    return bool(row)


def _comparison(actual, expected):
    if actual is None or expected is None:
        return "not_applicable"
    if actual > expected:
        return "beat"
    if actual < expected:
        return "miss"
    return "inline"


def earnings_metrics(payload: dict) -> list[dict]:
    """Extract only explicitly supplied earnings facts. Missing values remain missing."""
    if not isinstance(payload, dict):
        raise IngestionError("earnings payload must be an object")
    out = []
    specs = (
        ("revenue", "Revenue", "revenue_actual", "revenue_estimate", "revenue_previous", payload.get("revenue_unit", "USD")),
        ("eps", "EPS", "eps_actual", "eps_estimate", "eps_previous", payload.get("eps_unit", "USD/share")),
    )
    for kind, label, akey, ekey, pkey, unit in specs:
        actual, expected, previous = payload.get(akey), payload.get(ekey), payload.get(pkey)
        if actual is None and expected is None and previous is None:
            continue
        metric = {"metric_type": kind, "label": label, "actual_value": actual, "expected_value": expected,
                  "previous_value": previous, "unit": unit, "period": payload.get("period", ""),
                  "comparison": "not_applicable", "notes": "Verified reported/estimate values; blanks were not inferred."}
        clean = rf.validate_metric(metric, len(out))
        clean["comparison"] = _comparison(clean["actual_value"], clean["expected_value"])
        out.append(clean)
    for extra in payload.get("metrics") or []:
        out.append(rf.validate_metric(extra, len(out)))
    return out


def create_suggestion(item: ProviderItem | dict, actor: dict, conn, *, take_provider=None) -> dict:
    """Create one idempotent provider suggestion. Always draft; never announces/notifies."""
    rf._assert_admin(actor)
    item = normalize_item(item)
    fp = fingerprint(item)
    if already_ingested(item, conn):
        return {"status": "duplicate", "fingerprint": fp, "post_id": None}
    notes = "\n".join(f"- {fact}" for fact in item.facts)
    notes += f"\n\nSource: {item.source_name} — {item.source_url}\n{_marker(fp)}"
    post = {
        "ticker": item.ticker,
        "company_name": item.company_name,
        "headline": item.headline,
        "research_notes": notes,
        "category": item.category,
        "sentiment": item.sentiment,
        "source_name": item.source_name,
        "source_url": item.source_url,
        "tradestaar_take": "",
        "take_origin": "provider",
        "should_notify": False,
    }
    post_id = rf.create_draft(post, actor, metrics=list(item.metrics), conn=conn)
    result = {"status": "draft", "post_id": post_id, "fingerprint": fp, "primary_source": is_primary_source(item)}
    # Optional AI is intentionally a separate draft. Failure never changes/publishes provider draft.
    if take_provider is not None:
        import tradestaar_take
        verified = {
            "ticker": item.ticker, "company_name": item.company_name, "headline": item.headline,
            "research_notes": notes, "category": item.category, "sentiment": item.sentiment,
            "metrics": list(item.metrics),
            "sources": [{"id": fp, "label": item.source_name, "url": item.source_url, "facts": list(item.facts)}],
        }
        try:
            result["take"] = tradestaar_take.generate_take_draft(verified, actor, take_provider, conn=conn)
        except Exception as exc:
            result["take_error"] = type(exc).__name__
    return result


def ingest(items: Iterable[ProviderItem | dict], actor: dict, conn, *, take_provider=None) -> dict:
    summary = {"created": 0, "duplicates": 0, "skipped": 0, "errors": []}
    for raw in items:
        try:
            result = create_suggestion(raw, actor, conn, take_provider=take_provider)
            if result["status"] == "duplicate": summary["duplicates"] += 1
            else: summary["created"] += 1
        except Exception as exc:
            summary["skipped"] += 1
            summary["errors"].append(type(exc).__name__)
    return summary


def finnhub_articles_to_items(ticker: str, company_name: str, articles: Iterable[dict]) -> list[ProviderItem]:
    """Adapt existing Finnhub/news_fetcher article metadata without making new provider calls."""
    ticker = rf.normalize_ticker(ticker)
    out = []
    for row in articles or []:
        if not isinstance(row, dict):
            continue
        headline = _clean_text(row.get("headline"), 500)
        url = _clean_text(row.get("url"), 2000)
        summary = _clean_text(row.get("summary"), 5000)
        if not headline or not url or not summary:
            continue
        source = _clean_text(row.get("source") or "Finnhub", 200)
        external_id = _clean_text(row.get("id") or row.get("datetime") or url, 300)
        out.append(ProviderItem("finnhub", external_id, ticker, company_name, headline, source, url,
                                facts=(summary,), published_at=_clean_text(row.get("published_at") or row.get("datetime"), 100)))
    return out
