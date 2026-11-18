"""Seasonal Wealth Planner: contributions by month and portfolio, against goals
and IRA limits.

Nothing here is personal: the plan is whatever the investor enters. IRA
limits live in IRA_LIMITS with the IRS notice they come from; a year not in
the table shows no limit rather than a guessed one.

IRA timing rule: a contribution made from January 1 to the tax-filing
deadline (normally April 15) can be designated for the prior tax year. The
plan lets each IRA deposit name its tax year, and progress counts deposits
by that year.
"""
from __future__ import annotations

from datetime import date

# year: (limit, catch-up for age 50+, source)
IRA_LIMITS = {
    2025: (7000.0, 1000.0, "IRS Notice 2024-80"),
    2026: (7500.0, 1100.0, "IRS Notice 2025-67"),
    2027: (7500.0, 1100.0, "IRS Notice 2026-66 / IR-2026-136 (Nov 12, 2026)"),
}
IRA_KINDS = ("roth_ira", "traditional_ira")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
SEASONS = {"Winter": (1, 2, 3), "Spring": (4, 5, 6), "Summer": (7, 8, 9), "Fall": (10, 11, 12)}


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def ira_limit(year: int, age_50_plus: bool = False) -> dict | None:
    row = IRA_LIMITS.get(int(year))
    if not row:
        return None
    base, catch, src = row
    return {"limit": base + (catch if age_50_plus else 0.0), "base": base, "catch_up": catch, "source": src}


def prior_year_window(d: date) -> bool:
    """True when a deposit on d may still count toward the previous tax year."""
    return d.month < 4 or (d.month == 4 and d.day <= 15)


def clean_plan(d: dict, year: int, portfolio_ids: set[int]) -> dict:
    entries = []
    for e in (d.get("entries") or [])[:120]:
        try:
            pid = int(e.get("portfolio_id"))
        except (TypeError, ValueError):
            continue
        if pid not in portfolio_ids:
            continue
        amt = _f(e.get("amount"))
        if not amt or amt <= 0:
            continue
        kind = e.get("kind") if e.get("kind") in ("monthly", "one_time") else "monthly"
        months = sorted({int(m) for m in (e.get("months") or []) if str(m).isdigit() and 1 <= int(m) <= 12})
        if not months:
            continue
        if kind == "one_time":
            months = months[:1]
        ty = e.get("tax_year")
        ty = int(ty) if str(ty).isdigit() and int(ty) in (year - 1, year) else year
        entries.append({"portfolio_id": pid, "kind": kind, "months": months, "amount": round(amt, 2),
                        "label": str(e.get("label") or "")[:60], "tax_year": ty})
    goals = {}
    for k, v in (d.get("goals") or {}).items():
        if str(k).isdigit() and int(k) in portfolio_ids and _f(v) and _f(v) > 0:
            goals[str(int(k))] = round(_f(v), 2)
    targets = {}
    for k, v in (d.get("targets") or {}).items():
        if str(k).isdigit() and int(k) in portfolio_ids and _f(v) is not None and 0 <= _f(v) <= 100:
            targets[str(int(k))] = round(_f(v), 1)
    reserve = _f(d.get("cash_reserve"))
    return {"entries": entries, "goals": goals, "targets": targets,
            "cash_reserve": reserve if reserve and reserve > 0 else None,
            "age_50_plus": bool(d.get("age_50_plus"))}


def schedule(plan: dict, year: int, portfolios: list[dict], actual: list[dict], today: date | None = None) -> dict:
    """Month-by-month plan vs what was actually deposited.

    `actual`: DEPOSIT transactions [{portfolio_id, date, amount, tax_year}].
    """
    today = today or date.today()
    pmap = {p["id"]: p for p in portfolios}
    grid = {pid: [0.0] * 12 for pid in pmap}
    ira_planned = {}                       # tax_year -> amount across all IRAs
    for e in plan.get("entries") or []:
        pid = e["portfolio_id"]
        if pid not in grid:
            continue
        for m in e["months"]:
            grid[pid][m - 1] += e["amount"]
            if pmap[pid].get("kind") in IRA_KINDS:
                ira_planned[e["tax_year"]] = ira_planned.get(e["tax_year"], 0.0) + e["amount"]

    done = {pid: [0.0] * 12 for pid in pmap}
    ira_actual = {}
    for t in actual:
        pid = t["portfolio_id"]
        try:
            d = date.fromisoformat(str(t["date"])[:10])
        except ValueError:
            continue
        amt = _f(t.get("amount")) or 0.0
        if pid in done and d.year == year:
            done[pid][d.month - 1] += amt
        if pid in pmap and pmap[pid].get("kind") in IRA_KINDS:
            ty = t.get("tax_year") or d.year
            ira_actual[int(ty)] = ira_actual.get(int(ty), 0.0) + amt

    rows = []
    for pid, months in grid.items():
        p = pmap[pid]
        planned_total = sum(months)
        actual_total = sum(done[pid])
        goal = _f((plan.get("goals") or {}).get(str(pid)))
        due = sum(months[: today.month]) if today.year == year else (planned_total if today.year > year else 0.0)
        rows.append({"portfolio_id": pid, "name": p["name"], "kind": p.get("kind"),
                     "planned": months, "actual": done[pid], "planned_total": planned_total,
                     "actual_total": actual_total, "goal": goal,
                     "goal_pct": (actual_total / goal * 100) if goal else None,
                     "due_to_date": due, "behind_by": max(due - actual_total, 0.0),
                     "target_pct": _f((plan.get("targets") or {}).get(str(pid)))})

    ira = []
    for ty in sorted({year - 1, year} | set(ira_planned)):
        lim = ira_limit(ty, plan.get("age_50_plus"))
        planned = ira_planned.get(ty, 0.0)
        actual_ty = ira_actual.get(ty, 0.0)
        warn = []
        if lim and planned > lim["limit"] + 0.005:
            warn.append(f"The plan puts ${planned:,.0f} toward {ty}, over the ${lim['limit']:,.0f} IRA limit.")
        if lim and actual_ty > lim["limit"] + 0.005:
            warn.append(f"Deposits counted toward {ty} total ${actual_ty:,.0f}, over the ${lim['limit']:,.0f} limit. "
                        f"Ask the broker about removing the excess before the filing deadline.")
        if not lim and (planned or actual_ty):
            warn.append(f"The {ty} IRA limit is not in this app's table yet; check IRS.gov.")
        if planned or actual_ty or ty == year:
            ira.append({"tax_year": ty, "planned": planned, "actual": actual_ty, "limit": lim,
                        "room": (lim["limit"] - actual_ty) if lim else None, "warnings": warn})

    seasons = {name: sum(sum(r["planned"][m - 1] for m in ms) for r in rows) for name, ms in SEASONS.items()}
    return {"year": year, "rows": rows, "ira": ira, "seasons": seasons,
            "planned_total": sum(r["planned_total"] for r in rows),
            "actual_total": sum(r["actual_total"] for r in rows),
            "months": list(MONTHS), "current_month": today.month if today.year == year else None,
            "prior_year_window_open": today.year == year and prior_year_window(today)}
