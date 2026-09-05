"""One balance across every connected brokerage.

Schwab answers directly; Robinhood answers through SnapTrade. Both normalise
to the same account shape, so combining is mostly addition — except for the
one field that genuinely does not add up.

Schwab reports a start-of-day value, so it can compute a day change.
SnapTrade reports no such mark for any brokerage behind it. Adding "Schwab's
day change" to "nothing" and printing the result beside a combined balance
would answer a different question than the label asks, so the sum carries a
flag naming who is actually in it.
"""
import pytest

import portfolio


def summary(**kw):
    base = {
        "connected": True, "total_value": 0.0, "buying_power": 0.0,
        "daily_pnl": 0.0, "total_unrealized": 0.0, "open_positions": 0,
        "accounts": [], "error": None,
    }
    base.update(kw)
    return base


SCHWAB = summary(total_value=266.08, buying_power=0.0, daily_pnl=-0.004,
                 total_unrealized=0.0, open_positions=0,
                 accounts=[{"account_hash": "a", "institution": "Charles Schwab"}])

# Robinhood through the aggregator: a real balance, and no day change at all.
ROBINHOOD = summary(total_value=205.90, buying_power=0.0, daily_pnl=None,
                    total_unrealized=12.50, open_positions=2,
                    accounts=[{"account_hash": "b", "institution": "Robinhood"}])

BOTH = [("schwab", "Charles Schwab", SCHWAB),
        ("snaptrade", "Robinhood & others", ROBINHOOD)]


class TestTheMoneyAddsUp:
    def test_the_totals_are_summed(self):
        assert portfolio.combine(BOTH)["total_value"] == pytest.approx(471.98)

    def test_open_pnl_and_position_counts_are_summed(self):
        combined = portfolio.combine(BOTH)
        assert combined["total_unrealized"] == 12.50
        assert combined["open_positions"] == 2

    def test_a_disconnected_broker_contributes_nothing(self):
        off = summary(connected=False, total_value=None)
        combined = portfolio.combine([("schwab", "Charles Schwab", SCHWAB),
                                      ("snaptrade", "Robinhood & others", off)])
        assert combined["total_value"] == 266.08
        assert combined["broker_count"] == 1

    def test_nothing_connected_reports_absent_rather_than_zero(self):
        """A zero balance and an unknown balance are different facts."""
        off = summary(connected=False)
        combined = portfolio.combine([("schwab", "s", off), ("snaptrade", "r", off)])
        assert combined["connected"] is False
        assert combined["total_value"] is None
        assert combined["buying_power"] is None


class TestTheFieldThatDoesNotAddUp:
    def test_a_broker_with_no_day_change_is_left_out_not_counted_as_zero(self):
        combined = portfolio.combine(BOTH)
        assert combined["daily_pnl"] == pytest.approx(0.0)
        assert combined["daily_pnl_partial"] is True

    def test_the_figure_names_who_is_in_it(self):
        combined = portfolio.combine(BOTH)
        assert combined["daily_pnl_label"] == "Charles Schwab"
        assert combined["daily_pnl_missing"] == ["Robinhood & others"]

    def test_nothing_is_flagged_when_every_broker_reports_one(self):
        rh = dict(ROBINHOOD, daily_pnl=4.25)
        combined = portfolio.combine([("schwab", "Charles Schwab", SCHWAB),
                                      ("snaptrade", "Robinhood & others", rh)])
        assert combined["daily_pnl_partial"] is False
        assert combined["daily_pnl_label"] is None
        assert combined["daily_pnl"] == pytest.approx(4.25)

    def test_no_broker_reporting_one_gives_no_figure(self):
        rh = dict(ROBINHOOD)
        sch = dict(SCHWAB, daily_pnl=None)
        combined = portfolio.combine([("schwab", "s", sch), ("snaptrade", "r", rh)])
        assert combined["daily_pnl"] is None
        assert combined["daily_pnl_partial"] is False   # nothing to be partial about

    def test_a_flat_day_is_not_negative_zero(self):
        """Rounding a tiny loss leaves -0.0, which prints as "-0.00"."""
        combined = portfolio.combine(BOTH)
        assert "{:+,.2f}".format(combined["daily_pnl"]) == "+0.00"


class TestPositionsKnowWhereTheyLive:
    def test_each_account_carries_its_broker(self):
        combined = portfolio.combine(BOTH)
        assert [a["broker"] for a in combined["accounts"]] == ["schwab", "snaptrade"]

    def test_an_existing_broker_tag_is_not_overwritten(self):
        tagged = summary(accounts=[{"account_hash": "z", "broker": "already"}])
        combined = portfolio.combine([("schwab", "Charles Schwab", tagged)])
        assert combined["accounts"][0]["broker"] == "already"

    def test_the_per_broker_split_survives_for_display(self):
        sources = portfolio.combine(BOTH)["sources"]
        assert [s["total_value"] for s in sources] == [266.08, 205.90]


class TestOneBrokerFailingDoesNotHideTheOther:
    def test_a_broker_that_raised_is_reported_but_not_fatal(self, monkeypatch):
        def fake(broker, user_id):
            if broker == "schwab":
                raise RuntimeError("schwab 503")
            return ROBINHOOD
        monkeypatch.setattr(portfolio, "_summary_for", fake)
        combined = portfolio.get_account_summary(1)
        assert combined["total_value"] == 205.90
        assert combined["sources"][0]["error"] == "schwab 503"

    @pytest.mark.parametrize("junk", [None, "nope", 42, []])
    def test_a_summary_that_is_not_a_dict_is_survivable(self, junk):
        combined = portfolio.combine([("schwab", "s", junk),
                                      ("snaptrade", "r", ROBINHOOD)])
        assert combined["total_value"] == 205.90


class TestItLooksLikeASingleBroker:
    def test_the_shape_matches_what_one_broker_returns(self):
        """The account page and the Terminal read either without branching."""
        import schwab, snaptrade
        required = {"connected", "total_value", "buying_power", "daily_pnl",
                    "total_unrealized", "open_positions", "accounts", "error"}
        assert required <= set(portfolio.combine(BOTH))
        assert required <= set(schwab.get_account_summary(1))
        assert required <= set(snaptrade.get_account_summary(1))


class TestTheRiskEngineIsNotWiredToThis:
    def test_position_sizing_still_reads_the_manual_account_size(self):
        """Buying power sums for display only.

        Cash at one broker cannot settle an order at the other, so sizing a
        Schwab trade against a combined balance would produce orders that
        cannot fill. The risk settings stay the source of truth.
        """
        from pathlib import Path
        src = Path("app.py").read_text()
        risk_block = src[src.index('"account_size":        _sf('):][:400]
        assert "portfolio" not in risk_block
        assert "_get_portfolio_data" not in risk_block


# ── The pages that show the money ─────────────────────────────────────────────

@pytest.fixture
def client():
    from unittest.mock import patch
    import web_app, app as _app
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "test"
        sess["logged_in"] = True
    with patch.object(_app, "_auth_required", lambda *a, **k: False):
        yield c


@pytest.fixture
def two_brokers():
    """Both brokers connected, with the real shape of the live accounts."""
    from unittest.mock import patch
    import app as _app
    with patch.object(_app, "_get_schwab_data", return_value=SCHWAB), \
         patch.object(_app, "_get_snaptrade_data", return_value=ROBINHOOD):
        yield


class TestThePagesShowOneNumber:
    def test_the_brokers_page_leads_with_the_combined_total(self, client, two_brokers):
        body = client.get("/brokers").get_data(as_text=True)
        assert "$471.98" in body
        assert "Total across 2 brokerages" in body

    def test_the_combined_card_is_hidden_with_only_one_broker(self, client):
        """With one broker it is the card below it, repeated."""
        from unittest.mock import patch
        import app as _app
        off = summary(connected=False)
        with patch.object(_app, "_get_schwab_data", return_value=SCHWAB), \
             patch.object(_app, "_get_snaptrade_data", return_value=off):
            body = client.get("/brokers").get_data(as_text=True)
        assert "Total across" not in body

    def test_the_account_page_shows_the_combined_value(self, client, two_brokers):
        assert "471.98" in client.get("/account").get_data(as_text=True)

    def test_the_terminal_chip_shows_the_combined_value(self, client, two_brokers):
        assert "$472" in client.get("/terminal").get_data(as_text=True)

    def test_the_api_reports_the_split_alongside_the_total(self, client, two_brokers):
        body = client.get("/api/portfolio/summary").get_json()
        assert body["total_value"] == pytest.approx(471.98)
        assert [s["total_value"] for s in body["sources"]] == [266.08, 205.90]

    def test_the_api_never_leaks_account_numbers(self, client, two_brokers):
        body = client.get("/api/portfolio/summary").get_data(as_text=True)
        assert "accounts" not in body
        assert "account_hash" not in body


class TestThePagesSayWhoseDayChangeItIs:
    def test_the_brokers_page_names_the_gap(self, client, two_brokers):
        body = client.get("/brokers").get_data(as_text=True)
        assert "Charles Schwab only" in body
        assert "no start-of-day value" in body

    def test_the_terminal_marks_a_partial_figure(self, client, two_brokers):
        body = client.get("/terminal").get_data(as_text=True)
        assert "Charles Schwab only" in body

    def test_no_marker_when_every_broker_reports_one(self, client):
        from unittest.mock import patch
        import app as _app
        rh = dict(ROBINHOOD, daily_pnl=4.25)
        with patch.object(_app, "_get_schwab_data", return_value=SCHWAB), \
             patch.object(_app, "_get_snaptrade_data", return_value=rh):
            body = client.get("/brokers").get_data(as_text=True)
        assert "only" not in body.split("Day P&amp;L")[1][:400]
