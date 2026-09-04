"""The built-in macro schedule, and what happens when it runs out.

Finnhub's economic-calendar endpoint is on a premium plan, so this list is
the macro calendar rather than a fallback to one. It ended on 2026-09-16 —
twelve days after it was last read — and the card would have gone empty with
nothing on the page to say why. An exhausted calendar looks exactly like a
quiet fortnight.

Every date is checked against the issuing agency's published schedule: BLS
for payrolls, CPI and PPI; Census for retail sales; BEA for GDP and personal
income; the Federal Reserve for FOMC.
"""
from datetime import date

import pytest

import intel_engine as ie


SCHEDULE = {(row[0], row[2]) for row in ie._STATIC_ECON}
BY_DATE: dict[str, set] = {}
for _d, _t, _name, _impact in ie._STATIC_ECON:
    BY_DATE.setdefault(_d, set()).add(_name)


class TestTheDatesAreTheAgenciesDates:
    @pytest.mark.parametrize("day", ["2026-09-11", "2026-10-14", "2026-11-10",
                                     "2026-12-10"])
    def test_cpi_matches_the_bls_schedule(self, day):
        assert any("Consumer Price Index" in n for n in BY_DATE.get(day, ()))

    def test_cpi_is_not_on_the_ppi_date(self):
        """The old list had CPI on 2026-09-10. That is the PPI date."""
        assert not any("Consumer Price Index" in n
                       for n in BY_DATE.get("2026-09-10", ()))
        assert any("Producer Price Index" in n
                   for n in BY_DATE.get("2026-09-10", ()))

    @pytest.mark.parametrize("day", ["2026-09-04", "2026-10-02", "2026-11-06",
                                     "2026-12-04"])
    def test_payrolls_match_the_bls_schedule(self, day):
        assert any("Non-Farm Payrolls" in n for n in BY_DATE.get(day, ()))

    @pytest.mark.parametrize("day", ["2026-09-16", "2026-10-15", "2026-11-17",
                                     "2026-12-16"])
    def test_retail_sales_match_the_census_schedule(self, day):
        assert any("Retail Sales" in n for n in BY_DATE.get(day, ()))

    @pytest.mark.parametrize("day", ["2026-09-30", "2026-10-29", "2026-11-25",
                                     "2026-12-23"])
    def test_pce_matches_the_bea_schedule(self, day):
        assert any("PCE Price Index" in n for n in BY_DATE.get(day, ()))

    @pytest.mark.parametrize("day", ["2026-09-16", "2026-10-28", "2026-12-09",
                                     "2027-01-27", "2027-03-17", "2027-04-28",
                                     "2027-06-09", "2027-07-28", "2027-09-15",
                                     "2027-10-27", "2027-12-08"])
    def test_fomc_decisions_are_on_the_second_day_of_each_meeting(self, day):
        assert ("FOMC Interest Rate Decision" in BY_DATE.get(day, ())), day

    def test_every_fomc_decision_has_its_press_conference(self):
        decisions = {d for d, n in SCHEDULE if n == "FOMC Interest Rate Decision"}
        pressers = {d for d, n in SCHEDULE if n == "Fed Press Conference"}
        assert decisions == pressers

    def test_projections_only_on_the_meetings_that_publish_them(self):
        projection_days = {d for d, n in SCHEDULE if n == "FOMC Economic Projections"}
        assert projection_days == {"2026-09-16", "2026-12-09", "2027-03-17",
                                   "2027-06-09", "2027-09-15", "2027-12-08"}


class TestTheShapeOfEachRow:
    def test_every_date_parses(self):
        for day, _t, name, _i in ie._STATIC_ECON:
            date.fromisoformat(day)

    def test_every_row_is_in_order(self):
        days = [row[0] for row in ie._STATIC_ECON]
        assert days == sorted(days)

    def test_every_time_is_eastern(self):
        """These are hand-entered from agency schedules, which publish in ET."""
        assert all(row[1].endswith(" ET") for row in ie._STATIC_ECON)

    def test_every_impact_is_a_level_the_card_renders(self):
        assert {row[3] for row in ie._STATIC_ECON} <= {"HIGH", "MEDIUM"}

    def test_no_duplicate_rows(self):
        rows = [(r[0], r[1], r[2]) for r in ie._STATIC_ECON]
        assert len(rows) == len(set(rows))

    def test_every_event_gets_a_reason_and_not_the_generic_one(self):
        generic = ie._ECON_REASONS["default"]
        unnamed = {row[2] for row in ie._STATIC_ECON
                   if ie._econ_reason(row[2]) == generic}
        assert unnamed == set(), unnamed


class TestSayingWhenItRunsOut:
    def test_the_horizon_is_the_last_date(self):
        assert ie.STATIC_ECON_HORIZON == max(r[0] for r in ie._STATIC_ECON)

    def test_full_coverage_stops_before_the_fed_only_tail(self):
        """Past that date it is FOMC meetings and nothing else, which would
        read as a quiet couple of months rather than a gap."""
        assert ie.STATIC_ECON_FULL_THROUGH < ie.STATIC_ECON_HORIZON

    def test_coverage_is_reported_for_today(self):
        cover = ie.static_econ_coverage()
        assert cover["exhausted"] is False
        assert cover["days_of_full_coverage"] > 0

    def test_it_knows_when_it_is_exhausted(self):
        cover = ie.static_econ_coverage(today=date(2028, 1, 1))
        assert cover["exhausted"] is True

    def test_it_is_not_exhausted_on_the_last_day(self):
        cover = ie.static_econ_coverage(
            today=date.fromisoformat(ie.STATIC_ECON_HORIZON))
        assert cover["exhausted"] is False


class TestThePageReportsIt:
    PAGE = open("templates/catalyst_calendar.html").read()

    def test_it_says_so_when_the_schedule_has_ended(self):
        assert "econ_coverage.exhausted" in self.PAGE
        assert "not a quiet calendar" in self.PAGE

    def test_it_warns_before_the_schedule_ends(self):
        assert "days_of_full_coverage" in self.PAGE

    def test_the_route_passes_it(self):
        assert "econ_coverage=econ_coverage" in open("app.py").read()
