"""The morning briefing has to admit it is from the morning.

One model call per ET day is a reasonable way to spend the budget. Rendering
the result with only a date was not: a read written at 4am sat under a live
market story all day, one panel saying RISK ON above the fold and the other
RISK OFF below it, with different VIX values and opposite claims about the
20 EMA — and nothing on the page saying which was fresher.

Nothing here regenerates the briefing. It reports when it was written, how old
that makes it, and where the live regime has since moved away from what it
concluded.
"""
import os
from datetime import timedelta

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import app as legacy


def written(hours_ago: float) -> str:
    return (legacy._et_now() - timedelta(hours=hours_ago)).isoformat()


class TestItSaysWhenItWasWritten:
    def test_a_fresh_briefing_reports_its_age_in_minutes(self):
        aged = legacy._age_briefing({"generated_at": written(0.5)})
        assert 25 <= aged["age_minutes"] <= 35

    def test_an_old_briefing_reports_hours(self):
        aged = legacy._age_briefing({"generated_at": written(16)})
        assert 950 <= aged["age_minutes"] <= 970

    def test_it_gives_a_readable_clock_time(self):
        aged = legacy._age_briefing({"generated_at": written(3)})
        assert "M ET" in aged["written_label"]

    def test_a_briefing_written_this_hour_is_not_stale(self):
        assert legacy._age_briefing({"generated_at": written(0.5)})["stale"] is False

    def test_a_briefing_from_before_the_open_is_stale_by_the_afternoon(self):
        assert legacy._age_briefing({"generated_at": written(16)})["stale"] is True

    def test_the_boundary_is_where_it_is_declared(self):
        just_under = legacy._age_briefing({
            "generated_at": written((legacy.BRIEFING_STALE_MINUTES - 10) / 60)})
        just_over = legacy._age_briefing({
            "generated_at": written((legacy.BRIEFING_STALE_MINUTES + 10) / 60)})
        assert just_under["stale"] is False and just_over["stale"] is True


class TestOlderRowsStillWork:
    """Briefings saved before this change carry no stamp at all."""

    def test_no_stamp_reports_an_unknown_age_rather_than_raising(self):
        aged = legacy._age_briefing({"macro_bias": "risk_off"})
        assert aged["age_minutes"] is None
        assert aged["written_label"] == ""

    def test_an_unknown_age_is_not_claimed_to_be_fresh_or_stale(self):
        assert legacy._age_briefing({})["stale"] is False

    @pytest.mark.parametrize("stamp", ["", "not a date", None, 12345])
    def test_an_unreadable_stamp_does_not_raise(self, stamp):
        assert legacy._age_briefing({"generated_at": stamp})["age_minutes"] is None

    def test_a_naive_stamp_and_an_aware_clock_still_subtract(self):
        """This combination silently returned an unknown age, which is exactly
        the state the change was meant to remove."""
        naive = legacy._et_now().replace(tzinfo=None) - timedelta(hours=2)
        aged = legacy._age_briefing({"generated_at": naive.isoformat()})
        assert aged["age_minutes"] is not None


class TestItFlagsAContradiction:
    def _with_regime(self, monkeypatch, live, label=None):
        monkeypatch.setattr(legacy, "_live_regime",
                            lambda: (live, label or live.replace("_", " ").title()))

    def test_a_disagreement_is_stated_plainly(self, monkeypatch):
        self._with_regime(monkeypatch, "risk_on")
        aged = legacy._age_briefing({"macro_bias": "risk_off",
                                     "generated_at": written(16)})
        assert "risk off" in aged["conflict"]
        assert "Risk On" in aged["conflict"]

    def test_the_note_says_when_the_briefing_was_written(self, monkeypatch):
        self._with_regime(monkeypatch, "risk_on")
        aged = legacy._age_briefing({"macro_bias": "risk_off",
                                     "generated_at": written(16)})
        assert aged["written_label"] in aged["conflict"]

    def test_agreement_produces_no_note(self, monkeypatch):
        self._with_regime(monkeypatch, "risk_on")
        aged = legacy._age_briefing({"macro_bias": "risk_on",
                                     "generated_at": written(16)})
        assert aged["conflict"] == ""

    def test_a_neutral_briefing_is_not_called_a_contradiction(self, monkeypatch):
        """Neutral against risk-on is a difference of emphasis, not a clash."""
        self._with_regime(monkeypatch, "risk_on")
        aged = legacy._age_briefing({"macro_bias": "neutral",
                                     "generated_at": written(16)})
        assert aged["conflict"] == ""

    def test_an_unknown_live_regime_makes_no_claim(self, monkeypatch):
        self._with_regime(monkeypatch, "")
        aged = legacy._age_briefing({"macro_bias": "risk_off",
                                     "generated_at": written(16)})
        assert aged["conflict"] == ""

    def test_a_failing_regime_lookup_does_not_break_the_briefing(self, monkeypatch):
        def _boom():
            raise RuntimeError("market context unavailable")
        monkeypatch.setattr(legacy, "_live_regime", _boom)
        aged = legacy._age_briefing({"macro_bias": "risk_off",
                                     "generated_at": written(16)})
        assert aged["conflict"] == ""
        assert aged["age_minutes"] is not None


class TestReadingTheLiveRegime:
    @pytest.mark.parametrize("raw,expected", [
        ("Risk-On", "risk_on"), ("RISK ON", "risk_on"),
        ("risk_off", "risk_off"), ("Risk Off — defensive", "risk_off"),
        ("Choppy", "neutral"), ("", ""),
    ])
    def test_it_normalises_however_the_regime_is_spelled(self, raw, expected,
                                                        monkeypatch):
        monkeypatch.setattr(legacy, "_get_mkt_ctx", lambda: {"regime": raw})
        assert legacy._live_regime_bias() == expected

    def test_no_context_is_not_a_regime(self, monkeypatch):
        monkeypatch.setattr(legacy, "_get_mkt_ctx", lambda: None)
        assert legacy._live_regime_bias() == ""


class TestThePageShowsIt:
    # The renderer moved into the shared script partial when the command
    # centre and the macro page were split.
    PAGE = (open("templates/liquidity.html").read()
            + open("templates/_liq_scripts.html").read())

    def test_it_renders_when_the_briefing_was_written(self):
        assert "b.written_label" in self.PAGE

    def test_it_renders_the_age(self):
        assert "b.age_minutes" in self.PAGE

    def test_it_marks_a_stale_briefing(self):
        assert "is-stale" in self.PAGE
        assert ".ai-brief-time.is-stale" in self.PAGE

    def test_it_has_somewhere_to_put_the_conflict(self):
        assert 'id="ai-brief-conflict"' in self.PAGE
        assert ".ai-brief-conflict" in self.PAGE


class TestTheConflictNoteReadsAsASentence:
    def test_with_a_stamp_it_names_the_time(self, monkeypatch):
        monkeypatch.setattr(legacy, "_live_regime", lambda: ("risk_on", "Risk On"))
        note = legacy._age_briefing({"macro_bias": "risk_off",
                                     "generated_at": written(16)})["conflict"]
        assert note.startswith("Written at ")
        assert " at earlier today" not in note

    def test_without_a_stamp_it_still_reads_properly(self, monkeypatch):
        """"Written at earlier today" is not a sentence."""
        monkeypatch.setattr(legacy, "_live_regime", lambda: ("risk_on", "Risk On"))
        note = legacy._age_briefing({"macro_bias": "risk_off"})["conflict"]
        assert note.startswith("Written earlier today,")
        assert "at earlier" not in note


class TestTheNoteUsesThePagesOwnWords:
    """The strip said Caution, the market story said CAUTION, and the note
    underneath announced "the live regime is now neutral" — the internal
    bucket Caution falls into, and a word nothing else on the page used."""

    def _regime(self, monkeypatch, raw):
        monkeypatch.setattr(legacy, "_get_mkt_ctx", lambda: {"regime": raw})

    def test_it_names_the_regime_the_reader_can_see(self, monkeypatch):
        self._regime(monkeypatch, "Caution — Choppy")
        note = legacy._age_briefing({"macro_bias": "risk_off",
                                     "generated_at": written(16)})["conflict"]
        assert "Caution" in note
        assert "neutral" not in note

    def test_a_softening_is_not_described_as_a_reversal(self, monkeypatch):
        """Risk-off against a cautious read is the same call having softened."""
        self._regime(monkeypatch, "Caution — Choppy")
        note = legacy._age_briefing({"macro_bias": "risk_off",
                                     "generated_at": written(16)})["conflict"]
        assert "has since moved to" in note
        assert "the other way" not in note

    def test_an_actual_reversal_is_described_as_one(self, monkeypatch):
        self._regime(monkeypatch, "Risk-On")
        note = legacy._age_briefing({"macro_bias": "risk_off",
                                     "generated_at": written(16)})["conflict"]
        assert "reads the other way" in note

    def test_agreement_still_produces_nothing(self, monkeypatch):
        self._regime(monkeypatch, "Risk-On")
        assert legacy._age_briefing({"macro_bias": "risk_on",
                                     "generated_at": written(16)})["conflict"] == ""


class TestTheRegimeLabel:
    @pytest.mark.parametrize("raw,bucket,label", [
        ("Risk-On", "risk_on", "Risk On"),
        ("risk_off", "risk_off", "Risk Off"),
        ("Caution — Choppy", "neutral", "Caution — Choppy"),
        ("NEUTRAL", "neutral", "Neutral"),
    ])
    def test_it_returns_both_a_bucket_and_a_readable_label(self, monkeypatch,
                                                           raw, bucket, label):
        monkeypatch.setattr(legacy, "_get_mkt_ctx", lambda: {"regime": raw})
        assert legacy._live_regime() == (bucket, label)

    def test_no_context_is_not_a_regime(self, monkeypatch):
        monkeypatch.setattr(legacy, "_get_mkt_ctx", lambda: {})
        assert legacy._live_regime() == ("", "")

    def test_the_bucket_helper_still_answers_for_older_callers(self, monkeypatch):
        monkeypatch.setattr(legacy, "_get_mkt_ctx", lambda: {"regime": "Risk-On"})
        assert legacy._live_regime_bias() == "risk_on"
