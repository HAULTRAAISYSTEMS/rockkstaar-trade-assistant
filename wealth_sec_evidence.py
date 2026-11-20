"""Read-only SEC filing and XBRL evidence for verified issuer research."""
from __future__ import annotations
from datetime import datetime, timezone
import os
import requests

# SEC registrant CIK mappings; validate against live EDGAR responses before showing facts.
# Alphabet share classes intentionally share one registrant; do not infer ETF identities.
SEC_CIK = {
    "SPCX": "0001181412",
    "KLAC": "0000319201",
    "NVDA": "0001045810",
    "GOOG": "0001652044",
    "GOOGL": "0001652044",
    "V": "0001403161",
}
SEC_BASE = "https://data.sec.gov"
FORMS = {"10-K", "10-Q", "8-K", "20-F", "6-K"}
FACT_TAGS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "net_income": ("NetIncomeLoss",),
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capital_expenditures": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "long_term_debt": ("LongTermDebtNoncurrent",),
    "long_term_debt_current": ("LongTermDebtCurrent",),
    "short_term_borrowings": ("ShortTermBorrowings",),
    "interest_expense": ("InterestExpenseNonOperating", "InterestExpense",),
    "income_tax_expense": ("IncomeTaxExpenseBenefit",),
    "pretax_income": ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",),
    "stockholders_equity": ("StockholdersEquity",),
    "diluted_eps": ("EarningsPerShareDiluted",),
}


def _headers():
    return {"User-Agent": os.environ.get("SEC_EDGAR_USER_AGENT", "TradestaarElite research contact@haultra.ai"),
            "Accept": "application/json"}


def normalize_filings(payload, limit=20):
    recent = (payload or {}).get("filings", {}).get("recent", {})
    forms = recent.get("form") or []
    results = []
    for i, form in enumerate(forms):
        if form not in FORMS:
            continue
        try:
            accession = recent["accessionNumber"][i]
            filed = recent["filingDate"][i]
            document = recent["primaryDocument"][i]
            if not accession or not filed or not document or "/" in document or ".." in document:
                continue
            results.append({"form": form, "filed": filed, "accession": accession,
                            "url": f"https://www.sec.gov/Archives/edgar/data/{int(payload['cik'])}/{accession.replace('-', '')}/{document}"})
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        if len(results) >= limit:
            break
    return results


def normalize_facts(payload):
    facts = (payload or {}).get("facts", {}).get("us-gaap", {})
    result = {}
    for metric, tags in FACT_TAGS.items():
        candidates = []
        for tag in tags:
            for unit, entries in (facts.get(tag, {}).get("units") or {}).items():
                if unit != ("USD/shares" if metric == "diluted_eps" else "USD"):
                    continue
                for row in entries:
                    if row.get("form") not in {"10-K", "10-Q"} or not row.get("filed") or not row.get("end"):
                        continue
                    if not isinstance(row.get("val"), (int, float)):
                        continue
                    candidates.append({"value": row["val"], "period_end": row["end"], "start": row.get("start"),
                                       "filed": row["filed"], "form": row["form"],
                                       "accession": row.get("accn"), "tag": tag, "unit": unit})
        if candidates:
            # Keep source facts and period metadata; do not mix quarterly and annual figures.
            candidates.sort(key=lambda x: (x["filed"], x["period_end"]), reverse=True)
            result[metric] = candidates[:10]
    return result


def fetch_sec_evidence(ticker, *, get=None):
    cik = SEC_CIK.get(str(ticker or "").upper())
    if not cik:
        return {"status": "unsupported", "filings": [], "facts": {}, "warnings": ["No verified CIK mapping"]}
    get = get or requests.get
    output = {"ticker": ticker, "cik": cik, "status": "unavailable", "filings": [], "facts": {}, "warnings": []}
    for kind, url in (("filings", f"{SEC_BASE}/submissions/CIK{cik}.json"),
                      ("facts", f"{SEC_BASE}/api/xbrl/companyfacts/CIK{cik}.json")):
        try:
            response = get(url, headers=_headers(), timeout=8)
            response.raise_for_status()
            if len(response.content) > 8_000_000:
                raise ValueError("SEC payload exceeds limit")
            data = response.json()
            if int(data.get("cik", -1)) != int(cik):
                raise ValueError("SEC CIK mismatch")
            output[kind] = normalize_filings(data) if kind == "filings" else normalize_facts(data)
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            output["warnings"].append(f"{kind}: {type(exc).__name__}")
    output["status"] = "available" if output["filings"] and output["facts"] else "partial" if output["filings"] or output["facts"] else "unavailable"
    return output
