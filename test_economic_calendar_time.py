"""A macro release time is only labelled Eastern when it is known to be.

Every event time went through one formatter that appended " ET", including
the ones parsed straight out of the Finnhub payload — whose timezone Finnhub
does not document anywhere in its API reference. If that field is UTC, a US
CPI print at 13:30 was shown as "1:30 PM ET" on a trading calendar: four and
a half hours late, and on the wrong side of the open.

Rather than guess a conversion, the label is dropped from the provider path
and the card marks that time as supplied. The built-in schedule below is
hand-entered Eastern and keeps its label.
"""
import pytest

import intel_engine


class TestTheFormatter:
    def test_it_still_labels_a_time_we_know_is_eastern(self):
        assert intel_engine._format_time_12h(8, 30) == "8:30 AM ET"

    def test_an_unknown_timezone_gets_no_label(self):
        assert intel_engine._format_time_12h(13, 30, tz_label="") == "1:30 PM"

    @pytest.mark.parametrize("hour,minute,expected", [
        (0, 0, "12:00 AM ET"),
        (12, 0, "12:00 PM ET"),
        (12, 30, "12:30 PM ET"),
        (23, 59, "11:59 PM ET"),
        (9, 5, "9:05 AM ET"),
    ])
    def test_the_clock_itself_is_unchanged(self, hour, minute, expected):
        assert intel_engine._format_time_12h(hour, minute) == expected


class TestWhatTheCardIsTold:
    def test_the_built_in_schedule_is_eastern(self):
        events = intel_engine._econ_static_fallback()
        assert all(e["time_zone"] == "ET" for e in events)

    def test_the_provider_path_carries_an_empty_zone(self):
        """Empty, not missing — the template branches on it."""
        source = open("intel_engine.py").read()
        econ = source[source.index("def _econ_from_finnhub"):
                      source.index("def _econ_static_fallback")]
        assert '"time_zone":  ""' in econ
        assert 'tz_label=""' in econ

    def test_the_provider_path_never_calls_the_formatter_bare(self):
        """A bare call takes the Eastern default, which is the old bug."""
        import re
        source = open("intel_engine.py").read()
        econ = source[source.index("def _econ_from_finnhub"):
                      source.index("def _econ_static_fallback")]
        for call in re.findall(r"_format_time_12h\([^)]*\)", econ):
            assert 'tz_label=""' in call, call


def test_the_page_marks_a_time_it_cannot_vouch_for():
    page = open("templates/catalyst_calendar.html").read()
    assert 'event.time_zone == ""' in page
    assert "cal-tz" in page
