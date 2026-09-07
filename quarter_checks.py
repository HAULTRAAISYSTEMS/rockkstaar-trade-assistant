"""
quarter_checks.py — seven checks on a single quarter, from figures you typed.

Why this exists next to the annual scorecard
────────────────────────────────────────────
fundamentals_engine reads annual filings and scores the business out of forty:
is this worth owning. It is blind to the most recent quarter's detail, and
four of the checks here have no equivalent anywhere in it — operating
leverage, days sales outstanding, whether EPS growth is the business or the
buyback, and cash conversion on a year-to-date basis.

Why the figures are typed rather than fetched
─────────────────────────────────────────────
Reading a filing is the skill. The app can already show a reader numbers it
fetched; what it could not do was tell them whether they had pulled the right
lines out of a 10-Q themselves. So this grades what the reader entered, and
quarter_facts.py separately fetches the same figures from EDGAR to say where
the two disagree. The drill is the practice; the fetch is the answer key.

On thresholds
─────────────
Every band here is a starting point, not a verdict. A thin current ratio and
a long collection cycle mean different things at a retailer, a bank and a
chipmaker. The verdicts say what to look at, never what to conclude, and
every check states its arithmetic so the reader can disagree with it.

Missing inputs skip their check rather than defaulting. A check that grades
a zero it invented is worse than a check that does not run.
"""

from __future__ import annotations

# Verdicts, worst last — the summary counts them in this order.
CLEAN, WATCH, FLAG = "clean", "watch", "flag"

# Every check names the concept that explains it, so the page can open the
# same explainer the annual scorecard rows use.
CHECK_CONCEPTS = {
    "cash_conversion":   "free-cash-flow",
    "gross_margin":      "gross-margin",
    "operating_leverage": "operating-margin",
    "current_ratio":     "current-ratio",
    # No concept covers the collection cycle on its own; the cash flow
    # statement is where a reader goes to see the same money arrive.
    "dso":               "cash-flow-statement",
    "share_count":       "share-dilution",
    "free_cash_flow":    "free-cash-flow",
}


# Which statement rows each check reads. The guided walkthrough lights these
# up on the statement beside the result, because a number without the line it
# came from teaches where the form's fields are, not where the filing's are.
CHECK_ROWS = {
    "cash_conversion":    {"cash":   ["niy", "cfo"]},
    "gross_margin":       {"income": ["rev", "cogs"]},
    "operating_leverage": {"income": ["rev", "opex", "opinc"]},
    "current_ratio":      {"balance": ["ca", "cl"]},
    "dso":                {"balance": ["ar"], "income": ["rev"]},
    "share_count":        {"income": ["ni", "eps", "sh"]},
    "free_cash_flow":     {"cash":   ["cfo", "capex"]},
}


def _num(value):
    """Parse a figure a person typed. Returns None for anything unusable.

    Filings print negatives in parentheses and thousands separators
    everywhere, so "(1,234)" has to read as -1234 rather than as nothing.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return None if value != value else float(value)   # NaN is not a number
    text = str(value).strip().replace(",", "").replace("$", "").replace(" ", "")
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        out = float(text)
    except ValueError:
        return None
    return -out if negative else out


def _pct(x):
    return f"{x * 100:.1f}%"


def _signed(x):
    return f"{'+' if x >= 0 else ''}{x * 100:.1f}%"


def _com(x):
    return f"{x:,.0f}"


def _check(key, name, value, basis, says, verdict, skipped=None):
    return {
        "key": key,
        "name": name,
        "value": value,
        "basis": basis,
        "says": says,
        "verdict": verdict,
        "concept": CHECK_CONCEPTS.get(key),
        "rows": CHECK_ROWS.get(key, {}),
        "skipped": skipped,
    }


def _skip(key, name, needs):
    """A check that could not run says which figure it wanted.

    Silence reads as a pass. The original drill dropped a check whose prior
    operating expenses were zero and said nothing at all.
    """
    return _check(key, name, None, None, None, None, skipped=needs)


# ── The seven ─────────────────────────────────────────────────────────────────

def _cash_conversion(f):
    """1. Cash from operations against reported profit, both year-to-date.

    The cash flow statement is cumulative, never quarterly, so it has to be
    matched against the year-to-date net income printed at the top of it.
    """
    cfo, niy = f.get("cfo"), f.get("niy")
    if cfo is None or not niy:
        return _skip("cash_conversion", "Cash flow ÷ net income",
                     "cash from operations and year-to-date net income")
    ratio = cfo / niy
    verdict = CLEAN if ratio >= 1 else (WATCH if ratio >= 0.8 else FLAG)
    says = ("Cash exceeded reported profit. Normal and healthy — non-cash "
            "charges like depreciation get added back."
            if ratio >= 1 else
            "Profit exceeded cash collected. Scroll the adjustments and "
            "working-capital lines and name the gap before you accept it.")
    return _check("cash_conversion", "Cash flow ÷ net income",
                  f"{ratio:.2f}", f"{_com(cfo)} ÷ {_com(niy)}", says, verdict)


def _gross_margin(f):
    """2. Gross margin, and which way it moved."""
    rev, cogs = f.get("rev"), f.get("cogs")
    if not rev or cogs is None:
        return _skip("gross_margin", "Gross margin", "revenue and cost of revenue")
    margin = (rev - cogs) / rev
    rev_p, cogs_p = f.get("revP"), f.get("cogsP")
    if rev_p and cogs_p is not None:
        prior = (rev_p - cogs_p) / rev_p
        delta = (margin - prior) * 100
        basis = f"prior {_pct(prior)} · {'+' if delta >= 0 else ''}{delta:.1f} pts"
        verdict = CLEAN if delta >= -0.3 else (WATCH if delta >= -2 else FLAG)
        if delta >= 0.3:
            says = "Margin expanding — pricing power is holding or improving."
        elif delta >= -0.3:
            says = "Margin essentially flat. Stable position."
        else:
            says = ("Margin compressing. Either competitors are closing in or "
                    "input costs are rising; this shows up here before it "
                    "shows up in revenue.")
    else:
        basis, verdict = None, CLEAN
        says = ("Compare against the prior year and against peers — the level "
                "alone means nothing.")
    return _check("gross_margin", "Gross margin", _pct(margin), basis, says, verdict)


def _operating_leverage(f):
    """3. Did costs grow slower than sales.

    The original skipped silently when prior operating expenses were zero,
    because it tested truthiness rather than presence.
    """
    rev, rev_p = f.get("rev"), f.get("revP")
    opex, opex_p = f.get("opex"), f.get("opexP")
    if not rev or not rev_p or opex is None or not opex_p:
        return _skip("operating_leverage", "Revenue vs expense growth",
                     "revenue and total operating expenses, both periods")
    rev_growth = (rev - rev_p) / rev_p
    opex_growth = (opex - opex_p) / opex_p
    if opex_growth <= rev_growth:
        verdict = CLEAN
    elif opex_growth <= rev_growth * 1.5:
        verdict = WATCH
    else:
        verdict = FLAG
    says = ("Costs grew slower than sales, so the surplus falls to operating "
            "income. That is operating leverage."
            if opex_growth <= rev_growth else
            "Costs outran sales. Find what they are spending on and whether "
            "operating income still grew.")

    op_inc, op_inc_p = f.get("opinc"), f.get("opincP")
    if op_inc is not None and op_inc_p:
        inc_growth = (op_inc - op_inc_p) / op_inc_p
        says += f" Operating income {_signed(inc_growth)}."
        if inc_growth < 0:
            verdict = FLAG
    return _check("operating_leverage", "Revenue vs expense growth",
                  f"{_signed(rev_growth)} / {_signed(opex_growth)}",
                  "revenue growth / operating expense growth", says, verdict)


def _current_ratio(f):
    """4. Cover on the bills due within twelve months."""
    ca, cl = f.get("ca"), f.get("cl")
    if not ca or not cl:
        return _skip("current_ratio", "Current ratio",
                     "total current assets and total current liabilities")
    ratio = ca / cl
    verdict = CLEAN if ratio >= 1.5 else (WATCH if ratio >= 1 else FLAG)
    basis = f"{_com(ca)} ÷ {_com(cl)}"
    ca_p, cl_p = f.get("caP"), f.get("clP")
    if ca_p and cl_p:
        basis += f" · prior {ca_p / cl_p:.2f}"
    if ratio >= 1.5:
        says = "Comfortable cover on bills due within twelve months."
    elif ratio >= 1:
        says = ("Thin cover, but read the surrounding lines. Big payables can "
                "mean leverage over suppliers rather than weakness, and "
                "long-term securities do not count here.")
    else:
        says = ("Below one. Check whether liquid assets sit outside the "
                "current section before calling this a problem.")
    return _check("current_ratio", "Current ratio", f"{ratio:.2f}", basis, says, verdict)


def _dso(f):
    """5. How long after a sale the cash arrives."""
    ar, rev = f.get("ar"), f.get("rev")
    if not ar or not rev:
        return _skip("dso", "Days sales outstanding",
                     "accounts receivable and quarterly revenue")
    days = ar / rev * 90
    verdict = CLEAN if days <= 45 else (WATCH if days <= 70 else FLAG)
    says = ("How long after a sale the cash arrives. The level varies by "
            "industry — enterprise hardware runs long, consumer runs short. "
            "Track the direction next quarter, especially if revenue growth "
            "is slowing.")
    return _check("dso", "Days sales outstanding", f"{days:.0f} days",
                  f"{_com(ar)} ÷ {_com(rev)} × 90", says, verdict)


def _share_count(f):
    """6. Dilution, and whether EPS growth is the business or the buyback."""
    sh, sh_p = f.get("sh"), f.get("shP")
    if not sh or not sh_p:
        return _skip("share_count", "Diluted share count",
                     "diluted share count, both periods")
    change = (sh - sh_p) / sh_p
    verdict = CLEAN if change <= 0 else WATCH
    says = ("Share count shrinking — buybacks are returning value."
            if change <= 0 else
            "Share count growing. Your ownership is being diluted.")

    ni, ni_p = f.get("ni"), f.get("niP")
    eps, eps_p = f.get("eps"), f.get("epsP")
    if ni and ni_p and eps and eps_p:
        ni_growth = (ni - ni_p) / ni_p
        eps_growth = (eps - eps_p) / eps_p
        gap = (eps_growth - ni_growth) * 100
        says += f" Net income {_signed(ni_growth)} against EPS {_signed(eps_growth)}. "
        if abs(gap) < 3:
            says += ("Nearly identical, so the growth is the business rather "
                     "than the share count.")
        elif gap > 0:
            says += (f"EPS is outrunning profit by {gap:.1f} points — that gap "
                     "is the buyback doing the work.")
        else:
            says += "EPS is lagging profit, which points to dilution."
        # Profit falling while EPS rises is the buyback masking the business.
        if ni_growth <= 0 and eps_growth > 0:
            verdict = FLAG
    return _check("share_count", "Diluted share count", _signed(change),
                  f"{_com(sh)} vs {_com(sh_p)}", says, verdict)


def _free_cash_flow(f):
    """7. What is left after paying for the assets that keep it running.

    Capex intensity is stated against year-to-date revenue when that is
    given. The original inferred it as quarterly revenue scaled by
    (year-to-date net income ÷ quarterly net income), which only holds while
    net margin is flat across the quarters — exactly when it is least likely
    to be. An inferred denominator is now labelled as one.
    """
    cfo, capex = f.get("cfo"), f.get("capex")
    if cfo is None or capex is None:
        return _skip("free_cash_flow", "Free cash flow",
                     "cash from operations and purchases of property & equipment")
    spend = abs(capex)
    fcf = cfo - spend
    verdict = CLEAN if fcf > 0 else FLAG
    basis = f"{_com(cfo)} − {_com(spend)}"

    rev_ytd = f.get("revYtd")
    if rev_ytd:
        basis += f" · capex {_pct(spend / rev_ytd)} of year-to-date revenue"
    says = ("What is left after paying for the assets that keep the business "
            "running. This funds dividends, buybacks and debt repayment."
            if fcf > 0 else
            "Negative. The business is not covering its own upkeep from "
            "operations — find out whether that is a growth build or a "
            "structural problem.")
    return _check("free_cash_flow", "Free cash flow", _com(fcf), basis, says, verdict)


CHECKS = (_cash_conversion, _gross_margin, _operating_leverage,
          _current_ratio, _dso, _share_count, _free_cash_flow)

# The fields the form collects, in the order the filing presents them.
FIELDS = ("rev", "revP", "cogs", "cogsP", "opex", "opexP", "opinc", "opincP",
          "ni", "niP", "sh", "shP", "eps", "epsP",
          "ca", "caP", "cl", "clP", "ar", "arP",
          "niy", "cfo", "capex", "revYtd")


def parse(raw: dict) -> dict:
    """Coerce a form submission into numbers, dropping what cannot be read."""
    raw = raw if isinstance(raw, dict) else {}
    return {key: _num(raw.get(key)) for key in FIELDS}


def run(raw: dict) -> dict:
    """Grade a quarter. Returns the checks that ran and the ones that could not."""
    figures = parse(raw)
    results = [check(figures) for check in CHECKS]
    graded = [r for r in results if r["verdict"]]
    counts = {
        "clean": sum(1 for r in graded if r["verdict"] == CLEAN),
        "watch": sum(1 for r in graded if r["verdict"] == WATCH),
        "flag":  sum(1 for r in graded if r["verdict"] == FLAG),
    }
    return {
        "checks":   results,
        "graded":   len(graded),
        "counts":   counts,
        "figures":  figures,
        "summary": (
            f"{counts['clean']} clean · {counts['watch']} worth watching · "
            f"{counts['flag']} flagged. Thresholds are starting points, not "
            "verdicts. A thin current ratio and a long collection cycle mean "
            "different things at a retailer, a bank and a chipmaker — read the "
            "lines around the number before you grade it."
        ) if graded else None,
    }
