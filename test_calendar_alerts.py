"""The calendar knew what was coming; it only ever said so on screen.

Which is the one place you do not need telling. These cover the two things
that decide whether a notification system is worth keeping switched on:
it fires at the right moment, and it never fires twice for the same thing.

The schedule is deliberately not the guarantee. Every alert carries a key
that is a pure function of what it is about — "earn:AMD:2026-11-03:morning_of"
— and the delivery table holds UNIQUE(subscription_id, alert_key). So the
runner's cadence does not matter: fire it every five minutes inside the
window and the alert still goes out once. That is what lets the windows be
hours wide instead of a cron line that misses if a deploy lands on it.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import calendar_alerts as ca


ET = ZoneInfo("America/New_York")
TODAY = datetime(2026, 11, 3, tzinfo=ET).date()
TOMORROW = TODAY + timedelta(days=1)


def at(hour, minute=0, day=None):
    d = day or TODAY
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=ET)


def cal(**buckets):
    base = {"today": [], "tomorrow": [], "this_week": [], "coming_up": []}
    base.update(buckets)
    return base


def earning(ticker, day, time_label="AMC", eps_est=1.68, eps_actual=None):
    return {"ticker": ticker, "date": day.isoformat(), "time_label": time_label,
            "eps_est": eps_est, "eps_actual": eps_actual}


def macro(title, day, impact="HIGH"):
    return {"title": title, "date": day.isoformat(), "impact": impact}


def build(**over):
    kwargs = dict(
        calendar=cal(), macro_events=[], watchlist={"AMD"},
        triggers=list(ca.TRIGGERS), timings=list(ca.TIMINGS),
        now_local=at(8), prior_state={},
    )
    kwargs.update(over)
    return ca.build_alerts(**kwargs)


class TestItFiresAtTheRightHour:
    def test_the_evening_digest_covers_tomorrow(self):
        alerts = build(calendar=cal(tomorrow=[earning("AMD", TOMORROW)]),
                       now_local=at(19))
        assert [a["key"] for a in alerts] == [f"digest:evening_before:{TOMORROW}"]
        assert "Tomorrow" in alerts[0]["payload"]["title"]

    def test_the_morning_digest_covers_today(self):
        alerts = build(calendar=cal(today=[earning("AMD", TODAY)]), now_local=at(8))
        assert [a["key"] for a in alerts] == [f"digest:morning_of:{TODAY}"]
        assert "Today" in alerts[0]["payload"]["title"]

    @pytest.mark.parametrize("hour", [0, 5, 11, 14, 17, 23])
    def test_nothing_scheduled_fires_outside_both_windows(self, hour):
        alerts = build(calendar=cal(today=[earning("AMD", TODAY)],
                                    tomorrow=[earning("NVDA", TOMORROW)]),
                       now_local=at(hour))
        assert [a for a in alerts if a["key"].startswith("digest:")] == []

    def test_the_morning_digest_does_not_announce_tomorrow(self):
        """Being woken about something 24 hours out is how this gets muted."""
        alerts = build(calendar=cal(tomorrow=[earning("AMD", TOMORROW)]),
                       now_local=at(8))
        assert alerts == []

    @pytest.mark.parametrize("hour", [18, 19, 21])
    def test_the_window_is_wide_enough_to_survive_a_late_run(self, hour):
        alerts = build(calendar=cal(tomorrow=[earning("AMD", TOMORROW)]),
                       now_local=at(hour))
        assert len(alerts) == 1


class TestOneNotificationPerDayNotPerCompany:
    def test_four_companies_reporting_is_one_alert(self):
        rows = [earning(t, TOMORROW) for t in ("AMD", "NVDA", "SHOP", "ANET")]
        alerts = build(calendar=cal(tomorrow=rows), now_local=at(19),
                       watchlist={"AMD", "NVDA", "SHOP", "ANET"})
        assert len(alerts) == 1
        assert "4 on your watchlist" in alerts[0]["payload"]["title"]

    def test_the_key_depends_only_on_the_day_and_timing(self):
        """So a second pass over a changed calendar still dedupes."""
        a = build(calendar=cal(tomorrow=[earning("AMD", TOMORROW)]), now_local=at(19))
        b = build(calendar=cal(tomorrow=[earning("AMD", TOMORROW),
                                         earning("NVDA", TOMORROW)]),
                  now_local=at(19), watchlist={"AMD", "NVDA"})
        assert a[0]["key"] == b[0]["key"]

    def test_watchlist_names_lead_the_body(self):
        alerts = build(
            calendar=cal(tomorrow=[earning("ZZZ", TOMORROW), earning("AMD", TOMORROW)]),
            now_local=at(19), watchlist={"AMD"})
        body = alerts[0]["payload"]["body"]
        assert body.index("AMD") < body.index("ZZZ")

    def test_the_session_is_spelled_out(self):
        alerts = build(calendar=cal(tomorrow=[earning("AMD", TOMORROW, "BMO")]),
                       now_local=at(19))
        assert "before the open" in alerts[0]["payload"]["body"]

    def test_an_unannounced_session_says_so(self):
        alerts = build(calendar=cal(tomorrow=[earning("AMD", TOMORROW, "TBD")]),
                       now_local=at(19))
        assert "time not announced" in alerts[0]["payload"]["body"]


class TestTheTriggersAreIndependent:
    def test_watchlist_only_ignores_the_rest_of_the_market(self):
        alerts = build(
            calendar=cal(tomorrow=[earning("AMD", TOMORROW), earning("ZZZ", TOMORROW)]),
            now_local=at(19), watchlist={"AMD"}, triggers=["watchlist_earnings"])
        assert "ZZZ" not in alerts[0]["payload"]["body"]

    def test_large_cap_includes_names_you_do_not_track(self):
        alerts = build(calendar=cal(tomorrow=[earning("ZZZ", TOMORROW)]),
                       now_local=at(19), watchlist=set(),
                       triggers=["large_cap_earnings"])
        assert alerts and "ZZZ" in alerts[0]["payload"]["body"]

    def test_macro_alone_still_sends(self):
        alerts = build(calendar=cal(), macro_events=[macro("CPI", TOMORROW)],
                       now_local=at(19), triggers=["macro_high_impact"])
        assert alerts and "CPI" in alerts[0]["payload"]["body"]

    def test_low_impact_macro_is_not_high_impact(self):
        alerts = build(calendar=cal(),
                       macro_events=[macro("Truck tonnage", TOMORROW, impact="LOW")],
                       now_local=at(19), triggers=["macro_high_impact"])
        assert alerts == []

    def test_every_trigger_off_sends_nothing(self):
        alerts = build(calendar=cal(tomorrow=[earning("AMD", TOMORROW)]),
                       macro_events=[macro("CPI", TOMORROW)],
                       now_local=at(19), triggers=[])
        assert alerts == []


class TestAConfirmedDateIsAChangeNotAState:
    """An earnings date arrives as TBD and later becomes BMO or AMC. Without a
    record of what the feed said last time there is no change to notice — only
    a current value that looks identical on every pass."""

    PRIOR_TBD = {ca.event_key("earn", "AMD", TOMORROW): {"time_label": "TBD",
                                                         "eps_actual": None}}

    def test_tbd_becoming_a_session_fires(self):
        alerts = build(calendar=cal(tomorrow=[earning("AMD", TOMORROW, "AMC")]),
                       now_local=at(14), prior_state=self.PRIOR_TBD)
        assert len(alerts) == 1
        assert "confirmed its report time" in alerts[0]["payload"]["title"]
        assert "after the close" in alerts[0]["payload"]["body"]

    def test_it_is_not_gated_on_the_digest_windows(self):
        """The point of this one is that it lands when the company announces,
        not at a scheduled hour."""
        for hour in (2, 11, 15, 23):
            alerts = build(calendar=cal(tomorrow=[earning("AMD", TOMORROW, "AMC")]),
                           now_local=at(hour), prior_state=self.PRIOR_TBD)
            assert len(alerts) == 1, hour

    def test_a_first_sighting_is_not_a_confirmation(self):
        """Otherwise every company on a freshly loaded calendar would fire."""
        alerts = build(calendar=cal(tomorrow=[earning("AMD", TOMORROW, "AMC")]),
                       now_local=at(14), prior_state={})
        assert alerts == []

    def test_a_session_that_was_already_known_does_not_refire(self):
        prior = {ca.event_key("earn", "AMD", TOMORROW): {"time_label": "AMC",
                                                          "eps_actual": None}}
        alerts = build(calendar=cal(tomorrow=[earning("AMD", TOMORROW, "AMC")]),
                       now_local=at(14), prior_state=prior)
        assert alerts == []

    def test_still_tbd_is_not_a_confirmation(self):
        alerts = build(calendar=cal(tomorrow=[earning("AMD", TOMORROW, "TBD")]),
                       now_local=at(14), prior_state=self.PRIOR_TBD)
        assert alerts == []


class TestAResultLanding:
    PRIOR_PENDING = {ca.event_key("earn", "AMD", TODAY): {"time_label": "AMC",
                                                           "eps_actual": None}}

    def test_a_beat_says_so_with_both_numbers(self):
        alerts = build(
            calendar=cal(today=[earning("AMD", TODAY, eps_est=1.68, eps_actual=1.72)]),
            now_local=at(17), prior_state=self.PRIOR_PENDING)
        assert len(alerts) == 1
        assert alerts[0]["payload"]["title"] == "AMD reported — beat"
        assert "$1.72" in alerts[0]["payload"]["body"]
        assert "$1.68" in alerts[0]["payload"]["body"]

    @pytest.mark.parametrize("actual, verdict", [
        (1.50, "— missed"), (1.68, "— in line"), (2.00, "— beat")])
    def test_the_verdict_follows_the_number(self, actual, verdict):
        alerts = build(
            calendar=cal(today=[earning("AMD", TODAY, eps_est=1.68, eps_actual=actual)]),
            now_local=at(17), prior_state=self.PRIOR_PENDING)
        assert verdict in alerts[0]["payload"]["title"]

    def test_a_result_already_seen_does_not_refire(self):
        prior = {ca.event_key("earn", "AMD", TODAY): {"time_label": "AMC",
                                                       "eps_actual": 1.72}}
        alerts = build(
            calendar=cal(today=[earning("AMD", TODAY, eps_actual=1.72)]),
            now_local=at(17), prior_state=prior)
        assert alerts == []

    def test_a_pending_report_is_not_a_result(self):
        alerts = build(calendar=cal(today=[earning("AMD", TODAY, eps_actual=None)]),
                       now_local=at(17), prior_state=self.PRIOR_PENDING)
        assert alerts == []

    def test_no_consensus_still_reports_the_actual(self):
        """A missing estimate is a reason to omit the comparison, not the
        news that the company reported."""
        alerts = build(
            calendar=cal(today=[earning("AMD", TODAY, eps_est=None, eps_actual=1.72)]),
            now_local=at(17), prior_state=self.PRIOR_PENDING)
        assert alerts[0]["payload"]["title"] == "AMD reported"
        assert "$1.72" in alerts[0]["payload"]["body"]


class TestAnEmptyCalendarIsNotGoodNews:
    def test_nothing_scheduled_sends_nothing(self):
        """A feed that is down produces an empty calendar. "Nothing on
        tomorrow" would be a reassurance the app has no basis for — this app
        has spent a week learning that difference."""
        assert build(calendar=cal(), now_local=at(19)) == []
        assert build(calendar=cal(), now_local=at(8)) == []

    @pytest.mark.parametrize("junk", [None, {}, {"today": None}])
    def test_a_broken_calendar_does_not_raise(self, junk):
        assert build(calendar=junk, now_local=at(19)) == []

    def test_rows_without_a_usable_date_are_skipped(self):
        alerts = build(calendar=cal(tomorrow=[{"ticker": "AMD", "date": "soon"}]),
                       now_local=at(19))
        assert alerts == []


class TestThePreferenceEncoding:
    def test_never_chosen_takes_the_default(self):
        assert ca.parse_prefs(None, ca.DEFAULT_TRIGGERS) == list(ca.DEFAULT_TRIGGERS)

    def test_chose_nothing_is_honoured(self):
        """Collapsing this into the default would make "all off" impossible."""
        assert ca.parse_prefs("", ca.DEFAULT_TRIGGERS) == []

    def test_a_round_trip_keeps_the_choice(self):
        chosen = ["watchlist_earnings", "macro_high_impact"]
        assert ca.parse_prefs(ca.serialise_prefs(chosen, ca.TRIGGERS),
                              ca.DEFAULT_TRIGGERS) == chosen

    def test_unknown_values_are_dropped_on_the_way_in(self):
        assert "drop_tables" not in ca.serialise_prefs(
            ["watchlist_earnings", "drop_tables"], ca.TRIGGERS)

    def test_duplicates_collapse(self):
        assert ca.serialise_prefs(
            ["watchlist_earnings"] * 3, ca.TRIGGERS) == "watchlist_earnings"

    def test_the_market_wide_slate_is_off_by_default(self):
        """Dozens of companies a day in season. A stream nobody reads is
        worse than no stream."""
        assert "large_cap_earnings" not in ca.DEFAULT_TRIGGERS
        assert "large_cap_earnings" in ca.TRIGGERS


class TestTheKeysAreStable:
    def test_the_same_event_always_keys_the_same(self):
        assert (ca.alert_key("earn", "amd", TOMORROW, "morning_of")
                == ca.alert_key("earn", "AMD", TOMORROW.isoformat(), "morning_of"))

    def test_different_days_are_different_alerts(self):
        assert (ca.alert_key("earn", "AMD", TODAY, "morning_of")
                != ca.alert_key("earn", "AMD", TOMORROW, "morning_of"))

    def test_different_timings_are_different_alerts(self):
        assert (ca.alert_key("earn", "AMD", TODAY, "morning_of")
                != ca.alert_key("earn", "AMD", TODAY, "evening_before"))
