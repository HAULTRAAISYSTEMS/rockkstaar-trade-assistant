"""What the data sources actually return for a ticker.

Three times tonight a margin series was diagnosed by reading code and reasoning
about what EDGAR probably contains. Three times that was wrong. Neither the
laptop nor the cloud sandbox can reach data.sec.gov, so the only place the
question can be answered is the running app.

This inspects one ticker and reports every XBRL fact for the income-statement
concepts - period start, end, duration in days, form, value - and whether the
annual-period filter kept it and whether the de-duplicator picked it. It also
reports whether Finnhub's annual endpoint is available or premium-gated, which
determines whether the cross-check can fill gaps at all.

Read-only. No caching, no scoring, no writes.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

INCOME_CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
                "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet"],
    "gross_profit": ["GrossProfit", "GrossProfitLoss"],
    "operating_income": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities",
                         "OperatingProfit"],
    "net_income": ["NetIncomeLoss", "NetIncome",
                   "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "diluted_eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
}


def inspect_edgar(ticker: str) -> dict:
    """Every candidate fact per concept, with the filter's verdict on each."""
    import fundamentals_engine as fe

    out: dict = {"ticker": ticker.upper(), "concepts": {}, "error": None}
    cik, name = fe._edgar_cik(ticker.upper())
    if not cik:
        out["error"] = f"{ticker.upper()} not found in the SEC ticker directory"
        return out
    out["cik"], out["company_name"] = cik, name

    try:
        resp = fe._req_module.get(fe._EDGAR_FACTS_URL.format(cik=cik),
                                  timeout=30, headers=fe._EDGAR_HEADERS)
        if resp.status_code != 200:
            out["error"] = f"EDGAR returned HTTP {resp.status_code}"
            return out
        facts = resp.json()
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    namespaces = [ns for ns in (facts.get("facts", {}).get("us-gaap"),
                                facts.get("facts", {}).get("ifrs-full")) if ns]

    for field, names in INCOME_CONCEPTS.items():
        found = None
        for ns in namespaces:
            for concept_name in names:
                if concept_name in ns:
                    found = (concept_name, ns[concept_name])
                    break
            if found:
                break
        if not found:
            out["concepts"][field] = {"concept": None, "facts": [],
                                      "note": "no matching concept in any namespace"}
            continue

        concept_name, concept = found
        units = concept.get("units", {})
        values = units.get("USD") or units.get("USD/shares") or []
        rows = []
        for fact in values:
            form, end = fact.get("form"), fact.get("end")
            if form not in ("10-K", "20-F", "40-F") or not end or "val" not in fact:
                continue
            days = fe._period_days(fact)
            rows.append({
                "start": fact.get("start"), "end": end, "days": days,
                "form": form, "accn": fact.get("accn"), "value": fact.get("val"),
                "kept_by_filter": fe._is_annual_period(fact),
            })
        rows.sort(key=lambda r: (r["end"], -r["days"]), reverse=True)

        # Which fact the de-duplicator would actually select per period end.
        chosen: dict = {}
        for row in rows:
            if not row["kept_by_filter"]:
                continue
            incumbent = chosen.get(row["end"])
            if incumbent is None or (row["days"], str(row["accn"])) > (
                    incumbent["days"], str(incumbent["accn"])):
                chosen[row["end"]] = row
        for row in rows:
            row["selected"] = chosen.get(row["end"]) is row

        out["concepts"][field] = {
            "concept": concept_name,
            "total_annual_form_facts": len(rows),
            "kept_by_filter": sum(1 for r in rows if r["kept_by_filter"]),
            "distinct_periods_selected": len(chosen),
            "facts": rows[:40],
        }
    return out


def inspect_finnhub(ticker: str) -> dict:
    """Whether the annual endpoint is usable, and what it covers."""
    out = {"available": None, "reports": 0, "period_ends": [], "note": ""}
    try:
        from finnhub_ttm import fetch_finnhub_annual
        from finnhub_annual import parse_annual_reports
        reports = fetch_finnhub_annual(ticker.upper())
        if reports is None:
            out.update(available=False, note="request failed or no API key configured")
            return out
        if not reports:
            out.update(available=False,
                       note="empty - the endpoint is premium-gated on this plan, "
                            "so the cross-check cannot fill gaps")
            return out
        parsed = parse_annual_reports(reports)
        out.update(available=True, reports=len(reports),
                   period_ends=sorted(parsed, reverse=True),
                   fields={end: {k: v for k, v in row.items() if v is not None}
                           for end, row in list(parsed.items())[:6]})
    except Exception as exc:
        out.update(available=False, note=f"{type(exc).__name__}: {exc}")
    return out


def inspect(ticker: str) -> dict:
    return {"edgar": inspect_edgar(ticker), "finnhub": inspect_finnhub(ticker)}
