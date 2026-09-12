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


# ── The guided walkthrough ────────────────────────────────────────────────────

def meta_facts():
    """Meta's June 2026 filing, in the shape XBRL carries it."""
    Q, QP = ("2026-04-01", "2026-06-30"), ("2025-04-01", "2025-06-30")
    Y, YP = ("2026-01-01", "2026-06-30"), ("2025-01-01", "2025-06-30")
    F, FP = "2026-07-30", "2025-07-31"
    groups = [
        duration("RevenueFromContractWithCustomerExcludingAssessedTax",
                 [(*Q, 60801, "10-Q", F), (*QP, 47516, "10-Q", FP),
                  (*Y, 117111, "10-Q", F), (*YP, 89830, "10-Q", F)]),
        duration("CostOfRevenue", [(*Q, 11330, "10-Q", F), (*QP, 8491, "10-Q", FP)]),
        duration("ResearchAndDevelopmentExpense",
                 [(*Q, 21656, "10-Q", F), (*QP, 12942, "10-Q", FP)]),
        # Meta prints one combined line; operating expenses are not tagged.
        duration("CostsAndExpenses", [(*Q, 42026, "10-Q", F), (*QP, 27075, "10-Q", FP)]),
        duration("OperatingIncomeLoss", [(*Q, 18775, "10-Q", F), (*QP, 20441, "10-Q", FP)]),
        duration("NetIncomeLoss",
                 [(*Q, 15848, "10-Q", F), (*QP, 18337, "10-Q", FP),
                  (*Y, 42621, "10-Q", F), (*YP, 34981, "10-Q", F)]),
        duration("NetCashProvidedByUsedInOperatingActivities",
                 [(*Y, 64088, "10-Q", F), (*YP, 49587, "10-Q", F)]),
        duration("PaymentsToAcquirePropertyPlantAndEquipment",
                 [(*Y, 49113, "10-Q", F), (*YP, 29479, "10-Q", F)]),
        instant("AssetsCurrent", [("2026-06-30", 125475, "10-Q", F),
                                  ("2025-12-31", 108722, "10-Q", F)]),
        instant("LiabilitiesCurrent", [("2026-06-30", 56379, "10-Q", F),
                                       ("2025-12-31", 41836, "10-Q", F)]),
        instant("AccountsReceivableNetCurrent", [("2026-06-30", 21752, "10-Q", F),
                                                 ("2025-12-31", 19769, "10-Q", F)]),
        instant("Assets", [("2026-06-30", 449956, "10-Q", F),
                           ("2025-12-31", 366021, "10-Q", F)]),
        instant("Liabilities", [("2026-06-30", 188735, "10-Q", F),
                                ("2025-12-31", 148778, "10-Q", F)]),
        {"EarningsPerShareDiluted": {"units": {"USD/shares": [
            {"start": Q[0], "end": Q[1], "val": 6.18, "form": "10-Q", "filed": F, "accn": "a-" + F},
            {"start": QP[0], "end": QP[1], "val": 7.14, "form": "10-Q", "filed": FP, "accn": "a-" + FP},
        ]}}},
        {"WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
            {"start": Q[0], "end": Q[1], "val": 2566, "form": "10-Q", "filed": F, "accn": "a-" + F},
            {"start": QP[0], "end": QP[1], "val": 2570, "form": "10-Q", "filed": FP, "accn": "a-" + FP},
        ]}}},
    ]
    return facts(*groups)


@pytest.fixture(scope="module")
def walk():
    return qf.walkthrough("META", facts=meta_facts())


def row(sheet, key):
    return next(r for r in sheet["rows"] if r["key"] == key)


class TestTheStatementsAreRebuilt:
    def test_the_income_statement_reads_in_order(self, walk):
        keys = [r["key"] for r in walk["statements"]["income"]["rows"]]
        assert keys.index("rev") < keys.index("cogs") < keys.index("opinc") < keys.index("ni")

    def test_the_current_subtotal_sits_directly_above_the_total(self, walk):
        """Seeing 125,475 next to 449,956 is the whole lesson."""
        sheet = walk["statements"]["balance"]
        keys = [r["key"] for r in sheet["rows"]]
        assert keys.index("ca") + 1 == keys.index("assets")
        assert row(sheet, "ca")["now"] == 125475
        assert row(sheet, "assets")["now"] == 449956

    def test_the_balance_sheet_prior_column_is_the_last_year_end(self, walk):
        assert row(walk["statements"]["balance"], "ca")["prior_end"] == "2025-12-31"

    def test_the_cash_flow_is_the_cumulative_column(self, walk):
        """Six months, not the quarter — which is why niy is 42,621."""
        assert row(walk["statements"]["cash"], "niy")["now"] == 42621
        assert row(walk["statements"]["cash"], "cfo")["now"] == 64088

    def test_a_row_the_filer_never_tagged_is_dropped_not_shown_empty(self, walk):
        """A statement full of blanks teaches nothing and looks broken."""
        keys = [r["key"] for r in walk["statements"]["income"]["rows"]]
        assert "sales" not in keys          # never tagged in this fixture

    def test_the_operating_expense_subtotal_is_derived_and_labelled(self, walk):
        """Meta prints only the combined line; the check needs a row to point at."""
        opex = row(walk["statements"]["income"], "opex")
        assert opex["now"] == 30696 and opex["prior"] == 18584
        assert opex["derived"] is True
        assert "CostsAndExpenses" in opex["tag"]


class TestEachCheckNamesTheRowsItRead:
    def test_every_graded_check_points_at_something(self):
        import quarter_checks
        graded = [c for c in quarter_checks.run(
            {k: v for k, v in TestTheWorkedExample.__dict__.items()} if False else
            dict(rev=60801, revP=47516, cogs=11330, cogsP=8491, opex=30696,
                 opexP=18584, opinc=18775, opincP=20441, ni=15848, niP=18337,
                 sh=2566, shP=2570, eps=6.18, epsP=7.14, ca=125475, caP=108722,
                 cl=56379, clP=41836, ar=21752, arP=19769, niy=42621,
                 cfo=64088, capex=49113))["checks"] if c["verdict"]]
        assert graded and all(c["rows"] for c in graded)

    def test_the_rows_named_exist_on_the_statement(self, walk):
        """A check that highlights a row the statement does not carry is a
        check whose explanation points at nothing."""
        import quarter_checks
        for check_key, sheets in quarter_checks.CHECK_ROWS.items():
            for sheet_name, keys in sheets.items():
                present = {r["key"] for r in walk["statements"][sheet_name]["rows"]}
                missing = set(keys) - present
                assert not missing, f"{check_key} points at {missing} on {sheet_name}"

    def test_a_filer_who_tags_almost_nothing_still_renders(self):
        """Degrade to a short statement rather than an exception."""
        bare = facts(duration("Revenues", [
            ("2026-04-01", "2026-06-30", 100, "10-Q", "2026-07-30")]))
        out = qf.walkthrough("TINY", facts=bare)
        assert out["statements"]["income"]["rows"]
        assert out["statements"]["balance"]["rows"] == []


class TestTheWalkthroughEndpoint:
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

    def test_it_returns_figures_and_statements_together(self, client):
        """The route fetches the document itself now, so that a filing it
        cannot read can be explained rather than merely reported missing."""
        from unittest.mock import patch
        with patch.object(qf, "fetch_facts",
                          return_value=(meta_facts(), "0001326801")):
            body = client.get("/api/quarter/walkthrough/META").get_json()
        assert body["available"] is True
        assert body["figures"]["rev"] == 60801
        assert body["statements"]["income"]["rows"]

    def test_a_filing_it_cannot_read_says_which_kind_of_nothing(self):
        """"No quarterly filing found" is true and useless."""
        from unittest.mock import patch
        with patch.object(qf, "_recent_forms", return_value=["6-K", "F-1"]):
            why = qf.explain_gap("SKHY", {"entityName": "SK hynix Inc.",
                                          "facts": {"ffd": {}}}, "0002120882")
        assert "foreign private issuer" in why

    def test_the_document_is_fetched_once_for_both_halves(self):
        """It is tens of megabytes; twice would double the slowest step."""
        from unittest.mock import patch
        with patch.object(qf, "fetch_facts", return_value=(meta_facts(), "0001326801")) as fetch:
            qf.walkthrough("META")
        assert fetch.call_count == 1

    def test_a_junk_ticker_never_reaches_the_fetch(self, client):
        """Refused by the route or by the handler — either way, not fetched."""
        from unittest.mock import patch
        with patch.object(qf, "walkthrough") as walked:
            for junk in ("..%2Fetc", "A B", "'; DROP--"):
                assert client.get("/api/quarter/walkthrough/" + junk).status_code in (400, 404)
        assert not walked.called

    def test_an_unreadable_filing_answers_rather_than_500s(self, client):
        from unittest.mock import patch
        with patch.object(qf, "walkthrough", side_effect=RuntimeError("EDGAR 503")):
            body = client.get("/api/quarter/walkthrough/META").get_json()
        assert body["available"] is False
        assert "EDGAR 503" not in str(body)

    def test_the_page_offers_it(self, client):
        page = client.get("/fundamentals?tab=quarter").get_data(as_text=True)
        assert "Walk me through it" in page
        assert "qdStatementFor" in page


class TestTheTickerHasSomethingToPress:
    """A ticker was typed and nothing happened.

    The action lived in a toolbar above a form taller than a screen, so by the
    time you reach the fields every button is scrolled away — and the ticker
    box had nothing beside it. The Analyze tab has ANALYZE right there; this
    one did not.
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

    def test_the_button_sits_in_the_same_row_as_the_ticker(self, page):
        row = page[page.index('<div class="qd-id">'):]
        row = row[:row.index("</div>")]
        assert 'id="q-co"' in row
        assert "qdWalk()" in row

    def test_enter_on_the_ticker_fetches_rather_than_grading_an_empty_form(self, page):
        assert "if (e.target.id === 'q-co' || e.target.id === 'q-per') qdWalk();" in page

    def test_the_status_is_repeated_at_the_top(self, page):
        """The message about a click at the top must not print at the bottom."""
        assert 'id="q-status-top"' in page
        assert page.index('id="q-status-top"') < page.index('id="q-status"')

    def test_the_button_says_it_is_working(self, page):
        """EDGAR company facts is tens of megabytes; silence reads as a dead click."""
        assert "function qdBusy" in page
        assert "Reading the filing…" in page
        assert "b.disabled = true;" in page


class TestAFilerWhoChangedTags:
    """NVDA came back as the quarter ended 2020-01-26. Six years stale.

    Tag order was being read as priority: _pick returned the first tag with
    any data at all, so an older revenue tag whose history stops in 2020 won
    over the tag the company files under now. Every other figure was then
    filtered to that dead date, which is why most of the form came back empty
    and the label said 10-K.

    Preference between tags only matters within one period. Across periods,
    the most recent filing wins.
    """

    SWITCHED = facts(
        # The old tag, abandoned in 2020.
        duration("Revenues", [
            ("2019-10-28", "2020-01-26", 3105, "10-K", "2020-02-20")]),
        # The tag in use now.
        duration("RevenueFromContractWithCustomerExcludingAssessedTax", [
            ("2026-04-27", "2026-07-26", 96221, "10-Q", "2026-08-27"),
            ("2025-04-28", "2025-07-27", 46743, "10-Q", "2025-08-27")]),
    )

    def test_the_current_tag_wins_over_a_dead_one(self):
        found = qf.latest_quarter("NVDA", facts=self.SWITCHED)
        assert found["period_end"] == "2026-07-26"
        assert found["fields"]["rev"]["value"] == 96221

    def test_the_stale_period_is_not_what_the_page_reports(self):
        found = qf.latest_quarter("NVDA", facts=self.SWITCHED)
        assert found["form"] == "10-Q"
        assert found["fields"]["rev"]["value"] != 3105

    def test_tag_order_still_decides_within_one_period(self):
        """Two tags for the same quarter: the preferred one is used."""
        both = facts(
            duration("RevenueFromContractWithCustomerExcludingAssessedTax", [
                ("2026-04-27", "2026-07-26", 96221, "10-Q", "2026-08-27")]),
            duration("Revenues", [
                ("2026-04-27", "2026-07-26", 99999, "10-Q", "2026-08-27")]),
        )
        found = qf.latest_quarter("NVDA", facts=both)
        assert found["fields"]["rev"]["tag"] == \
            "RevenueFromContractWithCustomerExcludingAssessedTax"


class TestAFiftyTwoWeekFiscalYear:
    """NVIDIA's quarters end on a Sunday, so the year-ago column moves.

    The quarter ended 26 July 2026; the same quarter a year before ended
    27 July 2025. Matching the prior period on an exact date found nothing,
    so every year-ago figure came back blank and the checks that need two
    periods — gross margin's trend, operating leverage, the EPS-against-
    profit read — all said they were waiting on numbers that were there.
    """

    DRIFTED = facts(
        duration("RevenueFromContractWithCustomerExcludingAssessedTax", [
            ("2026-04-27", "2026-07-26", 96221, "10-Q", "2026-08-27"),
            ("2025-04-28", "2025-07-27", 46743, "10-Q", "2025-08-27")]),
        duration("CostOfRevenue", [
            ("2026-04-27", "2026-07-26", 24079, "10-Q", "2026-08-27"),
            ("2025-04-28", "2025-07-27", 12890, "10-Q", "2025-08-27")]),
        duration("OperatingExpenses", [
            ("2026-04-27", "2026-07-26", 8408, "10-Q", "2026-08-27"),
            ("2025-04-28", "2025-07-27", 5413, "10-Q", "2025-08-27")]),
        duration("OperatingIncomeLoss", [
            ("2026-04-27", "2026-07-26", 63734, "10-Q", "2026-08-27"),
            ("2025-04-28", "2025-07-27", 28440, "10-Q", "2025-08-27")]),
    )

    def test_the_year_ago_column_is_found_a_day_off(self):
        found = qf.latest_quarter("NVDA", facts=self.DRIFTED)
        assert found["fields"]["revP"]["value"] == 46743
        assert found["fields"]["revP"]["end"] == "2025-07-27"

    def test_the_checks_that_need_two_periods_can_now_run(self):
        import quarter_checks
        found = qf.latest_quarter("NVDA", facts=self.DRIFTED)
        figures = {k: v["value"] for k, v in found["fields"].items()}
        graded = {c["key"]: c for c in quarter_checks.run(figures)["checks"]}
        assert not graded["operating_leverage"]["skipped"]
        # 49,478 / 46,743 = 105.8511%, which rounds to 105.9. The original
        # walkthrough wrote 105.8 by truncating; rounding is the right read.
        assert graded["operating_leverage"]["value"] == "+105.9% / +55.3%"
        assert graded["operating_leverage"]["verdict"] == "clean"

    def test_a_neighbouring_quarter_is_never_mistaken_for_the_year_ago_one(self):
        """Ten days of slack, and ninety between quarters."""
        near = facts(duration("RevenueFromContractWithCustomerExcludingAssessedTax", [
            ("2026-04-27", "2026-07-26", 96221, "10-Q", "2026-08-27"),
            ("2025-07-28", "2025-10-26", 57006, "10-Q", "2025-11-19")]))
        found = qf.latest_quarter("NVDA", facts=near)
        assert "revP" not in found["fields"]

    def test_the_closest_match_wins_when_two_are_in_range(self):
        pair = facts(duration("RevenueFromContractWithCustomerExcludingAssessedTax", [
            ("2026-04-27", "2026-07-26", 96221, "10-Q", "2026-08-27"),
            ("2025-04-28", "2025-07-27", 46743, "10-Q", "2025-08-27"),
            ("2025-04-22", "2025-07-21", 11111, "10-Q", "2025-08-20")]))
        found = qf.latest_quarter("NVDA", facts=pair)
        assert found["fields"]["revP"]["value"] == 46743


class TestTheFilerWhoPrintsNoSubtotalAtAll:
    """KLA's income statement runs revenue, cost of revenue, R&D, SG&A,
    straight to operating income. There is no "total operating expenses" line
    on it, KLA has never tagged OperatingExpenses in its life, and its
    CostsAndExpenses stops in 2015 — so the check sat waiting forever for a
    figure the filing does not contain and never will.

    KLA's available pre-tax concept is not operating income. In the current
    quarter R&D plus SG&A is already 679,897, greater than the old 670,645
    derivation. The safe result is to leave the check ungraded.
    """

    KLA = facts(
        duration("RevenueFromContractWithCustomerExcludingAssessedTax", [
            ("2026-01-01", "2026-03-31", 3415078, "10-Q", "2026-05-01"),
            ("2025-01-01", "2025-03-31", 3063029, "10-Q", "2025-05-01")]),
        duration("CostOfRevenue", [
            ("2026-01-01", "2026-03-31", 1327672, "10-Q", "2026-05-01"),
            ("2025-01-01", "2025-03-31", 1175689, "10-Q", "2025-05-01")]),
        duration("ResearchAndDevelopmentExpense", [
            ("2026-01-01", "2026-03-31", 388763, "10-Q", "2026-05-01"),
            ("2025-01-01", "2025-03-31", 338043, "10-Q", "2025-05-01")]),
        duration("SellingGeneralAndAdministrativeExpense", [
            ("2026-01-01", "2026-03-31", 291134, "10-Q", "2026-05-01"),
            ("2025-01-01", "2025-03-31", 284864, "10-Q", "2025-05-01")]),
        # KLA does not tag OperatingIncomeLoss AT ALL. Its operating income
        # is only reachable through the pre-tax concept, which is why looking
        # for the literal tag found nothing and the derivation bailed even
        # though the rebuilt statement — which reads the resolved row —
        # printed the subtotal perfectly.
        duration(qf.PRETAX_TAGS[0], [
            ("2026-01-01", "2026-03-31", 1416761, "10-Q", "2026-05-01"),
            ("2025-01-01", "2025-03-31", 1264433, "10-Q", "2025-05-01")]),
    )

    def test_this_filer_really_does_not_tag_operating_income(self):
        """The fixture is only worth anything if it keeps that true."""
        assert qf._facts_for("OperatingIncomeLoss", self.KLA) == []
        assert "opinc" not in qf.latest_quarter("KLAC", facts=self.KLA)["fields"]

    def test_pretax_income_is_not_used_to_invent_operating_expenses(self):
        filed = qf.latest_quarter("KLAC", facts=self.KLA)
        assert "opex" not in filed["fields"]
        assert "opexP" not in filed["fields"]

    def test_an_unprovable_check_stays_ungraded(self):
        import quarter_checks as qc
        filed = qf.latest_quarter("KLAC", facts=self.KLA)
        figures = {k: v["value"] for k, v in filed["fields"].items()}
        check = [c for c in qc.run(figures, filed["profile"])["checks"]
                 if c["key"] == "operating_leverage"][0]
        assert check["verdict"] is None
        assert check["skipped"]

    def test_the_rebuilt_statement_keeps_pretax_in_its_own_row(self):
        st = qf.statements(self.KLA, "2026-03-31", "2025-03-31")
        by_key = {r["key"]: r for r in st["income"]["rows"]}
        assert "opex" not in by_key and "opinc" not in by_key
        assert by_key["pretax"]["now"] == 1416761
        assert by_key["rnd"]["now"] + by_key["admin"]["now"] == 679897

    def test_the_combined_total_is_still_preferred_when_a_filer_prints_one(self):
        """Meta's shape must not change: it prints one "Total costs and
        expenses" including cost of revenue, and that subtraction stays the
        first choice."""
        both = facts(
            duration("Revenues", [
                ("2026-04-01", "2026-06-30", 60801, "10-Q", "2026-07-30")]),
            duration("CostOfRevenue", [
                ("2026-04-01", "2026-06-30", 11330, "10-Q", "2026-07-30")]),
            duration("CostsAndExpenses", [
                ("2026-04-01", "2026-06-30", 42026, "10-Q", "2026-07-30")]),
            duration("OperatingIncomeLoss", [
                ("2026-04-01", "2026-06-30", 18775, "10-Q", "2026-07-30")]),
        )
        filed = qf.latest_quarter("META", facts=both)
        assert filed["fields"]["opex"]["value"] == 42026 - 11330
        assert "CostsAndExpenses" in filed["fields"]["opex"]["tag"]

    def test_a_bank_is_never_handed_a_derived_number(self):
        """No cost of revenue, and "operating income" would be pre-tax
        income — the subtraction would give a confident wrong answer rather
        than nothing. Both derivations gate on cost of revenue."""
        bank = facts(
            duration("RevenuesNetOfInterestExpense", [
                ("2026-04-01", "2026-06-30", 57347, "10-Q", "2026-08-04")]),
            duration("NoninterestExpense", [
                ("2026-04-01", "2026-06-30", 27316, "10-Q", "2026-08-04")]),
        )
        fields = {}
        qf._derive_operating_expenses(bank, fields, "2026-06-30", "2025-06-30")
        assert fields == {}

    def test_three_different_periods_are_not_a_subtotal(self):
        """Revenue from this quarter minus a cost of revenue from another is
        not an operating expense figure, it is noise."""
        mismatched = facts(
            duration("Revenues", [
                ("2026-01-01", "2026-03-31", 1000, "10-Q", "2026-05-01")]),
            duration("CostOfRevenue", [
                ("2025-10-01", "2025-12-31", 400, "10-K", "2026-02-01")]),
            duration("OperatingIncomeLoss", [
                ("2026-01-01", "2026-03-31", 300, "10-Q", "2026-05-01")]),
        )
        fields = {}
        qf._derive_operating_expenses(mismatched, fields, "2026-03-31", None)
        assert "opex" not in fields

    def test_same_end_but_different_starts_are_not_a_subtotal(self):
        mismatched = facts(
            duration("Revenues", [
                ("2026-04-01", "2026-06-30", 1000, "10-Q", "2026-08-01")]),
            duration("CostOfRevenue", [
                ("2026-04-01", "2026-06-30", 400, "10-Q", "2026-08-01")]),
            duration("CostsAndExpenses", [
                ("2026-03-25", "2026-06-30", 700, "10-Q", "2026-08-01")]),
        )
        assert "opex" not in qf.latest_quarter("EDGE", facts=mismatched)["fields"]

    def test_a_negative_derived_expense_is_rejected(self):
        impossible = facts(
            duration("Revenues", [
                ("2026-04-01", "2026-06-30", 1000, "10-Q", "2026-08-01")]),
            duration("CostOfRevenue", [
                ("2026-04-01", "2026-06-30", 400, "10-Q", "2026-08-01")]),
            duration("OperatingIncomeLoss", [
                ("2026-04-01", "2026-06-30", 700, "10-Q", "2026-08-01")]),
        )
        assert "opex" not in qf.latest_quarter("EDGE", facts=impossible)["fields"]

    def test_a_derived_subtotal_cannot_be_less_than_visible_components(self):
        contradictory = facts(
            duration("Revenues", [
                ("2026-04-01", "2026-06-30", 1000, "10-Q", "2026-08-01")]),
            duration("CostOfRevenue", [
                ("2026-04-01", "2026-06-30", 400, "10-Q", "2026-08-01")]),
            duration("CostsAndExpenses", [
                ("2026-04-01", "2026-06-30", 650, "10-Q", "2026-08-01")]),
            duration("ResearchAndDevelopmentExpense", [
                ("2026-04-01", "2026-06-30", 200, "10-Q", "2026-08-01")]),
            duration("SellingGeneralAndAdministrativeExpense", [
                ("2026-04-01", "2026-06-30", 100, "10-Q", "2026-08-01")]),
        )
        assert "opex" not in qf.latest_quarter("EDGE", facts=contradictory)["fields"]
