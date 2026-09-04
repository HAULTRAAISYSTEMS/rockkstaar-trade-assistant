"""The library renders, and every link in it goes somewhere.

A concept store nobody can reach is a text file. These check the two routes,
the search box, the 404 path, and — the part that actually matters — that
every "?" on the scorecard now links into a page that exists.
"""
import os

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import concepts as C
import web_app


@pytest.fixture(scope="module")
def client():
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
    return c


class TestTheIndex:
    def test_it_renders(self, client):
        assert client.get("/learn").status_code == 200

    def test_every_topic_is_on_it(self, client):
        html = client.get("/learn").get_data(as_text=True)
        for topic in C.by_topic():
            assert topic["name"] in html

    def test_every_concept_is_on_it(self, client):
        html = client.get("/learn").get_data(as_text=True)
        for concept in C.CONCEPTS:
            assert f'/learn/{concept["slug"]}' in html, concept["slug"]

    def test_nothing_leaked_an_unrendered_expression(self, client):
        html = client.get("/learn").get_data(as_text=True)
        assert "{{" not in html and "{%" not in html


class TestSearch:
    def test_a_hit_shows_the_concept(self, client):
        html = client.get("/learn?q=eps").get_data(as_text=True)
        assert "/learn/eps" in html

    def test_a_miss_says_so_rather_than_showing_nothing(self, client):
        html = client.get("/learn?q=zzzznotathing").get_data(as_text=True)
        assert "0 result" in html
        assert "Nothing matches that yet" in html

    def test_a_miss_still_offers_the_library(self, client):
        html = client.get("/learn?q=zzzznotathing").get_data(as_text=True)
        assert "/learn/vix" in html

    def test_an_empty_query_browses(self, client):
        assert client.get("/learn?q=").status_code == 200

    def test_a_very_long_query_does_not_break_it(self, client):
        assert client.get("/learn?q=" + "a" * 500).status_code == 200


class TestAConceptPage:
    @pytest.mark.parametrize("slug", [c["slug"] for c in C.CONCEPTS])
    def test_every_concept_has_a_page_that_renders(self, client, slug):
        response = client.get(f"/learn/{slug}")
        assert response.status_code == 200, slug
        html = response.get_data(as_text=True)
        assert "{{" not in html and "{%" not in html

    def test_it_shows_the_five_sections(self, client):
        html = client.get("/learn/free-cash-flow").get_data(as_text=True)
        for heading in ("What it is", "The arithmetic", "Why it matters",
                        "How to read it", "Where it misleads"):
            assert heading in html

    def test_it_points_at_the_live_surface(self, client):
        html = client.get("/learn/free-cash-flow").get_data(as_text=True)
        assert "See it on a real company" in html
        assert "/fundamentals" in html

    def test_a_macro_concept_points_at_the_calendar(self, client):
        html = client.get("/learn/cpi").get_data(as_text=True)
        assert "/calendar" in html

    def test_it_cites_its_source(self, client):
        html = client.get("/learn/vix").get_data(as_text=True)
        assert "cboe.com" in html

    def test_related_concepts_are_linked(self, client):
        html = client.get("/learn/eps").get_data(as_text=True)
        assert "/learn/share-dilution" in html

    def test_an_unknown_slug_is_a_404_that_still_helps(self, client):
        response = client.get("/learn/not-a-real-concept")
        assert response.status_code == 404
        html = response.get_data(as_text=True)
        assert "not-a-real-concept" in html
        assert "/learn/vix" in html, "the 404 should still offer the library"


class TestTheScorecardLinksIn:
    def test_the_row_expander_links_to_the_concept_page(self):
        page = open("templates/fundamentals.html").read()
        assert "url_for('learn_concept', slug=row.edu.slug)" in page

    def test_the_expander_shows_how_to_read_and_the_traps(self):
        page = open("templates/fundamentals.html").read()
        assert "row.edu.read_it" in page
        assert "row.edu.traps" in page

    @pytest.mark.parametrize("row", sorted(C.ROW_CONCEPTS))
    def test_every_scorecard_row_reaches_a_live_page(self, client, row):
        slug = C.ROW_CONCEPTS[row]
        assert client.get(f"/learn/{slug}").status_code == 200, row


class TestItIsReachable:
    def test_the_nav_has_a_link(self):
        nav = open("templates/_elite_navigation.html").read()
        assert "url_for('learn_index')" in nav

    def test_the_mobile_nav_has_one_too(self):
        nav = open("templates/_elite_navigation.html").read()
        assert nav.count("url_for('learn_index')") >= 2

    def test_the_link_highlights_on_both_learn_pages(self):
        nav = open("templates/_elite_navigation.html").read()
        assert "'learn_index', 'learn_concept'" in nav
