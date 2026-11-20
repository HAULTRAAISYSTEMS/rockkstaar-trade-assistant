"""Deterministic SEC financial explanations grounded in comparable filing facts.

No LLM guesses, unsupported causal attribution, or investment scores.
"""
from datetime import date


def _period_days(row):
    try:
        return (date.fromisoformat(row["period_end"]) - date.fromisoformat(row["start"])).days + 1
    except (KeyError, ValueError, TypeError):
        return None


def compare_periods(facts):
    comparisons = {}
    for metric, entries in (facts or {}).items():
        rows = [r for r in entries if isinstance(r.get("value"), (int, float))
                and r.get("period_end") and r.get("filed") and r.get("tag")]
        rows.sort(key=lambda r: (r["period_end"], r["filed"]), reverse=True)
        if not rows:
            continue
        latest = rows[0]
        duration = _period_days(latest)
        comparable = []
        for previous in rows[1:]:
            if previous["period_end"] == latest["period_end"]:
                continue
            if previous["tag"] != latest["tag"] or previous.get("unit") != latest.get("unit"):
                continue
            if metric not in {"assets", "liabilities"}:
                prior_days = _period_days(previous)
                if duration is None or prior_days is None or abs(duration-prior_days) > 7:
                    continue
            comparable.append(previous)
        if not comparable:
            comparisons[metric] = {"status": "not_comparable", "latest": latest,
                                   "reason": "No matching source tag, units and reporting duration"}
            continue
        previous = comparable[0]
        if previous["value"] == 0:
            comparisons[metric] = {"status": "not_comparable", "latest": latest,
                                   "reason": "Prior period value is zero"}
            continue
        delta = latest["value"]-previous["value"]
        pct = round(delta/abs(previous["value"])*100, 2)
        comparisons[metric] = {"status": "comparable", "latest": latest, "previous": previous,
                               "delta": delta, "change_pct": pct,
                               "comparison": "reported balance comparison" if metric in {"assets", "liabilities"} else "same-duration reported periods"}
    return comparisons


def explain_changes(comparisons):
    """Evidence-only narrative. An observed correlation is not a cause."""
    output = []
    for metric, row in comparisons.items():
        if row["status"] != "comparable":
            output.append({"metric": metric, "text": "A reliable period comparison is unavailable.",
                           "reason": row["reason"], "evidence": [row["latest"]]})
            continue
        direction = "increased" if row["delta"] > 0 else "decreased" if row["delta"] < 0 else "was unchanged"
        output.append({"metric": metric,
                       "text": f"{metric.replace('_', ' ').capitalize()} {direction} by {abs(row['change_pct']):.2f}% across the selected reported periods. The filings establish the change, not its cause.",
                       "evidence": [row["latest"], row["previous"]],
                       "change_pct": row["change_pct"]})
    return {"explanations": output, "causal_claims_verified": False,
            "mcff_scoring_enabled": False,
            "warning": "No causal explanation or valuation score is inferred from SEC facts alone."}
