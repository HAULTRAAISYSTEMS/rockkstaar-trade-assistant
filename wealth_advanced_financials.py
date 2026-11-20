"""Conservative derived financial metrics; never combine mismatched fiscal periods."""
from wealth_financial_contract import normalize_financial_contract


def _compatible(metrics, keys, *, duration=False):
    rows = [metrics.get(key) or {} for key in keys]
    if any(row.get("status") != "reported" or row.get("value") is None for row in rows):
        return None
    periods = {(row.get("period_start") if duration else None, row.get("period_end"),
                row.get("form")) for row in rows}
    if len(periods) != 1:
        return None
    if duration and any(not row.get("period_start") for row in rows):
        return None
    return rows


def derive_metrics(contract):
    metrics = contract.get("metrics") or {}
    derived = {}
    formulas = {
        "free_cash_flow": (("operating_cash_flow", "capital_expenditures"), True,
                           "Operating cash flow - capital expenditures"),
        "gross_margin_pct": (("gross_profit", "revenue"), True, "Gross profit / revenue * 100"),
        "operating_margin_pct": (("operating_income", "revenue"), True, "Operating income / revenue * 100"),
        "net_margin_pct": (("net_income", "revenue"), True, "Net income / revenue * 100"),
        "debt_to_equity": (("long_term_debt", "stockholders_equity"), False,
                           "Noncurrent long-term debt / stockholders equity"),
    }
    for key, (inputs, duration, formula) in formulas.items():
        rows = _compatible(metrics, inputs, duration=duration)
        result = {"status": "unavailable", "value": None, "formula": formula,
                  "inputs": list(inputs), "warning": "Missing or mismatched SEC reporting periods",
                  "period_end": None, "unit": None}
        if rows:
            a, b = (r["value"] for r in rows)
            if key == "free_cash_flow":
                value = a - abs(b)
            elif b != 0:
                value = a / b * (100 if key.endswith("_pct") else 1)
            else:
                value = None
            if value is not None:
                result.update(status="calculated", value=round(value, 4),
                              period_end=rows[0]["period_end"],
                              period_start=rows[0]["period_start"],
                              unit="USD" if key == "free_cash_flow" else "%" if key.endswith("_pct") else "ratio",
                              warning=None)
        derived[key] = result
    return derived


def advanced_financial_snapshot(snapshot):
    contract = normalize_financial_contract(snapshot)
    if contract.get("ok"):
        contract["derived_metrics"] = derive_metrics(contract)
    return contract
