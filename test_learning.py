"""The spaced repetition schedule.

Pure functions of their arguments and the clock, so a learner's whole history
can be replayed and asserted on without a database.
"""
from datetime import datetime, timedelta, timezone

import pytest

import learning as L


T0 = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def after(days=0, minutes=0):
    return T0 + timedelta(days=days, minutes=minutes)


class TestPromotion:
    def test_an_unseen_card_answered_right_enters_the_first_box(self):
        assert L.next_box(0, True) == 1

    def test_each_correct_answer_promotes_one_box(self):
        assert [L.next_box(b, True) for b in range(5)] == [1, 2, 3, 4, 5]

    def test_the_last_box_does_not_overflow(self):
        assert L.next_box(L.MAX_BOX, True) == L.MAX_BOX

    def test_a_wrong_answer_goes_back_to_box_one_not_zero(self):
        """Box 0 means never met. A card you have failed is not unmet."""
        assert L.next_box(5, False) == 1
        assert L.next_box(0, False) == 1

    def test_a_negative_or_missing_box_is_tolerated(self):
        assert L.next_box(None, True) == 1
        assert L.next_box(-3, True) == 1


class TestIntervals:
    def test_the_intervals_widen(self):
        assert list(L.INTERVALS_DAYS) == sorted(L.INTERVALS_DAYS)

    def test_a_first_correct_answer_comes_back_tomorrow(self):
        assert L.next_due(0, True, T0) == after(days=1)

    def test_a_well_known_card_waits_a_long_time(self):
        assert L.next_due(4, True, T0) == after(days=60)

    def test_a_wrong_answer_comes_back_within_the_session(self):
        """Not tomorrow — while the correction is still attached to the mistake."""
        assert L.next_due(4, False, T0) == after(minutes=L.RELEARN_MINUTES)
        assert L.next_due(4, False, T0) < after(days=1)


class TestIsDue:
    def test_a_card_never_seen_is_always_due(self):
        assert L.is_due(None) is True
        assert L.is_due({}) is True

    def test_a_card_due_in_the_past_is_due(self):
        assert L.is_due({"due_at": after(days=-1).isoformat()}, T0)

    def test_a_card_due_later_is_not(self):
        assert not L.is_due({"due_at": after(days=1).isoformat()}, T0)

    def test_a_card_due_exactly_now_is_due(self):
        assert L.is_due({"due_at": T0.isoformat()}, T0)

    @pytest.mark.parametrize("stored", [
        "2026-09-03T12:00:00+00:00", "2026-09-03T12:00:00Z",
        "2026-09-03T12:00:00", datetime(2026, 9, 3, 12, 0),
    ])
    def test_it_reads_whatever_format_the_timestamp_was_stored_in(self, stored):
        assert L.is_due({"due_at": stored}, T0)

    def test_an_unparseable_timestamp_makes_the_card_due_rather_than_stuck(self):
        """Failing open means a corrupt row is asked again, not lost forever."""
        assert L.is_due({"due_at": "not a date"}, T0)


class TestSchedule:
    def test_a_first_correct_answer(self):
        state = L.schedule(None, True, T0)
        assert state["box"] == 1
        assert state["seen"] == 1 and state["correct"] == 1
        assert state["last_correct"] is True

    def test_counts_accumulate(self):
        state = L.schedule({"box": 2, "seen": 5, "correct": 3}, True, T0)
        assert state["seen"] == 6 and state["correct"] == 4

    def test_a_wrong_answer_increments_seen_but_not_correct(self):
        state = L.schedule({"box": 3, "seen": 5, "correct": 5}, False, T0)
        assert state["seen"] == 6 and state["correct"] == 5
        assert state["box"] == 1

    def test_a_run_of_right_answers_pushes_the_interval_out(self):
        state, when = None, T0
        gaps = []
        for _ in range(5):
            state = L.schedule(state, True, when)
            due = datetime.fromisoformat(state["due_at"])
            gaps.append((due - when).days)
            when = due
        assert gaps == sorted(gaps) and gaps[-1] > gaps[0]

    def test_one_mistake_undoes_the_accumulated_interval(self):
        state = None
        for _ in range(4):
            state = L.schedule(state, True, T0)
        assert state["box"] == 4
        state = L.schedule(state, False, T0)
        assert state["box"] == 1


class TestPickNext:
    SLUGS = ["a", "b", "c", "d"]

    def test_an_empty_history_starts_at_the_beginning_of_the_library(self):
        """Order matters — concepts build on each other."""
        assert L.pick_next(self.SLUGS, {}, T0) == "a"

    def test_a_failed_card_comes_before_other_due_cards(self):
        records = {
            "a": {"box": 3, "seen": 3, "correct": 3, "last_correct": True,
                  "due_at": after(days=-5).isoformat()},
            "b": {"box": 1, "seen": 2, "correct": 0, "last_correct": False,
                  "due_at": after(days=-1).isoformat()},
        }
        assert L.pick_next(self.SLUGS, records, T0) == "b"

    def test_among_equals_the_most_overdue_comes_first(self):
        records = {
            "a": {"box": 2, "seen": 1, "correct": 1, "last_correct": True,
                  "due_at": after(days=-1).isoformat()},
            "b": {"box": 2, "seen": 1, "correct": 1, "last_correct": True,
                  "due_at": after(days=-9).isoformat()},
        }
        assert L.pick_next(self.SLUGS, records, T0) == "b"

    def test_a_due_review_beats_an_unmet_concept(self):
        records = {"c": {"box": 1, "seen": 1, "correct": 1, "last_correct": True,
                         "due_at": after(days=-1).isoformat()}}
        assert L.pick_next(self.SLUGS, records, T0) == "c"

    def test_nothing_due_and_nothing_new_returns_none(self):
        records = {s: {"box": 3, "seen": 2, "correct": 2, "last_correct": True,
                       "due_at": after(days=9).isoformat()} for s in self.SLUGS}
        assert L.pick_next(self.SLUGS, records, T0) is None

    def test_the_excluded_concept_is_not_asked_again_immediately(self):
        assert L.pick_next(self.SLUGS, {}, T0, exclude={"a"}) == "b"

    def test_excluding_everything_is_not_an_error(self):
        assert L.pick_next(self.SLUGS, {}, T0, exclude=set(self.SLUGS)) is None


class TestProgress:
    SLUGS = ["a", "b", "c", "d"]

    def test_a_blank_slate(self):
        p = L.progress(self.SLUGS, {}, T0)
        assert p["seen"] == 0 and p["unseen"] == 4
        assert p["due"] == 4 and p["percent_learned"] == 0

    def test_learned_means_retained_not_answered_once(self):
        """Box 3 is three correct answers and a three-week interval."""
        records = {"a": {"box": 1, "seen": 1, "correct": 1, "last_correct": True,
                         "due_at": after(days=1).isoformat()}}
        assert L.progress(self.SLUGS, records, T0)["learned"] == 0
        records["a"]["box"] = 3
        assert L.progress(self.SLUGS, records, T0)["learned"] == 1

    def test_a_repeatedly_failed_concept_is_flagged_as_shaky(self):
        records = {"a": {"box": 1, "seen": 4, "correct": 1, "last_correct": False,
                         "due_at": after(days=-1).isoformat()}}
        assert L.progress(self.SLUGS, records, T0)["struggling"] == 1

    def test_one_failure_on_a_first_meeting_is_not_shaky_yet(self):
        records = {"a": {"box": 1, "seen": 1, "correct": 0, "last_correct": False,
                         "due_at": after(days=-1).isoformat()}}
        assert L.progress(self.SLUGS, records, T0)["struggling"] == 0

    def test_due_counts_unmet_concepts_as_well_as_reviews(self):
        records = {"a": {"box": 2, "seen": 2, "correct": 2, "last_correct": True,
                         "due_at": after(days=-1).isoformat()}}
        p = L.progress(self.SLUGS, records, T0)
        assert p["due_reviews"] == 1
        assert p["due"] == 1 + 3

    def test_an_empty_library_does_not_divide_by_zero(self):
        assert L.progress([], {}, T0)["percent_learned"] == 0
