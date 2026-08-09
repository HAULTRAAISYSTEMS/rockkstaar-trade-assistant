"""Explainable news-sentiment scoring for Tradestaar Elite.

This module deliberately uses only headline text already supplied by the
application's configured news feeds.  Scores describe coverage tone; they are
not analyst ratings, price forecasts, or evidence of social-media activity.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import re


BULLISH_TERMS = {
    "beat estimates": 3, "raises guidance": 3, "raised guidance": 3,
    "upgrade": 2, "price target raised": 2, "approval": 2, "approved": 2,
    "record revenue": 2, "buyback": 2, "wins contract": 2, "partnership": 1,
    "growth": 1, "surge": 1, "rally": 1,
}
BEARISH_TERMS = {
    "missed estimates": 3, "cuts guidance": 3, "cut guidance": 3,
    "downgrade": 2, "price target cut": 2, "investigation": 2, "lawsuit": 2,
    "recall": 2, "warning": 2, "offering": 2, "layoffs": 1,
    "decline": 1, "falls": 1, "probe": 1,
}


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def score_headline(headline: object) -> dict:
    """Return a bounded, auditable tone score for one headline."""
    text = _clean(headline)
    lowered = text.lower()
    positive = [(term, weight) for term, weight in BULLISH_TERMS.items() if term in lowered]
    negative = [(term, weight) for term, weight in BEARISH_TERMS.items() if term in lowered]
    positive_weight = sum(weight for _, weight in positive)
    negative_weight = sum(weight for _, weight in negative)
    total = positive_weight + negative_weight
    score = round(100 * (positive_weight - negative_weight) / total) if total else 0
    label = "BULLISH" if score >= 20 else "BEARISH" if score <= -20 else "NEUTRAL"
    return {
        "score": score,
        "label": label,
        "bullish_terms": [term for term, _ in positive],
        "bearish_terms": [term for term, _ in negative],
        "evidence_count": total,
    }


def build_sentiment_snapshot(news: list[dict] | None, watchlist: list[str] | None = None) -> dict:
    """Aggregate headline tone by ticker and source without inventing coverage."""
    watch = {str(t).upper() for t in (watchlist or [])}
    articles = []
    grouped: dict[str, list[dict]] = defaultdict(list)
    sources: dict[str, int] = defaultdict(int)

    for raw in news or []:
        headline = _clean(raw.get("headline"))
        if not headline:
            continue
        ticker = _clean(raw.get("ticker")).upper() or "MARKET"
        source = _clean(raw.get("source")) or "Market feed"
        scored = score_headline(headline)
        article = {
            **raw, **scored, "headline": headline, "ticker": ticker,
            "source": source, "on_watchlist": ticker in watch,
        }
        articles.append(article)
        grouped[ticker].append(article)
        sources[source] += 1

    tickers = []
    for ticker, rows in grouped.items():
        score = round(sum(row["score"] for row in rows) / len(rows))
        evidence = sum(row["evidence_count"] for row in rows)
        tickers.append({
            "ticker": ticker,
            "score": score,
            "label": "BULLISH" if score >= 20 else "BEARISH" if score <= -20 else "NEUTRAL",
            "articles": len(rows),
            "confidence": min(100, round((len(rows) * 18) + (evidence * 7))),
            "on_watchlist": ticker in watch,
        })
    tickers.sort(key=lambda row: (-row["articles"], -abs(row["score"]), row["ticker"]))

    overall = round(sum(row["score"] for row in articles) / len(articles)) if articles else 0
    return {
        "overall_score": overall,
        "overall_label": "BULLISH" if overall >= 20 else "BEARISH" if overall <= -20 else "NEUTRAL",
        "article_count": len(articles),
        "tickers": tickers,
        "articles": articles,
        "sources": sorted(
            ({"name": name, "articles": count} for name, count in sources.items()),
            key=lambda row: (-row["articles"], row["name"]),
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "Deterministic weighted phrase matching on cached news headlines.",
    }
