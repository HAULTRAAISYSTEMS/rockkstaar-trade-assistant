"""Questions. What being asked about a concept actually looks like.

Three kinds, in ascending order of what they prove:

``definition``  Can you pick the right description out of four? This only
                proves recognition, which is the weakest form of knowing, but
                it is the right first contact with an idea and it is generated
                from the concept store so it cannot be wrong.

``formula``     Can you pick the right arithmetic? Also generated, also
                correct by construction. The distractors are drawn from
                neighbouring concepts on purpose: return on equity against
                return on invested capital is exactly the confusion worth
                drilling.

``judgement``   Given a situation and some numbers, what do you conclude?
                These are hand-written, because the interesting ones cannot be
                generated — they are the traps, stated as a scenario. This is
                the kind that proves you can use the idea.

Options are ordered by a hash of the question rather than shuffled at random,
so a question looks the same every time it is asked, a test can assert on it,
and the answer is not always in the same position.
"""
from __future__ import annotations

import hashlib

import concepts as C

# ── Hand-written judgement questions ─────────────────────────────────────────
# One entry per concept slug. Each option carries its own explanation, so a
# reader who picks wrongly is told why that specific answer is wrong rather
# than just being shown the right one.

JUDGEMENT: dict[str, list[dict]] = {
    "roe": [{
        "prompt": "A company's return on equity jumped from 12% to 25% in one year. "
                  "Net income barely moved, but shareholders' equity halved after a "
                  "large buyback. What happened?",
        "options": [
            ("The business became roughly twice as profitable.", False,
             "Net income barely moved, so profitability did not change. Only the denominator did."),
            ("Return on equity rose for a reason that has nothing to do with the business.", True,
             "Right. Buybacks shrink equity, and a smaller denominator lifts the ratio "
             "mechanically. This is the main reason return on invested capital is the better measure."),
            ("The company must have taken on debt.", False,
             "It may have, but the buyback alone explains the move. Debt would be a separate question."),
            ("Return on equity is unreliable and should be ignored.", False,
             "It is informative when you know what moved it. The skill is asking whether "
             "the numerator or the denominator changed."),
        ],
        "explain": "Whenever a ratio moves sharply, ask which half moved. Here the "
                   "numerator was flat and the denominator halved.",
    }],
    "earnings-quality": [{
        "prompt": "For four straight years a company has reported rising net income while "
                  "free cash flow has stayed flat and below it. Management points to growth. "
                  "What is the most reasonable reading?",
        "options": [
            ("Nothing to see — growing companies consume cash.", False,
             "Growth explains a gap. It does not explain a gap that persists for four "
             "years while the gap itself widens."),
            ("Worth a real explanation before trusting the earnings.", True,
             "Right. Profit persistently above cash generation is the single most useful "
             "warning sign in fundamental analysis, and it shows up here years before "
             "anything else breaks."),
            ("The company is definitely committing fraud.", False,
             "Far too strong. It is a question to answer, not a conclusion — there are "
             "legitimate explanations, and you should go and find which one applies."),
            ("Free cash flow is the wrong measure for a growing company.", False,
             "It is the right measure and the growth is the thing being tested. The "
             "question is whether the cash eventually arrives."),
        ],
        "explain": "Cash and profit diverging for one year is working capital. Diverging "
                   "for four is a question that deserves an answer you can actually find.",
    }],
    "eps": [{
        "prompt": "A company's earnings per share grew 10% last year. Net income was flat. "
                  "What most likely explains it?",
        "options": [
            ("The company bought back about 9% of its shares.", True,
             "Right. With a flat numerator, EPS can only rise if the share count falls. "
             "That is not worthless, but it is financial engineering rather than a "
             "better business."),
            ("Margins improved.", False,
             "Better margins would have lifted net income, and net income was flat."),
            ("Revenue grew 10%.", False,
             "Revenue growth that did not reach net income cannot reach EPS either."),
            ("The company issued new shares.", False,
             "Issuing shares raises the denominator, which would push EPS down."),
        ],
        "explain": "Always read EPS growth beside the share count. Same profit split "
                   "fewer ways is a different story from more profit.",
    }],
    "share-dilution": [{
        "prompt": "A company's diluted share count went from 152 million to 1,320 million "
                  "in a single year. Revenue and net income were both up modestly. What is "
                  "the most likely explanation?",
        "options": [
            ("Catastrophic dilution — the company issued shares to survive.", False,
             "Dilution of that scale alongside healthy revenue and profit does not fit. "
             "A company issuing eight times its share count is in distress, and the "
             "income statement would show it."),
            ("A stock split, most likely ten-for-one.", True,
             "Right. The ratio is close to a clean multiple and nothing else changed. A "
             "split multiplies the share count without diluting anyone."),
            ("A large acquisition paid for in stock.", False,
             "Possible in principle, but an acquisition that size would transform revenue, "
             "and revenue only moved modestly."),
            ("A data error that should be ignored.", False,
             "The figure is real. The mistake would be reading it as dilution."),
        ],
        "explain": "A clean multiple in the share count with nothing else moving is a "
                   "split. This is why a per-share series should be read from one filing.",
    }],
    "vix": [{
        "prompt": "The VIX is at 32. What does that imply about the next month?",
        "options": [
            ("The market is expected to fall about 32%.", False,
             "It says nothing about direction. It is a measure of expected size of "
             "movement, either way."),
            ("Daily moves of roughly 2% are expected.", True,
             "Right. Divide by 16 for a rough daily equivalent: 32 ÷ 16 = 2%. A market "
             "at 32 is pricing genuine uncertainty."),
            ("The market will definitely be volatile.", False,
             "It is what traders are paying for protection, not a forecast that must "
             "come true. Realised volatility usually lands below implied."),
            ("Nothing — the VIX is backward-looking.", False,
             "It is forward-looking by construction: it is extracted from the prices of "
             "options that have not expired yet."),
        ],
        "explain": "VIX ÷ 16 turns an annualised number into an intuitive daily one. "
                   "Size, not direction.",
    }],
    "pe-ratio": [{
        "prompt": "A cyclical manufacturer is trading at its lowest price-to-earnings ratio "
                  "in a decade. What should you check first?",
        "options": [
            ("Nothing — a low P/E is the definition of cheap.", False,
             "For a cyclical company it is often the opposite. The multiple is lowest "
             "when earnings are at their highest, which is the worst moment to buy."),
            ("Whether earnings are at a cyclical peak.", True,
             "Right. The denominator is what is unusual. A low multiple on peak earnings "
             "becomes a high multiple the moment earnings normalise."),
            ("Whether the dividend is safe.", False,
             "Worth knowing, but it does not address why the multiple is low."),
            ("Whether the sector's average P/E is higher.", False,
             "A sector comparison inherits the same problem if the whole sector is at "
             "a cyclical peak."),
        ],
        "explain": "For cyclicals the P/E is lowest at the top and highest at the bottom. "
                   "Always ask what the earnings in the denominator are doing.",
    }],
    "current-ratio": [{
        "prompt": "A clothing retailer reports a current ratio of 2.4. Most of its current "
                  "assets are inventory. How much comfort should you take?",
        "options": [
            ("A great deal — anything above 1.5 is healthy.", False,
             "The threshold assumes current assets convert to cash near their carrying "
             "value. For unsold seasonal stock that assumption is doing a lot of work."),
            ("Less than the number suggests, because inventory may not sell at cost.", True,
             "Right. Inventory counts as a current asset at what it cost, not what it "
             "will fetch. A warehouse of last season's stock flatters this ratio."),
            ("None — the current ratio is meaningless for retailers.", False,
             "Too strong. It is still informative, it just needs reading alongside what "
             "the current assets actually are."),
            ("It depends entirely on the debt-to-equity ratio.", False,
             "That is a separate question about long-term solvency, not near-term liquidity."),
        ],
        "explain": "Look through a liquidity ratio to what the assets actually are. Cash "
                   "is cash; inventory is a hope about a future sale.",
    }],
    "capex": [{
        "prompt": "A company's free cash flow jumped 40% this year. Operating cash flow was "
                  "flat and capital expenditure fell by half. What have you learned?",
        "options": [
            ("The business got much more cash-generative.", False,
             "Operating cash flow was flat, so the business generated the same cash. "
             "Only the spending changed."),
            ("Free cash flow rose because the company invested less, which may not last.", True,
             "Right. Cutting capex lifts free cash flow immediately and damages the "
             "business slowly. The question is whether the cut is efficiency or deferral."),
            ("Margins must have improved.", False,
             "Margins would have shown up in operating cash flow, which was flat."),
            ("The company is in financial distress.", False,
             "Possible, but not established. It could equally be the end of a build cycle."),
        ],
        "explain": "Free cash flow has two moving parts. A jump driven by the capex line "
                   "rather than the cash line is a different fact about the business.",
    }],
    "goodwill": [{
        "prompt": "A company's goodwill fell 18% year over year. Net income swung to a loss. "
                  "How worried should you be about solvency?",
        "options": [
            ("Very — the company lost money and its assets shrank.", False,
             "An impairment is non-cash. No money left the building this year, so it does "
             "not threaten solvency by itself."),
            ("The loss is non-cash, but it says something real about past decisions.", True,
             "Right. The cash was spent when the acquisition was made; this is management "
             "admitting it did not work. It is information about judgement, not liquidity."),
            ("Not at all — impairments should be ignored entirely.", False,
             "Companies would like you to think so. The write-down is the acknowledgement "
             "of a real loss, just one that happened earlier."),
            ("It means the company is being acquired.", False,
             "Goodwill falls on impairment, divestiture or currency movement. None of "
             "those is a takeover."),
        ],
        "explain": "Impairment is a cash-flow non-event and a capital-allocation event. "
                   "One impairment is a bad deal; a pattern is a habit.",
    }],
    "cpi": [{
        "prompt": "CPI comes in at 3.1% year over year. Economists expected 3.4%. Stocks "
                  "rally hard. Why?",
        "options": [
            ("Because 3.1% is low inflation.", False,
             "3.1% is not low by most standards. The level is not what moved the market."),
            ("Because inflation came in below what was already priced.", True,
             "Right. Markets move on the surprise, not the number. Softer than expected "
             "means the path of rates may be lower than assumed."),
            ("Because falling inflation always lifts stocks.", False,
             "Not always — inflation falling because the economy is collapsing is not "
             "good news for earnings."),
            ("Because CPI is the Fed's target measure.", False,
             "The Fed's 2% target is defined on PCE, not CPI."),
        ],
        "explain": "The release is not the news; the gap against expectations is. Knowing "
                   "the number without knowing the estimate tells you nothing.",
    }],
    "fed-funds-rate": [{
        "prompt": "The Fed cuts rates exactly as expected, and stocks fall sharply that "
                  "afternoon. What most likely happened?",
        "options": [
            ("The market misunderstood the decision.", False,
             "The decision was understood. Something else in the meeting moved things."),
            ("The statement or press conference signalled fewer cuts ahead than hoped.", True,
             "Right. The cut was already priced, so it changed nothing. Markets price the "
             "expected path, and the tone often moves more than the decision."),
            ("Rate cuts are bad for stocks.", False,
             "Usually the reverse, all else equal. The reaction here is about what was "
             "expected, not about the direction of the cut."),
            ("The cut was too small.", False,
             "It was exactly as expected, so its size was already in the price."),
        ],
        "explain": "An expected decision is not news. The statement wording, the dot plot "
                   "and the press conference are where the surprise lives.",
    }],
    "earnings-report": [{
        "prompt": "A company beats on both revenue and earnings per share, and the stock "
                  "drops 9%. What is the most common explanation?",
        "options": [
            ("The beat was not big enough to matter.", False,
             "Closer, but incomplete. The usual cause is forward-looking, not about the "
             "quarter just reported."),
            ("Guidance for the coming quarter was cut.", True,
             "Right. Markets price the future. A cut outlook routinely outweighs a beat "
             "on the quarter that has already happened."),
            ("The beat was on adjusted rather than GAAP figures.", False,
             "Worth checking and sometimes the cause, but a guidance cut is the far more "
             "common explanation for a drop of this size."),
            ("Investors take profits after every beat.", False,
             "Not a real mechanism. Stocks rise on beats often enough that this cannot "
             "be the explanation."),
        ],
        "explain": "Read the guidance before the results. The quarter is history; the "
                   "outlook is what is being priced.",
    }],
    "gross-margin": [{
        "prompt": "A software company's gross margin has slid from 82% to 71% over five "
                  "years while revenue grew every year. What is the most useful reading?",
        "options": [
            ("Nothing to worry about — 71% is still excellent.", False,
             "The level is excellent and the direction is the signal. A five-year slide "
             "is a trend, not a blip."),
            ("Something is eroding pricing power, and growth is masking it.", True,
             "Right. Direction matters more than level. Eleven points of margin over five "
             "years while growing means each new dollar of revenue is worth less."),
            ("The company must be cutting prices.", False,
             "Price is one explanation. A shift in mix toward lower-margin products or "
             "rising hosting costs would look identical here."),
            ("Gross margin does not matter for software.", False,
             "It matters most for software, where it is the clearest read on whether "
             "customers will pay for the product."),
        ],
        "explain": "Compare a company to its own history first. A high margin falling "
                   "steadily is a more interesting fact than a low margin holding flat.",
    }],
    "reporting-currency": [{
        "prompt": "A Taiwanese company's US listing trades at $429. Its filings show revenue "
                  "of 2,894 billion and a 52-week high of 2,535. What should you conclude?",
        "options": [
            ("The stock has fallen 83% from its high.", False,
             "That is the trap. The price is in dollars and the high is in New Taiwan "
             "dollars — the two are not on the same basis."),
            ("The figures are in different currencies and cannot be compared.", True,
             "Right. The filing is in New Taiwan dollars; the US listing quotes in "
             "dollars. Any comparison across them is arithmetic on incompatible units."),
            ("The company is enormously overvalued.", False,
             "You cannot conclude anything about valuation until the units agree."),
            ("The data is wrong and should be discarded.", False,
             "Both figures are correct. Putting them in the same sentence is the error."),
        ],
        "explain": "Check the currency before reading a single absolute figure. Ratios "
                   "survive a currency difference; prices and totals do not.",
    }],
    "roic": [{
        "prompt": "Two companies both earn 18% on equity. One has no debt; the other is "
                  "funded half by borrowing. What does return on invested capital tell you "
                  "that return on equity did not?",
        "options": [
            ("Nothing — both measure the same thing.", False,
             "They differ exactly where debt is involved, which is the whole point here."),
            ("The debt-free company earns more on the capital it actually uses.", True,
             "Right. Return on equity ignores borrowed money; return on invested capital "
             "puts it in the denominator. Identical returns on equity with different "
             "leverage means different underlying businesses."),
            ("The leveraged company is the better business.", False,
             "The reverse, on this evidence. It needed more total capital to produce the "
             "same return on the owners' share."),
            ("The leveraged company must be riskier but more profitable.", False,
             "Riskier, yes. More profitable per dollar of capital employed, no."),
        ],
        "explain": "Return on equity can be manufactured with borrowing. Return on "
                   "invested capital cannot, which is why it is the better read on quality.",
    }],
    "free-cash-flow": [{
        "prompt": "A company reports negative free cash flow for the third year running "
                  "while operating cash flow is strongly positive and rising. What is the "
                  "question you should be able to answer?",
        "options": [
            ("Whether the company is about to run out of money.", False,
             "Operating cash flow is strong and rising, so the business generates cash. "
             "The spending is a choice, not a shortfall."),
            ("When the investment is expected to start paying, and on what evidence.", True,
             "Right. Heavy capex is not a problem by itself — it is a bet. The question "
             "is whether you can say what the bet is and when it resolves."),
            ("Why the accounting is wrong.", False,
             "Nothing here suggests an accounting problem. Both figures can be true at once."),
            ("Whether to sell immediately.", False,
             "Sustained investment is how capital-intensive businesses get built. The "
             "judgement needs the answer to the question above first."),
        ],
        "explain": "Negative free cash flow from investment is a different fact from "
                   "negative free cash flow from weak operations. Read the two lines apart.",
    }],
    "restatement": [{
        "prompt": "A company you have researched files an 8-K under item 4.02. Its scorecard "
                  "still shows strong numbers. What does that mean?",
        "options": [
            ("The strong numbers stand until corrected figures arrive.", False,
             "The opposite. The company has said those figures should not be relied on, "
             "so the analysis built on them has no foundation."),
            ("Every figure in that analysis was built on numbers the company has withdrawn.", True,
             "Right. This is different in kind from a bad ratio. It is not a weak result, "
             "it is the absence of a reliable result."),
            ("It is a routine filing and can be ignored.", False,
             "Item 4.02 specifically means non-reliance on previously issued financial "
             "statements. There is nothing routine about it."),
            ("The scorecard should simply be refreshed.", False,
             "Refreshing pulls the same withdrawn figures. Corrected ones may take months."),
        ],
        "explain": "A restatement invalidates the inputs, not just the conclusion. This "
                   "app drops the verdict a full band on it, whatever the score.",
    }],
}


# ── Generated questions ──────────────────────────────────────────────────────

def _order(seed: str, options: list) -> list:
    """A stable ordering for a question's options.

    Not random: a question should look the same every time it is asked, so a
    reader is not re-reading four newly shuffled options, and a test can assert
    on what comes back. Not fixed either, or the answer would always be first.
    """
    digest = hashlib.sha256(seed.encode()).hexdigest()
    return sorted(options, key=lambda opt: hashlib.sha256(
        (digest + opt["text"]).encode()).hexdigest())


def _distractors(concept: dict, field: str, count: int = 3) -> list[str]:
    """Wrong answers, taken from the concepts most likely to be confused.

    Neighbours first — related concepts, then the same topic, then anywhere.
    Gross margin against operating margin is the confusion worth drilling; a
    balance-sheet term against a macro release is not a question, it is a
    formality.
    """
    seen = {concept["slug"]}
    picked: list[str] = []

    def take(candidates):
        for other in candidates:
            if other["slug"] in seen or not other.get(field):
                continue
            if other[field] == concept.get(field):
                continue
            seen.add(other["slug"])
            picked.append(other[field])
            if len(picked) >= count:
                return True
        return False

    related = [C.BY_SLUG[s] for s in concept.get("related", []) if s in C.BY_SLUG]
    same_topic = [c for c in C.CONCEPTS if c["topic"] == concept["topic"]]
    if take(related) or take(same_topic) or take(C.CONCEPTS):
        pass
    return picked[:count]


def _definition_question(concept: dict) -> dict | None:
    wrong = _distractors(concept, "one_liner")
    if len(wrong) < 3:
        return None
    options = [{"text": concept["one_liner"], "correct": True,
                "why": "Correct."}]
    options += [{"text": w, "correct": False,
                 "why": "That describes a different idea. Read the two side by side — "
                        "the distinction is the point of the question."}
                for w in wrong]
    return {
        "kind": "definition",
        "concept": concept["slug"],
        "prompt": f"Which of these describes {concept['name']}?",
        "options": _order(concept["slug"] + "definition", options),
        "explain": concept["what"],
    }


def _formula_question(concept: dict) -> dict | None:
    if not concept.get("formula"):
        return None
    wrong = _distractors(concept, "formula")
    if len(wrong) < 3:
        return None
    options = [{"text": concept["formula"], "correct": True, "why": "Correct."}]
    options += [{"text": w, "correct": False,
                 "why": "That is the arithmetic for a neighbouring measure. The "
                        "difference between them is usually the whole point."}
                for w in wrong]
    return {
        "kind": "formula",
        "concept": concept["slug"],
        "prompt": f"How is {concept['name']} calculated?",
        "options": _order(concept["slug"] + "formula", options),
        "explain": concept["read_it"],
    }


def _judgement_questions(concept: dict) -> list[dict]:
    out = []
    for index, spec in enumerate(JUDGEMENT.get(concept["slug"], [])):
        options = [{"text": text, "correct": correct, "why": why}
                   for text, correct, why in spec["options"]]
        out.append({
            "kind": "judgement",
            "concept": concept["slug"],
            "prompt": spec["prompt"],
            "options": _order(f"{concept['slug']}judgement{index}", options),
            "explain": spec["explain"],
        })
    return out


def for_concept(slug: str) -> list[dict]:
    """Every question that can be asked about one concept, easiest first."""
    concept = C.get(slug)
    if not concept:
        return []
    out = [q for q in (_definition_question(concept),
                       _formula_question(concept)) if q]
    out.extend(_judgement_questions(concept))
    return out


def pick(slug: str, box: int = 0) -> dict | None:
    """One question about a concept, chosen for how well it is already known.

    A first meeting gets recognition. Once a concept has been answered
    correctly a couple of times, recognition stops proving anything and the
    question becomes a judgement one where there is one to ask.
    """
    available = for_concept(slug)
    if not available:
        return None
    if box >= 2:
        harder = [q for q in available if q["kind"] == "judgement"]
        if harder:
            return harder[box % len(harder)]
    return available[min(box, len(available) - 1)]


def coverage() -> dict:
    """How much of the library can be asked about, and how deeply."""
    with_judgement = sum(1 for c in C.CONCEPTS if JUDGEMENT.get(c["slug"]))
    return {
        "concepts": len(C.CONCEPTS),
        "askable": sum(1 for c in C.CONCEPTS if for_concept(c["slug"])),
        "with_judgement": with_judgement,
        "questions": sum(len(for_concept(c["slug"])) for c in C.CONCEPTS),
    }


def grade(slug: str, kind: str, chosen: str) -> dict | None:
    """Mark an answer.

    The browser sends back which concept, which kind of question, and the text
    it picked — never whether that text was right. Correctness is re-derived
    here from the question bank, so a submitted answer cannot assert its own
    result.
    """
    question = next((q for q in for_concept(slug) if q["kind"] == kind), None)
    if question is None:
        return None
    picked = next((o for o in question["options"] if o["text"] == chosen), None)
    answer = next(o for o in question["options"] if o["correct"])
    return {
        "question": question,
        "picked": picked,
        "answer": answer,
        "correct": bool(picked and picked["correct"]),
        "unanswered": picked is None,
    }
