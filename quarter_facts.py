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

# A 52/53-week fiscal year does not repeat on the same calendar date. NVIDIA's
# quarters end on a Sunday, so the same quarter a year earlier ended 26 July
# one year and 27 July the next — and a 53-week year shifts one quarter by a
# further week. Requiring an exact date silently dropped every prior-year
# column for every filer on that calendar, which is most retailers and a good
# deal of tech. Ten days is wide enough for the drift and far short of the
# ninety-odd between one quarter and the next.
PERIOD_SLACK_DAYS = 10

# Pre-tax income, which is where a bank's income statement stops. There is no
# OperatingIncomeLoss anywhere in JPMorgan's filings — a bank has no operating
# section separate from a financing one, because financing IS the operation.
PRETAX_TAGS = [
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
]

# Each field: the label the form uses, and the XBRL tags to try in order.
#
# Order is preference, not priority — _pick gathers candidates across every
# tag and takes the most recent period, falling back to this order only to
# break a tie inside one period. So the general tags lead and the sector
# variants follow: a software company that tags both keeps the general
# reading, and a bank that tags neither of the first four still resolves.
#
# The sector tags are not decoration. A bank files RevenuesNetOfInterestExpense
# and NoninterestExpense and nothing else; an insurer files PremiumsEarnedNet
# and BenefitsLossesAndExpenses; a utility files RegulatedAndUnregulated-
# OperatingRevenue. Without them the drill anchored on nothing and the whole
# walkthrough came back empty for every company outside tech and retail.
QUARTER_TAGS = {
    "rev":    ("Revenue", ["RevenueFromContractWithCustomerExcludingAssessedTax",
                           "Revenues", "RevenueFromContractWithCustomerIncludingAssessedTax",
                           "SalesRevenueNet",
                           # sector variants
                           "RevenuesNetOfInterestExpense",
                           "RegulatedAndUnregulatedOperatingRevenue",
                           "PremiumsEarnedNet",
                           "InterestAndDividendIncomeOperating"]),
    "cogs":   ("Cost of revenue", ["CostOfRevenue", "CostOfGoodsAndServicesSold",
                                   "CostOfGoodsSold", "CostOfServices",
                                   "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"]),
    "opex":   ("Total operating expenses", ["OperatingExpenses",
                                            # sector variants
                                            "NoninterestExpense",
                                            "BenefitsLossesAndExpenses",
                                            "OperatingCostsAndExpenses"]),
    "opinc":  ("Operating income", ["OperatingIncomeLoss"] + PRETAX_TAGS),
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
                "Revenues", "RevenueFromContractWithCustomerIncludingAssessedTax",
                "RevenuesNetOfInterestExpense",
                "RegulatedAndUnregulatedOperatingRevenue",
                "PremiumsEarnedNet"]),
}

# When a sector tag wins, the generic label is wrong on the page. "Revenue"
# for a bank is "Total net revenue" and it is net of interest expense, which
# is a different thing from the top line of a software company. Naming the
# line the way the filing names it is most of what makes the drill teach.
TAG_LABELS = {
    "RevenuesNetOfInterestExpense":            "Total net revenue",
    "InterestAndDividendIncomeOperating":      "Interest and dividend income",
    "RegulatedAndUnregulatedOperatingRevenue": "Operating revenues",
    "PremiumsEarnedNet":                       "Net premiums earned",
    "NoninterestExpense":                      "Total noninterest expense",
    "BenefitsLossesAndExpenses":               "Total benefits, losses and expenses",
    "OperatingCostsAndExpenses":               "Total operating costs and expenses",
    PRETAX_TAGS[0]:                            "Income before income taxes",
    PRETAX_TAGS[1]:                            "Income before income taxes",
}


def _label_for(found: dict | None, default: str, suffix: str = "") -> str:
    """The filing's own name for the line, when the tag tells us one."""
    base = TAG_LABELS.get((found or {}).get("tag")) or default
    return base + suffix


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


def _pick(facts, tags, *, window, end=None, near=None, slack=0):
    """The best fact matching a duration window, or an instant.

    `window` is None for instants. `end` pins the period end exactly. `near`
    pins it approximately, within `slack` days, and takes the closest.

    Tag order expresses preference, not priority. It used to short-circuit on
    the first tag with any data at all, which is wrong whenever a filer has
    changed tags: NVIDIA's older revenue tag stops in 2020, so the walkthrough
    confidently returned a quarter from six years ago and every other figure
    was then filtered to that same dead date. Candidates are gathered across
    every tag and the most recent period wins; the tag list only breaks ties
    within one period.
    """
    from datetime import datetime

    def _date(text):
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None

    target = _date(near) if near else None
    candidates = []
    for rank, tag in enumerate(tags):
        for fact in _facts_for(tag, facts):
            fact_end = fact.get("end")
            if end and fact_end != end:
                continue
            distance = 0
            if target:
                actual = _date(fact_end)
                if actual is None:
                    continue
                distance = abs((actual - target).days)
                if distance > slack:
                    continue
            if window is None:
                if "start" in fact:
                    continue                      # a duration, not an instant
            else:
                length = _days(fact)
                if length is None or not (window[0] <= length <= window[1]):
                    continue
            candidates.append((distance, fact, rank, tag))

    if not candidates:
        return None

    # Nearest to the target date first when one was given; then the latest
    # period; then the preferred tag; then the latest filing, so an amended
    # figure supersedes.
    candidates.sort(key=lambda c: (-c[0], c[1].get("end") or "", -c[2],
                                   c[1].get("filed") or ""))
    _distance, best, _rank, tag = candidates[-1]
    return {"value": best.get("val"), "tag": tag,
            "end": best.get("end"), "start": best.get("start"),
            "form": best.get("form"), "filed": best.get("filed"),
            "accn": best.get("accn")}


def _prior_instant(facts, tags, current, prior_end):
    """The comparative balance-sheet column, as the filing presents it.

    Income-statement columns in a 10-Q are this quarter against the same
    quarter a year ago. Balance-sheet columns are not: they are this date
    against the previous fiscal year end. Meta's June 2026 10-Q prints
    31 December 2025 beside 30 June 2026, so a reader typing what is on the
    page must be compared against that date and not against June 2025.

    The filing states which date it means: both columns are tagged in the same
    submission, so the fact sharing this one's accession number is the column
    printed beside it. The year-ago instant remains the fallback for filers
    whose data does not carry one.
    """
    accn = (current or {}).get("accn")
    if accn:
        best = None
        for tag in tags:
            for fact in _facts_for(tag, facts):
                if fact.get("accn") != accn or "start" in fact:
                    continue
                if not fact.get("end") or fact["end"] >= (current or {}).get("end", ""):
                    continue
                if best is None or fact["end"] > best["end"]:
                    best = fact
            if best:
                return {"value": best.get("val"), "tag": tag, "end": best.get("end"),
                        "start": None, "form": best.get("form"),
                        "filed": best.get("filed"), "comparative": True}
    if prior_end:
        return _pick(facts, tags, window=None, near=prior_end,
                     slack=PERIOD_SLACK_DAYS)
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


# ── What kind of company is this ──────────────────────────────────────────────
#
# Three of the seven checks are not "missing data" at a bank. They are category
# errors. JPMorgan has never tagged AssetsCurrent in its life — not because the
# figure is late, but because a bank does not present a classified balance
# sheet at all. There is no twelve-month line drawn through its assets, so
# there is no current ratio to compute. Likewise CostOfRevenue: a bank has no
# cost of revenue, so gross margin is not thin or fat, it is undefined.
#
# Saying "waiting on total current assets" to someone reading a bank filing
# sends them hunting for a subtotal that does not exist and will never exist.
# That is worse than saying nothing. So the shape of the filer is detected
# first, and a check that cannot apply says so and explains why.
#
# Detection reads which concepts the filer has EVER tagged, across its whole
# history, rather than this quarter alone — a tag missing from one quarter is
# a gap, a tag missing from twenty years is a structure.

SHAPE_SIGNATURES = (
    ("bank", ["RevenuesNetOfInterestExpense", "NoninterestExpense",
              "InterestIncomeExpenseNet", "Deposits",
              "ProvisionForLoanLeaseAndOtherLosses"]),
    ("insurer", ["PremiumsEarnedNet", "BenefitsLossesAndExpenses",
                 "PolicyholderBenefitsAndClaimsIncurredNet",
                 "LiabilityForClaimsAndClaimsAdjustmentExpense"]),
    ("reit", ["RealEstateInvestmentPropertyNet", "RealEstateRevenueNet",
              "OperatingLeaseLeaseIncome"]),
    ("utility", ["RegulatedAndUnregulatedOperatingRevenue",
                 "PublicUtilitiesPropertyPlantAndEquipmentNet",
                 "UtilitiesOperatingExpense"]),
)

SHAPES = {
    "bank": {
        "phrase": "a bank",
        "label": "Bank / financial",
        "explains": (
            "A bank's statements are built differently. Money is the inventory, "
            "so there is no cost of revenue and no gross margin; the balance "
            "sheet is unclassified, so nothing is split into current and "
            "long-term; and lending is the operation, so the income statement "
            "runs interest income, interest expense, net interest income, "
            "provision for credit losses, noninterest expense, pre-tax income. "
            "Read a bank on net interest margin, efficiency ratio, credit "
            "provisions and capital — not on the three checks below that a "
            "bank filing has no lines for."
        ),
    },
    "insurer": {
        "phrase": "an insurer",
        "label": "Insurer",
        "explains": (
            "An insurer collects premiums now and pays claims later, so its "
            "balance sheet is unclassified — reserves have no twelve-month "
            "line through them — and it has no cost of revenue. Read it on "
            "the combined ratio, reserve development and investment income."
        ),
    },
    "reit": {
        "phrase": "a REIT",
        "label": "REIT / real estate",
        "explains": (
            "A REIT's assets are buildings, so nothing on its balance sheet is "
            "current and there is no cost of revenue. Depreciation on property "
            "also swamps reported net income, which is why REITs are read on "
            "funds from operations rather than on earnings or free cash flow."
        ),
    },
    "utility": {
        "phrase": "a regulated utility",
        "label": "Regulated utility",
        "explains": (
            "A regulated utility earns an allowed return set by its regulator, "
            "so margin is an outcome of the rate case rather than a sign of "
            "competitive strength. Read it on rate base growth, allowed return "
            "on equity and the capital plan."
        ),
    },
    "unclassified": {
        "phrase": "a company that does not classify its balance sheet",
        "label": "Unclassified balance sheet",
        "explains": (
            "This filer does not split its balance sheet into current and "
            "long-term, so there is no current-assets subtotal to divide. "
            "That is a presentation choice its industry allows, not a gap."
        ),
    },
    "operating": {
        "phrase": "this kind of company",
        "label": "Operating company",
        "explains": "",
    },
}

# Why each check cannot run, by shape. Pure lookup on one word, so a saved
# quarter can be regraded later without going back to EDGAR.
_NA_NO_COGS = ("There is no cost-of-revenue line in this filing, so there is "
               "no gross profit to take a margin on. This is not a missing "
               "number — the statement is not built that way.")
_NA_UNCLASSIFIED = ("This company does not present a classified balance sheet, "
                    "so there is no \u201ctotal current assets\u201d or "
                    "\u201ctotal current liabilities\u201d subtotal to divide. "
                    "The ratio is undefined here rather than unknown.")
_NA_NO_DSO = ("Days sales outstanding measures how long a customer takes to "
              "pay an invoice. This company does not sell on invoice terms, "
              "so there is no collection cycle to measure.")

# Morgan Stanley's operating cash flow for the six months to June 2026 was
# NEGATIVE 9.8 billion against 11.1 billion of net income, and there is
# nothing wrong with the company. A dealer's operating section is dominated
# by changes in trading inventory; a bank's by loan origination and deposit
# flows. The figure swings tens of billions between quarters and says nothing
# about earnings quality, which is the only thing this check was asking.
# Grading it "Flag" taught the exact opposite of the truth.
_NA_FINANCIAL_CASH = (
    "Operating cash flow at a financial firm is dominated by changes in "
    "trading inventory, loans and deposits, not by whether profits turn into "
    "cash. It swings tens of billions between quarters and can be deeply "
    "negative in a strong one \u2014 Morgan Stanley's was minus 9.8 billion "
    "in the first half of 2026 on 11.1 billion of profit. Nothing is wrong "
    "there; the ratio is simply measuring something else.")

SHAPE_NOT_APPLICABLE = {
    "bank": {
        "cash_conversion": _NA_FINANCIAL_CASH + " Read return on tangible "
                           "common equity and the efficiency ratio instead.",
        "gross_margin": _NA_NO_COGS + " A bank's equivalent question is net "
                        "interest margin: what it earns on assets minus what "
                        "it pays for funding.",
        "current_ratio": _NA_UNCLASSIFIED + " A bank's liquidity is read on "
                         "the liquidity coverage ratio and its deposit mix "
                         "instead.",
        "dso": _NA_NO_DSO + " The equivalent question at a bank is credit "
               "quality: net charge-offs and non-performing loans.",
        "free_cash_flow": ("Capital expenditure is not what constrains a bank. "
                           "Its capacity to lend, pay dividends and buy back "
                           "stock is set by regulatory capital \u2014 read CET1 "
                           "instead of free cash flow."),
    },
    "insurer": {
        "cash_conversion": _NA_FINANCIAL_CASH + " For an insurer the cash "
                           "arrives with the premium and leaves with the "
                           "claim, years apart.",
        "gross_margin": _NA_NO_COGS + " An insurer's equivalent is the combined "
                        "ratio: claims plus expenses against premiums earned. "
                        "Below 100% means the underwriting itself made money.",
        "current_ratio": _NA_UNCLASSIFIED,
        "dso": _NA_NO_DSO,
        "free_cash_flow": ("An insurer holds float \u2014 premiums collected "
                           "before claims are paid \u2014 so operating cash "
                           "flow is a timing artefact as much as a result. "
                           "Read underwriting profit and reserve development."),
    },
    "reit": {
        "gross_margin": _NA_NO_COGS,
        "current_ratio": _NA_UNCLASSIFIED,
        "dso": _NA_NO_DSO,
        "free_cash_flow": ("A REIT's capital spending is acquiring buildings, "
                           "not maintaining them, and the two are not split out. "
                           "Read funds from operations (FFO) and the payout "
                           "ratio against it instead."),
    },
    "utility": {
        "gross_margin": ("A regulated utility's margin is set by its rate case, "
                         "not by pricing power, so the level says more about "
                         "the regulator than the business."),
    },
    "unclassified": {
        "current_ratio": _NA_UNCLASSIFIED,
    },
    "operating": {},
}

# The names the form should use for a field when the shape changes what the
# line is called on the statement.
SHAPE_FIELD_LABELS = {
    "bank": {"rev": "Total net revenue", "opex": "Total noninterest expense",
             "opinc": "Income before income taxes"},
    "insurer": {"rev": "Total revenues",
                "opex": "Total benefits, losses and expenses",
                "opinc": "Income before income taxes"},
    "utility": {"rev": "Operating revenues"},
}


def _ever(facts: dict, tags) -> bool:
    """Has this filer ever tagged any of these concepts, in any period?"""
    return any(_facts_for(tag, facts) for tag in tags)


def detect_shape(facts: dict) -> str:
    """Which of the shapes above this filer is, from its own tag history."""
    for shape, signature in SHAPE_SIGNATURES:
        if _ever(facts, signature):
            return shape
    if not _ever(facts, INSTANT_TAGS["ca"][1]):
        return "unclassified"
    return "operating"


def context_for(shape: str | None) -> dict:
    """What the grader needs to know about this kind of company.

    A pure function of one word, deliberately: a saved quarter stores the
    shape and is regraded from it offline, without a second trip to EDGAR.
    """
    shape = shape if shape in SHAPES else "operating"
    return {
        "shape": shape,
        "shape_label": SHAPES[shape]["label"],
        "shape_phrase": SHAPES[shape]["phrase"],
        "explains": SHAPES[shape]["explains"],
        "not_applicable": dict(SHAPE_NOT_APPLICABLE.get(shape) or {}),
        "labels": dict(SHAPE_FIELD_LABELS.get(shape) or {}),
    }


def profile(facts: dict) -> dict:
    """The filer's shape and everything that follows from it."""
    return context_for(detect_shape(facts))


def all_contexts() -> dict:
    """Every shape, keyed by name — handed to the page once at render.

    A quarter saved months ago stores one word. Reopening it should put the
    same explanations back on screen without a network round trip just to
    learn that banks have no current ratio, which has not changed since.
    """
    return {shape: context_for(shape) for shape in SHAPES}


def fetch_facts(ticker: str) -> tuple[dict | None, str | None]:
    """The company-facts document and the CIK, or (None, None).

    Separated so the guided walkthrough can download it once and use it for
    both the figures and the rebuilt statements. It is tens of megabytes for a
    large filer; fetching it twice in one request doubles the slowest part of
    the page.
    """
    import fundamentals_engine as fe
    cik, _name = fe._edgar_cik(ticker)
    if not cik:
        return None, None
    try:
        resp = fe._req_module.get(
            fe._EDGAR_FACTS_URL.format(cik=cik),
            timeout=15, headers=fe._EDGAR_HEADERS,
        )
        if resp.status_code != 200:
            logger.warning("quarter facts: EDGAR %s for %s", resp.status_code, ticker)
            return None, cik
        return resp.json(), cik
    except Exception as exc:
        logger.warning("quarter facts: fetch failed for %s: %s", ticker, exc)
        return None, cik


# ── When there is nothing to read ─────────────────────────────────────────────
#
# "No quarterly filing found for SKHY" is true and useless. SKHY is SK hynix,
# a Korean company that listed an ADR on Nasdaq in July 2026: it is a foreign
# private issuer, so it files a 20-F once a year and 6-K interim reports, and
# it reports under IFRS rather than US GAAP. Its SEC company facts contain
# nothing but filing-fee data. No amount of extra tag coverage will ever make
# that readable, and a reader who is told only "not found" will reasonably
# assume the app is broken and try again.
#
# So the failure says which of these it is, and names the forms the company
# actually files, so the next question is obvious.

_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# Forms a foreign private issuer files instead of the 10-Q and 10-K.
FOREIGN_FORMS = {"20-F", "20-F/A", "40-F", "40-F/A", "6-K", "6-K/A",
                 "F-1", "F-1/A", "F-3", "F-3/A", "F-4", "F-6", "F-10"}
DOMESTIC_QUARTERLY = {"10-Q", "10-Q/A"}


def _recent_forms(cik: str) -> list[str]:
    """The form types this filer has actually submitted, newest first."""
    if not cik:
        return []
    try:
        import fundamentals_engine as fe
        resp = fe._req_module.get(_SUBMISSIONS_URL.format(cik=cik),
                                  timeout=10, headers=fe._EDGAR_HEADERS)
        if resp.status_code != 200:
            return []
        recent = ((resp.json().get("filings") or {}).get("recent") or {})
        seen, out = set(), []
        for form in (recent.get("form") or []):
            if form not in seen:
                seen.add(form)
                out.append(form)
        return out
    except Exception as exc:
        logger.warning("quarter facts: submissions lookup failed for %s: %s",
                       cik, exc)
        return []


def _listed(forms, limit=4):
    return ", ".join(forms[:limit]) if forms else ""


def explain_gap(ticker: str, facts: dict | None, cik: str | None) -> str:
    """Why this ticker has no quarter to read, in the reader's terms."""
    ticker = (ticker or "").upper()

    if not cik:
        return (f"{ticker} is not in the SEC's list of registered filers. ETFs, "
                "index funds, trusts, and many foreign and over-the-counter "
                "listings never file with the SEC at all, so there is no 10-Q "
                "behind them to read. The drill works on US-listed operating "
                "companies that file quarterly.")

    us_gaap = ((facts or {}).get("facts") or {}).get("us-gaap") or {}
    name = (facts or {}).get("entityName") or ticker
    forms = _recent_forms(cik)
    foreign = [f for f in forms if f in FOREIGN_FORMS]

    if not us_gaap:
        if foreign:
            return (f"{name} is a foreign private issuer. It files "
                    f"{_listed(foreign)} with the SEC \u2014 a 20-F once a year "
                    "and 6-K interim reports \u2014 not the quarterly 10-Q this "
                    "drill reads, and it reports under IFRS rather than US "
                    "GAAP, so there is no tagged quarterly data here at all. "
                    "Its numbers are published on its own investor-relations "
                    "site on its home market's calendar.")
        return (f"{name} has filed with the SEC"
                + (f" ({_listed(forms)})" if forms else "")
                + ", but has no tagged US GAAP financial data. That is usual "
                  "for a company that has only registered so far and not yet "
                  "filed its first financial report.")

    if forms and not any(f in DOMESTIC_QUARTERLY for f in forms):
        return (f"{name} files US GAAP figures"
                + (f" ({_listed(forms)})" if forms else "")
                + ", but no 10-Q. Some filers report only annually, and the "
                  "drill is built on the quarterly statements.")

    return (f"{name} has US GAAP data at the SEC, but no quarterly period this "
            "could anchor on \u2014 usually a filer between its registration "
            "and its first 10-Q, or one that tags its revenue under a name "
            "this does not know yet.")


def latest_quarter(ticker: str, facts: dict | None = None) -> dict | None:
    """Every figure the drill asks for, as the company filed it.

    Returns {"period_end": ..., "fields": {key: {value, tag, ...}}} or None
    when the company cannot be resolved or has filed no usable quarter.
    """
    if facts is None:
        facts, _cik = fetch_facts(ticker)
        if facts is None:
            return None
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
            fields[key] = dict(found, label=_label_for(found, label))
        if prior_end:
            found_prior = _pick(facts, tags, window=QUARTER_DAYS,
                                near=prior_end, slack=PERIOD_SLACK_DAYS)
            if found_prior:
                fields[key + "P"] = dict(
                    found_prior, label=_label_for(found_prior, label, ", year ago"))

    for key, (label, tags) in INSTANT_TAGS.items():
        found = _pick(facts, tags, window=None, end=end)
        if found:
            fields[key] = dict(found, label=_label_for(found, label))
        # The comparative column on a balance sheet in a 10-Q is the previous
        # FISCAL YEAR END, not the same date a year earlier. Reading it as a
        # year-ago instant compares the reader against a date the filing in
        # front of them does not print.
        found_prior = _prior_instant(facts, tags, found, prior_end)
        if found_prior:
            fields[key + "P"] = dict(
                found_prior, label=_label_for(found_prior, label, ", prior"))

    for key, (label, tags) in YTD_TAGS.items():
        found = _pick(facts, tags, window=YTD_DAYS, end=end)
        if found:
            fields[key] = dict(found, label=_label_for(found, label))

    _derive_operating_expenses(facts, fields, end, prior_end)

    return {
        "ticker": ticker.upper(),
        "company": name,
        "profile": profile(facts),
        "period_end": end,
        "prior_end": prior_end,
        "form": anchor.get("form"),
        "filed": anchor.get("filed"),
        "fields": fields,
    }


def _derive_operating_expenses(facts, fields, end, prior_end):
    # Note: the prior column is matched near its date, not on it, for the same
    # 52/53-week reason as everything else here.
    """Total operating expenses, when the filer never tagged it directly.

    Many filers present one "Total costs and expenses" line — CostsAndExpenses
    — which includes cost of revenue and is therefore not what the drill asks
    for. Meta files 42,026 there for a quarter whose operating expenses are
    30,696; handing that back as the answer would tell a reader who read the
    statement correctly that they were twelve billion dollars wrong.

    So it is derived rather than substituted, and only when both halves are
    present for the same period. The derivation is labelled, because a figure
    this app computed is not a figure the company filed.

    Two derivations, tried in order:

      CostsAndExpenses less cost of revenue. Meta's shape.

      Revenue less cost of revenue less operating income. KLA's shape, and
      the reason it exists: KLA has never tagged OperatingExpenses in its
      life, and its CostsAndExpenses stops in 2015. Its income statement runs
      revenue, cost of revenue, R&D, SG&A, straight to operating income, with
      no subtotal in between — so the check sat waiting for a line the filing
      does not contain. This second form is forced by the arithmetic of an
      income statement rather than assembled from components, which is its
      virtue: it captures whatever else sits in the operating section. For
      KLA that matters, because R&D plus SG&A comes to 679,897 against a
      derived 670,645, so there is another line in there. Adding up the
      components would have quietly missed it.

    Both self-gate on cost of revenue, which is what keeps them away from
    financial filers: a bank has no such line, and its "operating income" is
    pre-tax income, so the subtraction would produce a confident wrong
    answer rather than nothing.
    """
    for key, period in (("opex", end), ("opexP", prior_end)):
        if key in fields or not period:
            continue
        exact = (period == end)
        where = {"end": period} if exact else {"near": period, "slack": PERIOD_SLACK_DAYS}

        cogs = _pick(facts, QUARTER_TAGS["cogs"][1], window=QUARTER_DAYS, **where)
        if not cogs:
            continue                    # no cost of revenue: not this shape of filer

        total = _pick(facts, ["CostsAndExpenses"], window=QUARTER_DAYS, **where)
        value = source = None
        if total and total.get("end") == cogs.get("end"):
            try:
                value = float(total["value"]) - float(cogs["value"])
                source = "CostsAndExpenses \u2212 " + cogs["tag"]
            except (TypeError, ValueError):
                value = None

        if value is None:
            revenue = _pick(facts, QUARTER_TAGS["rev"][1], window=QUARTER_DAYS, **where)
            # The operating income this filer actually resolved to, not the
            # raw OperatingIncomeLoss tag. KLA never tags that one \u2014 its
            # operating income comes through the pre-tax concept, so hunting
            # for the literal tag found nothing and the derivation bailed,
            # while the rebuilt statement, which reads the resolved row,
            # printed the subtotal perfectly. Subtracting the same figure the
            # page shows also keeps the identity checkable by eye: take
            # revenue, take off cost of revenue, take off the operating
            # income printed above, and you land on this number.
            operating = fields.get("opinc" if key == "opex" else "opincP")
            if not operating:
                operating = _pick(facts, QUARTER_TAGS["opinc"][1],
                                  window=QUARTER_DAYS, **where)
            if not revenue or not operating:
                continue
            if not (revenue.get("end") == cogs.get("end") == operating.get("end")):
                continue                # three different periods is not a subtotal
            try:
                value = (float(revenue["value"]) - float(cogs["value"])
                         - float(operating["value"]))
            except (TypeError, ValueError):
                continue
            total = revenue
            source = (revenue["tag"] + " \u2212 " + cogs["tag"] + " \u2212 "
                      + (operating.get("tag") or "operating income"))

        label = QUARTER_TAGS["opex"][0] + ("" if key == "opex" else ", year ago")
        fields[key] = {
            "value": value,
            "tag": source,
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
        "profile": filed.get("profile") or context_for(None),
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


# ── Rebuilding the statements ─────────────────────────────────────────────────
#
# A number that appears in a box teaches where the form's fields are. A number
# with the line it came from teaches where the filing's are, which is the
# actual skill. XBRL gives the values, the tags and the periods but not the
# printed layout, so the statements are reconstructed here: the same rows in
# the same order, with the ones each check reads lit up beside it.
#
# This is a reconstruction and says so on the page. A filer who presents an
# unusual line will not have it here, and the link to the filing itself is
# always the authority.

# (row key, label, indent, tags). Indent mirrors how statements are set:
# 0 flush left for totals and headline lines, 1 for components.
INCOME_ROWS = [
    ("rev",    "Revenue", 0, QUARTER_TAGS["rev"][1]),
    ("cogs",   "Cost of revenue", 1, QUARTER_TAGS["cogs"][1]),
    ("gross",  "Gross profit", 0, ["GrossProfit"]),
    ("rnd",    "Research and development", 1, ["ResearchAndDevelopmentExpense"]),
    ("sales",  "Sales and marketing", 1, ["SellingAndMarketingExpense", "MarketingExpense"]),
    ("admin",  "General and administrative", 1,
     ["GeneralAndAdministrativeExpense", "SellingGeneralAndAdministrativeExpense"]),
    ("opex",   "Total operating expenses", 0, ["OperatingExpenses"]),
    ("costs",  "Total costs and expenses", 0, ["CostsAndExpenses"]),
    ("opinc",  "Operating income", 0, QUARTER_TAGS["opinc"][1]),
    ("pretax", "Income before income taxes", 1,
     ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]),
    ("tax",    "Provision for income taxes", 1, ["IncomeTaxExpenseBenefit"]),
    ("ni",     "Net income", 0, QUARTER_TAGS["ni"][1]),
    ("eps",    "Diluted earnings per share", 1, QUARTER_TAGS["eps"][1]),
    ("sh",     "Weighted-average shares, diluted", 1, QUARTER_TAGS["sh"][1]),
]

BALANCE_ROWS = [
    ("cash",   "Cash and cash equivalents", 1, ["CashAndCashEquivalentsAtCarryingValue"]),
    ("secs",   "Marketable securities", 1,
     ["MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
      "ShortTermInvestments"]),
    ("ar",     "Accounts receivable, net", 1, INSTANT_TAGS["ar"][1]),
    ("inv",    "Inventories", 1, ["InventoryNet"]),
    ("ca",     "Total current assets", 0, INSTANT_TAGS["ca"][1]),
    ("assets", "Total assets", 0, ["Assets"]),
    ("ap",     "Accounts payable", 1, ["AccountsPayableCurrent"]),
    ("cl",     "Total current liabilities", 0, INSTANT_TAGS["cl"][1]),
    ("ltd",    "Long-term debt", 1,
     ["LongTermDebtNoncurrent", "LongTermDebt"]),
    ("liab",   "Total liabilities", 0, ["Liabilities"]),
    ("equity", "Total stockholders' equity", 0, ["StockholdersEquity"]),
]

CASH_ROWS = [
    ("niy",    "Net income", 0, YTD_TAGS["niy"][1]),
    ("dep",    "Depreciation and amortization", 1,
     ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet"]),
    ("sbc",    "Stock-based compensation", 1, ["ShareBasedCompensation"]),
    ("cfo",    "Net cash provided by operating activities", 0, YTD_TAGS["cfo"][1]),
    ("capex",  "Purchases of property and equipment", 1, YTD_TAGS["capex"][1]),
]

# A bank's income statement is not the generic one with different numbers in
# it — it is a different sequence of lines. Rebuilding it as revenue, cost of
# revenue, gross profit would show a reader three blank rows and none of the
# lines they are actually looking at on the page in front of them.
BANK_INCOME_ROWS = [
    ("intinc", "Interest income", 1,
     ["InterestAndDividendIncomeOperating", "InterestIncomeOperating"]),
    ("intexp", "Interest expense", 1, ["InterestExpense", "InterestExpenseOperating"]),
    ("nii",    "Net interest income", 0,
     ["InterestIncomeExpenseNet",
      "InterestIncomeExpenseAfterProvisionForLoanLoss"]),
    ("nonint", "Noninterest revenue", 1, ["NoninterestIncome"]),
    ("rev",    "Total net revenue", 0,
     ["RevenuesNetOfInterestExpense", "Revenues"]),
    ("prov",   "Provision for credit losses", 1,
     ["ProvisionForLoanLeaseAndOtherLosses", "ProvisionForLoanAndLeaseLosses",
      "ProvisionForCreditLossesExpenseReversal"]),
    ("opex",   "Total noninterest expense", 0, ["NoninterestExpense"]),
    ("opinc",  "Income before income taxes", 0, PRETAX_TAGS),
    ("tax",    "Provision for income taxes", 1, ["IncomeTaxExpenseBenefit"]),
    ("ni",     "Net income", 0, QUARTER_TAGS["ni"][1]),
    ("eps",    "Diluted earnings per share", 1, QUARTER_TAGS["eps"][1]),
    ("sh",     "Weighted-average shares, diluted", 1, QUARTER_TAGS["sh"][1]),
]

BANK_BALANCE_ROWS = [
    ("cash",   "Cash and due from banks", 1,
     ["CashAndDueFromBanks", "CashAndCashEquivalentsAtCarryingValue"]),
    ("depbk",  "Deposits with banks", 1, ["InterestBearingDepositsInBanks"]),
    ("secs",   "Investment securities", 1,
     ["DebtSecuritiesAvailableForSaleExcludingAccruedInterest",
      "AvailableForSaleSecuritiesDebtSecurities", "MarketableSecurities"]),
    ("loans",  "Loans", 1,
     ["LoansAndLeasesReceivableNetReportedAmount",
      "FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss",
      "NotesReceivableNet"]),
    ("alll",   "Allowance for loan losses", 1,
     ["FinancingReceivableAllowanceForCreditLosses",
      "LoansAndLeasesReceivableAllowance"]),
    ("assets", "Total assets", 0, ["Assets"]),
    ("deposits", "Deposits", 1, ["Deposits"]),
    ("ltd",    "Long-term debt", 1, ["LongTermDebt", "LongTermDebtNoncurrent"]),
    ("liab",   "Total liabilities", 0, ["Liabilities"]),
    ("equity", "Total stockholders' equity", 0, ["StockholdersEquity"]),
]

INSURER_INCOME_ROWS = [
    ("prem",   "Net premiums earned", 1, ["PremiumsEarnedNet"]),
    ("invinc", "Net investment income", 1,
     ["NetInvestmentIncome", "GrossInvestmentIncomeOperating"]),
    ("rev",    "Total revenues", 0,
     ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]),
    ("losses", "Losses and loss adjustment expenses", 1,
     ["PolicyholderBenefitsAndClaimsIncurredNet",
      "LiabilityForClaimsAndClaimsAdjustmentExpenseClaimsIncurredNet"]),
    ("opex",   "Total benefits, losses and expenses", 0,
     ["BenefitsLossesAndExpenses", "OperatingCostsAndExpenses"]),
    ("opinc",  "Income before income taxes", 0, PRETAX_TAGS),
    ("tax",    "Provision for income taxes", 1, ["IncomeTaxExpenseBenefit"]),
    ("ni",     "Net income", 0, QUARTER_TAGS["ni"][1]),
    ("eps",    "Diluted earnings per share", 1, QUARTER_TAGS["eps"][1]),
    ("sh",     "Weighted-average shares, diluted", 1, QUARTER_TAGS["sh"][1]),
]

INCOME_ROWS_BY_SHAPE = {"bank": BANK_INCOME_ROWS, "insurer": INSURER_INCOME_ROWS}
BALANCE_ROWS_BY_SHAPE = {"bank": BANK_BALANCE_ROWS}

STATEMENT_TITLES = {
    "income":  ("Condensed consolidated statements of income", "Three months ended"),
    "balance": ("Condensed consolidated balance sheets", ""),
    "cash":    ("Condensed consolidated statements of cash flows", "Year to date"),
}

_FILING_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-Q"


def _row_values(facts, tags, *, window, now, prior, current_fact=None):
    """(now, prior) for one statement row, or (None, None) when untagged."""
    if window is None:
        first = _pick(facts, tags, window=None, end=now)
        second = _prior_instant(facts, tags, first, prior)
    else:
        first = _pick(facts, tags, window=window, end=now)
        second = (_pick(facts, tags, window=window, near=prior,
                        slack=PERIOD_SLACK_DAYS) if prior else None)
    return first, second


def statements(facts: dict, period_end: str, prior_end: str | None,
               ytd_prior_end: str | None = None, shape: str | None = None) -> dict:
    """The three statements, rebuilt, with only the rows this filer tagged.

    A row the company never tagged is dropped rather than shown empty: a
    statement full of blanks teaches nothing and looks broken.
    """
    income_rows = INCOME_ROWS_BY_SHAPE.get(shape) or INCOME_ROWS
    balance_rows = BALANCE_ROWS_BY_SHAPE.get(shape) or BALANCE_ROWS

    out = {}
    for name, rows, window, now, prior in (
        ("income",  income_rows,  QUARTER_DAYS, period_end, prior_end),
        ("balance", balance_rows, None,         period_end, prior_end),
        ("cash",    CASH_ROWS,    YTD_DAYS,     period_end, ytd_prior_end or prior_end),
    ):
        built = []
        for key, label, indent, tags in rows:
            first, second = _row_values(facts, tags, window=window, now=now, prior=prior)
            if not first:
                continue
            built.append({
                "key": key, "label": label, "indent": indent,
                "now": first.get("value"), "prior": (second or {}).get("value"),
                "tag": first.get("tag"),
                "prior_end": (second or {}).get("end"),
            })
        # Only the generic layout needs the derived subtotal; the sector
        # layouts read a total the filer prints outright.
        if name == "income" and rows is INCOME_ROWS:
            _insert_derived_opex(built)
        title, sub = STATEMENT_TITLES[name]
        out[name] = {"title": title, "sub": sub, "rows": built}
    return out


def _insert_derived_opex(rows: list) -> None:
    """Show the operating-expense subtotal a filer never printed.

    Meta files one "Total costs and expenses" that includes cost of revenue.
    The drill asks for operating expenses, and the check that reads them has
    to have a row to point at — otherwise the guided walkthrough highlights
    nothing on the line the reader most needs to see. It is inserted where the
    subtotal would sit, labelled as computed rather than filed.
    """
    by_key = {r["key"]: r for r in rows}
    if "opex" in by_key or "cogs" not in by_key:
        return
    cogs = by_key["cogs"]

    def less(*values):
        if any(v is None for v in values):
            return None
        total = values[0]
        for v in values[1:]:
            total -= v
        return total

    # Meta's shape: one combined "Total costs and expenses" to subtract from.
    anchor = by_key.get("costs")
    if anchor:
        now = less(anchor.get("now"), cogs.get("now"))
        prior = less(anchor.get("prior"), cogs.get("prior"))
        tag = "CostsAndExpenses − " + (cogs.get("tag") or "cost of revenue")
    else:
        # KLA's shape: no subtotal anywhere on the statement, so take it out
        # of the identity instead. Revenue less cost of revenue less
        # operating income IS operating expenses, by the construction of an
        # income statement.
        revenue, operating = by_key.get("rev"), by_key.get("opinc")
        if not revenue or not operating:
            return
        anchor = operating
        now = less(revenue.get("now"), cogs.get("now"), operating.get("now"))
        prior = less(revenue.get("prior"), cogs.get("prior"), operating.get("prior"))
        tag = ((revenue.get("tag") or "revenue") + " − "
               + (cogs.get("tag") or "cost of revenue") + " − "
               + (operating.get("tag") or "operating income"))

    if now is None:
        return

    derived = {
        "key": "opex",
        "label": "Total operating expenses",
        "indent": 0,
        "now": now,
        "prior": prior,
        "tag": tag,
        "derived": True,
        "prior_end": anchor.get("prior_end"),
    }
    # Where the subtotal belongs on the statement: after the expense lines it
    # totals, immediately before the operating income it produces.
    at = next((i for i, r in enumerate(rows) if r["key"] == "opinc"),
              rows.index(cogs) + 1)
    rows.insert(at, derived)


def walkthrough(ticker: str, facts: dict | None = None, cik: str | None = None) -> dict | None:
    """Everything the guided mode needs: the figures, and where they came from."""
    if facts is None:
        facts, cik = fetch_facts(ticker)
        if facts is None:
            return None
    filed = latest_quarter(ticker, facts=facts)
    if not filed:
        return None

    fields = filed.get("fields") or {}
    figures = {key: entry.get("value") for key, entry in fields.items()
               if entry.get("value") is not None}

    # The cash flow's prior column is the same year-to-date span a year back.
    ytd_prior = None
    ytd = fields.get("cfo") or fields.get("niy")
    if ytd and ytd.get("end"):
        ytd_prior = _prior_year_end(ytd["end"])

    prof = filed.get("profile") or context_for(None)

    return {
        "ticker": filed.get("ticker"),
        "company": filed.get("company"),
        "profile": prof,
        "period_end": filed.get("period_end"),
        "prior_end": filed.get("prior_end"),
        "form": filed.get("form"),
        "filed_on": filed.get("filed"),
        "figures": figures,
        "statements": statements(facts, filed.get("period_end"),
                                 filed.get("prior_end"), ytd_prior,
                                 shape=prof.get("shape")),
        "filing_url": _FILING_URL.format(cik=cik.lstrip("0")) if cik else None,
    }
