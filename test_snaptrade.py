"""Robinhood, read-only, through SnapTrade.

Robinhood publishes no brokerage API — the only official programmatic surface
is crypto. The endpoints the popular libraries call are Robinhood's private
mobile endpoints, reached by handing a third party a username, a password and
a 2FA code. This app takes the aggregator route instead, where the login
happens on Robinhood's own page and no credential passes through here.

The hard part is not the connection, it is the shape of what comes back.
Aggregated data is ragged: a field one brokerage always sends is null at the
next, a ticker arrives wrapped in two layers of object, and no brokerage
reports a start-of-day mark. These tests pin the normalisation, because the
account page renders Schwab and SnapTrade through one template and a shape
mismatch shows up as a wrong number rather than an error.
"""
from unittest.mock import patch

import pytest

import snaptrade as st


# ── Fixtures modelled on SnapTrade's documented payloads ──────────────────────

ACCOUNT = {
    "id": "917c8734-8470-4a3e-a18f-57c3f2ee6631",
    "number": "8MA12345",
    "institution_name": "Robinhood",
    "meta": {"type": "Individual"},
    "balance": {"total": {"amount": 27431.55, "currency": "USD"}},
}

EQUITY = {
    "symbol": {"symbol": {"symbol": "NVDA", "description": "NVIDIA Corp"}},
    "units": 40,
    "price": 230.36,
    "average_purchase_price": 198.10,
    "open_pnl": 1290.40,
}

# A brokerage that reports the ticker flat, with no open_pnl.
FLAT = {
    "symbol": {"raw_symbol": "amd", "description": "Advanced Micro Devices"},
    "units": 10,
    "price": 477.57,
    "average_purchase_price": 400.00,
}

OPTION = {
    "symbol": {"option_symbol": {
        "ticker": "AAPL  260320C00340000",
        "option_type": "CALL",
        "strike_price": 340,
        "expiration_date": "2026-03-20",
        "underlying_symbol": {"symbol": "AAPL"},
    }},
    "units": 2,
    "price": 4.15,
    "average_purchase_price": 3.10,
}

HOLDINGS = {
    "balances": [{"currency": {"code": "USD"}, "cash": 4212.19, "buying_power": 4212.19}],
    "positions": [EQUITY, FLAT],
    "option_positions": [OPTION],
}


@pytest.fixture
def account():
    return st._normalize_account(ACCOUNT, HOLDINGS)


# ── Symbols ───────────────────────────────────────────────────────────────────

class TestTheTickerIsBuriedTwoLevelsDeep:
    """SnapTrade returns {"symbol": {"symbol": {"symbol": "NVDA"}}}."""

    def test_it_walks_down_to_the_string(self):
        assert st._ticker(EQUITY["symbol"]) == ("NVDA", "NVIDIA Corp")

    def test_a_brokerage_that_skips_a_level_still_resolves(self):
        assert st._ticker(FLAT["symbol"])[0] == "AMD"

    def test_a_bare_string_resolves(self):
        assert st._ticker("tsla") == ("TSLA", "")

    @pytest.mark.parametrize("junk", [None, 42, [], {}, {"symbol": None}])
    def test_nothing_usable_gives_an_empty_ticker_not_an_exception(self, junk):
        assert st._ticker(junk)[0] == ""

    def test_a_cycle_does_not_hang(self):
        """A self-referencing object must terminate, not spin."""
        node = {}
        node["symbol"] = node
        assert st._ticker(node)[0] == ""


# ── Positions ─────────────────────────────────────────────────────────────────

class TestEquityPositions:
    def test_the_broker_supplied_pnl_is_used_when_present(self, account):
        nvda = next(p for p in account["positions"] if p["symbol"] == "NVDA")
        assert nvda["unrealized"] == 1290.40

    def test_pnl_is_derived_when_the_broker_omits_it(self, account):
        """(477.57 - 400.00) x 10."""
        amd = next(p for p in account["positions"] if p["symbol"] == "AMD")
        assert amd["unrealized"] == pytest.approx(775.70)

    def test_a_closed_row_is_not_a_holding(self):
        """Zero units is a record of a position, not a position."""
        closed = dict(EQUITY, units=0)
        acct = st._normalize_account(ACCOUNT, {"positions": [closed]})
        assert acct["position_count"] == 0

    def test_day_pnl_is_absent_rather_than_zero(self, account):
        """No aggregated brokerage reports a start-of-day mark.

        A zero here would render as "flat today" on a position that may have
        moved 6%. None renders as an em dash, which is true.
        """
        assert account["daily_pnl"] is None
        assert all(p["day_pnl"] is None for p in account["positions"])


class TestOptionPositions:
    def test_a_contract_is_a_hundred_shares(self, account):
        opt = next(p for p in account["positions"] if p["asset_type"] == "OPTION")
        assert opt["multiplier"] == 100.0
        assert opt["market_value"] == pytest.approx(4.15 * 2 * 100)

    def test_the_underlying_survives_the_occ_symbol(self, account):
        opt = next(p for p in account["positions"] if p["asset_type"] == "OPTION")
        assert opt["underlying"] == "AAPL"
        assert opt["strike_price"] == 340
        assert opt["option_type"] == "CALL"


# ── Account totals ────────────────────────────────────────────────────────────

class TestTheAccount:
    def test_the_brokerages_own_total_wins(self, account):
        """Summing marks drifts from what the brokerage says. Trust the broker."""
        assert account["total_value"] == 27431.55

    def test_a_missing_total_falls_back_to_cash_plus_marks(self):
        acct = st._normalize_account({"id": "x"}, HOLDINGS)
        marks = sum(p["market_value"] for p in acct["positions"])
        assert acct["total_value"] == pytest.approx(round(4212.19 + marks, 2))

    def test_it_matches_the_shape_schwab_returns(self, account):
        """Both brokers render through one template."""
        import schwab
        required = {
            "account_number", "account_hash", "account_type", "total_value",
            "cash_balance", "buying_power", "available_funds", "daily_pnl",
            "daily_pnl_pct", "total_unrealized", "equity_positions",
            "option_positions", "positions", "position_count",
        }
        assert required <= set(account)
        assert required <= set(schwab._normalize_account({"securitiesAccount": {}}))

    def test_the_brokerage_is_named(self, account):
        assert account["institution"] == "Robinhood"

    def test_the_name_is_found_on_the_authorization_when_absent_above(self):
        acct = st._normalize_account(
            {"id": "x", "brokerage_authorization": {"brokerage": {"display_name": "Webull"}}},
            {})
        assert acct["institution"] == "Webull"

    @pytest.mark.parametrize("holdings", [None, {}, {"positions": None}, {"positions": ["junk"]}])
    def test_unusable_holdings_give_an_empty_account_not_an_exception(self, holdings):
        assert st._normalize_account(ACCOUNT, holdings)["position_count"] == 0


# ── Configuration and connection state ────────────────────────────────────────

class TestConfiguration:
    def test_it_is_unconfigured_without_both_keys(self, monkeypatch):
        monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "abc")
        monkeypatch.delenv("SNAPTRADE_CONSUMER_KEY", raising=False)
        assert st.is_configured() is False

    def test_the_snaptrade_user_id_is_stable_for_a_local_user(self):
        """Re-registering under a new id orphans every linked brokerage."""
        assert st.snaptrade_user_id(7) == st.snaptrade_user_id(7)
        assert st.snaptrade_user_id(7) != st.snaptrade_user_id(8)

    def test_an_unconfigured_client_says_so_in_words_a_person_can_act_on(self, monkeypatch):
        monkeypatch.delenv("SNAPTRADE_CLIENT_ID", raising=False)
        monkeypatch.delenv("SNAPTRADE_CONSUMER_KEY", raising=False)
        with pytest.raises(RuntimeError, match="SNAPTRADE_CLIENT_ID"):
            st._client()

    def test_the_status_shape_matches_schwabs(self):
        """One template reads both."""
        import schwab
        shared = {"connected", "status", "css"}
        assert shared <= set(st.token_status(1))
        assert shared <= set(schwab.token_status(1))

    def test_no_keys_and_not_connected_are_different_states(self, monkeypatch):
        """"Set up SnapTrade" and "link an account" are different instructions."""
        monkeypatch.delenv("SNAPTRADE_CLIENT_ID", raising=False)
        monkeypatch.delenv("SNAPTRADE_CONSUMER_KEY", raising=False)
        assert st.token_status(1)["configured"] is False

        monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "id")
        monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "key")
        with patch.object(st, "_stored_secret", return_value=""):
            status = st.token_status(1)
        assert status["configured"] is True and status["connected"] is False


class TestRegistration:
    def test_an_existing_secret_is_reused(self):
        """Re-registering issues a new secret and strands the old links."""
        with patch.object(st, "_stored_secret", return_value="kept"), \
             patch.object(st, "_client") as client:
            assert st.register_user(1) == "kept"
        assert not client.called

    def test_a_registration_that_returns_no_secret_is_an_error(self):
        with patch.object(st, "_stored_secret", return_value=""), \
             patch.object(st, "_client") as client:
            client.return_value.authentication.register_snap_trade_user.return_value = {"userId": "x"}
            with pytest.raises(RuntimeError, match="userSecret"):
                st.register_user(1)


class TestDisconnect:
    def test_the_local_secret_is_cleared_even_if_the_remote_delete_fails(self):
        """Leaving behind a secret the user believes is gone is the worse end."""
        written = {}
        with patch.object(st, "_stored_secret", return_value="s"), \
             patch.object(st, "_client", side_effect=RuntimeError("offline")), \
             patch("database.set_user_setting", lambda uid, k, v: written.__setitem__(k, v)):
            st.clear_tokens(1)
        assert written["snaptrade_user_secret"] == ""


class TestTheSummary:
    def test_a_disconnected_user_gets_the_empty_shape_without_a_call(self):
        with patch.object(st, "is_connected", return_value=False), \
             patch.object(st, "fetch_accounts") as fetch:
            summary = st.get_account_summary(1)
        assert not fetch.called
        assert summary["connected"] is False
        assert summary["accounts"] == []

    def test_a_failure_reports_disconnected_rather_than_raising(self):
        with patch.object(st, "is_connected", return_value=True), \
             patch.object(st, "fetch_accounts", side_effect=RuntimeError("429")):
            summary = st.get_account_summary(1)
        assert summary["connected"] is False
        assert "429" in summary["error"]

    def test_totals_add_up_across_accounts(self, account):
        with patch.object(st, "is_connected", return_value=True), \
             patch.object(st, "fetch_accounts", return_value=[account, account]):
            summary = st.get_account_summary(1)
        assert summary["total_value"] == pytest.approx(account["total_value"] * 2)
        assert summary["open_positions"] == account["position_count"] * 2

    def test_day_pnl_stays_absent_when_no_account_reports_one(self, account):
        with patch.object(st, "is_connected", return_value=True), \
             patch.object(st, "fetch_accounts", return_value=[account]):
            assert st.get_account_summary(1)["daily_pnl"] is None

    def test_one_unreachable_brokerage_does_not_hide_the_others(self):
        """Holdings are fetched per account; one 500 must not empty the page."""
        client = patch.object(st, "_client").start()
        info = client.return_value.account_information
        info.list_user_accounts.return_value = [ACCOUNT, dict(ACCOUNT, id="second")]
        info.get_user_holdings.side_effect = [RuntimeError("upstream 500"), HOLDINGS]
        try:
            with patch.object(st, "_stored_secret", return_value="s"):
                accounts = st.fetch_accounts(1)
        finally:
            patch.stopall()
        assert len(accounts) == 2
        assert accounts[0]["position_count"] == 0
        assert accounts[1]["position_count"] == 3


# ── The routes ────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    import web_app, app as _app
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "test"
        sess["logged_in"] = True
    with patch.object(_app, "_auth_required", lambda *a, **k: False):
        yield c


class TestTheBrokersPage:
    def test_it_renders_with_nothing_configured(self, client):
        """The page has to explain the setup, not 500 on a missing key."""
        resp = client.get("/brokers")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "SNAPTRADE_CLIENT_ID" in body
        assert "dashboard.snaptrade.com" in body

    def test_it_says_robinhood_is_read_only(self, client):
        """Setting the expectation before the connection, not after."""
        assert "read-only" in client.get("/brokers").get_data(as_text=True).lower()

    def test_the_account_page_links_to_it(self, client):
        assert "/brokers" in client.get("/account").get_data(as_text=True)


class TestConnecting:
    def test_it_refuses_politely_when_unconfigured(self, client, monkeypatch):
        monkeypatch.delenv("SNAPTRADE_CLIENT_ID", raising=False)
        resp = client.get("/brokers/snaptrade/connect")
        assert resp.status_code == 302
        assert "/brokers" in resp.headers["Location"]

    def test_it_redirects_to_the_portal_snaptrade_returns(self, client):
        with patch.object(st, "is_configured", return_value=True), \
             patch.object(st, "build_portal_url", return_value="https://app.snaptrade.com/x"):
            resp = client.get("/brokers/snaptrade/connect?broker=ROBINHOOD")
        assert resp.headers["Location"] == "https://app.snaptrade.com/x"

    def test_the_broker_parameter_is_not_passed_through_unchecked(self, client):
        """It goes into a third party's URL; only a plain slug may travel."""
        seen = {}
        def _portal(uid, redirect_to=None, broker=None):
            seen["broker"] = broker
            return "https://app.snaptrade.com/x"
        with patch.object(st, "is_configured", return_value=True), \
             patch.object(st, "build_portal_url", _portal):
            client.get("/brokers/snaptrade/connect?broker=../../evil%20thing")
        assert seen["broker"] is None

    def test_a_portal_failure_does_not_500(self, client):
        with patch.object(st, "is_configured", return_value=True), \
             patch.object(st, "build_portal_url", side_effect=RuntimeError("rate limited")):
            resp = client.get("/brokers/snaptrade/connect")
        assert resp.status_code == 302


class TestTheSummaryEndpoint:
    def test_it_never_returns_the_user_secret(self, client):
        """The secret authenticates every call; it must not reach a browser."""
        with patch.object(st, "token_status", return_value={"connected": True}), \
             patch.object(st, "get_account_summary", return_value={
                 "connected": True, "total_value": 1.0, "buying_power": 1.0,
                 "daily_pnl": None, "total_unrealized": 0.0, "open_positions": 0,
                 "accounts": [], "error": None,
                 "user_secret": "SHOULD-NEVER-APPEAR"}):
            body = client.get("/api/snaptrade/summary").get_data(as_text=True)
        assert "SHOULD-NEVER-APPEAR" not in body
        assert "accounts" not in body

    def test_a_disconnected_user_gets_a_clean_answer(self, client):
        body = client.get("/api/snaptrade/summary").get_json()
        assert body["connected"] is False
