"""Annual financial statements from Finnhub, used to cross-check EDGAR.

EDGAR is the source of record - it is the filing itself. But the historical
series assembled from XBRL facts have repeatedly come out wrong in ways that are
invisible on the page: quarterly facts selected instead of annual ones, concepts
covering different year sets and then paired by index. This module fetches the
same statements from Finnhub's financials-reported endpoint with freq=annual,
which supplies the period dates directly rather than making us infer them from
XBRL durations.

The two are reconciled rather than one replacing the other. Where they agree,
nothing changes. Where EDGAR has no value, Finnhub fills the gap and the source
is recorded. Where they disagree materially, the discrepancy is reported so it
reaches the page instead of being silently resolved in favour of whichever
source happened to be consulted first.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Disagreements below this are rounding, restatement noise, or presentation
# differences. Above it, something is wrong and a human should see it.
DISCREPANCY_TOLERANCE = 0.02

# XBRL concept names as they appear in Finnhub's `report.ic` block, in priority
# order. These mirror the concept lists the EDGAR path already searches.
CONCEPTS: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet",
    ],
    "gross_profit":     ["GrossProfit", "GrossProfitLoss"],
    "operating_income": ["OperatingIncomeLoss", "OperatingProfit"],
    "net_income":       ["NetIncomeLoss", "NetIncome",
                         "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "diluted_eps":      ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
}


def _concept_value(rows, names) -> float | None:
    """First matching concept value from a Finnhub statement block."""
    by_concept: dict[str, float] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        concept, value = row.get("concept"), row.get("value")
        if concept and isinstance(value, (int, float)) and concept not in by_concept:
            by_concept[str(concept)] = float(value)
    for name in names:
        if name in by_concept:
            return by_concept[name]
    return None


def parse_annual_reports(reports) -> dict[str, dict]:
    """Map Finnhub annual reports to {period_end: {field: value}}.

    Only 10-K style annual reports are kept. Keyed on endDate so the result
    lines up with the fiscal year ends the EDGAR path now produces.
    """
    out: dict[str, dict] = {}
    for report in reports or []:
        if not isinstance(report, dict):
            continue
        end = str(report.get("endDate") or "")[:10]
        if not end:
            continue
        income = (report.get("report") or {}).get("ic") or []
        values = {field: _concept_value(income, names) for field, names in CONCEPTS.items()}
        if any(v is not None for v in values.values()):
            out[end] = values
    return out


def reconcile(field: str, timeline: list, edgar: list, finnhub: dict) -> tuple[list, list]:
    """Merge one EDGAR series with Finnhub's, aligned on ``timeline``.

    Returns (values, notes). EDGAR wins where both agree or where only EDGAR
    has a value. Finnhub fills gaps. Material disagreements keep the EDGAR
    figure - the filing is the record - and are reported so the page can show
    that the two sources do not match.
    """
    values, notes = list(edgar or []), []
    while len(values) < len(timeline):
        values.append(None)
    for index, end in enumerate(timeline):
        theirs = (finnhub.get(end) or {}).get(field)
        ours = values[index] if index < len(values) else None
        if theirs is None:
            continue
        if ours is None:
            values[index] = theirs
            notes.append({"field": field, "period": end, "kind": "filled",
                          "edgar": None, "finnhub": theirs})
            continue
        if ours == 0 or theirs == 0:
            continue
        drift = abs(ours - theirs) / max(abs(ours), abs(theirs))
        if drift > DISCREPANCY_TOLERANCE:
            notes.append({"field": field, "period": end, "kind": "disagreement",
                          "edgar": ours, "finnhub": theirs, "drift": drift})
    return values, notes


def cross_check(raw: dict, reports) -> dict:
    """Reconcile every income-statement series on a raw fundamentals dict.

    Mutates and returns ``raw``, adding ``_source_notes``. Never raises: a
    cross-check failing must not take down a scorecard that was otherwise fine.
    """
    try:
        finnhub = parse_annual_reports(reports)
        if not finnhub:
            return raw
        timeline = list(raw.get("fiscal_period_ends") or [])
        if not timeline:
            return raw
        notes: list[dict] = []
        for field in CONCEPTS:
            merged, field_notes = reconcile(field, timeline, raw.get(field), finnhub)
            raw[field] = merged
            notes.extend(field_notes)
        raw["_source_notes"] = notes
        if notes:
            logger.info("fundamentals cross-check  ticker=%s  filled=%d  disagreements=%d",
                        raw.get("ticker"),
                        sum(1 for n in notes if n["kind"] == "filled"),
                        sum(1 for n in notes if n["kind"] == "disagreement"))
    except Exception as exc:
        logger.warning("fundamentals cross-check failed for %s: %s", raw.get("ticker"), exc)
    return raw
