"""Questions built from a real company's real filings.

Everything in questions.py is written once and asked of everyone. These are
computed from whatever scorecard is already in the cache, so the arithmetic in
front of the reader is a company's actual numbers rather than a worked example
with round figures in it. That is the thing a textbook cannot do and this app
can, because the numbers are already here.

Three shapes, all correct by construction because the answer is computed from
the same figures the question quotes:

``compute``    Two figures from the filing and a ratio to work out. The
               distractors are the wrong operations someone actually performs
               — the reciprocal, the difference, the two numbers confused.

``threshold``  The real value against the bar the scorecard scores it at.
               Not "is this good", which is a judgement, but "does this clear
               the line and what does that mean", which is checkable.

``trend``      The five-year trail, and what direction it is going. Reading a
               series is a different skill from reading a number.

Nothing here triggers a fetch. If a company's scorecard is not already cached
the review falls back to the written questions, because a learning page that
stalls for eight seconds on a cold EDGAR call teaches nothing.
"""
from __future__ import annotations

import hashlib

# Rows the scorecard produces that map onto a concept and carry a threshold
# worth asking about. The bar is the same one the scorecard scores against,
# quoted here so a question can never disagree with the card beside it.
THRESHOLDS: dict[str, dict] = {
    "current_ratio": {
        "concept": "current-ratio", "bar": 1.5, "direction": "above",
        "name": "current ratio", "fmt": "{:.2f}",
        "pass_means": "short-term assets comfortably cover short-term bills",
        "fail_means": "the cushion is thin, and below 1.0 the bills exceed the assets",
    },
    "debt_to_equity": {
        "concept": "debt-to-equity", "bar": 1.0, "direction": "below",
        "name": "debt-to-equity ratio", "fmt": "{:.2f}",
        "pass_means": "the business is funded more by its owners than by lenders",
        "fail_means": "lenders have a larger claim than the owners do",
    },
    "gross_margin": {
        "concept": "gross-margin", "bar": None, "direction": "trend",
        "name": "gross margin", "fmt": "{:.1f}%",
        "pass_means": "pricing power is holding",
        "fail_means": "each dollar of sales is leaving less behind than it used to",
    },
    "roe": {
        "concept": "roe", "bar": 15.0, "direction": "above",
        "name": "return on equity", "fmt": "{:.1f}%",
        "pass_means": "the company earns well on the owners' money",
        "fail_means": "returns on the owners' money are ordinary",
    },
    "roic": {
        "concept": "roic", "bar": 10.0, "direction": "above",
        "name": "return on invested capital", "fmt": "{:.1f}%",
        "pass_means": "it earns more than capital typically costs, so growth creates value",
        "fail_means": "returns are close to what capital costs, so growth adds little",
    },
    "capex_ratio": {
        "concept": "capex", "bar": 10.0, "direction": "below",
        "name": "capital expenditure as a share of revenue", "fmt": "{:.1f}%",
        "pass_means": "the business does not need heavy reinvestment to stand still",
        "fail_means": "a large share of revenue goes back into the asset base",
    },
}


def _order(seed: str, options: list) -> list:
    """Stable ordering, as in the written bank — same question, same layout."""
    digest = hashlib.sha256(seed.encode()).hexdigest()
    return sorted(options, key=lambda opt: hashlib.sha256(
        (digest + opt["text"]).encode()).hexdigest())


def _rows(scorecard: dict) -> dict:
    return {row["key"]: row
            for section in (scorecard.get("sections") or [])
            for row in section.get("rows") or []}


def _name(scorecard: dict, ticker: str) -> str:
    company = (scorecard.get("company_name") or "").strip()
    return f"{company} ({ticker})" if company else ticker


def _threshold_question(scorecard: dict, ticker: str, row: dict,
                        spec: dict) -> dict | None:
    """Does this real figure clear the bar, and what does that mean?"""
    if spec["bar"] is None or row.get("passed") not in (True, False):
        return None
    value = row.get("value")
    if not value or value == "N/A":
        return None
    passed = bool(row["passed"])
    bar = spec["fmt"].format(spec["bar"])
    above = spec["direction"] == "above"
    right = (f"It clears the bar — {spec['pass_means']}." if passed
             else f"It misses the bar — {spec['fail_means']}.")
    wrong_side = (f"It misses the bar — {spec['fail_means']}." if passed
                  else f"It clears the bar — {spec['pass_means']}.")
    options = [
        {"text": right, "correct": True, "why": "Correct."},
        {"text": wrong_side, "correct": False,
         "why": f"Compare the figure to {bar} again. The test is "
                f"{'above' if above else 'below'} that line."},
        {"text": "There is no threshold for this — it depends entirely on the industry.",
         "correct": False,
         "why": "Industry context matters for interpretation, but this scorecard "
                "scores against a stated bar, and the figure is on one side of it."},
        {"text": "The figure cannot be judged without the share price.",
         "correct": False,
         "why": "This is a measure of the business, not of what it costs. Price "
                "belongs to the valuation questions."},
    ]
    return {
        "kind": "live-threshold",
        "concept": spec["concept"],
        "ticker": ticker,
        "prompt": (f"{_name(scorecard, ticker)} has a {spec['name']} of {value}. "
                   f"The bar is {bar} or {'higher' if above else 'lower'}. "
                   f"What does that tell you?"),
        "options": _order(f"{ticker}{row['key']}threshold", options),
        "explain": row.get("working") or "",
    }


def _compute_question(scorecard: dict, ticker: str, row: dict,
                      spec: dict) -> dict | None:
    """The two figures from the filing, and the ratio to work out."""
    working = row.get("working") or ""
    value = row.get("value")
    if "/" not in working or not value or value == "N/A":
        return None
    left = working.split("=")[0].strip()
    if not left or len(left) > 120:
        return None
    try:
        answer_value = float(str(value).rstrip("%x").replace(",", ""))
    except ValueError:
        return None
    # Distractors are the mistakes people actually make: inverting the ratio,
    # and slipping a decimal place.
    candidates = [answer_value,
                  (1 / answer_value if answer_value else 0),
                  answer_value * 10,
                  answer_value / 10]
    seen, options = set(), []
    for index, candidate in enumerate(candidates):
        text = spec["fmt"].format(candidate)
        if text in seen:
            continue
        seen.add(text)
        options.append({
            "text": text,
            "correct": index == 0,
            "why": ("Correct." if index == 0 else
                    "That is the ratio the other way up." if index == 1 else
                    "Check the decimal place."),
        })
    if len(options) < 3:
        return None
    return {
        "kind": "live-compute",
        "concept": spec["concept"],
        "ticker": ticker,
        "prompt": f"{_name(scorecard, ticker)} reports {left}. What is the {spec['name']}?",
        "options": _order(f"{ticker}{row['key']}compute", options[:4]),
        "explain": working,
    }


def _trend_question(scorecard: dict, ticker: str) -> dict | None:
    """Reading a five-year series is a different skill from reading a number."""
    history = [row for row in (scorecard.get("history") or [])
               if row.get("revenue_num") is not None]
    if len(history) < 4:
        return None
    newest, oldest = history[0]["revenue_num"], history[-1]["revenue_num"]
    if not oldest:
        return None
    # Oldest first, the way the card prints it.
    series = list(reversed([row["revenue"] for row in history]))
    values = list(reversed([row["revenue_num"] for row in history]))
    fell = sum(1 for a, b in zip(values, values[1:]) if b is not None and a is not None and b < a)
    grew_overall = newest > oldest
    right = (f"Revenue is higher than five years ago, with {fell} down "
             f"year{'' if fell == 1 else 's'} along the way."
             if grew_overall else
             f"Revenue is lower than five years ago, with {fell} down "
             f"year{'' if fell == 1 else 's'} along the way.")
    options = [
        {"text": right, "correct": True, "why": "Correct — read the ends and count the dips."},
        {"text": ("Revenue has risen every single year." if fell else
                  "Revenue has fallen every single year."),
         "correct": False,
         "why": ("There is at least one down year in the series." if fell else
                 "Compare each year to the one before it again.")},
        {"text": "Revenue is flat — the changes are noise.",
         "correct": False,
         "why": "Compare the first and last figures; the difference is not noise."},
        {"text": "The series is too short to say anything.",
         "correct": False,
         "why": "Five annual figures is the standard window for a revenue trend."},
    ]
    return {
        "kind": "live-trend",
        "concept": "revenue-growth",
        "ticker": ticker,
        "prompt": (f"{_name(scorecard, ticker)} reported revenue of "
                   f"{' → '.join(series)} across five years, oldest first. "
                   f"Which reading is right?"),
        "options": _order(f"{ticker}revenuetrend", options),
        "explain": "The direction over the whole window and the number of down "
                   "years are two separate facts, and both matter.",
    }


def build(scorecard: dict | None, ticker: str) -> list[dict]:
    """Every live question this scorecard can support."""
    if not scorecard or scorecard.get("error") or not ticker:
        return []
    rows = _rows(scorecard)
    out: list[dict] = []
    for key, spec in THRESHOLDS.items():
        row = rows.get(key)
        if not row:
            continue
        for question in (_compute_question(scorecard, ticker, row, spec),
                         _threshold_question(scorecard, ticker, row, spec)):
            if question:
                out.append(question)
    trend = _trend_question(scorecard, ticker)
    if trend:
        out.append(trend)
    return out


def for_concept(scorecard: dict | None, ticker: str, slug: str) -> list[dict]:
    """Live questions about one concept, if this company can support any."""
    return [q for q in build(scorecard, ticker) if q["concept"] == slug]


def grade(scorecard: dict | None, ticker: str, kind: str, slug: str,
          chosen: str) -> dict | None:
    """Mark a live answer, re-deriving correctness from the same scorecard.

    As with the written bank, the browser sends the text it picked and never
    the verdict.
    """
    question = next((q for q in build(scorecard, ticker)
                     if q["kind"] == kind and q["concept"] == slug), None)
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
