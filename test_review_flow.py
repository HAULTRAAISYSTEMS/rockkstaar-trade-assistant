"""The review loop end to end: ask, mark, remember, come back.

Also the security shape of it — the browser is told which question it is
answering, never whether its answer was right.
"""
import os
import re
import sqlite3

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import concepts as C
import database
import questions as Q
import web_app


SCHEMA = """
CREATE TABLE concept_reviews(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL, slug TEXT NOT NULL,
    box INTEGER NOT NULL DEFAULT 0, due_at TEXT,
    seen INTEGER NOT NULL DEFAULT 0, correct INTEGER NOT NULL DEFAULT 0,
    last_correct INTEGER NOT NULL DEFAULT 0, last_seen_at TEXT,
    UNIQUE(user_id, slug));
"""


class _KeepOpen:
    """The code under test closes what get_db() hands it."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        pass


@pytest.fixture(autouse=True)
def db(monkeypatch):
    """Every test in this file gets its own empty schedule.

    Autouse and function-scoped on purpose. Without it the page tests read and
    wrote whatever database the environment happened to point at, which made
    them depend on each other's order and on the developer's own progress.
    """
    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    conn = _KeepOpen(real)
    conn.executescript(SCHEMA)
    monkeypatch.setattr(database, "get_db", lambda *a, **k: conn)
    # app.py imported these by name, so patching the module is not enough.
    import app as legacy
    monkeypatch.setattr(legacy, "get_concept_reviews", database.get_concept_reviews)
    monkeypatch.setattr(legacy, "save_concept_review", database.save_concept_review)
    monkeypatch.setattr(legacy, "reset_concept_reviews", database.reset_concept_reviews)
    yield conn
    real.close()


class TestPersistence:
    def test_a_new_user_has_no_history(self, db):
        assert database.get_concept_reviews(1) == {}

    def test_a_review_round_trips(self, db):
        database.save_concept_review(1, "vix", {
            "box": 2, "due_at": "2026-09-08T12:00:00+00:00", "seen": 3,
            "correct": 2, "last_correct": True,
            "last_seen_at": "2026-09-04T12:00:00+00:00"})
        record = database.get_concept_reviews(1)["vix"]
        assert record["box"] == 2 and record["seen"] == 3
        assert record["last_correct"] is True

    def test_saving_the_same_concept_twice_updates_rather_than_duplicates(self, db):
        for box in (1, 2, 3):
            database.save_concept_review(1, "vix", {
                "box": box, "due_at": "x", "seen": box, "correct": box,
                "last_correct": True, "last_seen_at": "y"})
        assert database.get_concept_reviews(1)["vix"]["box"] == 3
        assert db.execute("SELECT COUNT(*) c FROM concept_reviews").fetchone()["c"] == 1

    def test_one_users_progress_is_invisible_to_another(self, db):
        database.save_concept_review(1, "vix", {
            "box": 4, "due_at": "x", "seen": 4, "correct": 4,
            "last_correct": True, "last_seen_at": "y"})
        assert database.get_concept_reviews(2) == {}

    def test_a_reset_clears_only_that_user(self, db):
        for uid in (1, 2):
            database.save_concept_review(uid, "vix", {
                "box": 1, "due_at": "x", "seen": 1, "correct": 1,
                "last_correct": True, "last_seen_at": "y"})
        database.reset_concept_reviews(1)
        assert database.get_concept_reviews(1) == {}
        assert database.get_concept_reviews(2) != {}

    def test_an_anonymous_caller_writes_nothing(self, db):
        database.save_concept_review(0, "vix", {
            "box": 1, "due_at": "x", "seen": 1, "correct": 1,
            "last_correct": True, "last_seen_at": "y"})
        assert db.execute("SELECT COUNT(*) c FROM concept_reviews").fetchone()["c"] == 0


@pytest.fixture
def client():
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
    return c


@pytest.fixture
def token():
    from flask import session as flask_session
    from flask_wtf.csrf import generate_csrf
    with web_app.app.test_request_context():
        header = generate_csrf()
        stored = flask_session.get("csrf_token")
    return header, stored


def asked(html):
    """The concept, kind and options the page is currently asking about."""
    return {
        "slug": re.search(r'name="slug" value="([^"]+)"', html).group(1),
        "kind": re.search(r'name="kind" value="([^"]+)"', html).group(1),
        "options": re.findall(r'name="choice" value="([^"]+)"', html),
    }


class TestTheReviewPage:
    def test_it_renders_a_question(self, client):
        html = client.get("/learn/review").get_data(as_text=True)
        question = asked(html)
        assert C.get(question["slug"]) is not None
        assert len(question["options"]) == 4

    def test_it_starts_at_the_beginning_of_the_library(self, client):
        html = client.get("/learn/review").get_data(as_text=True)
        assert asked(html)["slug"] == C.CONCEPTS[0]["slug"]

    def test_nothing_leaks_which_option_is_right(self, client):
        html = client.get("/learn/review").get_data(as_text=True)
        question = asked(html)
        right = next(o["text"] for o in Q.for_concept(question["slug"])[0]["options"]
                     if o["correct"])
        # The correct text appears once, as an option, with nothing marking it.
        marked = re.search(r'value="' + re.escape(right) + r'"[^>]*(correct|is-answer)',
                           html)
        assert marked is None

    def test_it_shows_progress(self, client):
        html = client.get("/learn/review").get_data(as_text=True)
        assert "learned" in html and "met" in html

    def test_it_requires_a_login(self, monkeypatch):
        """Guarded like every other page.

        The app deliberately opens up when no user account exists yet, so a
        fresh deploy can be set up — which means this has to force the
        authenticated case rather than relying on an empty test database.
        """
        import app as legacy
        monkeypatch.setattr(legacy, "_auth_required", lambda: True)
        anon = web_app.app.test_client()
        for path in ("/learn", "/learn/review", "/learn/vix"):
            assert anon.get(path).status_code in (302, 401, 403), path

    def test_nothing_leaked_an_unrendered_expression(self, client):
        html = client.get("/learn/review").get_data(as_text=True)
        assert "{{" not in html and "{%" not in html


class TestAnswering:
    def _answer(self, client, token, correct=True):
        header, stored = token
        with client.session_transaction() as sess:
            sess["csrf_token"] = stored
        question = asked(client.get("/learn/review").get_data(as_text=True))
        options = Q.for_concept(question["slug"])
        graded = next(q for q in options if q["kind"] == question["kind"])
        choice = next(o["text"] for o in graded["options"]
                      if o["correct"] is correct)
        return client.post("/learn/review",
                           data={"slug": question["slug"], "kind": question["kind"],
                                 "choice": choice},
                           headers={"X-CSRFToken": header}), question["slug"]

    def test_a_right_answer_is_confirmed(self, client, token):
        response, _ = self._answer(client, token, correct=True)
        assert response.status_code == 200
        assert "Correct" in response.get_data(as_text=True)

    def test_a_wrong_answer_says_so_and_explains(self, client, token):
        response, _ = self._answer(client, token, correct=False)
        html = response.get_data(as_text=True)
        assert "Not quite" in html
        assert "The idea" in html

    def test_a_wrong_answer_reveals_the_right_one(self, client, token):
        response, slug = self._answer(client, token, correct=False)
        html = response.get_data(as_text=True)
        assert "is-answer" in html

    def test_the_answer_page_offers_the_next_question(self, client, token):
        response, _ = self._answer(client, token, correct=True)
        assert "/learn/review" in response.get_data(as_text=True)

    def test_the_answer_page_links_to_the_full_concept(self, client, token):
        response, slug = self._answer(client, token, correct=True)
        assert f"/learn/{slug}" in response.get_data(as_text=True)

    def test_an_unsigned_post_is_rejected(self, client):
        response = client.post("/learn/review",
                               data={"slug": "vix", "kind": "definition", "choice": "x"})
        assert response.status_code == 400


class TestTheLibraryIndex:
    def test_it_shows_the_review_call_to_action(self, client):
        html = client.get("/learn").get_data(as_text=True)
        assert "/learn/review" in html

    def test_it_reports_progress(self, client):
        html = client.get("/learn").get_data(as_text=True)
        assert "learned" in html
