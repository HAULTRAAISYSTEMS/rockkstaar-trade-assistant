"""Evidence-first intelligence for Discover, Verify and M.C.F.F.

This module never changes existing M.C.F.F. scoring or generates buy signals.
"""
from datetime import date


def assess(contract):
    metrics = contract.get("metrics") or {}
    derived = contract.get("derived_metrics") or {}
    issues, positives, risks = [], [], []
    if not contract.get("ok"):
        return {"eligible": False, "integrity": "unavailable", "issues": ["SEC issuer evidence unavailable"],
                "discover": [], "verify": [], "mcff": {"eligible": False, "evidence": [], "risks": []}}
    essential = ("revenue", "net_income", "operating_cash_flow", "assets", "liabilities")
    for key in essential:
        row = metrics.get(key) or {}
        if row.get("status") != "reported":
            issues.append(key + ": missing verified SEC fact")
        elif (row.get("freshness") or {}).get("status") in ("stale", "invalid", "unknown"):
            issues.append(key + ": filing freshness not confirmed")
        if row.get("warning"):
            issues.append(key + ": " + row["warning"])
    for key in ("free_cash_flow", "gross_margin_pct", "operating_margin_pct", "net_margin_pct"):
        row = derived.get(key) or {}
        if row.get("status") != "calculated":
            issues.append(key + ": cannot calculate with matching reporting periods")
            continue
        value = row["value"]
        label = key.replace("_", " ")
        if value < 0:
            risks.append(label + " is negative (" + str(value) + ")")
        else:
            positives.append(label + " is nonnegative (" + str(value) + ")")
    debt = derived.get("identified_debt_components") or {}
    if debt.get("status") != "calculated":
        issues.append("Debt components incomplete; total debt cannot be verified")
    roic = derived.get("roic_proxy_pct") or {}
    if roic.get("status") != "calculated":
        issues.append("ROIC proxy unavailable or missing required inputs")
    integrity = "complete" if not issues else "partial"
    # Discover observations are not screens or rankings until coverage is sufficient.
    eligible = not issues
    return {
        "eligible": eligible, "integrity": integrity, "issues": issues,
        "discover": positives if eligible else [],
        "verify": [{"type": "risk", "text": x} for x in risks] +
                  [{"type": "data_gap", "text": x} for x in issues],
        "mcff": {"eligible": eligible, "evidence": positives if eligible else [],
                 "risks": risks, "note": "Supplemental SEC observations only; existing M.C.F.F. score is unchanged"},
        "scoring_eligible": False,
    }
