"""A long-term holder's thesis, checked against every quarter.

A ten-year holder should not sell because the price fell; they should sell
when the business breaks. "When the business breaks" is easy to decide
calmly and hard to decide in the middle of a 40% drawdown, so the reader
writes the rules down once — why they own it, and which numbers would make
them reconsider — and every quarter the drill says whether any rule tripped.

Rules are thresholds on figures the drill already has. A rule can require a
streak ("revenue shrinking two quarters in a row"), counted across the
quarters the reader has saved for that ticker, because one bad quarter in a
cyclical business is noise and three is a trend.
"""
from __future__ import annotations

import re

from quarter_checks import _num


def _ratio(a, b):
    return a / b if a is not None and b not in (None, 0) else None


def _growth(now, prior):
    return now / prior - 1 if now is not None and prior not in (None, 0) and prior > 0 else None


def metrics(figures: dict) -> dict:
    """The handful of business measures a thesis is written against."""
    f = {k: _num(v) for k, v in (figures or {}).items() if not str(k).startswith("_")}
    rev, cogs = f.get("rev"), f.get("cogs")
    return {
        "rev_growth":   _growth(rev, f.get("revP")),
        "eps_growth":   _growth(f.get("eps"), f.get("epsP")),
        "gross_margin": _ratio(rev - cogs, rev) if rev is not None and cogs is not None else None,
        "op_margin":    _ratio(f.get("opinc"), rev),
        "net_margin":   _ratio(f.get("ni"), rev),
        # Cash over profit only means something when there is profit. A new
        # public company coming out of losses (SpaceX, 2026) can have a tiny
        # or negative year-to-date net income, and the ratio swings to -31×.
        "cash_conv":    (_ratio(f.get("cfo"), f.get("niy"))
                         if (f.get("niy") or 0) > 0 else None),
        "_cash_nm":     f.get("niy") is not None and f.get("niy") <= 0,
        "current":      _ratio(f.get("ca"), f.get("cl")),
        "share_growth": _growth(f.get("sh"), f.get("shP")),
    }


# key: (label, metric, "below"/"above", default threshold, unit, default streak, on by default)
# Percent thresholds are stored as whole percents (35 means 35%).
RULES = {
    "rev_shrinks":  ("Revenue shrinking year over year", "rev_growth", "below", 0, "%", 2, True),
    "eps_shrinks":  ("EPS shrinking year over year", "eps_growth", "below", 0, "%", 2, False),
    "op_margin":    ("Operating margin falls below", "op_margin", "below", 35, "%", 2, True),
    "gross_margin": ("Gross margin falls below", "gross_margin", "below", 55, "%", 2, False),
    "cash_conv":    ("Cash flow ÷ net income below", "cash_conv", "below", 0.8, "×", 2, True),
    "current":      ("Current ratio falls below", "current", "below", 1.2, "×", 1, False),
    "dilution":     ("Share count grows more than", "share_growth", "above", 3, "%", 2, False),
}


def default_thesis() -> dict:
    return {
        "why": "",
        "rules": [{"key": k, "on": spec[6], "value": spec[3], "streak": spec[5]}
                  for k, spec in RULES.items()],
    }


def clean(payload: dict) -> dict:
    """What a client sent, reduced to what a thesis may contain."""
    payload = payload or {}
    by_key = {}
    for r in payload.get("rules") or []:
        if isinstance(r, dict) and r.get("key") in RULES:
            by_key[r["key"]] = r
    rules = []
    for key, spec in RULES.items():
        r = by_key.get(key, {})
        value = _num(r.get("value"))
        try:
            streak = int(r.get("streak", spec[5]))
        except (TypeError, ValueError):
            streak = spec[5]
        rules.append({
            "key": key,
            "on": bool(r.get("on", spec[6])) if key in by_key else spec[6],
            "value": spec[3] if value is None else value,
            "streak": max(1, min(streak, 8)),
        })
    why = str(payload.get("why") or "")[:2000]
    return {"why": why, "rules": rules}


_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def period_key(period: str | None, fallback: str = "") -> str:
    """Sort key for a saved quarter: its period-end date if it names one."""
    m = _DATE.search(period or "")
    return m.group(1) if m else (fallback or "")


def _breached(rule: dict, value) -> bool | None:
    if value is None:
        return None
    label, metric, op, _d, unit, _s, _on = RULES[rule["key"]]
    limit = rule["value"] / 100.0 if unit == "%" else rule["value"]
    return value < limit if op == "below" else value > limit


def _show(value, unit, signed=False):
    if value is None:
        return "—"
    if unit == "%":
        return f"{value * 100:+.1f}%" if signed else f"{value * 100:.1f}%"
    return f"{value:.2f}×"


def evaluate(thesis: dict, current: dict, history: list[dict]) -> dict:
    """Each rule against this quarter, with streaks counted back through history.

    `history` is the reader's earlier saved quarters for the ticker, newest
    first, as figure dicts. Status per rule:
      tripped  — breached for at least `streak` quarters in a row, ending now
      warning  — breached now, streak not yet reached
      ok       — not breached now
      no_data  — this quarter lacks the figures
    """
    thesis = clean(thesis)
    quarters = [metrics(current)] + [metrics(h) for h in (history or [])]
    out = []
    for rule in thesis["rules"]:
        if not rule["on"]:
            continue
        label, metric, op, _d, unit, _s, _on = RULES[rule["key"]]
        now = quarters[0][metric]
        hit = _breached(rule, now)
        run = 0
        for q in quarters:
            if _breached(rule, q[metric]):
                run += 1
            else:
                break
        if hit is None:
            status = "no_data"
        elif not hit:
            status = "ok"
        elif run >= rule["streak"]:
            status = "tripped"
        else:
            status = "warning"
        limit = f"{rule['value']:g}{'%' if unit == '%' else '×'}"
        text = label if rule["key"] in ("rev_shrinks", "eps_shrinks") else f"{label} {limit}"
        if rule["streak"] > 1:
            text += f", {rule['streak']} quarters in a row"
        detail = None
        if status == "warning":
            detail = (f"Breached this quarter ({run} of {rule['streak']}). "
                      "One quarter is not a trend — watch the next report.")
        elif status == "tripped":
            detail = (f"Breached {run} quarters running. Your own rule says "
                      "re-examine the position: is this the cycle, or the business?")
        elif status == "no_data":
            detail = ("Not meaningful this quarter: year-to-date net income is zero or "
                      "negative, so cash ÷ profit has no sensible value."
                      if metric == "cash_conv" and quarters[0].get("_cash_nm")
                      else "This quarter does not have the figures for this rule.")
        out.append({"key": rule["key"], "text": text, "status": status,
                    "value": _show(now, unit, metric.endswith("growth")), "run": run, "detail": detail})
    tripped = sum(1 for r in out if r["status"] == "tripped")
    warning = sum(1 for r in out if r["status"] == "warning")
    return {"why": thesis["why"], "rules": out, "tripped": tripped, "warning": warning,
            "history_quarters": len(history or [])}
