"""score_provenance.py — how much of a grade is about the stock.

A swing score is seven weighted categories normalised to one number, and that
number becomes a letter on a card. The letter is the most authoritative thing
on the page and the least examinable: it does not say what went into it, and
— the part that matters — it does not say what DIDN'T.

Every category degrades silently. compute_swing_score reads its inputs with
data.get(...) and falls through to a default when they are absent, so a stock
whose four-hour trend never loaded is scored as though its four-hour trend
were Neutral. A stock whose daily bars failed scores no pullback points, no
extension penalty and no 20 EMA confluence, because all three read figures
that are not there. The arithmetic is identical to a stock that genuinely has
none of those things, and the output is identical too: a D.

The app's own logs say this happens:

    fetch_swing_data: insufficient daily bars for AAPL
    (yfinance=failed, chart_api tried) — EMA/fib skipped

A D that means "this setup has no edge" and a D that means "the price feed
was down" are different claims, and the card made them in the same type. A
grade nobody can tell apart from a data outage is worse than no grade,
because it is acted on.

So this module reports what the score was allowed to see. It changes no
arithmetic and no threshold; compute_swing_score is untouched. It answers one
question — which of the inputs this grade reads actually arrived — and lets
the page say so.

A note on how absence is decided. A key that is missing, or present and None,
is absent. A key that is present and zero is PRESENT: a relative volume of
0.0 and an extension of 0.0% are real readings, and treating falsiness as
absence is the same mistake that once dropped a breakeven EPS consensus of
0.00 from the earnings calendar — exactly the companies where it mattered.
"""

from __future__ import annotations

# Each category of compute_swing_score, the inputs it reads, and what its
# score means when none of them arrived. `floor` is roughly what the category
# contributes while blind — not used in any arithmetic, only to explain to a
# reader how a missing feed moves a grade.
CATEGORIES = (
    {
        "key": "trend",
        "name": "Trend quality",
        "max": 2.0,
        "inputs": ("daily_trend", "h4_trend", "daily_hh_hl", "h4_hh_hl",
                   "daily_lh_ll"),
        "blind": "scored as a neutral trend, which is not the same as a "
                 "trend that was checked and found neutral",
    },
    {
        "key": "pullback",
        "name": "Pullback quality",
        "max": 2.0,
        "inputs": ("in_demand_zone", "in_supply_zone", "fib_50", "fib_618",
                   "pct_from_ema20", "pct_from_ema50",
                   "distance_to_demand_pct"),
        "blind": "scored as no pullback, because every level it looks for "
                 "— zones, fib retracements, the 20 and 50 EMA — "
                 "comes from the daily bars",
    },
    {
        "key": "confirmation",
        "name": "Confirmation",
        "max": 1.5,
        "inputs": ("m15_confirmation",),
        "blind": "scored as unconfirmed, which is also what an absent "
                 "15-minute read looks like",
    },
    {
        "key": "risk_reward",
        "name": "Risk and reward",
        "max": 1.5,
        "inputs": ("risk_reward",),
        "blind": "scored at its neutral default; the trade plan has to run "
                 "before the score for this to carry a real number",
    },
    {
        "key": "volume",
        "name": "Volume",
        "max": 1.0,
        "inputs": ("rel_volume",),
        "blind": "scored as weak participation, which is indistinguishable "
                 "from no volume data",
    },
    {
        "key": "market",
        "name": "Market alignment",
        "max": 1.0,
        "inputs": ("daily_trend", "catalyst_score"),
        "blind": "scored as unaligned",
    },
    {
        "key": "extension",
        "name": "Extension penalty",
        "max": 0.0,          # a penalty: it can only subtract
        "inputs": ("pct_from_ema20",),
        "blind": "not applied at all, so an extended stock is not marked "
                 "down for it",
    },
)


def _present(data: dict, key: str) -> bool:
    """Is this input actually here?

    Absent means the key is missing or None. Zero is present: a relative
    volume of 0.0 and an extension of 0.0% are readings, not gaps.
    """
    if not isinstance(data, dict):
        return False
    if key not in data:
        return False
    return data[key] is not None


def coverage(data: dict) -> dict:
    """What the score was allowed to see.

    Returns:
      complete      every input arrived
      blind         category keys with NO input at all
      partial       category keys missing some but not all
      missing       the individual input names that did not arrive
      by_category   one entry per category: name, present/total, blind
      seen, total   input counts, for a one-line summary
      bias_known    whether trade_bias arrived; without it the score is
                    computed against a Neutral bias
    """
    data = data if isinstance(data, dict) else {}
    by_category, blind, partial, missing = [], [], [], []
    seen = total = 0

    for cat in CATEGORIES:
        here = [k for k in cat["inputs"] if _present(data, k)]
        gone = [k for k in cat["inputs"] if not _present(data, k)]
        seen += len(here)
        total += len(cat["inputs"])
        for k in gone:
            if k not in missing:
                missing.append(k)
        if not here:
            blind.append(cat["key"])
        elif gone:
            partial.append(cat["key"])
        by_category.append({
            "key": cat["key"], "name": cat["name"],
            "present": len(here), "total": len(cat["inputs"]),
            "blind": not here, "missing": gone,
            "blind_note": cat["blind"] if not here else "",
        })

    return {
        "complete": not missing,
        "blind": blind,
        "partial": partial,
        "missing": missing,
        "by_category": by_category,
        "seen": seen,
        "total": total,
        "bias_known": _present(data, "trade_bias"),
    }


def caveat(data: dict) -> str:
    """One sentence for the card, or '' when the score saw everything.

    Deliberately says what is missing and what that does to the grade, and
    stops there. It does not estimate what the score would have been, because
    that is not knowable from absent data.
    """
    cov = coverage(data)
    if cov["complete"]:
        return ""

    blind_names = [c["name"] for c in cov["by_category"] if c["blind"]]
    if blind_names:
        if len(blind_names) == 1:
            which = blind_names[0].lower()
        else:
            which = ", ".join(n.lower() for n in blind_names[:-1])
            which += f" and {blind_names[-1].lower()}"
        return (
            f"Scored on {cov['seen']} of {cov['total']} inputs. Nothing "
            f"arrived for {which}, so {'that category' if len(blind_names) == 1 else 'those categories'} "
            "scored a default rather than a reading — a grade this far "
            "down may be the data rather than the setup."
        )
    return (
        f"Scored on {cov['seen']} of {cov['total']} inputs. Some readings "
        "were missing, so parts of this grade fell back to defaults."
    )


def trustworthy(data: dict) -> bool:
    """Is this grade safe to read as a judgement about the stock?

    False when any category is wholly blind. A category missing one of
    several interchangeable signals is still making a real assessment; a
    category with nothing at all is not assessing anything.
    """
    return not coverage(data)["blind"]
