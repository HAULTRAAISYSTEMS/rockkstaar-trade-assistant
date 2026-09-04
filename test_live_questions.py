"""Questions computed from a real company's filings.

The point of these is that the arithmetic in front of the reader is an actual
company's numbers rather than a worked example with round figures in it. That
only works if the computed answer is genuinely correct and the distractors are
genuinely wrong — a live question that is subtly ambiguous is worse than a
written one, because the reader has no way to check it.
"""
from unittest.mock import patch

import pytest

import fundamentals_engine as fe
import live_questions as LQ
from test_edgar_pipeline import facts, _Resp


@pytest.fixture(scope="module")
def card():
    with patch.object(fe, "_edgar_cik", return_value=("1", "KLA Corporation")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts())):
        return fe.score_fundamentals(fe.fetch_fundamentals_edgar("KLAC"))


@pytest.fixture(scope="module")
def built(card):
    return LQ.build(card, "KLAC")


class TestItProducesQuestions:
    def test_a_real_scorecard_yields_several(self, built):
        assert len(built) >= 5

    def test_every_kind_is_represented(self, built):
        kinds = {q["kind"] for q in built}
        assert {"live-compute", "live-threshold"} <= kinds

    def test_every_question_names_the_company(self, built):
        assert all("KLAC" in q["prompt"] for q in built)

    def test_every_question_names_a_real_concept(self, built):
        import concepts as C
        assert all(C.get(q["concept"]) is not None for q in built)

    def test_every_question_carries_its_ticker(self, built):
        """The form posts it back so the answer is graded off the same card."""
        assert all(q["ticker"] == "KLAC" for q in built)


class TestTheAnswersAreRight:
    def test_the_computed_answer_matches_the_scorecard(self, card, built):
        rows = {r["key"]: r for s in card["sections"] for r in s["rows"]}
        for question in built:
            if question["kind"] != "live-compute":
                continue
            key = next(k for k, spec in LQ.THRESHOLDS.items()
                       if spec["concept"] == question["concept"])
            shown = next(o["text"] for o in question["options"] if o["correct"])
            assert rows[key]["value"] in shown, question["concept"]

    def test_the_threshold_verdict_matches_how_the_card_scored_it(self, card, built):
        rows = {r["key"]: r for s in card["sections"] for r in s["rows"]}
        for question in built:
            if question["kind"] != "live-threshold":
                continue
            key = next(k for k, spec in LQ.THRESHOLDS.items()
                       if spec["concept"] == question["concept"])
            right = next(o["text"] for o in question["options"] if o["correct"])
            assert ("clears the bar" in right) == bool(rows[key]["passed"])

    @pytest.mark.parametrize("kind", ["live-compute", "live-threshold", "live-trend"])
    def test_exactly_one_option_is_right(self, built, kind):
        for question in [q for q in built if q["kind"] == kind]:
            assert sum(1 for o in question["options"] if o["correct"]) == 1

    def test_no_two_options_read_the_same(self, built):
        for question in built:
            texts = [o["text"] for o in question["options"]]
            assert len(texts) == len(set(texts)), question["prompt"][:60]

    def test_every_option_explains_itself(self, built):
        for question in built:
            assert all(o.get("why") for o in question["options"])

    def test_the_working_is_shown_afterwards(self, built):
        computes = [q for q in built if q["kind"] == "live-compute"]
        assert all("=" in q["explain"] for q in computes)


class TestTheDistractorsAreTheRealMistakes:
    def test_the_inverted_ratio_is_offered(self, built):
        """Getting a ratio the wrong way up is the mistake people actually make."""
        question = next(q for q in built if q["kind"] == "live-compute")
        whys = [o["why"] for o in question["options"] if not o["correct"]]
        assert any("the other way up" in w for w in whys)

    def test_a_decimal_slip_is_offered(self, built):
        question = next(q for q in built if q["kind"] == "live-compute")
        whys = [o["why"] for o in question["options"] if not o["correct"]]
        assert any("decimal" in w for w in whys)

    def test_the_answer_is_not_always_in_the_same_place(self, built):
        slots = {next(i for i, o in enumerate(q["options"]) if o["correct"])
                 for q in built}
        assert len(slots) > 1


class TestNothingIsFetched:
    def test_no_scorecard_means_no_questions(self):
        assert LQ.build(None, "KLAC") == []

    def test_an_errored_scorecard_yields_nothing(self):
        assert LQ.build({"error": "not found"}, "KLAC") == []

    def test_no_ticker_yields_nothing(self, card):
        assert LQ.build(card, "") == []

    def test_a_scorecard_with_no_rows_yields_nothing(self):
        assert LQ.build({"sections": []}, "KLAC") == []

    def test_a_row_reading_na_is_skipped(self, card):
        stripped = {**card, "sections": [
            {**s, "rows": [{**r, "value": "N/A", "passed": None}
                           for r in s["rows"]]}
            for s in card["sections"]], "history": []}
        assert LQ.build(stripped, "KLAC") == []


class TestFiltering:
    def test_it_can_be_asked_for_one_concept(self, card):
        found = LQ.for_concept(card, "KLAC", "current-ratio")
        assert found and all(q["concept"] == "current-ratio" for q in found)

    def test_a_concept_with_no_live_question_returns_nothing(self, card):
        assert LQ.for_concept(card, "KLAC", "vix") == []


class TestGrading:
    def test_a_right_answer_is_marked_right(self, card, built):
        question = built[0]
        right = next(o["text"] for o in question["options"] if o["correct"])
        result = LQ.grade(card, "KLAC", question["kind"], question["concept"], right)
        assert result["correct"] is True

    def test_a_wrong_answer_is_marked_wrong_and_explained(self, card, built):
        question = built[0]
        wrong = next(o["text"] for o in question["options"] if not o["correct"])
        result = LQ.grade(card, "KLAC", question["kind"], question["concept"], wrong)
        assert result["correct"] is False and result["picked"]["why"]

    def test_a_submitted_answer_cannot_assert_its_own_correctness(self, card, built):
        question = built[0]
        result = LQ.grade(card, "KLAC", question["kind"], question["concept"], "correct")
        assert result["correct"] is False and result["unanswered"] is True

    def test_grading_without_the_scorecard_returns_none(self, built):
        """A cache that expired between asking and answering must not crash."""
        question = built[0]
        assert LQ.grade(None, "KLAC", question["kind"], question["concept"], "x") is None

    def test_grading_an_unknown_question_returns_none(self, card):
        assert LQ.grade(card, "KLAC", "live-compute", "vix", "x") is None
