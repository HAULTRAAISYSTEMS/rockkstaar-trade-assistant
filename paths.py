"""Reading paths: an order to meet the ideas in.

Fifty-two concepts in nine topics is a reference, and a reference assumes you
already know what you are looking for. Someone learning does not — that is the
condition. A path is the answer to "where do I start", and then to "what now"
fifty more times.

What makes this a path and not a list is the ``why`` on each step. Ordering
alone is a table of contents; saying why this idea comes after that one is the
part that teaches, because the connections between these ideas are most of the
subject. Cash flow means little until you know what net income is and why
anyone would doubt it.

Paths deliberately overlap. Earnings per share belongs in reading an income
statement and in judging what a company costs, and meeting it twice from two
directions is how it sticks rather than a duplication to be normalised away.
"""
from __future__ import annotations

import concepts as C

# A step is considered behind you once its concept is in box 2 — met, and
# answered correctly at least twice. Box 3 is the "learned" bar used on the
# dashboard; requiring it to move on would stall a first read-through for
# three weeks on the first card.
STEP_DONE_BOX = 2


PATHS: list[dict] = [
    {
        "slug": "read-an-income-statement",
        "name": "Read an income statement",
        "blurb": "Follow one year of a company from what it sold to what reached shareholders.",
        "outcome": "You can open any income statement, read it top to bottom, and say "
                   "where the money went at each step.",
        "steps": [
            ("annual-report-10k", "Start with the document. Everything else in this "
             "path is a line inside it, and knowing where it comes from is what "
             "separates reading accounts from reading about them."),
            ("revenue", "The top line, and the ceiling on everything below it. No "
             "amount of cost control grows a shrinking business."),
            ("gross-profit", "The first subtraction: what the product cost to make. "
             "This is where you find out whether the product itself makes money."),
            ("gross-margin", "The same fact as a percentage, so it can be compared "
             "across years and against competitors. This is where pricing power shows."),
            ("operating-income", "Now subtract the cost of running the company — "
             "research, sales, head office. The business, before financing and tax."),
            ("operating-margin", "Read it beside gross margin. If one holds while the "
             "other falls, you have located the problem."),
            ("net-income", "Everything is off now, including interest and tax. This is "
             "the number the headlines quote and the one most shaped by judgement."),
            ("net-margin", "The whole statement in one figure — a good summary and a "
             "poor diagnostic, which is why you walked down rather than starting here."),
            ("eps", "Finally, divide by the shares. This is the bridge from the company "
             "to the slice of it you actually own."),
        ],
    },
    {
        "slug": "read-a-balance-sheet",
        "name": "Read a balance sheet",
        "blurb": "What a company owns, what it owes, and whether it survives a bad year.",
        "outcome": "You can look at a balance sheet and say whether the company is "
                   "under near-term pressure and what would put it there.",
        "steps": [
            ("balance-sheet", "A photograph, not a film. Start with why it always "
             "balances — it explains the whole structure."),
            ("equity", "The owners' share, and what absorbs losses before lenders are "
             "at risk. It is also the denominator of return on equity later."),
            ("current-ratio", "The first liquidity read: can next year's bills be paid "
             "with next year's assets. Companies fail on cash, not on profit."),
            ("total-debt", "The claim that ranks ahead of yours. Lenders get paid first, "
             "in good times and in liquidation."),
            ("short-term-debt", "Total debt is the wrong question for survival. What "
             "matters is what has to be repaid soon."),
            ("cash-coverage", "Now put the two together. This is the survival question "
             "answered directly, and it is why some companies sail through downturns."),
            ("goodwill", "The one large asset that can vanish overnight, because it is "
             "a judgement about past acquisitions rather than a thing you could sell."),
            ("impairment", "What that vanishing looks like when it happens, and what it "
             "tells you about management rather than about liquidity."),
            ("retained-earnings", "The long memory: every dollar ever earned and kept. "
             "One good year is invisible here; a decade is not."),
        ],
    },
    {
        "slug": "follow-the-cash",
        "name": "Follow the cash",
        "blurb": "Profit is an opinion. Cash is a fact. Learn to check one against the other.",
        "outcome": "You can tell whether a company's reported profit is backed by money, "
                   "and say what to ask when it is not.",
        "steps": [
            ("net-income", "Start with what is being checked. Profit involves judgement "
             "about when a sale counts and how fast an asset wears out."),
            ("cash-flow-statement", "The check itself, and why it exists as a separate "
             "statement rather than a footnote."),
            ("operating-cash-flow", "What the core business actually produced in money, "
             "before deciding what to spend it on."),
            ("capex", "The spending that keeps the business standing. This is what "
             "separates companies that print cash from companies that consume it."),
            ("free-cash-flow", "Subtract one from the other and you have the money "
             "genuinely available to owners — the thing a business is ultimately worth."),
            ("earnings-quality", "Now compare it back to net income. This single "
             "comparison is the most useful check in fundamental analysis."),
            ("financing-cash-flow", "Last, the third section: whether the company is "
             "returning money to its funders or raising more from them."),
        ],
    },
    {
        "slug": "judge-a-business",
        "name": "Judge whether it is a good business",
        "blurb": "Returns on capital, and what it means when they stay high for a decade.",
        "outcome": "You can distinguish a business that earns more than its capital "
                   "costs from one that is merely large or merely growing.",
        "steps": [
            ("equity", "The owners' capital, because the first return measure is "
             "calculated against it."),
            ("roe", "Profit per dollar of owners' money. Simple, useful, and easy to "
             "flatter with borrowing — which is the next step."),
            ("debt-to-equity", "How much of the business is funded by lenders. This is "
             "the lever that inflates return on equity without improving anything."),
            ("roic", "The measure that closes that hole by putting borrowed money in "
             "the denominator. The closest single number to 'is this a good business'."),
            ("gross-margin", "Returns tell you the business earns well. Margin tells you "
             "whether anything is stopping a competitor from taking it."),
            ("moat", "Put them together. Sustained high returns with non-eroding margin "
             "is the arithmetic signature of something protecting the business."),
        ],
    },
    {
        "slug": "growth-and-who-it-belongs-to",
        "name": "Growth, and who it belongs to",
        "blurb": "A company can grow every year and leave its owners worse off. "
                 "Learn to see the difference.",
        "outcome": "You can tell growth that reaches you from growth that is diluted "
                   "away, and spot a stock split before you mistake it for either.",
        "steps": [
            ("revenue", "Growth starts at the top line, because it is the size of "
             "the business and the ceiling on everything below it."),
            ("revenue-growth", "What matters is not one year's rate but the streak. A "
             "streak is evidence of demand that keeps showing up."),
            ("net-income", "Growth that never reaches profit is a different fact about "
             "the business, so this is the next place to look."),
            ("eps", "And then the step almost everyone skips: the same profit, divided "
             "among however many shares now exist."),
            ("eps-growth", "Over long horizons prices track this more closely than they "
             "track revenue. It is the compounding engine."),
            ("share-dilution", "The cost that never appears as a cost. Revenue, profit "
             "and your slice can move in three different directions at once."),
            ("stock-split", "Last, the thing that looks exactly like enormous dilution "
             "and is not — so you never mistake one for the other."),
        ],
    },
    {
        "slug": "what-it-costs",
        "name": "Work out what it costs",
        "blurb": "The scorecard says what is worth owning. This is the other half.",
        "outcome": "You can say what is already priced into a stock, and why a low "
                   "multiple is not the same as cheap.",
        "steps": [
            ("market-cap", "What the market says the whole company is worth. Every other "
             "figure is only large or small relative to this."),
            ("eps", "The profit attached to one share — the denominator of the multiple "
             "everyone quotes."),
            ("pe-ratio", "Dollars of price per dollar of profit. Shorthand for how much "
             "optimism is already in the price."),
            ("trailing-twelve-months", "Which twelve months of profit, though? A fiscal "
             "year that ended eleven months ago describes a different company."),
            ("free-cash-flow", "The cash version of profit, which resists the accounting "
             "choices that shape reported earnings."),
            ("price-to-fcf", "The same multiple against cash instead. Read as a yield it "
             "becomes comparable to everything else you could own."),
        ],
    },
    {
        "slug": "understand-an-earnings-report",
        "name": "Understand an earnings report",
        "blurb": "Why a company can beat on every number and still fall nine per cent.",
        "outcome": "You can read a results release in the right order and say what will "
                   "actually move the stock.",
        "steps": [
            ("earnings-report", "What the event is, when it happens, and the four things "
             "to read in order."),
            ("eps", "The headline number, and why it should be read diluted and beside "
             "the share count."),
            ("guidance", "What management says about the quarter that has not happened. "
             "This routinely matters more than the one that has."),
            ("quarterly-report-10q", "The full filing that follows the release, where "
             "the detail behind the headline lives."),
            ("current-report-8k", "How the release reaches the market, and the other "
             "item codes worth recognising on sight."),
            ("restatement", "The worst of those codes, and why it is different in kind "
             "from a bad result."),
        ],
    },
    {
        "slug": "read-the-macro-calendar",
        "name": "Read the macro calendar",
        "blurb": "The releases that move every stock at once, and what each one measures.",
        "outcome": "You can look at a week's calendar and say which days carry risk, "
                   "and why the market reacts to the surprise rather than the number.",
        "steps": [
            ("cpi", "The monthly inflation reading, and the clearest example of markets "
             "reacting to the gap against expectations rather than to the level."),
            ("ppi", "Inflation upstream at the factory gate, which also reads directly "
             "on the gross margins you learned to read earlier."),
            ("pce", "The one the Federal Reserve actually targets. Watching CPI and "
             "assuming the Fed sees the same number is a consequential mistake."),
            ("nonfarm-payrolls", "The other release that reliably moves everything. "
             "Employment drives spending, and wages feed inflation."),
            ("fed-funds-rate", "Where all of it lands. Rates are the discount rate on "
             "the future, which is why they move growth stocks hardest."),
            ("gdp", "The broadest read, and a lesson in why a backward-looking release "
             "often moves nothing."),
            ("retail-sales", "A faster monthly read on the consumer, well before GDP "
             "confirms it."),
        ],
    },
    {
        "slug": "volatility-and-risk",
        "name": "Volatility and risk",
        "blurb": "What the market's own fear gauge is saying, and what it is not saying.",
        "outcome": "You can read the VIX as an expected daily move and explain why a low "
                   "reading is not the same as safety.",
        "steps": [
            ("vix", "Start with what it actually measures — expected size of movement, "
             "extracted from what traders are paying for protection."),
            ("beta", "How one stock moves relative to all of it, and why low beta is not "
             "low risk."),
            ("fed-funds-rate", "The single largest scheduled source of volatility, and "
             "why an expected decision can still move the market hard."),
        ],
    },
    {
        "slug": "companies-that-dont-file-in-dollars",
        "name": "Companies that do not file in dollars",
        "blurb": "How to read a foreign filer without producing numbers that look right and are not.",
        "outcome": "You can analyse a 20-F filer and know exactly which comparisons are "
                   "safe across currencies and which are meaningless.",
        "steps": [
            ("foreign-annual-report-20f", "The form itself, and why excluding it quietly "
             "excludes most of Asia and Europe from what you look at."),
            ("reporting-currency", "The trap underneath it. Mixing two currencies "
             "produces a plausible number that is wrong by the exchange rate."),
            ("gross-margin", "A ratio, so it survives the currency difference intact — "
             "both halves are in the same unit and cancel."),
            ("market-cap", "An absolute figure, so it does not. This is where providers "
             "quote a home listing against a US share price."),
            ("pe-ratio", "And the multiple that gets built from both, which is how a "
             "perfectly ordinary company ends up looking absurdly cheap."),
        ],
    },
]


# ── Lookup ───────────────────────────────────────────────────────────────────

def _expand(path: dict) -> dict:
    """A path with its steps resolved to real concepts, unknown slugs dropped."""
    steps = []
    for index, (slug, why) in enumerate(path["steps"], start=1):
        concept = C.get(slug)
        if concept is None:
            continue
        steps.append({"n": index, "concept": concept, "slug": slug, "why": why})
    return {**path, "steps": steps}


ALL: list[dict] = [_expand(p) for p in PATHS]
BY_SLUG: dict[str, dict] = {p["slug"]: p for p in ALL}


def get(slug: str) -> dict | None:
    return BY_SLUG.get((slug or "").strip().lower())


def with_progress(path: dict, records: dict) -> dict:
    """A path annotated with how far this learner has come.

    ``next_step`` is the first step not yet behind them, which is what the page
    offers as the thing to do now. A completed path has none.
    """
    steps = []
    done = 0
    next_step = None
    for step in path["steps"]:
        record = records.get(step["slug"]) or {}
        box = int(record.get("box") or 0)
        seen = int(record.get("seen") or 0)
        is_done = box >= STEP_DONE_BOX
        if is_done:
            done += 1
        elif next_step is None:
            next_step = step["slug"]
        steps.append({**step, "seen": bool(seen), "done": is_done, "box": box})
    total = len(steps)
    return {
        **path,
        "steps": steps,
        "done": done,
        "total": total,
        "percent": round(done / total * 100) if total else 0,
        "next_step": next_step,
        "complete": total > 0 and done == total,
    }


def all_with_progress(records: dict) -> list[dict]:
    return [with_progress(path, records) for path in ALL]


def paths_containing(slug: str) -> list[dict]:
    """Every path a concept appears in, so a concept page can offer its context."""
    return [p for p in ALL if any(s["slug"] == slug for s in p["steps"])]


def coverage() -> dict:
    """Which concepts no path reaches — the gaps a learner would never be led to."""
    on_a_path = {s["slug"] for p in ALL for s in p["steps"]}
    return {
        "paths": len(ALL),
        "steps": sum(len(p["steps"]) for p in ALL),
        "concepts_on_a_path": len(on_a_path),
        "orphans": sorted(c["slug"] for c in C.CONCEPTS if c["slug"] not in on_a_path),
    }
