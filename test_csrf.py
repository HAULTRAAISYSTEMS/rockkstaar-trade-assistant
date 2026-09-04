"""Every state-changing endpoint is CSRF-protected.

Seventeen endpoints carried @csrf.exempt. Eleven of them changed state:
adding a ticker to a watchlist, clearing scanner alerts, disconnecting Schwab,
importing broker trades into the journal, recording trade outcomes, writing and
deleting study-log entries, and two endpoints that spend money on model calls.
Any page on the internet could fire those at a logged-in user's browser and the
session cookie would ride along.

They were exempt because the calls are scattered through page templates as bare
fetch() with no token, so turning protection on broke the UI. The token is now
attached by a shim over fetch and XMLHttpRequest, loaded blocking in <head>
before any page script, and the exemptions are gone.
"""
import os
import re

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")

APP_SRC = open("app.py").read()
BASE = open("templates/base.html").read()
SHIM = open("static/js/csrf.js").read()


def test_no_endpoint_is_exempt():
    assert "csrf.exempt" not in APP_SRC


def test_protection_is_still_switched_on():
    """Removing the exemptions is worthless if nothing is checking."""
    assert "CSRFProtect(app)" in APP_SRC


class TestTheShimIsReachable:
    def test_base_loads_it(self):
        assert "js/csrf.js" in BASE

    def test_it_loads_before_any_page_script(self):
        """A call site that runs first would go out unsigned."""
        shim_at = BASE.index("js/csrf.js")
        for match in re.finditer(r"<script\b", BASE):
            if match.start() > shim_at:
                continue
            snippet = BASE[match.start():match.start() + 200]
            assert "csrf.js" in snippet, f"script runs before the shim: {snippet[:80]}"

    def test_it_is_not_deferred_or_async(self):
        tag = BASE[BASE.index("js/csrf.js") - 120:BASE.index("js/csrf.js") + 60]
        assert "defer" not in tag and "async" not in tag

    def test_the_page_still_publishes_the_token(self):
        assert 'name="csrf-token"' in BASE


class TestTheShimItself:
    def test_safe_methods_are_left_alone(self):
        assert "GET: 1" in SHIM and "HEAD: 1" in SHIM

    def test_the_token_never_leaves_the_origin(self):
        assert "sameOrigin" in SHIM
        assert "window.location.origin" in SHIM

    def test_xhr_is_covered_too(self):
        assert "XMLHttpRequest.prototype.send" in SHIM

    def test_an_absent_token_does_not_throw(self):
        """A page rendered without the meta tag must not break every fetch."""
        assert "if (!token) { return; }" in SHIM


@pytest.fixture(scope="module")
def client():
    import web_app
    return web_app.app.test_client()


@pytest.fixture(scope="module")
def signed(client):
    """A client whose session carries a token, and the token to send."""
    import web_app
    from flask import session as flask_session
    from flask_wtf.csrf import generate_csrf
    with web_app.app.test_request_context():
        header_token = generate_csrf()
        session_token = flask_session.get("csrf_token")
    with client.session_transaction() as sess:
        sess["csrf_token"] = session_token
    return client, header_token


# One per protected verb-and-shape, not all eleven: the decorator is gone
# globally, so these prove the mechanism rather than enumerate the routes.
STATE_CHANGING = [
    ("POST", "/api/scanner/add"),
    ("POST", "/api/scanner/alerts/seen"),
    ("POST", "/api/scanner/alerts/clear"),
    ("POST", "/schwab/disconnect"),
    ("POST", "/schwab/sync-import"),
    ("POST", "/api/institutional/record-outcome"),
    ("POST", "/api/opportunity/refresh"),
    ("POST", "/api/research/ask"),
    ("POST", "/api/ask"),
    ("POST", "/api/study-log"),
    ("DELETE", "/api/study-log/1"),
]


def _is_csrf_rejection(response) -> bool:
    body = response.get_data(as_text=True).lower()
    return response.status_code == 400 and "csrf" in body


@pytest.mark.parametrize("method,path", STATE_CHANGING)
def test_an_unsigned_request_is_rejected(client, method, path):
    response = client.open(path, method=method, json={})
    assert _is_csrf_rejection(response), response.get_data(as_text=True)[:200]


@pytest.mark.parametrize("method,path", STATE_CHANGING)
def test_a_signed_request_gets_past_the_csrf_layer(signed, method, path):
    """Past CSRF, not necessarily to a 200.

    These all need a login, and some validate their body before anything
    else, so the handler's own 400 is a pass - it means the request reached
    the handler. Only a CSRF rejection is a failure here.
    """
    client, token = signed
    response = client.open(path, method=method, json={},
                           headers={"X-CSRFToken": token})
    assert not _is_csrf_rejection(response), response.get_data(as_text=True)[:200]
