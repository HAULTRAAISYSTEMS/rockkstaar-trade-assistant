"""An hour is not how long someone leaves an app open.

WTF_CSRF_TIME_LIMIT was never set, so Flask-WTF's default applied and every
form token expired after 3600 seconds. A page loaded on a phone or an iPad
sits there while the device sleeps; an hour later the first button that posts
anything failed with "The CSRF token has expired" and a 400:

    7:20 PM  TRADESTAAR ELITE / 400
             Security check (CSRF): reload the page to renew your form token.

The session was fine. The form was stale, and the page's own advice —
reload — could not be followed, because a failed POST leaves the browser on
the posted URL with no form on it.

The expiry only ever guarded against a token stolen and replayed long after
the fact, and anyone holding the token holds the cookie it is bound to, which
is good for thirty days either way. So the limit is gone and the token lives
as long as its session. These tests hold that line from both sides: age must
stop mattering, and everything that was rejected before must still be.
"""
import time

import pytest
from itsdangerous import URLSafeTimedSerializer

import app as legacy
import web_app


SALT = "wtf-csrf-token"
SESSION_TOKEN = "s" * 40


def _serializer(secret=None):
    return URLSafeTimedSerializer(secret or legacy.app.secret_key, salt=SALT)


def _signed(raw=SESSION_TOKEN, age_seconds=0, secret=None):
    """A CSRF token as it would have been signed `age_seconds` ago."""
    ser = _serializer(secret)
    if not age_seconds:
        return ser.dumps(raw)
    real = time.time
    time.time = lambda: real() - age_seconds
    try:
        return ser.dumps(raw)
    finally:
        time.time = real


@pytest.fixture
def ctx():
    import flask
    with web_app.app.test_request_context("/"):
        flask.session["csrf_token"] = SESSION_TOKEN
        yield


def _validate(token):
    from flask_wtf.csrf import validate_csrf
    validate_csrf(token)


class TestTheLimitIsGone:
    def test_the_config_says_so_explicitly(self):
        """Unset is not the same as set to None here — unset means 3600."""
        assert "WTF_CSRF_TIME_LIMIT" in legacy.app.config
        assert legacy.app.config["WTF_CSRF_TIME_LIMIT"] is None

    @pytest.mark.parametrize("label, age", [
        ("fresh", 0),
        ("just under the old limit", 3500),
        ("just over the old limit", 3700),
        ("overnight", 14 * 3600),
        ("a week", 7 * 86400),
        ("the full session lifetime", 30 * 86400),
    ])
    def test_age_no_longer_decides(self, ctx, label, age):
        _validate(_signed(age_seconds=age))

    def test_the_exact_case_from_the_screenshot(self, ctx):
        """An iPad left alone for two hours, then one tap."""
        _validate(_signed(age_seconds=7200))

    def test_it_really_did_fail_before(self, ctx):
        """Pin the old behaviour so the reason for this change stays legible:
        with the default limit restored, the same token is rejected."""
        from flask_wtf.csrf import validate_csrf
        from wtforms import ValidationError
        token = _signed(age_seconds=7200)
        legacy.app.config["WTF_CSRF_TIME_LIMIT"] = 3600
        try:
            with pytest.raises(ValidationError, match="expired"):
                validate_csrf(token)
        finally:
            legacy.app.config["WTF_CSRF_TIME_LIMIT"] = None
        validate_csrf(token)          # and accepted again once restored


class TestEverythingElseIsStillRejected:
    """Removing the clock must not remove the check."""

    def test_a_token_signed_with_another_secret(self, ctx):
        from wtforms import ValidationError
        with pytest.raises(ValidationError):
            _validate(_signed(secret="a-different-secret-entirely"))

    def test_a_token_belonging_to_a_different_session(self, ctx):
        from wtforms import ValidationError
        with pytest.raises(ValidationError, match="do not match"):
            _validate(_signed(raw="other-session-token"))

    @pytest.mark.parametrize("junk", ["", "   ", "not-a-token", "....", None])
    def test_garbage(self, ctx, junk):
        from wtforms import ValidationError
        with pytest.raises(ValidationError):
            _validate(junk)

    def test_an_old_forged_token_is_rejected_for_being_forged(self, ctx):
        """The one that matters: age is no longer the thing standing between
        an attacker and a successful post, so the signature has to be."""
        from wtforms import ValidationError
        with pytest.raises(ValidationError):
            _validate(_signed(age_seconds=30 * 86400, secret="attacker-secret"))

    def test_a_post_without_a_token_is_still_refused(self):
        client = web_app.app.test_client()
        with client.session_transaction() as s:
            s["user_id"] = 1
        assert client.post("/watchlist/add", data={"ticker": "NVDA"}).status_code == 400


class TestTheErrorPageCanBeActedOn:
    """Even with the limit gone a token can still fail — signing out in
    another tab rotates the session. The page has to leave somewhere to go."""

    @staticmethod
    def _rendered_csrf_page():
        client = web_app.app.test_client()
        with client.session_transaction() as s:
            s["user_id"] = 1
        r = client.post("/watchlist/add", data={"ticker": "NVDA"})
        assert r.status_code == 400
        return r.get_data(as_text=True)

    def test_it_no_longer_tells_the_reader_to_do_the_impossible(self):
        """Asserted against the rendered page, not the template source — the
        template explains the old wording in a comment."""
        assert "reload the page to renew your form token" not in self._rendered_csrf_page()

    def test_it_says_nothing_was_changed(self):
        assert "Nothing was changed" in self._rendered_csrf_page()

    def test_a_same_origin_referrer_is_offered_as_the_way_back(self):
        client = web_app.app.test_client()
        with client.session_transaction() as s:
            s["user_id"] = 1
        r = client.post("/watchlist/add", data={"ticker": "NVDA"},
                        headers={"Referer": "/setups"})
        assert r.status_code == 400
        assert "/setups" in r.get_data(as_text=True)

    @pytest.mark.parametrize("referrer", [
        "https://evil.example.com/phish",
        "//evil.example.com/phish",
        "http://evil.example.com",
    ])
    def test_an_offsite_referrer_is_never_put_in_the_href(self, referrer):
        """It comes from the request, so it is attacker-influenced."""
        client = web_app.app.test_client()
        with client.session_transaction() as s:
            s["user_id"] = 1
        r = client.post("/watchlist/add", data={"ticker": "NVDA"},
                        headers={"Referer": referrer})
        assert "evil.example.com" not in r.get_data(as_text=True)

    def test_it_does_not_offer_the_request_that_just_failed(self):
        client = web_app.app.test_client()
        with client.session_transaction() as s:
            s["user_id"] = 1
        r = client.post("/watchlist/add", data={"ticker": "NVDA"},
                        headers={"Referer": "/watchlist/add"})
        body = r.get_data(as_text=True)
        assert 'href="/watchlist/add"' not in body


class TestTheFailureStaysIdentifiable:
    """Plain copy for the reader, a marker for everything else."""

    def test_the_page_carries_a_machine_readable_reason(self):
        client = web_app.app.test_client()
        with client.session_transaction() as s:
            s["user_id"] = 1
        body = client.post("/watchlist/add", data={"ticker": "NVDA"}).get_data(as_text=True)
        assert '<meta name="error-reason" content="csrf">' in body

    def test_a_non_csrf_error_page_does_not_claim_to_be_one(self):
        client = web_app.app.test_client()
        with client.session_transaction() as s:
            s["user_id"] = 1
        body = client.get("/no-such-page-anywhere").get_data(as_text=True)
        assert 'content="csrf"' not in body
