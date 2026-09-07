"""The answer key: what the reader typed against what the company filed.

The drill's value is that the figures are pulled out of the 10-Q by hand. Its
weakness was that nothing told the reader whether they had pulled the right
lines. This reads the same quarter out of EDGAR's XBRL and reports the
differences.

The trap these tests exist for: a 10-Q reports the three-month period and the
cumulative year-to-date figure in the same document, both ending on the same
date. Only the duration separates them, so a naive read of "the latest
revenue fact" returns nine months where three were wanted — and every margin
and growth rate computed from it is then quietly wrong.
"""
import pytest

import quarter_facts as qf


def duration(tag, rows):
    """rows: (start, end, value, form, filed). Accession keyed off the filing date."""
    return {tag: {"units": {"USD": [
        {"start": s, "end": e, "val": v, "form": f, "filed": d, "accn": "acc-" + d}
        for s, e, v, f, d in rows]}}}


def instant(tag, rows):
    """rows: (end, value, form, filed) — both balance-sheet columns of one
    filing share its accession number, which is how the comparative column is
    identified."""
    return {tag: {"units": {"USD": [
        {"end": e, "val": v, "form": f, "filed": d, "accn": "acc-" + d}
        for e, v, f, d in rows]}}}


def facts(*groups):
    merged = {}
    for g in groups:
        merged.update(g)
    return {"entityName": "Test Filer Inc.", "facts": {"us-gaap": merged}}


# A filer that reports the quarter and the year-to-date side by side, both
# ending 2026-06-30, exactly as a real 10-Q does.
FIXTURE = facts(
    duration("RevenueFromContractWithCustomerExcludingAssessedTax", [
        ("2026-04-01", "2026-06-30", 60801, "10-Q", "2026-07-30"),   # the quarter
        ("2026-01-01", "2026-06-30", 118000, "10-Q", "2026-07-30"),  # year to date
        ("2025-04-01", "2025-06-30", 47516, "10-Q", "2025-07-31"),   # a year earlier
    ]),
    duration("CostOfRevenue", [
        ("2026-04-01", "2026-06-30", 11330, "10-Q", "2026-07-30"),
        ("2025-04-01", "2025-06-30", 8491, "10-Q", "2025-07-31"),
    ]),
    duration("NetIncomeLoss", [
        ("2026-04-01", "2026-06-30", 15848, "10-Q", "2026-07-30"),
        ("2026-01-01", "2026-06-30", 42621, "10-Q", "2026-07-30"),
        ("2025-04-01", "2025-06-30", 18337, "10-Q", "2025-07-31"),
    ]),
    duration("NetCashProvidedByUsedInOperatingActivities", [
        ("2026-01-01", "2026-06-30", 64088, "10-Q", "2026-07-30"),
    ]),
    duration("PaymentsToAcquirePropertyPlantAndEquipment", [
        ("2026-01-01", "2026-06-30", 49113, "10-Q", "2026-07-30"),
    ]),
    # Meta's real shape: 125,475 at 30 June 2026 against 108,722 at the
    # previous fiscal year end, both tagged in the July 2026 submission. The
    # June 2025 instant exists too, from the prior year's 10-Q, and is NOT the
    # column this filing prints.
    instant("AssetsCurrent", [
        ("2026-06-30", 125475, "10-Q", "2026-07-30"),
        ("2025-12-31", 108722, "10-Q", "2026-07-30"),
        ("2025-06-30", 999999, "10-Q", "2025-07-31"),
    ]),
    instant("LiabilitiesCurrent", [
        ("2026-06-30", 56379, "10-Q", "2026-07-30"),
        ("2025-12-31", 41836, "10-Q", "2026-07-30"),
    ]),
)


@pytest.fixture
def filed():
    return qf.latest_quarter("TEST", facts=FIXTURE)


class TestItReadsTheQuarterNotTheYearToDate:
    def test_the_quarter_is_the_three_month_column(self, filed):
        """118,000 is six months of revenue. Taking it would wreck every ratio."""
        assert filed["fields"]["rev"]["value"] == 60801

    def test_the_year_to_date_column_is_read_separately(self, filed):
        assert filed["fields"]["revYtd"]["value"] == 118000
        assert filed["fields"]["niy"]["value"] == 42621

    def test_the_quarter_and_the_year_to_date_net_income_do_not_collide(self, filed):
        assert filed["fields"]["ni"]["value"] == 15848
        assert filed["fields"]["niy"]["value"] == 42621

    def test_the_period_is_anchored_on_revenue(self, filed):
        assert filed["period_end"] == "2026-06-30"
        assert filed["prior_end"] == "2025-06-30"

    def test_the_prior_year_column_is_the_same_quarter_not_the_newest(self, filed):
        assert filed["fields"]["revP"]["value"] == 47516
        assert filed["fields"]["cogsP"]["value"] == 8491

    def test_balance_sheet_instants_come_from_the_period_end(self, filed):
        assert filed["fields"]["ca"]["value"] == 125475

    def test_the_prior_column_is_the_fiscal_year_end_not_a_year_ago(self, filed):
        """A 10-Q's balance sheet is this date against the last year end.

        Meta's June 2026 filing prints 31 December 2025 beside it. Comparing
        the reader against June 2025 marks them wrong for typing what is on
        the page — the fixture's 999,999 is that trap.
        """
        assert filed["fields"]["caP"]["value"] == 108722
        assert filed["fields"]["caP"]["end"] == "2025-12-31"

    def test_the_tag_used_is_reported_so_it_can_be_argued_with(self, filed):
        assert filed["fields"]["rev"]["tag"] == \
            "RevenueFromContractWithCustomerExcludingAssessedTax"


class TestWhenThereIsNothingToRead:
    def test_a_filer_with_no_quarterly_revenue_returns_nothing(self):
        annual_only = facts(duration("Revenues", [
            ("2025-01-01", "2025-12-31", 200000, "10-K", "2026-01-29")]))
        assert qf.latest_quarter("TEST", facts=annual_only) is None

    @pytest.mark.parametrize("junk", [{}, {"facts": {}}, {"facts": {"us-gaap": {}}}])
    def test_an_empty_document_is_survivable(self, junk):
        assert qf.latest_quarter("TEST", facts=junk) is None


class TestTheComparison:
    def test_the_comparative_column_a_reader_can_see_matches(self, filed):
        out = qf.compare({"caP": 108722, "clP": 41836}, filed)
        assert all(r["state"] == "match" for r in out["rows"]
                   if r["key"] in ("caP", "clP"))

    def test_a_figure_typed_correctly_matches(self, filed):
        out = qf.compare({"rev": 60801}, filed)
        row = next(r for r in out["rows"] if r["key"] == "rev")
        assert row["state"] == "match"
        assert out["matched"] == 1

    def test_a_reader_who_grabbed_the_year_to_date_column_is_told(self, filed):
        """The exact mistake the duration filter exists to catch."""
        out = qf.compare({"rev": 118000}, filed)
        row = next(r for r in out["rows"] if r["key"] == "rev")
        assert row["state"] == "differs"
        assert row["filed"] == 60801
        assert row["tag"]

    def test_rounding_is_not_a_difference(self, filed):
        """Readers round. Half a percent is the same number."""
        out = qf.compare({"rev": 60800}, filed)
        assert next(r for r in out["rows"] if r["key"] == "rev")["state"] == "match"

    def test_a_units_slip_is_named_as_one(self, filed):
        """Reading the right row off a statement headed "in millions".

        Telling someone they picked the wrong line when they did not teaches
        them the wrong lesson.
        """
        out = qf.compare({"rev": 60.801}, filed)
        row = next(r for r in out["rows"] if r["key"] == "rev")
        assert row["state"] == "differs"
        assert row["scaled"] is True

    def test_a_genuinely_wrong_line_is_not_called_a_units_slip(self, filed):
        out = qf.compare({"rev": 11330}, filed)
        assert next(r for r in out["rows"] if r["key"] == "rev")["scaled"] is False

    def test_a_tag_the_filer_never_used_is_reported_as_unfiled_not_wrong(self, filed):
        """Filers choose their own tags. Absence is not the reader's error."""
        out = qf.compare({"opex": 30696}, filed)
        row = next(r for r in out["rows"] if r["key"] == "opex")
        assert row["state"] == "unfiled"
        assert out["differed"] == 0

    def test_a_field_left_blank_is_not_graded(self, filed):
        out = qf.compare({}, filed)
        assert out["matched"] == 0 and out["differed"] == 0
        assert all(r["state"] == "blank" for r in out["rows"])

    def test_no_filing_is_an_answer_rather_than_an_exception(self):
        assert qf.compare({"rev": 1}, None)["available"] is False


class TestTheEndpoints:
    @pytest.fixture
    def client(self):
        from unittest.mock import patch
        import web_app, app as _app
        c = web_app.app.test_client()
        with c.session_transaction() as sess:
            sess["user_id"] = 1
            sess["logged_in"] = True
        with patch.object(_app, "_auth_required", lambda *a, **k: False):
            yield c

    def test_the_drill_grades_without_fetching_anything(self, client):
        """The whole premise: the reader's numbers, not the app's."""
        from unittest.mock import patch
        import re
        page = client.get("/fundamentals?tab=quarter").get_data(as_text=True)
        token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
        with patch.object(qf, "latest_quarter") as fetch:
            resp = client.post("/api/quarter/check",
                               json={"cfo": 100, "niy": 80},
                               headers={"X-CSRFToken": token})
        assert not fetch.called
        assert resp.get_json()["counts"]["clean"] == 1

    def test_the_answer_key_refuses_a_junk_ticker(self, client):
        assert client.get("/api/quarter/filed/..%2Fetc").status_code in (400, 404)

    def test_an_unreadable_filing_answers_rather_than_500s(self, client):
        from unittest.mock import patch
        with patch.object(qf, "latest_quarter", side_effect=RuntimeError("EDGAR 503")):
            body = client.get("/api/quarter/filed/META").get_json()
        assert body["available"] is False
        assert "EDGAR 503" not in str(body)      # no upstream detail to the browser

    def test_the_tab_is_on_the_page(self, client):
        page = client.get("/fundamentals").get_data(as_text=True)
        assert 'id="tab-quarter"' in page
        assert "This quarter" in page


class TestTotalOperatingExpenses:
    """The line most filers never tag under the name the drill asks for.

    Meta presents one "Total costs and expenses" — CostsAndExpenses — which
    includes cost of revenue. For the September quarter that is 42,026 against
    operating expenses of 30,696. Substituting it would tell a reader who read
    the statement correctly that they were twelve billion dollars wrong.
    """

    COMBINED = facts(
        duration("RevenueFromContractWithCustomerExcludingAssessedTax", [
            ("2026-04-01", "2026-06-30", 60801, "10-Q", "2026-07-30"),
            ("2025-04-01", "2025-06-30", 47516, "10-Q", "2025-07-31")]),
        duration("CostOfRevenue", [
            ("2026-04-01", "2026-06-30", 11330, "10-Q", "2026-07-30"),
            ("2025-04-01", "2025-06-30", 8491, "10-Q", "2025-07-31")]),
        duration("CostsAndExpenses", [
            ("2026-04-01", "2026-06-30", 42026, "10-Q", "2026-07-30"),
            ("2025-04-01", "2025-06-30", 27075, "10-Q", "2025-07-31")]),
    )

    def test_the_combined_line_is_never_handed_back_as_operating_expenses(self):
        filed = qf.latest_quarter("TEST", facts=self.COMBINED)
        assert filed["fields"]["opex"]["value"] != 42026

    def test_it_is_derived_by_taking_cost_of_revenue_back_out(self):
        filed = qf.latest_quarter("TEST", facts=self.COMBINED)
        assert filed["fields"]["opex"]["value"] == 30696          # 42,026 − 11,330
        assert filed["fields"]["opexP"]["value"] == 18584         # 27,075 − 8,491

    def test_the_derivation_is_labelled_as_one(self):
        """A figure this app computed is not a figure the company filed."""
        filed = qf.latest_quarter("TEST", facts=self.COMBINED)
        assert filed["fields"]["opex"]["derived"] is True
        assert "CostsAndExpenses −" in filed["fields"]["opex"]["tag"]

    def test_a_reader_who_read_the_statement_right_is_told_they_were_right(self):
        filed = qf.latest_quarter("TEST", facts=self.COMBINED)
        out = qf.compare({"opex": 30696}, filed)
        assert next(r for r in out["rows"] if r["key"] == "opex")["state"] == "match"

    def test_a_direct_tag_always_wins_over_the_derivation(self):
        direct = facts(
            duration("RevenueFromContractWithCustomerExcludingAssessedTax", [
                ("2026-04-01", "2026-06-30", 60801, "10-Q", "2026-07-30")]),
            duration("CostOfRevenue", [
                ("2026-04-01", "2026-06-30", 11330, "10-Q", "2026-07-30")]),
            duration("OperatingExpenses", [
                ("2026-04-01", "2026-06-30", 30696, "10-Q", "2026-07-30")]),
            duration("CostsAndExpenses", [
                ("2026-04-01", "2026-06-30", 42026, "10-Q", "2026-07-30")]),
        )
        filed = qf.latest_quarter("TEST", facts=direct)
        assert filed["fields"]["opex"]["tag"] == "OperatingExpenses"
        assert not filed["fields"]["opex"].get("derived")

    def test_half_a_derivation_is_no_derivation(self):
        """Without cost of revenue there is nothing to subtract."""
        partial = facts(
            duration("RevenueFromContractWithCustomerExcludingAssessedTax", [
                ("2026-04-01", "2026-06-30", 60801, "10-Q", "2026-07-30")]),
            duration("CostsAndExpenses", [
                ("2026-04-01", "2026-06-30", 42026, "10-Q", "2026-07-30")]),
        )
        filed = qf.latest_quarter("TEST", facts=partial)
        assert "opex" not in filed["fields"]


class TestThePageTeaches:
    """The reader is learning to read a filing, not to fill in a form.

    The styling bug that prompted this: the stylesheet was written into the
    template between {% endblock %} and {% block scripts %}, which Jinja
    discards, so the whole tab rendered as unstyled HTML.
    """

    @classmethod
    @pytest.fixture(scope="class")
    def page(cls):
        from unittest.mock import patch
        import web_app, app as _app
        c = web_app.app.test_client()
        with c.session_transaction() as sess:
            sess["user_id"] = 1
            sess["logged_in"] = True
        with patch.object(_app, "_auth_required", lambda *a, **k: False):
            return c.get("/fundamentals?tab=quarter").get_data(as_text=True)

    def test_no_markup_is_stranded_outside_a_jinja_block(self):
        """Anything outside a block in a child template is silently dropped."""
        from pathlib import Path
        import re
        src = Path("templates/fundamentals.html").read_text()
        body = re.sub(r"\{%\s*block .*?%\}.*?\{%\s*endblock\s*%\}", "",
                      src, flags=re.S)
        assert "<style" not in body
        assert "<div" not in body

    def test_the_drill_styles_ship_in_the_stylesheet(self):
        from pathlib import Path
        css = Path("static/css/fundamentals.css").read_text()
        for rule in (".qd-intro", ".qd-tip", ".qd-badge", ".qd-cmp", ".qd-results"):
            assert rule in css

    def test_every_field_says_where_to_find_it(self, page):
        """A learner needs the statement and the line, not just a label."""
        for probe in ("Income statement, the very first line.",
                      "Cash flow statement, its first line",
                      "Balance sheet, near the top of current assets"):
            assert probe in page

    def test_every_field_lists_what_else_the_line_is_called(self, page):
        for alias in ("Net sales", "Cost of goods sold",
                      "Weighted-average shares", "Capital expenditures"):
            assert alias in page

    def test_it_warns_about_the_trap_that_breaks_every_ratio(self, page):
        """Taking the year-to-date column for the quarter."""
        assert "Three-month columns" in page
        assert "Always cumulative, never quarterly" in page

    def test_a_worked_example_is_one_click_away(self, page):
        assert "Load a worked example" in page
        assert "qdExample" in page

    def test_the_hints_can_all_be_opened_at_once(self, page):
        assert "Show me where" in page
        assert "qdToggleHints" in page
