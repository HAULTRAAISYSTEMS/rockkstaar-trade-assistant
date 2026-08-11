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

CATALYST_EXPLANATIONS = (
    (("guidance", "outlook", "forecast"), "Guidance can change forward estimates before the next reported quarter."),
    (("earnings", "estimates", "revenue", "eps"), "Earnings news can reset growth expectations and near-term valuation."),
    (("upgrade", "downgrade", "price target"), "A rating change can affect positioning, momentum, and near-term demand for shares."),
    (("approval", "fda", "trial", "drug"), "A regulatory or clinical update can materially change the probability of future revenue."),
    (("contract", "partnership", "deal", "acquisition", "merger"), "A major agreement can change the company's revenue path or strategic value."),
    (("lawsuit", "probe", "investigation", "recall"), "Legal or regulatory risk can raise costs and pressure investor confidence."),
    (("offering", "share sale", "dilution"), "New share issuance can dilute existing holders and increase near-term supply."),
    (("buyback", "repurchase", "dividend"), "Capital-return news can change share supply and the market's view of management confidence."),
    (("layoff", "layoffs", "job cuts"), "Workforce changes may signal cost control, weaker demand, or a shift in company priorities."),
)


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


def explain_headline(headline: object, reason: object = "") -> str:
    """Return a short catalyst explanation grounded only in supplied text."""
    text = f"{_clean(headline)} {_clean(reason)}".lower()
    for terms, explanation in CATALYST_EXPLANATIONS:
        if any(term in text for term in terms):
            return explanation
    return "This headline is on the scanner because the live feed classified it as a market-relevant catalyst."


def enrich_news_article(raw: dict, watchlist: list[str] | set[str] | None = None) -> dict:
    """Build the auditable fields used by Elite News Scanner cards."""
    item = dict(raw or {})
    headline = _clean(item.get("headline"))
    ticker = _clean(item.get("ticker")).upper() or "MARKET"
    watch = {str(value).upper() for value in (watchlist or [])}
    scored = score_headline(headline)
    impact = _clean(item.get("impact")).upper() or "MEDIUM"
    base_importance = {"CRITICAL": 94, "HIGH": 82, "MEDIUM": 66, "LOW": 42}.get(impact, 58)
    evidence_bonus = min(6, scored["evidence_count"] * 2)
    watch_bonus = 4 if ticker in watch or bool(item.get("on_watchlist")) else 0
    importance = min(99, base_importance + evidence_bonus + watch_bonus)
    item.update({
        **scored,
        "ticker": ticker,
        "headline": headline,
        "source": _clean(item.get("source")) or "Market feed",
        "impact": impact,
        "importance": importance,
        "on_watchlist": ticker in watch or bool(item.get("on_watchlist")),
        "why_it_matters": explain_headline(headline, item.get("reason")),
        "search_text": " ".join((ticker, headline, _clean(item.get("source")), _clean(item.get("summary")), _clean(item.get("reason")))).lower(),
    })
    return item


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
