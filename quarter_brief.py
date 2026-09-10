"""quarter_brief.py — why a check flagged, in the filing's own words.

The seven checks say what happened. "Costs outran sales, +28% revenue against
+65% operating expenses" is correct and, to someone learning, not yet an
answer: it does not say what the money went on. The reader's next question is
always the same one — is this a company losing control of its costs, or a
company building something.

For Meta's June 2026 quarter the answer is written down. Its own management
discussion says cost of revenue rose "primarily due to higher infrastructure
expenses related to our data centers, technical infrastructure, and
third-party cloud services", and research and development rose 67%. That is
the data-centre story, told by the company, in the same document the figures
came from.

So this module does not ask a model what it thinks happened. It assembles
evidence and then asks the model to write over it:

  1. The decomposition. Which component line actually moved, from the same
     XBRL the drill already reads. Operating expenses grew 65% — but R&D grew
     67% and cost of revenue 33%, and capital expenditure went from x to y.
     No model is involved and this alone is frequently the whole answer.

  2. The company's own words. The management discussion from that exact
     filing, narrowed to the paragraphs about the line that flagged. Filers
     explain their own variances: "primarily due to", "driven by".

  3. Only then, prose. The model is given the evidence and the quotes and may
     use nothing else, following the rule this codebase already sets in
     tradestaar_take.py: AI is an analysis assistant, never a source of
     financial facts. If the evidence is thin it has to say so rather than
     reach for what it remembers about the company.

Without an API key the first two still work, and they are the part that
cannot be wrong.
"""

from __future__ import annotations

import logging
import re

import quarter_facts as qf

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
# A headline, three paragraphs and three watch items, as JSON. At 1000 the
# object was being cut off mid-string, json.loads threw, and the whole brief
# came back as if no key were configured.
MAX_TOKENS = 2200

# Stamped into every cached brief. A brief written by an older build — before
# the filing could be reached at all, or while the model call was failing
# silently — is not worth serving forever, so a bumped version misses the
# cache and is written again.
BRIEF_VERSION = 3

# The filing document is fetched whole and can be tens of megabytes of inline
# XBRL. Past this it is not worth the wait on a small dyno.
MAX_DOCUMENT_BYTES = 24 * 1024 * 1024
MAX_DISCUSSION_CHARS = 7000
MAX_QUOTES = 6

_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/"

# Its own headers, deliberately. fundamentals_engine pins Host: data.sec.gov,
# which is right for the XBRL API and silently fatal here: the filings live on
# www.sec.gov, and a request carrying the wrong Host is routed by that header,
# not by the URL. Both the file index and the document came back empty every
# time, so every brief showed its table and no filing at all.
_HEADERS = {
    "User-Agent": "Tradestaar Elite (contact@haultra.ai)",
    "Accept-Encoding": "gzip, deflate",
}

# The XBRL viewer renders every statement as R1.htm, R2.htm and so on, and
# the certifications come as exhibits. Matching those by prefix threw away
# the 10-Q of any company whose ticker begins with an r or an e.
_VIEWER_PAGE = re.compile(r"^r\d+\.html?$", re.I)
_EXHIBIT = re.compile(r"(^|[-_])ex[-_]?\d|cert|graphic", re.I)


# ── 1. What actually moved ────────────────────────────────────────────────────
#
# Each check names the component lines underneath it. "q" is the three-month
# column, "ytd" the cumulative one, "instant" a balance-sheet date. Rows the
# filer never tagged are dropped, so a lean filer gets a short table rather
# than a wall of blanks.

_RND = ["ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"]
_SM = ["SellingAndMarketingExpense", "MarketingExpense",
       "SellingGeneralAndAdministrativeExpense"]
_GA = ["GeneralAndAdministrativeExpense"]
_DA = ["DepreciationDepletionAndAmortization",
       "DepreciationAmortizationAndAccretionNet", "DepreciationNonproduction"]
_SBC = ["ShareBasedCompensation"]
_BUYBACK = ["PaymentsForRepurchaseOfCommonStock"]
_CASH = ["CashAndCashEquivalentsAtCarryingValue"]
_SECS = ["MarketableSecuritiesCurrent", "ShortTermInvestments",
         "AvailableForSaleSecuritiesDebtSecuritiesCurrent"]
_INV = ["InventoryNet"]
_AP = ["AccountsPayableCurrent"]
_STD = ["LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper"]

EVIDENCE = {
    "operating_leverage": [
        ("Revenue", qf.QUARTER_TAGS["rev"][1], "q"),
        ("Cost of revenue", qf.QUARTER_TAGS["cogs"][1], "q"),
        ("Research and development", _RND, "q"),
        ("Sales and marketing", _SM, "q"),
        ("General and administrative", _GA, "q"),
        ("Total operating expenses", qf.QUARTER_TAGS["opex"][1], "q"),
        ("Operating income", qf.QUARTER_TAGS["opinc"][1], "q"),
        ("Depreciation and amortization, year to date", _DA, "ytd"),
        ("Capital expenditure, year to date", qf.YTD_TAGS["capex"][1], "ytd"),
    ],
    "gross_margin": [
        ("Revenue", qf.QUARTER_TAGS["rev"][1], "q"),
        ("Cost of revenue", qf.QUARTER_TAGS["cogs"][1], "q"),
        ("Gross profit", ["GrossProfit"], "q"),
        ("Operating income", qf.QUARTER_TAGS["opinc"][1], "q"),
        ("Depreciation and amortization, year to date", _DA, "ytd"),
        ("Capital expenditure, year to date", qf.YTD_TAGS["capex"][1], "ytd"),
    ],
    "cash_conversion": [
        ("Net income, year to date", qf.YTD_TAGS["niy"][1], "ytd"),
        ("Cash from operations, year to date", qf.YTD_TAGS["cfo"][1], "ytd"),
        ("Revenue, year to date", qf.YTD_TAGS["revYtd"][1], "ytd"),
        ("Depreciation and amortization, year to date", _DA, "ytd"),
        ("Stock-based compensation, year to date", _SBC, "ytd"),
        ("Accounts receivable", qf.INSTANT_TAGS["ar"][1], "instant"),
        ("Inventories", _INV, "instant"),
    ],
    "free_cash_flow": [
        ("Cash from operations, year to date", qf.YTD_TAGS["cfo"][1], "ytd"),
        ("Capital expenditure, year to date", qf.YTD_TAGS["capex"][1], "ytd"),
        ("Depreciation and amortization, year to date", _DA, "ytd"),
        ("Revenue, year to date", qf.YTD_TAGS["revYtd"][1], "ytd"),
    ],
    "current_ratio": [
        ("Cash and cash equivalents", _CASH, "instant"),
        ("Marketable securities", _SECS, "instant"),
        ("Accounts receivable", qf.INSTANT_TAGS["ar"][1], "instant"),
        ("Inventories", _INV, "instant"),
        ("Total current assets", qf.INSTANT_TAGS["ca"][1], "instant"),
        ("Accounts payable", _AP, "instant"),
        ("Debt due within a year", _STD, "instant"),
        ("Total current liabilities", qf.INSTANT_TAGS["cl"][1], "instant"),
    ],
    "dso": [
        ("Accounts receivable", qf.INSTANT_TAGS["ar"][1], "instant"),
        ("Revenue", qf.QUARTER_TAGS["rev"][1], "q"),
    ],
    "share_count": [
        ("Diluted share count", qf.QUARTER_TAGS["sh"][1], "q"),
        ("Net income", qf.QUARTER_TAGS["ni"][1], "q"),
        ("Diluted EPS", qf.QUARTER_TAGS["eps"][1], "q"),
        ("Share repurchases, year to date", _BUYBACK, "ytd"),
        ("Stock-based compensation, year to date", _SBC, "ytd"),
    ],
}


def _growth(now, prior):
    try:
        if now is None or not prior:
            return None
        return (float(now) - float(prior)) / abs(float(prior))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def decompose(facts: dict, check_key: str, period_end: str,
              prior_end: str | None, ytd_prior_end: str | None = None) -> list[dict]:
    """The component lines under one check, this period against the last.

    Operating expenses grew 65% is a fact about a subtotal. Which of research
    and development, cost of revenue and marketing did the growing is the
    question the reader is actually asking, and it is sitting in the same
    XBRL document.
    """
    rows = []
    for label, tags, window in EVIDENCE.get(check_key, []):
        if window == "instant":
            now = qf._pick(facts, tags, window=None, end=period_end)
            prior = qf._prior_instant(facts, tags, now, prior_end)
        else:
            span = qf.QUARTER_DAYS if window == "q" else qf.YTD_DAYS
            back = prior_end if window == "q" else (ytd_prior_end or prior_end)
            now = qf._pick(facts, tags, window=span, end=period_end)
            prior = (qf._pick(facts, tags, window=span, near=back,
                              slack=qf.PERIOD_SLACK_DAYS) if back else None)
        if not now:
            continue
        rows.append({
            "label": label,
            "now": now.get("value"),
            "prior": (prior or {}).get("value"),
            "change": _growth(now.get("value"), (prior or {}).get("value")),
            "tag": now.get("tag"),
        })

    _add_derived_opex(facts, rows, check_key, period_end, prior_end)
    return rows


def _add_derived_opex(facts, rows, check_key, period_end, prior_end):
    """The operating-expense subtotal a filer never printed.

    Meta files one "Total costs and expenses" that includes cost of revenue,
    so without this the evidence has no subtotal for the components to be a
    share OF, and the reader is told which line moved but not how much of the
    increase it was. The form already shows this derivation; the brief should
    agree with the form.
    """
    if check_key != "operating_leverage":
        return
    if any(r["label"] == "Total operating expenses" for r in rows):
        return

    def _pair(tags, end):
        return (qf._pick(facts, tags, window=qf.QUARTER_DAYS, end=end)
                if end == period_end else
                qf._pick(facts, tags, window=qf.QUARTER_DAYS, near=end,
                         slack=qf.PERIOD_SLACK_DAYS))

    # Two derivations, matching quarter_facts. Meta prints a combined total to
    # subtract from; KLA prints no subtotal at all, so it comes out of the
    # income-statement identity instead. Both need a cost of revenue, which is
    # what keeps them away from banks.
    def _less(period):
        cogs = _pair(qf.QUARTER_TAGS["cogs"][1], period)
        if not cogs:
            return None, None
        total = _pair(["CostsAndExpenses"], period)
        if total and total.get("end") == cogs.get("end"):
            try:
                return (float(total["value"]) - float(cogs["value"]),
                        "CostsAndExpenses less cost of revenue")
            except (TypeError, ValueError):
                pass
        revenue = _pair(qf.QUARTER_TAGS["rev"][1], period)
        # The resolved operating-income tags, not the literal
        # OperatingIncomeLoss \u2014 KLA never tags that one.
        operating = _pair(qf.QUARTER_TAGS["opinc"][1], period)
        if not revenue or not operating:
            return None, None
        if not (revenue.get("end") == cogs.get("end") == operating.get("end")):
            return None, None
        try:
            return (float(revenue["value"]) - float(cogs["value"])
                    - float(operating["value"]),
                    "revenue less cost of revenue less operating income")
        except (TypeError, ValueError):
            return None, None

    now, source = _less(period_end)
    if now is None:
        return
    prior = (_less(prior_end)[0] if prior_end else None)
    row = {"label": "Total operating expenses", "now": now, "prior": prior,
           "change": _growth(now, prior), "derived": True,
           "tag": source}
    # Where the subtotal belongs on a statement: after its components.
    after = max((i for i, r in enumerate(rows)
                 if r["label"] in ("Cost of revenue", "Research and development",
                                   "Sales and marketing",
                                   "General and administrative")), default=-1)
    rows.insert(after + 1, row)


# ── 1b. The explanation the arithmetic already supports ───────────────────────
#
# The first version left the reader with a table and an apology when no model
# was configured, which is not what they asked for. They asked why.
#
# Most of "why" is arithmetic. If operating expenses grew 22.9% and research
# and development grew 32.3% and accounts for two thirds of the increase in
# money, then research and development is the answer, and no language model is
# required to notice it. If capital expenditure and depreciation are both
# climbing steeply, the money is going into physical capacity; if capex is
# falling, it is not a build-out. If operating income grew anyway, the
# spending is being carried.
#
# So this is written from the figures, always, with or without a key. It never
# names a cause it cannot compute — it will say the filing has to be read for
# that — and the model, when configured, adds what management actually said on
# top of it.

# The subtotal each check is about, and the lines that add up to it.
DRIVERS = {
    # Cost of revenue is NOT one of these. Every filer presents operating
    # expenses net of it — Apple's 19,075 excludes a 54,647 cost of revenue,
    # and Meta's is literally total costs LESS cost of revenue. Counting it as
    # a component made it the "biggest mover" of a subtotal it is not inside,
    # and the share of the change came out over 100%.
    "operating_leverage": ("Total operating expenses",
                           ["Research and development", "Sales and marketing",
                            "General and administrative"]),
    "gross_margin": ("Cost of revenue", []),
    "cash_conversion": ("Cash from operations, year to date", []),
    "free_cash_flow": ("Cash from operations, year to date", []),
    "current_ratio": ("Total current liabilities",
                      ["Accounts payable", "Debt due within a year"]),
    "dso": ("Accounts receivable", []),
    "share_count": ("Diluted share count", []),
}


def _by_label(rows):
    return {row["label"]: row for row in rows}


def _pct_words(change):
    return f"{change * 100:+.1f}%"


def _delta(row):
    try:
        return float(row["now"]) - float(row["prior"])
    except (TypeError, ValueError):
        return None


def _biggest_mover(rows, parts):
    """The line that put the most money into the change, not the biggest %.

    A tiny line doubling is a bigger percentage and a smaller cause. What the
    reader wants is the line the money actually went to.
    """
    best, best_size = None, 0.0
    for label in parts:
        row = _by_label(rows).get(label)
        if not row:
            continue
        moved = _delta(row)
        if moved is None:
            continue
        if abs(moved) > best_size:
            best, best_size = row, abs(moved)
    return best


def _cash_story(lookup) -> list[str]:
    """Where the profit went, if it did not arrive as cash.

    A cash-flow statement starts at net income and walks down to cash. The
    walk has two kinds of step: non-cash charges added back, and the
    balance-sheet lines that swallowed or released money. Both are in the
    evidence, so the gap can be attributed rather than described.
    """
    ni = lookup.get("Net income, year to date")
    cfo = lookup.get("Cash from operations, year to date")
    if not ni or not cfo or ni.get("now") is None or cfo.get("now") is None:
        return []
    try:
        profit, cash = float(ni["now"]), float(cfo["now"])
    except (TypeError, ValueError):
        return []
    gap = profit - cash
    if gap <= 0:
        return [f"Cash of {_fmt(cash)} came in against {_fmt(profit)} of "
                "reported profit. Cash ahead of profit is the normal, healthy "
                "direction: depreciation and stock compensation are charged "
                "against profit but never leave the building."]

    out = [f"{_fmt(profit)} of profit turned into {_fmt(cash)} of cash, a gap "
           f"of {_fmt(gap)}. That gap is the whole question here."]

    adds = 0.0
    named = []
    for label, short in (("Depreciation and amortization, year to date", "depreciation"),
                         ("Stock-based compensation, year to date", "stock compensation")):
        row = lookup.get(label)
        try:
            if row and row.get("now") is not None:
                adds += float(row["now"])
                named.append(f"{short} {_fmt(row['now'])}")
        except (TypeError, ValueError):
            pass

    absorbed = gap + adds
    if named:
        out.append(
            f"Working the other way first: {' and '.join(named)} are charges "
            "against profit that never leave the building, so they push cash "
            f"UP by {_fmt(adds)}. Which means the balance sheet absorbed "
            f"roughly {_fmt(absorbed)} — more than the headline gap.")

    swallowed, parts = 0.0, []
    for label, short in (("Accounts receivable", "receivables"),
                         ("Inventories", "inventories")):
        row = lookup.get(label)
        moved = _delta(row) if row else None
        if moved and moved > 0:
            swallowed += moved
            parts.append(f"{short} grew {_fmt(moved)}")
    if parts:
        line = ("And there it is: " + " and ".join(parts) + ".")
        if absorbed > 0:
            line += f" Together {_fmt(swallowed)}, about " \
                    f"{min(swallowed / absorbed, 1.0) * 100:.0f}% of it."
        line += (" Receivables are sales already booked as profit whose money "
                 "has not arrived. Inventory is cash already spent on goods "
                 "not yet sold. Both sit between the profit and the cash.")
        out.append(line)

        ar = lookup.get("Accounts receivable")
        if ar and ar.get("change") is not None:
            # Deliberately no verdict here. The receivable figures move from
            # the last fiscal year end, matching the year-to-date cash flow,
            # which is why they explain the gap. Revenue growth is measured
            # against the same quarter a year earlier. Setting one percentage
            # against the other reads like a comparison and is not one.
            out.append(
                f"Receivables are {_pct_words(ar['change'])} since the last "
                "fiscal year end, the same span the cash flow covers, which "
                "is why they account for the gap. Whether the balance is "
                "proportionate to the sales behind it is a different question, "
                "and it is the one Days sales outstanding answers above — that "
                "check puts the balance against a quarter of revenue, so both "
                "sides are on one basis. Building working capital ahead of "
                "sales is what fast growth looks like; it turns into a problem "
                "only when the collection cycle keeps stretching after growth "
                "slows.")
    return out


def _margin_story(lookup) -> list[str]:
    """Margin is two growth rates, and which one won."""
    rev, cogs = lookup.get("Revenue"), lookup.get("Cost of revenue")
    if not rev or not cogs:
        return []
    if rev.get("change") is None or cogs.get("change") is None:
        return []
    out = [f"Revenue grew {_pct_words(rev['change'])} and the cost of "
           f"delivering it {_pct_words(cogs['change'])}. Margin is nothing "
           "more than which of those two ran faster."]
    spread = rev["change"] - cogs["change"]
    if spread > 0.02:
        out.append(
            "Sales outran costs, so more of every dollar stayed in the "
            "business. That is either pricing power or a cheaper mix, and the "
            "filing is where it says which.")
    elif spread < -0.02:
        out.append(
            "Costs outran sales, so each dollar of revenue is carrying more "
            "cost than it did. Either competitors are pressing on price or "
            "inputs got dearer — this shows up here well before it shows up "
            "in the revenue line.")
    else:
        out.append("The two moved together, so the margin is holding.")
    return out


def narrate(check: dict, rows: list[dict]) -> list[str]:
    """Why this graded the way it did, from the figures alone."""
    key = check.get("key") or ""
    lookup = _by_label(rows)
    total_label, parts = DRIVERS.get(key, (None, []))
    out = []

    # 1. The line that moved the money — or, where the story is not "one
    #    component moved", the story that check actually has.
    if key == "cash_conversion":
        out.extend(_cash_story(lookup))
    elif key == "gross_margin":
        out.extend(_margin_story(lookup))

    total = lookup.get(total_label) if total_label else None
    mover = _biggest_mover(rows, parts) if not out else None
    if mover and total:
        share = None
        total_moved, mover_moved = _delta(total), _delta(mover)
        if total_moved and mover_moved is not None and total_moved != 0:
            share = mover_moved / total_moved
        line = (f"{mover['label']} is the line that moved: "
                f"{_fmt(mover['prior'])} to {_fmt(mover['now'])}, "
                f"{_pct_words(mover['change'])}.")
        if share is not None and 0 < share <= 1.2:
            line += (f" That one line is {share * 100:.0f}% of the whole "
                     f"change in {total_label.lower()}.")
        out.append(line)
    elif mover:
        out.append(f"{mover['label']} moved most: {_fmt(mover['prior'])} to "
                   f"{_fmt(mover['now'])}, {_pct_words(mover['change'])}.")

    # 2. Is this building something, or is it just costing more? Capital
    #    spending and depreciation answer that without anybody's opinion.
    capex = lookup.get("Capital expenditure, year to date")
    dep = lookup.get("Depreciation and amortization, year to date")
    if capex and capex.get("change") is not None:
        moved = capex["change"]
        if moved >= 0.25:
            said = (f"Capital expenditure is {_pct_words(moved)} year to date, "
                    f"at {_fmt(capex['now'])}")
            if dep and dep.get("change") is not None:
                said += (f", and depreciation {_pct_words(dep['change'])} behind "
                         "it — capacity that was bought earlier arriving on the "
                         "income statement")
            said += (". Money on that scale goes into physical capacity: "
                     "buildings, equipment, data centres. The filing names what "
                     "it was.")
            out.append(said)
        elif moved <= -0.15:
            out.append(
                f"Capital expenditure is {_pct_words(moved)} year to date, at "
                f"{_fmt(capex['now'])}, so this is not a build-out quarter. "
                "Whatever grew, it was not the cost of new capacity.")

    # 3. Did it still work? A cost line growing is only a problem if the
    #    profit it was supposed to produce did not follow. This belongs to the
    #    check about operating expenses and nowhere else.
    op = lookup.get("Operating income") if key == "operating_leverage" else None
    if op and op.get("change") is not None:
        if op["change"] > 0:
            out.append(
                f"Operating income still grew {op['change'] * 100:.1f}%, so "
                "the extra spending is being carried rather than eating the "
                "business. Costs outrunning sales matters when profit stops "
                "growing; here it has not.")
        else:
            out.append(
                f"Operating income fell {abs(op['change']) * 100:.1f}%. The "
                "spending is not being carried this quarter, which is what "
                "turns a cost increase from an investment into a problem "
                "worth naming.")

    # Check-specific closers where the arithmetic says something particular.
    if key == "dso":
        # No growth-rate comparison here: the receivable balance moves from
        # the last fiscal year end and quarterly revenue from the same quarter
        # a year ago, so setting one percentage against the other reads like a
        # comparison and is not one. The ratio is the honest form, and the
        # check already computes it.
        ar, rev = lookup.get("Accounts receivable"), lookup.get("Revenue")
        try:
            balance, quarter = float(ar["now"]), float(rev["now"])
        except (TypeError, ValueError, KeyError):
            balance = quarter = None
        if balance and quarter:
            out.append(
                f"{_fmt(balance)} is owed to them against {_fmt(quarter)} of "
                f"sales in the quarter — that ratio is the {balance / quarter * 90:.0f} "
                "days above. Receivables rise with sales, so the level on its "
                "own says little; it is the direction over three or four "
                "quarters that tells you whether customers are taking longer "
                "to pay, and that matters most when revenue growth is slowing.")
    if key == "share_count":
        buyback = lookup.get("Share repurchases, year to date")
        sbc = lookup.get("Stock-based compensation, year to date")
        if buyback and sbc and buyback.get("now") and sbc.get("now"):
            out.append(
                f"They spent {_fmt(buyback['now'])} buying stock back against "
                f"{_fmt(sbc['now'])} issued as compensation. The share count "
                "is the net of those two, which is why it moves so little "
                "either way.")
    if key == "free_cash_flow":
        cfo, capex_row = lookup.get("Cash from operations, year to date"), capex
        if cfo and capex_row and cfo.get("now") is not None and capex_row.get("now") is not None:
            try:
                left = float(cfo["now"]) - abs(float(capex_row["now"]))
                out.append(
                    f"{_fmt(cfo['now'])} came in from operations and "
                    f"{_fmt(abs(float(capex_row['now'])))} went straight back "
                    f"out into assets, leaving {_fmt(left)}. That remainder is "
                    "what pays dividends, buybacks and debt.")
            except (TypeError, ValueError):
                pass

    if not out:
        out.append(
            "The lines underneath this check were not tagged in enough detail "
            "to say which one moved. The filing itself is the place to look.")
    return out


# ── 2. What the company said about it ─────────────────────────────────────────

DISCUSSION_TERMS = {
    "operating_leverage": ["costs and expenses", "cost of revenue",
                           "research and development", "marketing and sales",
                           "sales and marketing", "general and administrative",
                           "headcount", "income from operations",
                           "operating margin", "operating expenses"],
    "gross_margin": ["cost of revenue", "cost of sales", "gross margin",
                     "gross profit", "infrastructure"],
    "cash_conversion": ["operating activities", "cash provided by operating",
                        "working capital", "accounts receivable"],
    "free_cash_flow": ["capital expenditures", "purchases of property",
                       "investing activities", "data center", "infrastructure"],
    # Filers write "Cash, cash equivalents, and marketable securities were
    # $90.26 billion" — the phrase with "and" in it never appears, so a term
    # list carrying only the textbook wording matches nothing.
    "current_ratio": ["sources of liquidity", "liquidity", "capital resources",
                      "cash, cash equivalents", "credit facility",
                      "commercial paper", "marketable securities"],
    "dso": ["accounts receivable", "cash collections", "collections",
            "receivable"],
    "share_count": ["share repurchase", "repurchases of common stock",
                    "repurchase", "net share settlement",
                    "restricted stock unit", "dilutive",
                    "share-based compensation", "stock-based compensation"],
}

# A paragraph that merely mentions a line is a table caption. One that
# explains it says why.
_EXPLAINS = re.compile(
    r"primarily due to|primarily driven|driven by|\bdue to\b|"
    r"attributable to|reflect(?:s|ing)? |"
    r"consist(?:ed|ing) of|as a result of|resulting from|to support|"
    r"we anticipate|we expect",
    re.I)

_MDA_START = re.compile(
    r"Management'?s\s+Discussion\s+and\s+Analysis", re.I)
_MDA_END = re.compile(
    r"Item\s*3[.\s]+Quantitative\s+and\s+Qualitative", re.I)


def _strip_html(html: str) -> str:
    """Filing HTML to readable paragraphs.

    Block tags become newlines before anything else is removed, otherwise the
    whole filing collapses into one line and paragraph selection has nothing
    to select.
    """
    from html import unescape
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br[^>]*>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|td|th|li|h[1-6]|table)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text)


def _paragraphs(text: str) -> list[str]:
    out = []
    for chunk in re.split(r"\n\s*\n|\n(?=\s*[A-Z])", text):
        cleaned = " ".join(chunk.split())
        if len(cleaned) >= 80:
            out.append(cleaned)
    return out


def discussion(text: str, check_key: str) -> list[str]:
    """The management-discussion paragraphs that explain this check's line.

    Narrowed to the discussion section first: the same phrases appear in the
    risk factors and the notes, where they are boilerplate rather than an
    explanation of this quarter.
    """
    body = text
    starts = [m.end() for m in _MDA_START.finditer(text)]
    if starts:
        # The first hit is the table of contents; the section itself is the
        # last one that has real length after it.
        begin = starts[-1] if len(starts) > 1 else starts[0]
        end = _MDA_END.search(text, begin)
        body = text[begin:end.start() if end else len(text)]

    terms = DISCUSSION_TERMS.get(check_key) or []
    if not terms:
        return []

    # Ranked, not taken in document order. Every check's terms match SOMETHING
    # in the income-statement discussion, which comes first, so document order
    # handed the free-cash-flow question three paragraphs about operating
    # income and never reached the one about capital expenditure. A term
    # earlier in the list is more specific to the check, and a term in the
    # paragraph's opening words means the paragraph is ABOUT that line rather
    # than mentioning it in passing.
    scored, seen = [], set()
    for order, para in enumerate(_paragraphs(body)):
        low = para.lower()
        if not _EXPLAINS.search(para):
            continue
        score = 0
        for rank, term in enumerate(terms):
            where = low.find(term)
            if where < 0:
                continue
            weight = len(terms) - rank
            score += weight * (3 if where < 150 else 1)
        if not score:
            continue
        key = low[:90]
        if key in seen:
            continue
        seen.add(key)
        scored.append((score, order, para[:1200]))

    if not scored:
        return []
    # A weak match is worse than no match: it fills the prompt with the
    # income-statement discussion when the question was about capital
    # spending, and the model then writes about the wrong thing. Anything
    # scoring well below the best match is dropped rather than padded in.
    scored.sort(key=lambda row: (-row[0], row[1]))
    floor = scored[0][0] * 0.45
    best = [row for row in scored[:MAX_QUOTES] if row[0] >= floor]
    best.sort(key=lambda row: row[1])          # back into reading order
    return [para for _score, _order, para in best]


def _primary_document(cik: str, accn: str) -> str | None:
    """The filing's main document, from the submission's own file index."""
    import fundamentals_engine as fe
    base = _ARCHIVE.format(cik=str(cik).lstrip("0"), accn=accn.replace("-", ""))
    try:
        resp = fe._req_module.get(base + "index.json", timeout=15,
                                  headers=_HEADERS)
        if resp.status_code != 200:
            return None
        items = ((resp.json().get("directory") or {}).get("item") or [])
    except Exception as exc:
        logger.warning("quarter brief: filing index failed for %s: %s", accn, exc)
        return None

    best, best_size = None, 0
    for item in items:
        name = str(item.get("name") or "")
        if not name.lower().endswith((".htm", ".html")):
            continue
        low = name.lower()
        if _VIEWER_PAGE.match(low) or _EXHIBIT.search(low):
            continue
        if "index" in low or low.startswith("filingsummary"):
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size > best_size:
            best, best_size = name, size
    return base + best if best else None


def filing_text(cik: str, accn: str) -> tuple[str, str | None]:
    """(plain text, url) for the filing that reported these figures."""
    if not cik or not accn:
        return "", None
    url = _primary_document(cik, accn)
    if not url:
        return "", None
    import fundamentals_engine as fe
    try:
        resp = fe._req_module.get(url, timeout=30, headers=_HEADERS)
        if resp.status_code != 200:
            return "", url
        raw = resp.content or b""
        if len(raw) > MAX_DOCUMENT_BYTES:
            logger.info("quarter brief: %s is %d bytes, skipping", url, len(raw))
            return "", url
        return _strip_html(raw.decode("utf-8", "ignore")), url
    except Exception as exc:
        logger.warning("quarter brief: document fetch failed for %s: %s", url, exc)
        return "", url


# ── 3. The prose, over the evidence and nothing else ──────────────────────────

SYSTEM = (
    "You explain one financial check on one quarter to someone who is "
    "learning to read filings. You are an analysis assistant and never a "
    "source of financial facts. Use only the evidence and the quoted filing "
    "text supplied to you. Never state a figure that is not in the evidence, "
    "never bring in anything you recall about the company, its stock price, "
    "its competitors or later events, and never give investment advice. If "
    "the evidence does not explain the result, say plainly that the filing "
    "does not say and name what a reader would have to look at next."
)


def _fmt(value):
    if value is None:
        return "not filed"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:,.2f}" if abs(number) < 1000 else f"{number:,.0f}"


def _evidence_block(rows):
    lines = []
    for row in rows:
        change = (f"  ({row['change'] * 100:+.1f}%)"
                  if row.get("change") is not None else "")
        lines.append(f"- {row['label']}: {_fmt(row['now'])} "
                     f"vs {_fmt(row['prior'])} prior{change}")
    return "\n".join(lines) or "No component lines were tagged in this filing."


def compose(check: dict, rows: list[dict], quotes: list[str], *,
            company: str, period_end: str, api_key: str | None):
    """(brief, why_not). Every failure says what happened.

    This used to swallow everything and return None, so a missing package, a
    rejected model name, a rate limit and a response cut off by the token
    budget all surfaced to the reader as the same sentence: that no API key
    was configured. The key was configured. Whatever goes wrong now, it says
    so.
    """
    if not api_key:
        return None, ("No ANTHROPIC_API_KEY is set on this server, so the "
                      "filing's own account of this is not available.")
    import json as _json

    prompt = f"""Company: {company}
Quarter ended: {period_end}
Check: {check.get('name')} — graded {check.get('verdict')}
What the check computed: {check.get('value')} ({check.get('basis') or 'no basis given'})

COMPONENT LINES FROM THE SAME FILING
{_evidence_block(rows)}

WHAT MANAGEMENT SAID, quoted from this filing's discussion section
{chr(10).join('"' + q + '"' for q in quotes) or 'No relevant passage was found in the filing text.'}

Write JSON with these keys and nothing else:
  "headline": one sentence, under 20 words, naming the actual cause.
  "paragraphs": 2 to 3 short paragraphs. The first says which component line
     moved and by how much. The second says why, in management's own terms,
     and may quote a short phrase from the filing. The third, if the evidence
     supports it, says whether this looks like spending that builds something
     or spending that is getting away from them — and if the evidence does
     not support saying, say that instead.
  "watch": 2 or 3 short items to check next quarter, each a specific line or
     ratio, not general advice.
  "grounded": true only if the filing's own words explained it; false if you
     are working from the numbers alone."""

    try:
        import anthropic
    except ImportError:
        return None, ("The anthropic package is not installed on this server, "
                      "so the filing's own account of this is not available.")

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        logger.warning("quarter brief: model call failed: %s", exc)
        return None, f"The model call failed: {exc}"

    text = "".join(getattr(b, "text", "") for b in response.content).strip()
    stopped = getattr(response, "stop_reason", None)
    if not text:
        return None, f"The model returned nothing (stop reason: {stopped})."

    # Truncation first. A JSON object cut off mid-string has no closing brace,
    # so the "did it answer in prose" test fires and reports the wrong fault.
    if stopped == "max_tokens":
        logger.warning("quarter brief: answer truncated at %d tokens", MAX_TOKENS)
        return None, ("The answer was cut off by the token budget before it "
                      "was complete.")

    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        logger.warning("quarter brief: no JSON in the answer (%s)", text[:200])
        return None, "The model answered in prose rather than the JSON asked for."
    try:
        out = _json.loads(match.group(0))
    except ValueError as exc:
        logger.warning("quarter brief: unparseable answer (%s): %s", stopped, exc)
        return None, f"The model's answer would not parse: {exc}"
    if not isinstance(out, dict):
        return None, "The model's answer was not an object."

    brief = {
        "headline": str(out.get("headline") or "").strip(),
        "paragraphs": [str(p).strip() for p in (out.get("paragraphs") or []) if str(p).strip()],
        "watch": [str(w).strip() for w in (out.get("watch") or []) if str(w).strip()],
        "grounded": bool(out.get("grounded")),
    }
    if not brief["paragraphs"]:
        return None, "The model returned no paragraphs."
    return brief, None


# ── Putting it together ───────────────────────────────────────────────────────

def explain(ticker: str, check: dict, *, facts: dict, cik: str | None,
            period_end: str, prior_end: str | None, accn: str | None = None,
            ytd_prior_end: str | None = None, api_key: str | None = None) -> dict:
    """Evidence, quotes and prose for one flagged check."""
    check_key = check.get("key") or ""
    rows = decompose(facts, check_key, period_end, prior_end, ytd_prior_end)

    text, url = filing_text(cik, accn) if accn else ("", None)
    quotes = discussion(text, check_key) if text else []
    if quotes:
        # Keep the prompt small; the first passages are the closest matches.
        total, kept = 0, []
        for quote in quotes:
            if total + len(quote) > MAX_DISCUSSION_CHARS:
                break
            kept.append(quote)
            total += len(quote)
        quotes = kept

    brief, why_not = compose(
        check, rows, quotes,
        company=(facts or {}).get("entityName") or ticker.upper(),
        period_end=period_end, api_key=api_key)

    return {
        "available": True,
        "ticker": ticker.upper(),
        "check": check_key,
        "name": check.get("name"),
        "verdict": check.get("verdict"),
        "period_end": period_end,
        "evidence": rows,
        # Written from the figures, always. Most of "why" is arithmetic, and
        # a reader who pressed a button labelled "why is this flagged" should
        # never be handed a table and an apology.
        "plain": narrate(check, rows),
        "quotes": quotes,
        "filing_url": url,
        "brief": brief,
        "version": BRIEF_VERSION,
        # Said plainly. Guessing at the reason is what sent the reader to
        # check an API key that was already set.
        "note": None if brief else (
            "Written from the figures in the filing. " + (why_not or "")),
        "quotes_note": None if quotes else (
            "Management's own discussion of this could not be read out of the "
            "filing document."),
    }
