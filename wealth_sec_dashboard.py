"""SEC fact presentation: source-first, never invent missing values."""
from datetime import date


def build_dashboard(evidence):
    facts = evidence.get("facts") or {}
    metrics = {}
    for key, entries in facts.items():
        rows = []
        for item in entries:
            try:
                end = date.fromisoformat(str(item["period_end"]))
                filed = date.fromisoformat(str(item["filed"]))
                if filed < end or filed > date.today():
                    continue
                rows.append({**item, "period_end": end.isoformat(), "filed": filed.isoformat()})
            except (KeyError, ValueError, TypeError):
                continue
        rows.sort(key=lambda x: (x["period_end"], x["filed"]), reverse=True)
        latest = rows[0] if rows else None
        previous = next((r for r in rows[1:] if r["period_end"] != latest["period_end"] and r["form"] == latest["form"]), None) if latest else None
        change = None
        if latest and previous and previous["value"] and latest["tag"] == previous["tag"]:
            # Period-length metadata absent: never compare flows as QoQ/YoY.
            if key in {"assets", "liabilities"}:
                change = round((latest["value"] / previous["value"] - 1) * 100, 2)
        metrics[key] = {"status": "reported" if latest else "missing", "latest": latest,
                        "previous": previous, "balance_sheet_change_pct": change,
                        "warning": "Cash-flow and income period durations require verification" if key not in {"assets", "liabilities"} else None}
    return {"ticker": evidence.get("ticker"), "cik": evidence.get("cik"),
            "status": evidence.get("status"), "metrics": metrics,
            "filings": evidence.get("filings") or [], "warnings": evidence.get("warnings") or [],
            "source": "SEC EDGAR XBRL; values as filed, not adjusted",
            "valuation_ready": False}
