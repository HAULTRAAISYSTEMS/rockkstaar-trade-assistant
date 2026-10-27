"""earnings_timing.py — what a calendar row actually knows about when.

The calendar printed "TBD" under every date more than a few weeks out and
left it there. Three things were wrong with that, in rising order of harm.

It is an acronym. A reader learning the market has no reason to know it
means the time of day rather than the date, the ticker, or the estimate.

It was swallowing real information. Finnhub reports three sessions — bmo,
amc and dmh, meaning during market hours — and the bulk-calendar ingestion
mapped everything that was not bmo or amc onto "TBD". So a company that HAD
announced it reports mid-session was shown as unknown. company_research_data
already handled dmh correctly, which is how we know this was an oversight
rather than a decision.

And the serious one: an unannounced time is evidence about the DATE, and the
page was throwing it away. A company fixes its reporting date and its
reporting session in the same press release. So a row whose session is blank
is, nearly always, a row whose date nobody has announced either — the
provider has projected it. The old calendar rendered that projection in the
same weight as a date confirmed three days out. A reader planning around it
could not tell the difference, and the difference is the whole point.

This module is deliberately only about that, and makes no claim it cannot
support. It does not pretend to know a confirmation status no feed supplies.
It says what the session is when the session is known, says plainly that it
is not announced when it is not, and marks the date provisional on the same
evidence rather than on a guess about how far away it is.
"""

from __future__ import annotations

# The provider's codes, lower-cased, mapped onto what they mean. Anything
# not here is unknown, which is a legitimate answer and not a failure.
SESSIONS = {
    "bmo": ("Before the open", "Before open"),
    "amc": ("After the close", "After close"),
    "dmh": ("During market hours", "Mid-session"),
}

# The strings the rest of the app already passes around. Kept as the internal
# contract — fifteen call sites read time_label — with the display text
# derived here instead of each caller inventing its own wording.
ALIASES = {
    "before market open": "bmo",
    "before open": "bmo",
    "pre-market": "bmo",
    "premarket": "bmo",
    "after market close": "amc",
    "after close": "amc",
    "post-market": "amc",
    "postmarket": "amc",
    "during market hours": "dmh",
    "mid-session": "dmh",
}

UNKNOWN_TEXT = "Time not announced"
UNKNOWN_SHORT = "Not announced"

# Shown beside a date the company has not confirmed. Worded to say what is
# actually known — that the session is unannounced and what normally follows
# from that — rather than asserting a provenance no feed reports.
PROVISIONAL_NOTE = (
    "The company has not announced the session yet, which it normally does "
    "when it confirms the date. Treat this date as provisional — it can "
    "move by days."
)


def normalise(value) -> str:
    """A provider's session value reduced to bmo / amc / dmh, or ''.

    Accepts the raw feed codes, the display strings the app has historically
    stored, and None. Anything unrecognised is '' — unknown, not an error.
    """
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text in SESSIONS:
        return text
    if text in ALIASES:
        return ALIASES[text]
    # "TBD" and friends are the absence of an answer, spelled several ways.
    if text in ("tbd", "n/a", "na", "none", "unknown", "-", "—"):
        return ""
    return ""


def describe(value) -> dict:
    """What this row knows about its timing.

    Returns:
      code        'bmo' | 'amc' | 'dmh' | ''      the normalised session
      text        the long form for a card
      short       the compact form for a dense row
      known       whether the company has stated a session
      provisional whether the DATE should be read as unconfirmed
      note        why, when it is provisional; '' otherwise
    """
    code = normalise(value)
    if code:
        long_text, short_text = SESSIONS[code]
        return {"code": code, "text": long_text, "short": short_text,
                "known": True, "provisional": False, "note": ""}
    return {"code": "", "text": UNKNOWN_TEXT, "short": UNKNOWN_SHORT,
            "known": False, "provisional": True, "note": PROVISIONAL_NOTE}


def label_for_storage(value) -> str:
    """The compact code the rest of the app stores in time_label.

    Unchanged behaviour for bmo and amc, so nothing downstream shifts under
    its feet; dmh now survives instead of collapsing into TBD.
    """
    code = normalise(value)
    return code.upper() if code else "TBD"
