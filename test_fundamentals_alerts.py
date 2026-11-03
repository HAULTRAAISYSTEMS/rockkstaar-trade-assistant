"""The page was right. It just never came to find you.

The fundamentals engine computes every ratio correctly and the peer panel
ranks them. Both are true only while someone is looking. A company whose
return on equity crosses into negative territory, or that slides from level
with its peers to materially behind, does so quietly.

The hardest thing to get right here is not the arithmetic, it is the
refusal. These ratios move slightly every quarter, so "a number changed" as
a trigger would fire constantly and be muted inside a week — taking the real
ones with it. And a number that STOPPED ARRIVING is not a number that moved:
a renamed provider field, an unparsed filing, a failed fetch all look like a
value going absent, and reading that as a collapse to zero would send
"Intel's return on equity just went negative" because of a JSON key. That
notification is worse than none — it is confidently false, fast, about
money. Most of what follows pins that.
"""
import pytest

import fundamentals_alerts as fa


def snap(ticker="INTC", **over):
    base = {"ticker": ticker, "verdict": "Strong Company",
            "roe": 12.0, "roic": 8.0, "net_margin": 10.0,
            "operating_margin": 14.0, "gross_margin": 45.0,
            "revenue_growth": 5.0, "debt_to_equity": 0.4,
            "current_ratio": 1.8, "behind_peers": []}
    base.update(over)
    return base


def keys(alerts):
    return {a["kind"] for a in alerts}


class TestLinesThatMeanSomething:
    def test_return_on_equity_crossing_into_loss(self):
        alerts = fa.detect(snap(roe=4.0), snap(roe=-11.2))
        assert len(alerts) == 1
        assert alerts[0]["title"] == "INTC return on equity turned negative"
        assert "4.0%" in alerts[0]["body"] and "-11.2%" in alerts[0]["body"]

    def test_and_crossing_back_out_of_it(self):
        alerts = fa.detect(snap(roe=-3.0), snap(roe=2.0))
        assert alerts[0]["title"] == "INTC return on equity turned positive"

    def test_debt_passing_equity(self):
        alerts = fa.detect(snap(debt_to_equity=0.84), snap(debt_to_equity=1.15))
        assert alerts[0]["title"] == "INTC now owes more than it owns"
        assert "1.15x" in alerts[0]["body"]

    def test_current_ratio_falling_under_one(self):
        alerts = fa.detect(snap(current_ratio=1.2), snap(current_ratio=0.9))
        assert "short-term bills" in alerts[0]["title"]

    def test_a_swing_to_a_net_loss(self):
        alerts = fa.detect(snap(net_margin=3.0), snap(net_margin=-2.0))
        assert "swung to a net loss" in alerts[0]["title"]

    def test_moving_a_long_way_without_crossing_is_not_a_crossing(self):
        """24 points of return on equity is a big move and still on the same
        side of the line. Firing here is how this becomes noise."""
        assert fa.detect(snap(roe=40.0), snap(roe=16.0)) == []

    def test_landing_exactly_on_the_line_has_not_crossed_it(self):
        """A return on equity of exactly zero is not yet negative."""
        assert fa.detect(snap(roe=5.0), snap(roe=0.0)) == []

    def test_a_tiny_move_across_the_line_still_counts(self):
        """The size of the step is not the point — the side it lands on is."""
        alerts = fa.detect(snap(roe=0.1), snap(roe=-0.1))
        assert len(alerts) == 1


class TestANumberThatVanishedDidNotMove:
    """The whole module's reason for existing, and the one that would do
    real damage if wrong."""

    @pytest.mark.parametrize("gone", [None, "", "n/a", float("nan"), "12%"])
    def test_a_ratio_becoming_unreadable_fires_nothing(self, gone):
        assert fa.detect(snap(roe=12.0), snap(roe=gone)) == []

    def test_a_ratio_appearing_for_the_first_time_fires_nothing(self):
        """Absent to -11% is a number arriving, not a company collapsing."""
        assert fa.detect(snap(roe=None), snap(roe=-11.2)) == []

    def test_both_sides_must_be_real_for_every_crossing_rule(self):
        for rule in fa.CROSSINGS:
            assert fa.detect(snap(**{rule["key"]: 5.0}),
                             snap(**{rule["key"]: None})) == []
            assert fa.detect(snap(**{rule["key"]: None}),
                             snap(**{rule["key"]: 5.0})) == []

    def test_a_verdict_that_disappeared_is_not_a_downgrade(self):
        """An empty verdict is a scoring failure, not a conclusion."""
        assert fa.detect(snap(verdict="Strong Company"), snap(verdict=None)) == []
        assert fa.detect(snap(verdict=None), snap(verdict="Watch")) == []

    def test_one_ratio_vanishing_does_not_block_another_crossing(self):
        alerts = fa.detect(snap(roe=12.0, net_margin=3.0),
                           snap(roe=None, net_margin=-2.0))
        assert len(alerts) == 1
        assert "net loss" in alerts[0]["title"]


class TestAFirstSightingIsNotAChange:
    def test_no_previous_snapshot_is_silent(self):
        """Otherwise every newly tracked company announces its whole balance
        sheet the moment it is added."""
        assert fa.detect({}, snap(roe=-11.2)) == []
        assert fa.detect(None, snap(roe=-11.2)) == []

    def test_an_identical_snapshot_is_silent(self):
        assert fa.detect(snap(), snap()) == []


class TestSlippingBehindThePeerGroup:
    def test_newly_behind_fires(self):
        alerts = fa.detect(snap(behind_peers=[]),
                           snap(behind_peers=["roe", "net_margin"]))
        assert alerts[0]["kind"] == "peer_slippage"
        assert "return on equity" in alerts[0]["body"]

    def test_already_behind_does_not_re_announce(self):
        """A company behind on return on equity for six quarters should not
        say so every run."""
        assert fa.detect(snap(behind_peers=["roe"]),
                         snap(behind_peers=["roe"])) == []

    def test_catching_up_is_not_an_alert_here(self):
        assert fa.detect(snap(behind_peers=["roe"]), snap(behind_peers=[])) == []

    def test_slipping_on_something_new_while_still_behind_on_another(self):
        alerts = fa.detect(snap(behind_peers=["roe"]),
                           snap(behind_peers=["roe", "debt_to_equity"]))
        assert len(alerts) == 1
        assert "debt to equity" in alerts[0]["body"]
        assert "return on equity" not in alerts[0]["body"]

    def test_the_key_is_stable_for_the_same_slide(self):
        a = fa.detect(snap(behind_peers=[]), snap(behind_peers=["roe"]))
        b = fa.detect(snap(behind_peers=[]), snap(behind_peers=["roe"]))
        assert a[0]["key"] == b[0]["key"]


class TestTheScorecardsOwnConclusion:
    def test_a_downgrade_fires(self):
        alerts = fa.detect(snap(verdict="Strong Company"), snap(verdict="Watch"))
        assert alerts[0]["title"] == "INTC: Strong Company → Watch"

    def test_an_upgrade_fires_too(self):
        alerts = fa.detect(snap(verdict="Watch"), snap(verdict="Strong Company"))
        assert alerts[0]["kind"] == "verdict_change"

    def test_the_same_verdict_is_silent(self):
        assert fa.detect(snap(verdict="Watch"), snap(verdict="Watch")) == []


class TestTriggersAreIndependent:
    @pytest.mark.parametrize("trigger, expected", [
        ("threshold_crossing", {"threshold_crossing"}),
        ("peer_slippage", {"peer_slippage"}),
        ("verdict_change", {"verdict_change"}),
    ])
    def test_one_trigger_on_yields_only_that_kind(self, trigger, expected):
        alerts = fa.detect(
            snap(roe=4.0, verdict="Strong Company", behind_peers=[]),
            snap(roe=-11.2, verdict="Watch", behind_peers=["roe"]),
            triggers=[trigger])
        assert keys(alerts) == expected

    def test_all_off_is_silent(self):
        assert fa.detect(snap(roe=4.0), snap(roe=-11.2), triggers=[]) == []

    def test_all_on_catches_everything(self):
        alerts = fa.detect(
            snap(roe=4.0, verdict="Strong Company", behind_peers=[]),
            snap(roe=-11.2, verdict="Watch", behind_peers=["roe"]))
        assert keys(alerts) == set(fa.TRIGGERS)


class TestTheSnapshot:
    def test_it_keeps_absent_ratios_as_none_rather_than_dropping_them(self):
        """So the next run can tell "absent again" from "never tracked"."""
        s = fa.snapshot("INTC", {"roe": None, "verdict": "Watch"})
        assert "roe" in s and s["roe"] is None

    def test_it_reads_the_peer_panel_for_what_is_behind(self):
        comparison = {"rows": [{"key": "roe", "verdict": "behind"},
                               {"key": "roic", "verdict": "in_line"},
                               {"key": "net_margin", "verdict": "no_comparison"}]}
        assert fa.snapshot("INTC", {}, comparison)["behind_peers"] == ["roe"]

    def test_no_comparison_means_no_peer_claim(self):
        assert fa.snapshot("INTC", {}, None)["behind_peers"] == []

    @pytest.mark.parametrize("junk", [None, "nonsense", 0, []])
    def test_junk_data_does_not_raise(self, junk):
        assert fa.snapshot("INTC", junk)["ticker"] == "INTC"

    def test_strings_that_look_like_numbers_are_not_numbers(self):
        """A provider returning "12.4%" must not become 12.4 silently."""
        assert fa.snapshot("INTC", {"roe": "12.4%"})["roe"] is None


class TestThePayload:
    def test_it_links_to_the_company(self):
        alerts = fa.detect(snap(roe=4.0), snap(roe=-11.2))
        assert fa.payload(alerts[0])["url"] == "/fundamentals?ticker=INTC"

    def test_the_tag_is_the_key_so_a_repeat_replaces_rather_than_stacks(self):
        alerts = fa.detect(snap(roe=4.0), snap(roe=-11.2))
        assert fa.payload(alerts[0])["tag"] == alerts[0]["key"]

    def test_a_long_body_is_truncated_not_dropped(self):
        assert len(fa.payload({"key": "x", "title": "t", "body": "y" * 400})["body"]) == 240
