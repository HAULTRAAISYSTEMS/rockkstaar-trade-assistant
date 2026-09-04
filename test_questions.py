"""The question bank.

Two of the three kinds are generated from the concept store, so they cannot
contain a wrong answer — but they can contain an ambiguous one, which is worse
than useless in a learning tool. Most of what follows checks that the four
options are genuinely distinguishable and that the right one is the only one
that fits.
"""
import pytest

import concepts as C
import questions as Q


ALL = [q for c in C.CONCEPTS for q in Q.for_concept(c["slug"])]


def qid(q):
    return f'{q["concept"]}:{q["kind"]}'


class TestEveryQuestionIsWellFormed:
    @pytest.mark.parametrize("q", ALL, ids=[qid(q) for q in ALL])
    def test_it_has_a_prompt(self, q):
        assert q["prompt"] and q["prompt"].strip().endswith("?")

    @pytest.mark.parametrize("q", ALL, ids=[qid(q) for q in ALL])
    def test_it_offers_four_options(self, q):
        assert len(q["options"]) == 4

    @pytest.mark.parametrize("q", ALL, ids=[qid(q) for q in ALL])
    def test_exactly_one_option_is_right(self, q):
        assert sum(1 for o in q["options"] if o["correct"]) == 1

    @pytest.mark.parametrize("q", ALL, ids=[qid(q) for q in ALL])
    def test_no_two_options_are_the_same(self, q):
        texts = [o["text"] for o in q["options"]]
        assert len(texts) == len(set(texts))

    @pytest.mark.parametrize("q", ALL, ids=[qid(q) for q in ALL])
    def test_every_option_explains_itself(self, q):
        """A learner who picks wrongly is told why that answer is wrong."""
        assert all(o.get("why") for o in q["options"])

    @pytest.mark.parametrize("q", ALL, ids=[qid(q) for q in ALL])
    def test_it_explains_the_idea_afterwards(self, q):
        assert len(q["explain"]) >= 40

    @pytest.mark.parametrize("q", ALL, ids=[qid(q) for q in ALL])
    def test_it_names_a_real_concept(self, q):
        assert C.get(q["concept"]) is not None


class TestTheAnswerIsNotGiveable:
    def test_the_right_answer_is_not_always_first(self):
        firsts = [q["options"][0]["correct"] for q in ALL]
        assert 0 < sum(firsts) < len(firsts), "position leaks the answer"

    def test_positions_are_spread_across_all_four_slots(self):
        slots = {next(i for i, o in enumerate(q["options"]) if o["correct"])
                 for q in ALL}
        assert slots == {0, 1, 2, 3}

    def test_the_ordering_is_stable_between_calls(self):
        """A question should look the same every time, not reshuffle."""
        first = [o["text"] for o in Q.for_concept("vix")[0]["options"]]
        second = [o["text"] for o in Q.for_concept("vix")[0]["options"]]
        assert first == second

    def test_the_right_answer_is_not_reliably_the_longest(self):
        longest = sum(
            1 for q in ALL
            if max(q["options"], key=lambda o: len(o["text"]))["correct"])
        assert longest < len(ALL) * 0.6, "length leaks the answer"


class TestGeneratedQuestionsAreUnambiguous:
    DEFS = [q for q in ALL if q["kind"] == "definition"]

    @pytest.mark.parametrize("q", DEFS, ids=[qid(q) for q in DEFS])
    def test_no_distractor_is_the_concepts_own_description(self, q):
        concept = C.get(q["concept"])
        wrong = [o["text"] for o in q["options"] if not o["correct"]]
        assert concept["one_liner"] not in wrong

    @pytest.mark.parametrize("q", DEFS, ids=[qid(q) for q in DEFS])
    def test_every_distractor_belongs_to_a_different_concept(self, q):
        by_line = {c["one_liner"]: c["slug"] for c in C.CONCEPTS}
        for opt in q["options"]:
            if not opt["correct"]:
                assert by_line.get(opt["text"]) != q["concept"]

    def test_formula_questions_only_exist_where_there_is_a_formula(self):
        for q in ALL:
            if q["kind"] == "formula":
                assert C.get(q["concept"])["formula"]

    def test_a_formula_distractor_is_never_the_same_arithmetic(self):
        for q in ALL:
            if q["kind"] != "formula":
                continue
            right = C.get(q["concept"])["formula"]
            assert right not in [o["text"] for o in q["options"] if not o["correct"]]


class TestCoverage:
    def test_every_concept_can_be_asked_about(self):
        missing = [c["slug"] for c in C.CONCEPTS if not Q.for_concept(c["slug"])]
        assert missing == []

    def test_the_hardest_ideas_have_a_judgement_question(self):
        """Recognition does not prove you can use an idea. These are the ones
        where being able to use it is the point."""
        for slug in ("roe", "roic", "earnings-quality", "eps", "vix", "cpi",
                     "pe-ratio", "share-dilution", "free-cash-flow",
                     "earnings-report", "fed-funds-rate", "reporting-currency"):
            assert Q.JUDGEMENT.get(slug), slug

    def test_every_judgement_question_names_a_concept_that_exists(self):
        for slug in Q.JUDGEMENT:
            assert C.get(slug) is not None, slug

    def test_an_unknown_concept_yields_no_questions(self):
        assert Q.for_concept("not-a-concept") == []


class TestPicking:
    def test_a_first_meeting_is_recognition(self):
        assert Q.pick("roe", box=0)["kind"] == "definition"

    def test_a_well_known_concept_gets_the_harder_question(self):
        assert Q.pick("roe", box=3)["kind"] == "judgement"

    def test_a_concept_with_no_judgement_question_still_returns_something(self):
        slug = next(c["slug"] for c in C.CONCEPTS if not Q.JUDGEMENT.get(c["slug"]))
        assert Q.pick(slug, box=5) is not None

    def test_an_unknown_concept_returns_none(self):
        assert Q.pick("nope") is None


class TestGrading:
    def test_a_right_answer_is_marked_right(self):
        q = Q.for_concept("vix")[0]
        right = next(o["text"] for o in q["options"] if o["correct"])
        assert Q.grade("vix", q["kind"], right)["correct"] is True

    def test_a_wrong_answer_is_marked_wrong_and_explained(self):
        q = Q.for_concept("vix")[0]
        wrong = next(o["text"] for o in q["options"] if not o["correct"])
        result = Q.grade("vix", q["kind"], wrong)
        assert result["correct"] is False
        assert result["picked"]["why"]

    def test_a_wrong_answer_still_reveals_the_right_one(self):
        q = Q.for_concept("vix")[0]
        wrong = next(o["text"] for o in q["options"] if not o["correct"])
        assert Q.grade("vix", q["kind"], wrong)["answer"]["correct"] is True

    def test_a_submitted_answer_cannot_assert_its_own_correctness(self):
        """The browser sends the text it picked, never the verdict."""
        assert Q.grade("vix", "judgement", "correct")["correct"] is False

    def test_text_that_is_not_an_option_counts_as_unanswered(self):
        result = Q.grade("vix", "judgement", "something nobody offered")
        assert result["unanswered"] is True and result["correct"] is False

    @pytest.mark.parametrize("slug,kind", [
        ("nope", "definition"), ("vix", "not-a-kind"), ("", ""),
    ])
    def test_an_unknown_question_grades_to_none_rather_than_raising(self, slug, kind):
        assert Q.grade(slug, kind, "x") is None

    def test_every_question_in_the_bank_can_be_graded(self):
        for q in ALL:
            right = next(o["text"] for o in q["options"] if o["correct"])
            assert Q.grade(q["concept"], q["kind"], right)["correct"]
