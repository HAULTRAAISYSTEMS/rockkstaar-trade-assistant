"""What a calendar row actually knows about when it is.

The calendar printed "TBD" under every date more than a few weeks out. It is
an acronym a learner has no reason to decode; it was swallowing a session the
provider HAD reported; and it was discarding the one piece of evidence on the
row about whether the date itself is real.
"""
import pytest

import earnings_timing as et


class TestTheSessionsAProviderActuallyReports:
    @pytest.mark.parametrize("raw, code, short", [
        ("bmo", "bmo", "Before open"),
        ("amc", "amc", "After close"),
        ("dmh", "dmh", "Mid-session"),
        ("BMO", "bmo", "Before open"),
        ("  Amc  ", "amc", "After close"),
    ])
    def test_it_reads_the_feed_codes(self, raw, code, short):
        out = et.describe(raw)
        assert out["code"] == code
        assert out["short"] == short
        assert out["known"] is True

    def test_during_market_hours_is_not_unknown(self):
        """The bug this exists for. Finnhub reports three sessions and the
        bulk ingestion mapped everything that was not bmo or amc onto TBD, so
        a company that HAD said it reports mid-session was shown as unknown.
        company_research_data handled dmh correctly the whole time, which is
        how we know it was an oversight rather than a decision."""
        out = et.describe("dmh")
        assert out["known"] is True
        assert out["provisional"] is False
        assert "TBD" not in out["text"]

    def test_it_also_reads_the_display_strings_the_app_has_stored(self):
        """Rows saved by other code paths carry the long form, not the code."""
        for stored in ("Before market open", "After market close",
                       "During market hours"):
            assert et.describe(stored)["known"] is True


class TestNotKnowingIsAnAnswer:
    @pytest.mark.parametrize("raw", ["", None, "TBD", "tbd", "n/a", "unknown",
                                     "   ", "—", "something else"])
    def test_anything_unrecognised_is_unknown_not_an_error(self, raw):
        out = et.describe(raw)
        assert out["known"] is False
        assert out["code"] == ""

    def test_it_says_so_in_words_rather_than_an_acronym(self):
        out = et.describe(None)
        assert out["text"] == "Time not announced"
        assert "TBD" not in out["text"]
        assert "TBD" not in out["short"]


class TestAnUnannouncedSessionIsEvidenceAboutTheDate:
    """The serious one. A company fixes its reporting date and its reporting
    session in the same press release, so a blank session is nearly always a
    date nobody has announced — the provider projected it. The calendar
    rendered that in the same weight as a date confirmed three days out."""

    def test_an_unknown_session_marks_the_date_provisional(self):
        out = et.describe("")
        assert out["provisional"] is True
        assert out["note"]

    def test_a_known_session_does_not(self):
        for code in ("bmo", "amc", "dmh"):
            assert et.describe(code)["provisional"] is False
            assert et.describe(code)["note"] == ""

    def test_the_note_says_what_is_known_not_what_is_guessed(self):
        """It must not assert a confirmation status no feed supplies, nor
        invent a mechanism. It reports the missing session and what normally
        follows from it."""
        note = et.describe("")["note"]
        assert "has not announced" in note
        assert "provisional" in note
        # No claim about where the date came from, because we cannot know.
        assert "estimated from" not in note.lower()
        assert "last year" not in note.lower()

    def test_it_never_claims_a_date_is_confirmed(self):
        """Knowing the session makes a date non-provisional, which is a much
        weaker claim than confirmed, and the wording must not drift into the
        stronger one."""
        for code in ("bmo", "amc", "dmh", "", None):
            out = et.describe(code)
            assert "confirmed" not in out["text"].lower()
            assert "confirmed" not in out["short"].lower()


class TestTheStoredContractDoesNotShiftUnderneathAnyone:
    """time_label is read in fifteen places. bmo and amc must keep producing
    exactly what they always did."""

    @pytest.mark.parametrize("raw, stored", [
        ("bmo", "BMO"), ("amc", "AMC"),
        ("", "TBD"), (None, "TBD"), ("garbage", "TBD"),
    ])
    def test_existing_values_are_unchanged(self, raw, stored):
        assert et.label_for_storage(raw) == stored

    def test_only_dmh_is_new(self):
        assert et.label_for_storage("dmh") == "DMH"


class TestItSurvivesWhateverTheFeedSends:
    @pytest.mark.parametrize("raw", [0, 1, [], {}, object(), 3.5, True])
    def test_a_non_string_does_not_raise(self, raw):
        out = et.describe(raw)
        assert out["known"] is False
        assert et.label_for_storage(raw) == "TBD"
