"""Stable source-backed metric contract shared by Tradestaar research surfaces.

The SEC snapshot is the sole authority for SEC-reported metrics. This adapter
never substitutes estimates, market quotes or stale fiscal periods.
"""
from __future__ import annotations
from datetime import date, datetime, timezone

METRIC_LABELS = {
    "revenue": "Revenue",
    "net_income": "Net income",
    "assets": "Total assets",
    "liabilities": "Total liabilities",
    "operating_cash_flow": "Operating cash flow",
    "capital_expenditures": "Capital expenditures",
    "gross_profit": "Gross profit",
    "operating_income": "Operating income",
    "cash": "Cash and equivalents",
    "long_term_debt": "Long-term debt (noncurrent)",
    "long_term_debt_current": "Current portion of long-term debt",
    "short_term_borrowings": "Short-term borrowings",
    "interest_expense": "Interest expense",
    "income_tax_expense": "Income tax expense",
    "stockholders_equity": "Stockholders equity",
    "diluted_eps": "Diluted EPS",
}
BALANCE_METRICS = {"assets", "liabilities", "cash", "long_term_debt", "long_term_debt_current", "short_term_borrowings", "stockholders_equity"}
FLOW_METRICS = {"revenue", "net_income", "operating_cash_flow", "capital_expenditures", "gross_profit", "operating_income", "diluted_eps", "interest_expense", "income_tax_expense"}


def _freshness(filed, today=None):
    try:
        filed_date = date.fromisoformat(filed)
    except (ValueError, TypeError):
        return {"status": "unknown", "days_since_filing": None}
    age = ((today or datetime.now(timezone.utc).date()) - filed_date).days
    if age < 0:
        return {"status": "invalid", "days_since_filing": age}
    return {"status": "recent" if age <= 120 else "aging" if age <= 400 else "stale",
            "days_since_filing": age}


def normalize_financial_contract(snapshot, *, today=None):
    """Present the same SEC facts, periods and provenance to every consumer."""
    if not snapshot.get("ok"):
        return {"ok": False, "ticker": snapshot.get("ticker"),
                "status": snapshot.get("status", "unavailable"),
                "metrics": {}, "scoring_eligible": False,
                "error": snapshot.get("error", "SEC evidence unavailable")}
    dashboard = snapshot.get("dashboard") or {}
    raw = dashboard.get("metrics") or {}
    metrics = {}
    for key, label in METRIC_LABELS.items():
        item = raw.get(key) or {}
        latest = item.get("latest") or {}
        has_fact = (
            isinstance(latest.get("value"), (int, float))
            and not isinstance(latest.get("value"), bool)
            and latest.get("unit") == ("USD/shares" if key == "diluted_eps" else "USD")
            and latest.get("period_end")
            and latest.get("filed")
            and latest.get("accession")
            and latest.get("form") in ("10-K", "10-Q")
        )
        freshness = _freshness(latest.get("filed"), today) if has_fact else {
            "status": "unknown", "days_since_filing": None}
        if freshness["status"] == "invalid":
            has_fact = False
        metrics[key] = {
            "label": label, "status": "reported" if has_fact else "missing",
            "value": latest.get("value") if has_fact else None,
            "unit": latest.get("unit") if has_fact else None,
            "period_start": latest.get("start") if has_fact else None,
            "period_end": latest.get("period_end") if has_fact else None,
            "filed": latest.get("filed") if has_fact else None,
            "form": latest.get("form") if has_fact else None,
            "accession": latest.get("accession") if has_fact else None,
            "tag": latest.get("tag") if has_fact else None,
            "period_type": "instant" if key in BALANCE_METRICS else "duration",
            "freshness": freshness,
            "warning": ("Duration start missing; growth comparison blocked"
                        if has_fact and key in FLOW_METRICS and not latest.get("start")
                        else item.get("warning") if has_fact else "No verified SEC fact"),
        }
    return {"ok": True, "schema_version": "1.0", "ticker": snapshot.get("ticker"),
            "cik": snapshot.get("cik"), "status": snapshot.get("status"),
            "source": snapshot.get("source"), "retrieved_at": snapshot.get("retrieved_at"),
            "cache_status": snapshot.get("cache_status"), "metrics": metrics,
            "warnings": snapshot.get("warnings") or [],
            "scoring_eligible": False}
