"""The concept store is content, so the tests are an editorial standard.

A wrong or half-written financial explanation is worse than no explanation —
it will be believed. These checks are what stands between a concept being
written and it reaching a reader: every field present and substantial, every
cross-reference resolving, every scorecard row covered, every claim carrying
the primary source it was checked against.
"""
import re

import pytest

import concepts as C


REQUIRED_TEXT = ("slug", "name", "topic", "one_liner", "what", "why",
                 "read_it")
ALL_SLUGS = {c["slug"] for c in C.CONCEPTS}


def ids(concepts):
    return [c["slug"] for c in concepts]


@pytest.mark.parametrize("concept", C.CONCEPTS, ids=ids(C.CONCEPTS))
class TestEveryConceptIsComplete:
    @pytest.mark.parametrize("field", REQUIRED_TEXT)
    def test_the_required_fields_are_present_and_not_blank(self, concept, field):
        assert concept.get(field), f"{concept.get('slug')} missing {field}"
        assert str(concept[field]).strip() == str(concept[field])

    def test_the_slug_is_url_safe(self, concept):
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", concept["slug"])

    def test_the_topic_exists(self, concept):
        assert concept["topic"] in C.TOPICS

    def test_the_level_is_one_we_render(self, concept):
        assert concept["level"] in C.LEVELS

    def test_the_one_liner_is_one_line(self, concept):
        """It is used in tooltips and search results, where length breaks layout."""
        assert "\n" not in concept["one_liner"]
        assert len(concept["one_liner"]) <= 140, len(concept["one_liner"])

    def test_what_is_a_real_explanation_not_a_stub(self, concept):
        assert len(concept["what"]) >= 120, concept["slug"]

    def test_why_answers_why_anyone_should_care(self, concept):
        assert len(concept["why"]) >= 80, concept["slug"]

    def test_read_it_gives_the_reader_something_to_do(self, concept):
        assert len(concept["read_it"]) >= 80, concept["slug"]

    def test_it_names_the_ways_it_misleads(self, concept):
        """The traps are the part a glossary leaves out and a learner needs."""
        traps = concept.get("traps") or []
        assert len(traps) >= 2, f"{concept['slug']} has {len(traps)} traps"
        assert all(len(t) >= 40 for t in traps), concept["slug"]

    def test_every_related_slug_resolves(self, concept):
        for slug in concept.get("related", []):
            assert slug in ALL_SLUGS, f"{concept['slug']} -> missing {slug}"

    def test_it_does_not_relate_to_itself(self, concept):
        assert concept["slug"] not in concept.get("related", [])

    def test_it_carries_a_primary_source(self, concept):
        sources = concept.get("sources") or []
        assert sources, concept["slug"]
        assert all(s.startswith("https://") for s in sources), concept["slug"]

    def test_it_says_where_to_see_it_live(self, concept):
        """The whole point: a definition attached to a real company's numbers."""
        live = concept.get("see_live") or {}
        assert live.get("surface"), concept["slug"]
        assert live.get("note"), concept["slug"]


class TestTheStoreAsAWhole:
    def test_slugs_are_unique(self):
        slugs = [c["slug"] for c in C.CONCEPTS]
        assert len(slugs) == len(set(slugs))

    def test_names_are_unique(self):
        names = [c["name"] for c in C.CONCEPTS]
        assert len(names) == len(set(names))

    def test_no_alias_collides_with_another_concepts_name(self):
        """Search ranks an alias hit as an exact match, so a collision would
        send the reader to the wrong card."""
        by_alias: dict[str, list] = {}
        for concept in C.CONCEPTS:
            for alias in [concept["name"]] + list(concept.get("aka", [])):
                by_alias.setdefault(alias.lower(), []).append(concept["slug"])
        clashes = {a: s for a, s in by_alias.items() if len(s) > 1}
        assert clashes == {}, clashes

    def test_every_topic_has_at_least_one_concept(self):
        assert all(t["concepts"] for t in C.by_topic())

    def test_every_topic_starts_at_the_foundation(self):
        """A topic whose easiest concept is level 2 has a missing first step."""
        for topic in C.by_topic():
            assert topic["concepts"][0]["level"] == 1, topic["key"]

    def test_relationships_are_worth_following(self):
        assert all(c.get("related") for c in C.CONCEPTS)


class TestTheScorecardIsFullyCovered:
    """Every row with a "?" must have something behind it."""

    ROW_KEYS = [
        "current_ratio", "debt_to_equity", "cash_covers_debt",
        "retained_earnings_growth", "goodwill_ratio", "revenue_growth",
        "gross_margin", "operating_margin", "net_margin", "eps_growth",
        "fcf_positive", "fcf_vs_net_income", "ocf_trend", "capex_ratio",
        "debt_financing", "roe", "roic", "moat", "share_dilution",
    ]

    @pytest.mark.parametrize("row", ROW_KEYS)
    def test_the_row_resolves_to_a_concept(self, row):
        assert C.for_row(row) is not None, row

    def test_no_mapping_points_at_a_concept_that_does_not_exist(self):
        for row, slug in C.ROW_CONCEPTS.items():
            assert slug in ALL_SLUGS, f"{row} -> {slug}"

    def test_the_engine_still_gets_its_education_dict(self):
        import fundamentals_engine as fe
        for row in self.ROW_KEYS:
            entry = fe.EDUCATION[row]
            assert entry["def"] and entry["why"]

    def test_the_row_expander_can_reach_the_full_card(self):
        """The "?" links into the library, so it needs the slug."""
        import fundamentals_engine as fe
        assert all(fe.EDUCATION[row].get("slug") for row in self.ROW_KEYS)


class TestTheThingsTheUserAskedToLearn:
    """The request that started this: EPS, the VIX, earnings, reading a
    company's statements."""

    @pytest.mark.parametrize("slug", [
        "eps", "vix", "earnings-report", "cpi", "fed-funds-rate",
        "annual-report-10k", "balance-sheet", "cash-flow-statement",
        "free-cash-flow", "pe-ratio",
    ])
    def test_it_is_covered(self, slug):
        assert C.get(slug) is not None, slug

    @pytest.mark.parametrize("query,expected", [
        ("eps", "eps"),
        ("EPS", "eps"),
        ("earnings per share", "eps"),
        ("vix", "vix"),
        ("fear gauge", "vix"),
        ("10-K", "annual-report-10k"),
        ("P/E", "pe-ratio"),
        ("free cash flow", "free-cash-flow"),
        ("CPI", "cpi"),
        ("FOMC", "fed-funds-rate"),
        ("moat", "moat"),
        ("dot plot", "fed-funds-rate"),
    ])
    def test_searching_the_obvious_term_finds_it_first(self, query, expected):
        results = C.search(query)
        assert results, query
        assert results[0]["slug"] == expected, [r["slug"] for r in results[:3]]


class TestLookup:
    def test_get_is_case_insensitive(self):
        assert C.get("VIX")["slug"] == "vix"

    def test_get_tolerates_whitespace(self):
        assert C.get("  vix  ")["slug"] == "vix"

    @pytest.mark.parametrize("bad", ["", None, "not-a-concept"])
    def test_an_unknown_slug_is_none_not_an_error(self, bad):
        assert C.get(bad) is None

    def test_an_empty_search_returns_nothing(self):
        assert C.search("") == []

    def test_search_respects_its_limit(self):
        assert len(C.search("the", limit=3)) <= 3

    def test_related_resolves_both_ways_for_a_known_pair(self):
        assert "eps" in [c["slug"] for c in C.related_to("share-dilution")]

    def test_related_to_an_unknown_slug_is_empty(self):
        assert C.related_to("nope") == []

    def test_topics_come_back_in_declaration_order(self):
        assert [t["key"] for t in C.by_topic()] == [
            k for k in C.TOPICS if any(c["topic"] == k for c in C.CONCEPTS)]

    def test_concepts_within_a_topic_run_easiest_first(self):
        for topic in C.by_topic():
            levels = [c["level"] for c in topic["concepts"]]
            assert levels == sorted(levels), topic["key"]
