"""Spaced repetition: deciding what to ask, and when to ask it again.

Reading a definition once and agreeing with it is not learning it. The gap
between recognising an explanation and being able to use it is where most
self-teaching fails, and it closes by being asked — repeatedly, at widening
intervals, with the things you get wrong coming back sooner than the things
you get right.

The scheme here is a Leitner box: every concept sits in a box, a correct
answer promotes it and lengthens the wait, a wrong answer sends it back to the
start. It is deliberately simpler than the SM-2 family used by flashcard apps,
which tune an ease factor per card from a self-reported difficulty rating.
Self-rating is unreliable and the tuning needs hundreds of reviews per card to
pay for itself; a learner working through fifty concepts will never get there.
Boxes need no rating and are legible — a reader can be shown exactly why a
card is due.

Everything in this module is a pure function of its arguments and the clock,
so the schedule can be tested without a database and a user's whole history
can be replayed to check it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Box 0 is unseen. A card promoted out of the last box keeps its interval.
# The steps widen roughly threefold: a day, then most of a week, then a
# fortnight, a month, and a quarter. Anything longer stops being revision and
# starts being a fresh encounter.
INTERVALS_DAYS: tuple[int, ...] = (0, 1, 3, 8, 21, 60)
MAX_BOX = len(INTERVALS_DAYS) - 1

# A card answered wrongly does not wait a day — it comes back inside the same
# sitting, once a few others have been in between, which is when the correction
# is still fresh enough to attach to the mistake.
RELEARN_MINUTES = 10


def now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value) -> datetime | None:
    """A stored timestamp, however it was written."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def next_box(box: int, correct: bool) -> int:
    """Where a card goes after being answered.

    Wrong sends it to box 1 rather than box 0: box 0 means never seen, and a
    card you have seen and failed is not the same as a card you have not met.
    """
    box = max(0, int(box or 0))
    if not correct:
        return 1
    return min(box + 1, MAX_BOX)


def next_due(box: int, correct: bool, at: datetime | None = None) -> datetime:
    """When a card should next be asked."""
    at = at or now()
    if not correct:
        return at + timedelta(minutes=RELEARN_MINUTES)
    return at + timedelta(days=INTERVALS_DAYS[min(next_box(box, correct), MAX_BOX)])


def is_due(record: dict | None, at: datetime | None = None) -> bool:
    """Whether a card is ready to be asked. An unseen card always is."""
    if not record:
        return True
    due = _parse(record.get("due_at"))
    return due is None or due <= (at or now())


def schedule(record: dict | None, correct: bool, at: datetime | None = None) -> dict:
    """The updated state of one card after an answer.

    Returns the fields to persist. The caller owns storage; this owns the rule.
    """
    at = at or now()
    box = int((record or {}).get("box") or 0)
    seen = int((record or {}).get("seen") or 0)
    right = int((record or {}).get("correct") or 0)
    promoted = next_box(box, correct)
    return {
        "box": promoted,
        "due_at": next_due(box, correct, at).isoformat(),
        "seen": seen + 1,
        "correct": right + (1 if correct else 0),
        "last_correct": bool(correct),
        "last_seen_at": at.isoformat(),
    }


def pick_next(slugs: list[str], records: dict[str, dict],
              at: datetime | None = None, exclude: set[str] | None = None) -> str | None:
    """The concept to ask about next.

    Order of preference, and the reasoning for each:

    1. Cards that are due and have been failed before. The correction has the
       most to offer where it has already failed to land once.
    2. Anything else due, oldest first, so nothing sits overdue indefinitely
       while newer cards cycle.
    3. A concept never seen, in library order, so a new learner meets ideas in
       the order they build on each other rather than at random.

    Returns None when there is nothing due and nothing new — which is the
    honest answer, and better than manufacturing a review to fill a page.
    """
    at = at or now()
    exclude = exclude or set()
    candidates = [s for s in slugs if s not in exclude]

    due_seen: list[tuple] = []
    unseen: list[str] = []
    for index, slug in enumerate(candidates):
        record = records.get(slug)
        if not record or not record.get("seen"):
            unseen.append(slug)
            continue
        if not is_due(record, at):
            continue
        failed = 0 if record.get("last_correct") else -1   # failed sorts first
        due_at = _parse(record.get("due_at")) or at
        due_seen.append((failed, due_at, index, slug))

    if due_seen:
        due_seen.sort(key=lambda row: (row[0], row[1], row[2]))
        return due_seen[0][3]
    return unseen[0] if unseen else None


def progress(slugs: list[str], records: dict[str, dict],
             at: datetime | None = None) -> dict:
    """A summary of where a learner stands, for the dashboard.

    "Learned" means box 3 or higher: answered correctly enough times that the
    next review is three weeks out. It is a claim about retention, so it is
    deliberately harder to reach than answering right once.
    """
    at = at or now()
    total = len(slugs)
    seen = learned = due = struggling = 0
    for slug in slugs:
        record = records.get(slug)
        if not record or not record.get("seen"):
            continue
        seen += 1
        box = int(record.get("box") or 0)
        if box >= 3:
            learned += 1
        if box <= 1 and int(record.get("seen") or 0) >= 2:
            struggling += 1
        if is_due(record, at):
            due += 1
    return {
        "total": total,
        "seen": seen,
        "unseen": total - seen,
        "learned": learned,
        "struggling": struggling,
        "due": due + (total - seen),
        "due_reviews": due,
        "percent_learned": round(learned / total * 100) if total else 0,
    }
