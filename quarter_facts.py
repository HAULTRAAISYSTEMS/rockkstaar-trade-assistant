"""
quarter_facts.py — the answer key for the seven-check drill.

The drill's value is that the reader pulls the figures out of the 10-Q
themselves. That is also its weakness: nothing tells them whether they pulled
the right lines. This fetches the same quarter from SEC EDGAR's XBRL company
facts and reports, field by field, whether what was typed matches what the
company filed — and which tag the comparison used, so a disagreement can be
argued with rather than merely obeyed.

What "the same quarter" means
─────────────────────────────
Income-statement and cash-flow concepts are durations; balance-sheet concepts
are instants. A 10-Q reports roughly ninety days for the quarter and a
cumulative year-to-date figure in the same document, both ending on the same
date — so a naive read of "the latest fact" silently returns nine months of
revenue where three were wanted. Duration length is the only thing that
separates them, which is why every lookup here states the window it accepts.

Why a mismatch is not automatically an error
────────────────────────────────────────────
Filers choose their own tags. "Total operating expenses" may be filed as
OperatingExpenses, or as CostsAndExpenses which includes cost of revenue, or
not tagged at all. So a difference is reported as a difference, with the tag
named, and never as "you got it wrong".
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# A typed figure and a filed one rarely match to the cent: filings are in
# millions or thousands and readers round. Half a percent is close enough to
# call the same number, and far tighter than a wrong line would ever land.
TOLERANCE = 0.005

# Quarterly durations are not exactly ninety days — 13-week quarters, 52/53
# week fiscal years and holiday-shifted period ends all move it around.
QUARTER_DAYS = (80, 100)
YTD_DAYS = (150, 400)          # six, nine or twelve months into the year

# Each field: the label the form uses, and the XBRL tags to try in order.
# Order matters — the first tag a filer actually used wins.
QUARTER_TAGS = {
    "rev":    ("Revenue", ["RevenueFromContractWithCustomerExcludingAssessedTax",
                           "Revenues", "RevenueFromContractWithCustomerIncludingAssessedTax",
                           "SalesRevenueNet"]),
    "cogs":   ("Cost of revenue", ["CostOfRevenue", "CostOfGoodsAndServicesSold",
                                   "CostOfGoodsSold"]),
    "opex":   ("Total operating expenses", ["OperatingExpenses"]),
    "opinc":  ("Operating income", ["OperatingIncomeLoss"]),
    "ni":     ("Net income", ["NetIncomeLoss", "ProfitLoss"]),
    "sh":     ("Diluted share count",
               ["WeightedAverageNumberOfDilutedSharesOutstanding",
                "WeightedAverageNumberOfDilutedSharesOutstandingBasicAndDiluted"]),
    "eps":    ("Diluted EPS", ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"]),
}

INSTANT_TAGS = {
    "ca": ("Total current assets", ["AssetsCurrent"]),
    "cl": ("Total current liabilities", ["LiabilitiesCurrent"]),
    "ar": ("Accounts receivable, net",
           ["AccountsReceivableNetCurrent",
            "ReceivablesNetCurrent",
            "AccountsAndOtherReceivablesNetCurrent"]),
}

YTD_TAGS = {
    "niy":    ("Net income (year to date)", ["NetIncomeLoss", "ProfitLoss"]),
    "cfo":    ("Cash from operating activities",
               ["NetCashProvidedByUsedInOperatingActivities",
                "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
    "capex":  ("Purchases of property & equipment",
               ["PaymentsToAcquirePropertyPlantAndEquipment",
                "PaymentsToAcquireProductiveAssets"]),
    "revYtd": ("Revenue (year to date)",
               ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "RevenueFromContractWithCustomerIncludingAssessedTax"]),
}


def _facts_for(tag: str, facts: dict) -> list[dict]:
    """Every reported fact for one us-gaap concept, across unit types."""
    node = ((facts.get("facts") or {}).get("us-gaap") or {}).get(tag)
    if not isinstance(node, dict):
        return []
    out = []
    for unit_facts in (node.get("units") or {}).values():
        if isinstance(unit_facts, list):
            out.extend(f for f in unit_facts if isinstance(f, dict))
    return out


def _days(fact: dict) -> int | None:
    from datetime import datetime
    start, end = fact.get("start"), fact.get("end")
    if not start or not end:
        return None
    try:
        return (datetime.strptime(end, "%Y-%m-%d").date()
                - datetime.strptime(start, "%Y-%m-%d").date()).days
    except (TypeError, ValueError):
        return None


def _pick(facts, tags, *, window, end=None):
    """The most recently filed fact matching a duration window, or an instant.

    `window` is None for instants. `end` pins the period end so the prior-year
    comparison lands on the same quarter rather than on whatever is newest.
    """
    for tag in tags:
        candidates = []
        for fact in _facts_for(tag, facts):
            if end and fact.get("end") != end:
                continue
            if window is None:
                if "start" in fact:
                    continue                      # a duration, not an instant
            else:
                length = _days(fact)
                if length is None or not (window[0] <= length <= window[1]):
                    continue
            candidates.append(fact)
        if not candidates:
            continue
        # Latest period end, then latest filing — an amended figure supersedes.
        candidates.sort(key=lambda f: (f.get("end") or "", f.get("filed") or ""))
        best = candidates[-1]
        return {"value": best.get("val"), "tag": tag,
                "end": best.get("end"), "start": best.get("start"),
                "form": best.get("form"), "filed": best.get("filed")}
    return None


def _prior_year_end(end: str) -> str | None:
    """The same period end one year earlier, as a date string."""
    from datetime import datetime
    try:
        d = datetime.strptime(end, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    try:
        return d.replace(year=d.year - 1).isoformat()
    except ValueError:              # 29 February
        return d.replace(year=d.year - 1, day=28).isoformat()


def latest_quarter(ticker: str, facts: dict | None = None) -> dict | None:
    """Every figure the drill asks for, as the company filed it.

    Returns {"period": ..., "fields": {key: {value, tag, ...}}} or None when
    the company cannot be resolved or has filed no usable quarter.
    """
    if facts is None:
        import fundamentals_engine as fe
        cik, name = fe._edgar_cik(ticker)
        if not cik:
            return None
        try:
            resp = fe._req_module.get(
                fe._EDGAR_FACTS_URL.format(cik=cik),
                timeout=15, headers=fe._EDGAR_HEADERS,
            )
            if resp.status_code != 200:
                logger.warning("quarter facts: EDGAR %s for %s", resp.status_code, ticker)
                return None
            facts = resp.json()
        except Exception as exc:
            logger.warning("quarter facts: fetch failed for %s: %s", ticker, exc)
            return None
    else:
        name = facts.get("entityName")

    # Anchor on revenue: it is the one line every filer tags, and its period
    # end defines which quarter the rest of the figures must come from.
    anchor = _pick(facts, QUARTER_TAGS["rev"][1], window=QUARTER_DAYS)
    if not anchor or not anchor.get("end"):
        return None
    end = anchor["end"]
    prior_end = _prior_year_end(end)

    fields: dict[str, dict] = {}
    for key, (label, tags) in QUARTER_TAGS.items():
        found = _pick(facts, tags, window=QUARTER_DAYS, end=end)
        if found:
            fields[key] = dict(found, label=label)
        if prior_end:
            found_prior = _pick(facts, tags, window=QUARTER_DAYS, end=prior_end)
            if found_prior:
                fields[key + "P"] = dict(found_prior, label=label + ", year ago")

    for key, (label, tags) in INSTANT_TAGS.items():
        found = _pick(facts, tags, window=None, end=end)
        if found:
            fields[key] = dict(found, label=label)
        if prior_end:
            found_prior = _pick(facts, tags, window=None, end=prior_end)
            if found_prior:
                fields[key + "P"] = dict(found_prior, label=label + ", prior")

    for key, (label, tags) in YTD_TAGS.items():
        found = _pick(facts, tags, window=YTD_DAYS, end=end)
        if found:
            fields[key] = dict(found, label=label)

    _derive_operating_expenses(facts, fields, end, prior_end)

    return {
        "ticker": ticker.upper(),
        "company": name,
        "period_end": end,
        "prior_end": prior_end,
        "form": anchor.get("form"),
        "filed": anchor.get("filed"),
        "fields": fields,
    }


def _derive_operating_expenses(facts, fields, end, prior_end):
    """Total operating expenses, when the filer never tagged it directly.

    Many filers present one "Total costs and expenses" line — CostsAndExpenses
    — which includes cost of revenue and is therefore not what the drill asks
    for. Meta files 42,026 there for a quarter whose operating expenses are
    30,696; handing that back as the answer would tell a reader who read the
    statement correctly that they were twelve billion dollars wrong.

    So it is derived rather than substituted, and only when both halves are
    present for the same period. The derivation is labelled, because a figure
    this app computed is not a figure the company filed.
    """
    for key, period in (("opex", end), ("opexP", prior_end)):
        if key in fields or not period:
            continue
        total = _pick(facts, ["CostsAndExpenses"], window=QUARTER_DAYS, end=period)
        cogs = _pick(facts, QUARTER_TAGS["cogs"][1], window=QUARTER_DAYS, end=period)
        if not total or not cogs:
            continue
        try:
            value = float(total["value"]) - float(cogs["value"])
        except (TypeError, ValueError):
            continue
        label = QUARTER_TAGS["opex"][0] + ("" if key == "opex" else ", year ago")
        fields[key] = {
            "value": value,
            "tag": "CostsAndExpenses − " + cogs["tag"],
            "derived": True,
            "end": period, "start": total.get("start"),
            "form": total.get("form"), "filed": total.get("filed"),
            "label": label,
        }


def compare(typed: dict, filed: dict | None) -> dict:
    """Field by field, does what was typed match what was filed.

    Every row is one of: match, differs, not filed (the company did not tag
    it under any name this knows), or not entered. A difference names the tag
    used, because filers choose their own and the reader may well be right.
    """
    if not filed:
        return {"available": False, "rows": [], "matched": 0, "differed": 0}

    import quarter_checks

    fields = filed.get("fields") or {}
    rows, matched, differed = [], 0, 0

    for key in quarter_checks.FIELDS:
        entered = typed.get(key)
        found = fields.get(key)
        label = (found or {}).get("label") or key
        if found is None:
            if entered is not None:
                rows.append({"key": key, "label": label, "state": "unfiled",
                             "typed": entered, "filed": None, "tag": None})
            continue
        value = found.get("value")
        if entered is None:
            rows.append({"key": key, "label": label, "state": "blank",
                         "typed": None, "filed": value, "tag": found.get("tag")})
            continue
        # Scale-free comparison: a reader working in millions against a filing
        # in units is a unit mismatch, not a wrong line, and says so below.
        base = max(abs(value), abs(entered), 1e-9)
        close = abs(value - entered) / base <= TOLERANCE
        rows.append({
            "key": key, "label": label,
            "state": "match" if close else "differs",
            "typed": entered, "filed": value, "tag": found.get("tag"),
            "derived": bool(found.get("derived")),
            "scaled": (not close and _looks_scaled(entered, value)),
        })
        if close:
            matched += 1
        else:
            differed += 1

    return {
        "available": True,
        "period_end": filed.get("period_end"),
        "form": filed.get("form"),
        "filed_on": filed.get("filed"),
        "company": filed.get("company"),
        "rows": rows,
        "matched": matched,
        "differed": differed,
    }


def _looks_scaled(entered: float, value: float) -> bool:
    """Is the gap a thousands/millions mismatch rather than a wrong line?

    Worth separating: reading the right row in a statement headed "in
    millions" and typing it verbatim is a units slip, not a comprehension
    failure, and telling someone they picked the wrong line when they did not
    teaches them the wrong lesson.
    """
    if not entered or not value:
        return False
    ratio = abs(value / entered)
    return any(abs(ratio - scale) / scale <= 0.02
               for scale in (1e3, 1e6, 1e9, 1e-3, 1e-6, 1e-9))
