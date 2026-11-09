"""A form left open on a phone no longer ends on a security-check error page.

iOS drops the session cookie when Safari or the home-screen app is suspended
(unless "Remember me" was ticked) and restores the page from memory with its
old form token, so the next post failed CSRF validation — on the sign-in page,
every attempt. And the live pages now poll every few seconds; each poll used
to re-send the session cookie and could put back a cookie from before a new
token was minted. These pin the recoveries and the quiet polls.
"""
import os
import re

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import web_app
import app as legacy


@pytest.fixture
def csrf_on():
    before = web_app.app.config.get("WTF_CSRF_ENABLED", True)
    web_app.app.config["WTF_CSRF_ENABLED"] = True
    yield
    web_app.app.config["WTF_CSRF_ENABLED"] = before


@pytest.fixture
def signed_in():
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
    return c


def test_a_stale_sign_in_form_shows_the_form_again(csrf_on):
    c = web_app.app.test_client()
    r = c.post("/login", data={"username": "x", "password": "y", "csrf_token": "stale",
                               "next": "/terminal"})
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "sign-in form had expired" in html
    assert 'name="csrf_token"' in html and 'value="/terminal"' in html


def test_a_stale_sign_out_still_signs_you_out(csrf_on, signed_in):
    r = signed_in.post("/logout", data={"csrf_token": "stale"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")
    with signed_in.session_transaction() as sess:
        assert "user_id" not in sess


def test_a_stale_form_while_signed_out_goes_to_sign_in(csrf_on, monkeypatch):
    monkeypatch.setattr(legacy, "_auth_required", lambda: True)
    c = web_app.app.test_client()
    r = c.post("/research-digest/reviewed", data={"csrf_token": "stale"},
               headers={"Referer": "http://localhost/terminal"})
    assert r.status_code == 302 and r.headers["Location"].startswith("/login")
    assert "expired=1" in r.headers["Location"]
    assert "You were signed out" in c.get(r.headers["Location"]).get_data(as_text=True)
    assert "next=/terminal" in r.headers["Location"] or "next=%2Fterminal" in r.headers["Location"]


def test_a_stale_form_while_signed_in_is_still_refused(csrf_on, signed_in):
    r = signed_in.post("/research-digest/reviewed", data={"csrf_token": "stale"})
    assert r.status_code == 400
    assert 'name="error-reason" content="csrf"' in r.get_data(as_text=True)


def test_the_token_endpoint_gives_a_token_that_works(csrf_on, signed_in):
    r = signed_in.get("/api/csrf-token")
    assert r.status_code == 200 and "no-store" in r.headers["Cache-Control"]
    token = r.get_json()["token"]
    ok = signed_in.post("/research-digest/reviewed", data={"csrf_token": token})
    assert ok.status_code == 302   # accepted → redirect back to Today


def test_the_token_endpoint_is_open_to_the_sign_in_page(csrf_on, monkeypatch):
    monkeypatch.setattr(legacy, "_auth_required", lambda: True)
    r = web_app.app.test_client().get("/api/csrf-token")
    assert r.status_code == 200 and r.get_json()["signed_in"] is False


def test_api_polls_do_not_resend_the_session_cookie(signed_in, monkeypatch):
    with signed_in.session_transaction() as sess:
        sess.permanent = True
    monkeypatch.setattr(legacy, "_get_mkt_ctx", lambda: {})
    r = signed_in.get("/api/today/pulse")
    assert r.status_code == 200
    assert "Set-Cookie" not in r.headers


def test_page_loads_still_refresh_a_remembered_session(signed_in):
    with signed_in.session_transaction() as sess:
        sess.permanent = True
    r = signed_in.get("/learn") if "learn_page" in web_app.app.view_functions else signed_in.get("/")
    assert "Set-Cookie" in r.headers


def test_forms_refresh_their_token_before_posting():
    js = open("static/js/csrf.js").read()
    assert "/api/csrf-token" in js and "e.persisted" in js and "requestSubmit" in js
    for page in ("templates/login.html", "templates/register.html"):
        assert "js/csrf.js" in open(page).read()
