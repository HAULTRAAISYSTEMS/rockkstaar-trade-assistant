"""Command Center alert feed + the sell-side insider scoring it depends on."""
from datetime import date

import pytest

import command_center_alerts as cca
import smart_money as sm


def ev(**kw):
    base = dict(activity="SELL", role="Director", owner="X", owner_cik="1", ticker="ZZZ",
                buy_value=0, sell_value=0, holdings_change_pct=None, planned_10b5_1=False,
                exercise_linked_sale=False, buy_shares=0, sell_shares=0)
    base.update(kw)
    return base


def score(event, universe=None):
    return sm._score_event(dict(event), [dict(x) for x in (universe or [event])])


# --- sell-side scoring ------------------------------------------------------
# Before this work every sale scored on holdings-percentage alone, so a $50M CEO
# exit and a $180K director sale both landed on -16 "Neutral".

def test_sale_size_in_dollars_now_matters():
    big = score(ev(role="Chief Executive Officer", sell_value=50_000_000, holdings_change_pct=-12))
    small = score(ev(role="Director", sell_value=180_000, holdings_change_pct=-12))
    assert big["score"] < small["score"], "a $50M sale must outweigh a $180K one"


def test_senior_officer_sale_outweighs_junior():
    ceo = score(ev(role="Chief Executive Officer", sell_value=2_000_000, holdings_change_pct=-12))
    vp = score(ev(role="VP Sales", sell_value=2_000_000, holdings_change_pct=-12))
    assert ceo["score"] < vp["score"]


@pytest.mark.parametrize("role", [
    "Chief Executive Officer", "Chief Financial Officer", "Chief Operating Officer",
    "President", "Chairman of the Board",
])
def test_executive_roles_are_recognised(role):
    assert sm._is_executive(role)


@pytest.mark.parametrize("role", ["10% Owner", "Ten Percent Owner"])
def test_major_holder_recognised(role):
    assert sm._is_major_holder(role)


def test_cluster_selling_is_detected():
    sellers = [ev(owner=f"P{i}", owner_cik=str(i), role="Chief Financial Officer",
                  sell_value=5_000_000, holdings_change_pct=-15) for i in range(4)]
    result = score(sellers[0], sellers)
    assert result["score"] <= -60
    assert "Strong Bearish" == result["label"], "the Strong Bearish label was previously unreachable"


def test_strong_bearish_is_reachable():
    """Worst-case sell used to bottom out near -38 while the label needed -60."""
    worst = score(ev(role="Chief Executive Officer", sell_value=100_000_000, holdings_change_pct=-80))
    assert worst["score"] <= -60


def test_planned_10b5_1_sale_is_still_discounted():
    planned = score(ev(role="Chief Executive Officer", sell_value=50_000_000,
                       holdings_change_pct=-12, planned_10b5_1=True))
    unplanned = score(ev(role="Chief Executive Officer", sell_value=50_000_000, holdings_change_pct=-12))
    assert planned["score"] > unplanned["score"], "pre-planned sales must stay discounted"


def test_small_routine_sale_stays_neutral():
    result = score(ev(role="VP", sell_value=40_000, holdings_change_pct=-1))
    assert result["label"] == "Neutral"


def test_buy_side_scoring_is_unchanged():
    result = score(ev(activity="BUY", role="Chief Executive Officer", buy_value=1_000_000,
                      holdings_change_pct=12))
    assert result["score"] == 75 and result["label"] == "Strong Bullish"


# --- alert rules ------------------------------------------------------------

def test_large_sale_and_cluster_sell_rules_fire():
    rules = sm.resolve_alert_rules({})
    big = ev(sell_value=5_000_000, cluster_sellers=3, holdings_change_pct=-12)
    matches = sm.match_alert_rules(big, rules)
    assert any("sale over" in m for m in matches)
    assert any("3+ insiders sell" in m for m in matches)


def test_new_rules_default_on_but_saved_choices_win():
    resolved = sm.resolve_alert_rules({"cluster_buy_3": False})
    assert resolved["cluster_buy_3"] is False
    assert resolved["large_sale_1m"] is True and resolved["cluster_sell_3"] is True


def test_small_sale_does_not_trigger_large_sale_rule():
    matches = sm.match_alert_rules(ev(sell_value=50_000), sm.resolve_alert_rules({}))
    assert not any("sale over" in m for m in matches)


# --- alert builders ---------------------------------------------------------

def test_insider_alert_carries_ticker_owner_and_date():
    events = [{"ticker": "nvda", "owner": "Jane Doe", "role": "Chief Executive Officer",
               "trade_date": "2026-08-28", "alert_matches": ["Insider open-market sale over $1,000,000"],
               "signal": {"score": -66}}]
    alerts = cca.build_insider_alerts(events)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["ticker"] == "NVDA" and alert["severity"] == "high"
    assert "Jane Doe" in alert["message"] and "2026-08-28" in alert["message"]


def test_events_without_matches_produce_nothing():
    assert cca.build_insider_alerts([{"ticker": "AAPL", "alert_matches": []}]) == []


def test_earnings_alert_window_and_severity():
    rows = [
        {"ticker": "AAPL", "date": "2026-09-01", "days_away": 0},
        {"ticker": "MSFT", "date": "2026-09-05", "days_away": 4},
        {"ticker": "AMZN", "date": "2026-10-01", "days_away": 30},
    ]
    alerts = cca.build_earnings_alerts(rows, within_days=7, today=date(2026, 9, 1))
    tickers = [a["ticker"] for a in alerts]
    assert tickers == ["AAPL", "MSFT"], "reports beyond the window must be excluded"
    assert alerts[0]["severity"] == "high" and "today" in alerts[0]["message"]
    assert alerts[1]["severity"] == "medium"


def test_earnings_days_away_derived_when_absent():
    alerts = cca.build_earnings_alerts([{"ticker": "TSLA", "date": "2026-09-03"}],
                                       within_days=7, today=date(2026, 9, 1))
    assert len(alerts) == 1 and "2 days" in alerts[0]["message"]


def test_past_earnings_are_ignored():
    alerts = cca.build_earnings_alerts([{"ticker": "TSLA", "date": "2026-08-20", "days_away": -5}],
                                       today=date(2026, 9, 1))
    assert alerts == []


# --- persistence ------------------------------------------------------------

def test_sync_skips_alerts_already_stored():
    """Re-running a sync must not duplicate the same filing every page load."""
    alerts = [{"ticker": "NVDA", "alert_type": "insider", "message": "m1", "severity": "high"}]
    existing = [{"ticker": "NVDA", "alert_type": "insider", "message": "m1"}]
    written = []
    added = cca.sync_alerts(alerts, existing, lambda *a: written.append(a))
    assert added == 0 and written == []


def test_sync_writes_new_alerts_once():
    alerts = [
        {"ticker": "NVDA", "alert_type": "insider", "message": "m1", "severity": "high"},
        {"ticker": "NVDA", "alert_type": "insider", "message": "m1", "severity": "high"},
        {"ticker": "AAPL", "alert_type": "earnings", "message": "m2", "severity": "medium"},
    ]
    written = []
    added = cca.sync_alerts(alerts, [], lambda *a: written.append(a))
    assert added == 2, "duplicates inside one batch must collapse"
    assert {w[0] for w in written} == {"NVDA", "AAPL"}


def test_sync_survives_a_failing_write():
    def boom(*_args):
        raise RuntimeError("db down")
    added = cca.sync_alerts(
        [{"ticker": "NVDA", "alert_type": "insider", "message": "m", "severity": "high"}], [], boom)
    assert added == 0, "a storage failure must not raise into the page render"
