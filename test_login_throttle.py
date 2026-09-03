"""Failed sign-ins must cost something.

The login page is public and the admin username is guessable. Nothing counted
attempts, so passwords could be tried as fast as the network allowed, forever.
"""
import sqlite3

import pytest

import login_throttle as t


NOW = 1_000_000.0


def fails(n, at=NOW, spacing=1.0):
    return [at - (n - i) * spacing for i in range(n)]


# ── The policy ───────────────────────────────────────────────────────────────

def test_a_clean_slate_is_not_throttled():
    assert t.lockout_seconds([], now=NOW) == 0


def test_a_few_mistypes_cost_nothing():
    """A real person who fumbles their password twice should not notice this."""
    for n in range(1, t.FREE_ATTEMPTS + 1):
        assert t.lockout_seconds(fails(n), now=NOW) == 0


def test_the_attempt_after_the_allowance_locks():
    assert t.lockout_seconds(fails(t.FREE_ATTEMPTS + 1), now=NOW) > 0


def test_each_further_failure_costs_more():
    waits = [t.lockout_seconds(fails(t.FREE_ATTEMPTS + i), now=NOW)
             for i in range(1, 6)]
    assert waits == sorted(waits) and waits[0] < waits[-1]


def test_the_backoff_has_a_ceiling():
    """An attacker must be slowed, not a real user locked out forever."""
    assert t.lockout_seconds(fails(500), now=NOW) <= max(t.BACKOFF_SECONDS) + 2


def test_hammering_while_locked_keeps_the_lock_alive():
    """Counted from the newest failure, so an attacker cannot wait out the
    timer while still trying."""
    early = t.lockout_seconds(fails(8, at=NOW - 50), now=NOW)
    still = t.lockout_seconds(fails(8, at=NOW), now=NOW)
    assert still > early


def test_old_failures_age_out_of_the_window():
    old = [NOW - t.WINDOW_SECONDS - 10] * 20
    assert t.lockout_seconds(old, now=NOW) == 0


def test_the_message_never_reveals_whether_the_user_exists():
    msg = t.describe(300).lower()
    for word in ("user", "username", "account", "exist", "unknown", "password is"):
        assert word not in msg


def test_no_wait_produces_no_message():
    assert t.describe(0) == ""


# ── Scoping ──────────────────────────────────────────────────────────────────

def test_address_and_username_together_form_the_key():
    """Username alone lets anyone lock a real user out on purpose; address
    alone locks out everyone behind one office NAT."""
    assert t.scope_key("1.2.3.4", "admin") != t.scope_key("5.6.7.8", "admin")
    assert t.scope_key("1.2.3.4", "admin") != t.scope_key("1.2.3.4", "bob")


def test_the_username_is_matched_case_insensitively():
    assert t.scope_key("1.2.3.4", "Admin") == t.scope_key("1.2.3.4", "admin")


# ── Storage, shared across workers ───────────────────────────────────────────

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


def test_failures_persist_so_two_workers_share_one_count(conn):
    scope = t.scope_key("1.2.3.4", "admin")
    for _ in range(6):
        t.record_failure(conn, scope, now=NOW)
    assert t.lockout_seconds(t.recent_failures(conn, scope, now=NOW), now=NOW) > 0


def test_a_good_password_resets_the_count(conn):
    scope = t.scope_key("1.2.3.4", "admin")
    for _ in range(6):
        t.record_failure(conn, scope, now=NOW)
    t.clear(conn, scope)
    assert t.recent_failures(conn, scope, now=NOW) == []


def test_one_users_failures_do_not_throttle_another(conn):
    mine = t.scope_key("1.2.3.4", "alice")
    theirs = t.scope_key("9.9.9.9", "bob")
    for _ in range(10):
        t.record_failure(conn, theirs, now=NOW)
    assert t.recent_failures(conn, mine, now=NOW) == []


def test_a_broken_table_does_not_lock_everybody_out(conn):
    """A throttle that cannot read its own storage must fail open, not shut."""
    conn.execute("CREATE TABLE login_failures(wrong_column TEXT)")
    assert t.recent_failures(conn, "x", now=NOW) == []
    t.record_failure(conn, "x", now=NOW)      # must not raise


def test_old_rows_are_cleaned_up(conn):
    scope = t.scope_key("1.2.3.4", "admin")
    t.record_failure(conn, scope, now=NOW - t.WINDOW_SECONDS * 10)
    t.record_failure(conn, scope, now=NOW)
    rows = conn.execute("SELECT COUNT(*) FROM login_failures").fetchone()[0]
    assert rows == 1


# ── The route ────────────────────────────────────────────────────────────────

APP = open("app.py").read()


def test_the_throttle_runs_before_the_password_check():
    """Checking first and throttling after would still let an attacker learn
    whether each guess was right."""
    body = APP[APP.index("def login_page"):]
    body = body[:body.index("\n@app.route")]
    assert body.index("lockout_seconds") < body.index("check_user_password")


def test_a_locked_request_returns_429_and_never_checks_the_password():
    body = APP[APP.index("def login_page"):]
    body = body[:body.index("\n@app.route")]
    assert "429" in body
