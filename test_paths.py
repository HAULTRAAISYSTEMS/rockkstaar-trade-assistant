"""Reading paths: the order, and the reasons for it.

A library assumes you know what you are looking for, which is exactly what a
learner does not. These check that every path is a real route — no dead
references, no step that arrives before the idea it depends on, and a reason
attached to every step, because the ordering without the reasons is a table of
contents rather than teaching.
"""
import os

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import concepts as C
import paths as P
import web_app


PATH_IDS = [p["slug"] for p in P.ALL]


@pytest.mark.parametrize("path", P.ALL, ids=PATH_IDS)
class TestEveryPathIsWellFormed:
    def test_it_has_a_name_and_a_blurb(self, path):
        assert path["name"] and path["blurb"]

    def test_it_promises_an_outcome(self, path):
        """A path should say what you will be able to do, not just what it covers."""
        assert len(path["outcome"]) >= 60, path["slug"]

    def test_the_slug_is_url_safe(self, path):
        import re
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", path["slug"])

    def test_it_is_long_enough_to_be_a_path(self, path):
        assert len(path["steps"]) >= 3, path["slug"]

    def test_it_is_short_enough_to_finish(self, path):
        assert len(path["steps"]) <= 12, path["slug"]

    def test_every_step_resolves_to_a_real_concept(self, path):
        for step in path["steps"]:
            assert C.get(step["slug"]) is not None, step["slug"]

    def test_every_step_says_why_it_follows_the_last(self, path):
        """The reasons are the teaching. Ordering alone is a contents page."""
        for step in path["steps"]:
            assert len(step["why"]) >= 50, (path["slug"], step["slug"])

    def test_no_concept_appears_twice_in_one_path(self, path):
        slugs = [s["slug"] for s in path["steps"]]
        assert len(slugs) == len(set(slugs)), path["slug"]

    def test_the_steps_are_numbered_from_one(self, path):
        assert [s["n"] for s in path["steps"]] == list(range(1, len(path["steps"]) + 1))


class TestTheSetOfPaths:
    def test_slugs_are_unique(self):
        assert len(PATH_IDS) == len(set(PATH_IDS))

    def test_names_are_unique(self):
        names = [p["name"] for p in P.ALL]
        assert len(names) == len(set(names))

    def test_every_concept_is_reachable_from_some_path(self):
        """A concept no path leads to is one a learner will never be shown."""
        assert P.coverage()["orphans"] == []

    def test_paths_are_allowed_to_overlap(self):
        """Meeting an idea twice from two directions is how it sticks."""
        appearances = [s["slug"] for p in P.ALL for s in p["steps"]]
        assert len(appearances) > len(set(appearances))

    def test_a_shared_concept_reports_every_path_it_is_on(self):
        on = {p["slug"] for p in P.paths_containing("eps")}
        assert "read-an-income-statement" in on
        assert "what-it-costs" in on

    def test_a_concept_on_no_path_reports_none(self):
        assert P.paths_containing("not-a-concept") == []


class TestOrderMakesSense:
    """Dependencies inside a path, where one idea is built from another."""

    @pytest.mark.parametrize("path_slug,earlier,later", [
        ("read-an-income-statement", "revenue", "gross-profit"),
        ("read-an-income-statement", "gross-profit", "gross-margin"),
        ("read-an-income-statement", "operating-income", "operating-margin"),
        ("read-an-income-statement", "net-income", "eps"),
        ("read-a-balance-sheet", "balance-sheet", "equity"),
        ("read-a-balance-sheet", "total-debt", "short-term-debt"),
        ("read-a-balance-sheet", "short-term-debt", "cash-coverage"),
        ("read-a-balance-sheet", "goodwill", "impairment"),
        ("follow-the-cash", "operating-cash-flow", "free-cash-flow"),
        ("follow-the-cash", "capex", "free-cash-flow"),
        ("follow-the-cash", "free-cash-flow", "earnings-quality"),
        ("follow-the-cash", "net-income", "earnings-quality"),
        ("judge-a-business", "roe", "roic"),
        ("judge-a-business", "roic", "moat"),
        ("what-it-costs", "eps", "pe-ratio"),
        ("what-it-costs", "free-cash-flow", "price-to-fcf"),
        ("growth-and-who-it-belongs-to", "eps", "eps-growth"),
        ("growth-and-who-it-belongs-to", "share-dilution", "stock-split"),
        ("companies-that-dont-file-in-dollars", "reporting-currency", "market-cap"),
    ])
    def test_the_prerequisite_comes_first(self, path_slug, earlier, later):
        order = [s["slug"] for s in P.get(path_slug)["steps"]]
        assert order.index(earlier) < order.index(later), path_slug

    def test_a_path_starts_with_something_a_beginner_can_start_with(self):
        for path in P.ALL:
            first = C.get(path["steps"][0]["slug"])
            assert first["level"] <= 2, (path["slug"], first["slug"])


class TestProgress:
    PATH = P.get("follow-the-cash")

    def test_a_blank_slate_starts_at_step_one(self):
        annotated = P.with_progress(self.PATH, {})
        assert annotated["done"] == 0 and annotated["percent"] == 0
        assert annotated["next_step"] == self.PATH["steps"][0]["slug"]
        assert annotated["complete"] is False

    def test_a_concept_in_box_two_counts_as_behind_you(self):
        first = self.PATH["steps"][0]["slug"]
        annotated = P.with_progress(self.PATH, {first: {"box": 2, "seen": 2}})
        assert annotated["done"] == 1
        assert annotated["next_step"] == self.PATH["steps"][1]["slug"]

    def test_met_but_not_solid_does_not_count_as_done(self):
        """Box 1 means it was answered wrong or only met once."""
        first = self.PATH["steps"][0]["slug"]
        annotated = P.with_progress(self.PATH, {first: {"box": 1, "seen": 1}})
        assert annotated["done"] == 0
        assert annotated["steps"][0]["seen"] is True
        assert annotated["steps"][0]["done"] is False

    def test_the_next_step_skips_over_what_is_already_known(self):
        records = {s["slug"]: {"box": 3, "seen": 3} for s in self.PATH["steps"][:3]}
        annotated = P.with_progress(self.PATH, records)
        assert annotated["next_step"] == self.PATH["steps"][3]["slug"]

    def test_a_finished_path_has_no_next_step(self):
        records = {s["slug"]: {"box": 4, "seen": 4} for s in self.PATH["steps"]}
        annotated = P.with_progress(self.PATH, records)
        assert annotated["complete"] is True
        assert annotated["next_step"] is None
        assert annotated["percent"] == 100

    def test_progress_from_another_path_counts_here_too(self):
        """Shared concepts are shared progress — you learned the idea, not the step."""
        annotated = P.with_progress(P.get("what-it-costs"),
                                    {"eps": {"box": 3, "seen": 3}})
        step = next(s for s in annotated["steps"] if s["slug"] == "eps")
        assert step["done"] is True

    def test_every_path_annotates_without_error_on_an_empty_history(self):
        assert len(P.all_with_progress({})) == len(P.ALL)


@pytest.fixture(scope="module")
def client():
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
    return c


class TestThePages:
    def test_the_index_lists_every_path(self, client):
        html = client.get("/learn/paths").get_data(as_text=True)
        for path in P.ALL:
            assert f'/learn/path/{path["slug"]}' in html

    @pytest.mark.parametrize("slug", PATH_IDS)
    def test_every_path_page_renders(self, client, slug):
        response = client.get(f"/learn/path/{slug}")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "{{" not in html and "{%" not in html

    def test_a_path_page_links_every_step_to_its_concept(self, client):
        path = P.get("follow-the-cash")
        html = client.get(f'/learn/path/{path["slug"]}').get_data(as_text=True)
        for step in path["steps"]:
            assert f'/learn/{step["slug"]}' in html

    def test_a_path_page_shows_the_reasons(self, client):
        path = P.get("follow-the-cash")
        html = client.get(f'/learn/path/{path["slug"]}').get_data(as_text=True)
        assert path["steps"][2]["why"][:40] in html

    def test_an_unknown_path_is_a_404_that_still_offers_the_others(self, client):
        response = client.get("/learn/path/not-a-path")
        assert response.status_code == 404
        assert "/learn/path/follow-the-cash" in response.get_data(as_text=True)

    def test_a_concept_page_says_which_paths_it_is_on(self, client):
        html = client.get("/learn/eps").get_data(as_text=True)
        assert "/learn/path/read-an-income-statement" in html

    def test_the_library_index_offers_paths(self, client):
        html = client.get("/learn").get_data(as_text=True)
        assert "/learn/paths" in html
