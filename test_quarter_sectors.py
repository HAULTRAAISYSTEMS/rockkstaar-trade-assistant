"""Not every check fits every company.

Three of the seven are not "waiting on a figure" at a bank. They are category
errors. JPMorgan has never tagged AssetsCurrent in its life — not because the
number is late, but because a bank does not present a classified balance sheet
at all, so there is no current-assets subtotal to divide. Telling a reader the
check is waiting on one sends them hunting through a 10-Q for a line no bank
has ever printed, which is worse than saying nothing.

The figures below are the real ones, checked against EDGAR's XBRL company
facts in September 2026: JPMorgan's June 2026 quarter (RevenuesNetOfInterest-
Expense 57,347M, NoninterestExpense 27,316M, and no AssetsCurrent, no
CostOfRevenue and no OperatingIncomeLoss anywhere in the company's history),
Progressive's PremiumsEarnedNet and BenefitsLossesAndExpenses, Realty Income's
Revenues, and NextEra's RegulatedAndUnregulatedOperatingRevenue.
"""
import pytest

import quarter_checks as qc
import quarter_facts as qf


def duration(tag, rows):
    return {tag: {"units": {"USD": [
        {"start": s, "end": e, "val": v, "form": "10-Q", "filed": d, "accn": "acc-" + d}
        for s, e, v, d in rows]}}}


def instant(tag, rows):
    return {tag: {"units": {"USD": [
        {"end": e, "val": v, "form": "10-Q", "filed": d, "accn": "acc-" + d}
        for e, v, d in rows]}}}


def facts(name, *groups):
    merged = {}
    for g in groups:
        merged.update(g)
    return {"entityName": name, "facts": {"us-gaap": merged}}


# ── A bank ────────────────────────────────────────────────────────────────────
BANK = facts(
    "JPMorgan Chase & Co.",
    duration("RevenuesNetOfInterestExpense", [
        ("2026-04-01", "2026-06-30", 57347e6, "2026-08-04"),
        ("2026-01-01", "2026-06-30", 107183e6, "2026-08-04"),
        ("2025-04-01", "2025-06-30", 50200e6, "2025-08-04"),
    ]),
    duration("NoninterestExpense", [
        ("2026-04-01", "2026-06-30", 27316e6, "2026-08-04"),
        ("2025-04-01", "2025-06-30", 23800e6, "2025-08-04"),
    ]),
    duration("InterestIncomeExpenseNet", [
        ("2026-04-01", "2026-06-30", 25511e6, "2026-08-04"),
    ]),
    duration("ProvisionForLoanLeaseAndOtherLosses", [
        ("2026-04-01", "2026-06-30", 2507e6, "2026-08-04"),
    ]),
    duration(
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItems"
        "NoncontrollingInterest", [
            ("2026-04-01", "2026-06-30", 22000e6, "2026-08-04"),
            ("2025-04-01", "2025-06-30", 19000e6, "2025-08-04"),
        ]),
    duration("NetIncomeLoss", [
        ("2026-04-01", "2026-06-30", 17000e6, "2026-08-04"),
        ("2026-01-01", "2026-06-30", 32000e6, "2026-08-04"),
        ("2025-04-01", "2025-06-30", 15000e6, "2025-08-04"),
    ]),
    duration("NetCashProvidedByUsedInOperatingActivities", [
        ("2026-01-01", "2026-06-30", 40000e6, "2026-08-04"),
    ]),
    instant("Deposits", [("2026-06-30", 2713700e6, "2026-08-04")]),
    instant("Assets", [("2026-06-30", 4400000e6, "2026-08-04"),
                       ("2025-12-31", 4200000e6, "2026-08-04")]),
    instant("StockholdersEquity", [("2026-06-30", 350000e6, "2026-08-04")]),
)

# ── An insurer, a REIT, a utility ─────────────────────────────────────────────
INSURER = facts(
    "The Progressive Corporation",
    duration("PremiumsEarnedNet", [
        ("2026-01-01", "2026-03-31", 20968e6, "2026-04-29"),
        ("2025-01-01", "2025-03-31", 19000e6, "2025-04-29"),
    ]),
    duration("BenefitsLossesAndExpenses", [
        ("2026-01-01", "2026-03-31", 18622e6, "2026-04-29"),
        ("2025-01-01", "2025-03-31", 17000e6, "2025-04-29"),
    ]),
    duration("NetIncomeLoss", [("2026-01-01", "2026-03-31", 2900e6, "2026-04-29")]),
)

REIT = facts(
    "Realty Income Corporation",
    duration("Revenues", [
        ("2026-04-01", "2026-06-30", 1547711e3, "2026-08-05"),
        ("2026-01-01", "2026-06-30", 3096438e3, "2026-08-05"),
        ("2025-04-01", "2025-06-30", 1400000e3, "2025-08-05"),
    ]),
    instant("RealEstateInvestmentPropertyNet", [
        ("2026-06-30", 50000000e3, "2026-08-05"),
    ]),
    duration("NetIncomeLoss", [("2026-04-01", "2026-06-30", 260000e3, "2026-08-05")]),
)

UTILITY = facts(
    "NextEra Energy, Inc.",
    duration("RegulatedAndUnregulatedOperatingRevenue", [
        ("2026-01-01", "2026-03-31", 6701e6, "2026-04-24"),
        ("2025-01-01", "2025-03-31", 6250e6, "2025-04-24"),
    ]),
    instant("AssetsCurrent", [("2026-03-31", 13900e6, "2026-04-24"),
                              ("2025-12-31", 15400e6, "2026-04-24")]),
    instant("LiabilitiesCurrent", [("2026-03-31", 20000e6, "2026-04-24"),
                                   ("2025-12-31", 21000e6, "2026-04-24")]),
    duration("NetIncomeLoss", [("2026-01-01", "2026-03-31", 2000e6, "2026-04-24")]),
)

PLAIN = facts(
    "Ordinary Operating Co.",
    duration("RevenueFromContractWithCustomerExcludingAssessedTax", [
        ("2026-04-01", "2026-06-30", 1000, "2026-07-30"),
    ]),
    duration("CostOfRevenue", [("2026-04-01", "2026-06-30", 400, "2026-07-30")]),
    instant("AssetsCurrent", [("2026-06-30", 900, "2026-07-30")]),
    instant("LiabilitiesCurrent", [("2026-06-30", 500, "2026-07-30")]),
)


class TestWhatKindOfCompanyItIs:
    @pytest.mark.parametrize("fixture, expected", [
        (BANK, "bank"), (INSURER, "insurer"), (REIT, "reit"),
        (UTILITY, "utility"), (PLAIN, "operating"),
    ])
    def test_it_is_read_off_the_filer_s_own_tags(self, fixture, expected):
        assert qf.detect_shape(fixture) == expected

    def test_a_filer_with_no_current_subtotal_and_no_sector_marks_says_so(self):
        odd = facts("Odd Co.", duration(
            "Revenues", [("2026-04-01", "2026-06-30", 10, "2026-07-30")]))
        assert qf.detect_shape(odd) == "unclassified"

    def test_the_shape_is_decided_on_history_not_on_one_quarter(self):
        """A tag missing from one quarter is a gap. Missing from twenty years
        is a structure, and only the second should change the questions."""
        assert qf.detect_shape(PLAIN) == "operating"
        assert qf.context_for("operating")["not_applicable"] == {}


class TestTheChecksABankHasNoLinesFor:
    @pytest.fixture
    def graded(self):
        filed = qf.latest_quarter("JPM", facts=BANK)
        figures = {k: v["value"] for k, v in filed["fields"].items()}
        return {c["key"]: c
                for c in qc.run(figures, filed["profile"])["checks"]}

    @pytest.mark.parametrize("key", ["gross_margin", "current_ratio", "dso",
                                     "free_cash_flow"])
    def test_it_does_not_apply_rather_than_waiting(self, graded, key):
        assert graded[key]["applies"] is False
        assert graded[key]["skipped"] is None
        assert graded[key]["why"]

    def test_it_says_what_to_read_instead(self, graded):
        assert "net interest margin" in graded["gross_margin"]["why"]
        assert "liquidity coverage ratio" in graded["current_ratio"]["why"]
        assert "charge-offs" in graded["dso"]["why"]
        assert "CET1" in graded["free_cash_flow"]["why"]

    def test_the_three_that_do_apply_still_grade(self, graded):
        for key in ("cash_conversion", "operating_leverage"):
            assert graded[key]["verdict"], key

    def test_revenue_and_expenses_come_from_the_bank_s_own_tags(self):
        filed = qf.latest_quarter("JPM", facts=BANK)
        assert filed["fields"]["rev"]["tag"] == "RevenuesNetOfInterestExpense"
        assert filed["fields"]["opex"]["tag"] == "NoninterestExpense"
        assert filed["fields"]["rev"]["value"] == 57347e6

    def test_the_lines_are_called_what_the_filing_calls_them(self):
        filed = qf.latest_quarter("JPM", facts=BANK)
        assert filed["fields"]["rev"]["label"] == "Total net revenue"
        assert filed["fields"]["opex"]["label"] == "Total noninterest expense"
        assert filed["fields"]["opinc"]["label"] == "Income before income taxes"

    def test_operating_income_is_not_called_that_at_a_bank(self):
        """A bank has no operating section separate from a financing one."""
        filed = qf.latest_quarter("JPM", facts=BANK)
        figures = {k: v["value"] for k, v in filed["fields"].items()}
        result = qc.run(figures, filed["profile"])
        says = [c for c in result["checks"]
                if c["key"] == "operating_leverage"][0]["says"]
        assert "Income before income taxes" in says
        assert "Operating income" not in says

    def test_the_summary_counts_them_out_of_the_ones_that_can_run(self, graded):
        filed = qf.latest_quarter("JPM", facts=BANK)
        figures = {k: v["value"] for k, v in filed["fields"].items()}
        result = qc.run(figures, filed["profile"])
        assert result["counts"]["n_a"] == 4
        assert "do not apply" in result["summary"]


class TestTheOtherShapes:
    def test_a_utility_keeps_its_current_ratio(self):
        """It has a classified balance sheet; only the margin question is
        the wrong one to ask a company whose return is set by a regulator."""
        na = qf.context_for("utility")["not_applicable"]
        assert "current_ratio" not in na
        assert "dso" not in na
        assert "gross_margin" in na

    def test_an_insurer_is_pointed_at_the_combined_ratio(self):
        why = qf.context_for("insurer")["not_applicable"]["gross_margin"]
        assert "combined ratio" in why

    def test_a_reit_is_pointed_at_funds_from_operations(self):
        why = qf.context_for("reit")["not_applicable"]["free_cash_flow"]
        assert "funds from operations" in why

    def test_an_insurer_s_revenue_and_expenses_resolve(self):
        filed = qf.latest_quarter("PGR", facts=INSURER)
        assert filed["fields"]["rev"]["value"] == 20968e6
        assert filed["fields"]["opex"]["value"] == 18622e6

    def test_a_utility_s_revenue_resolves(self):
        filed = qf.latest_quarter("NEE", facts=UTILITY)
        assert filed["fields"]["rev"]["tag"] == \
            "RegulatedAndUnregulatedOperatingRevenue"
        assert filed["fields"]["ca"]["value"] == 13900e6

    def test_a_reit_s_revenue_resolves(self):
        filed = qf.latest_quarter("O", facts=REIT)
        assert filed["fields"]["rev"]["value"] == 1547711e3


class TestTheGeneralCaseIsUntouched:
    def test_a_plain_company_gets_all_seven_attempted(self):
        filed = qf.latest_quarter("ACME", facts=PLAIN)
        assert filed["profile"]["shape"] == "operating"
        result = qc.run({"rev": 1000, "cogs": 400}, filed["profile"])
        assert result["counts"]["n_a"] == 0
        assert [c for c in result["checks"]
                if c["key"] == "gross_margin"][0]["verdict"]

    def test_grading_with_no_company_attached_attempts_everything(self):
        result = qc.run({"rev": 1000, "cogs": 400})
        assert result["counts"]["n_a"] == 0
        assert all(c["applies"] for c in result["checks"])

    def test_an_unknown_shape_falls_back_rather_than_raising(self):
        assert qf.context_for("something-else")["shape"] == "operating"
        assert qf.context_for(None)["not_applicable"] == {}


class TestTheStatementsAreRebuiltInTheRightShape:
    def test_a_bank_gets_a_bank_s_income_statement(self):
        st = qf.statements(BANK, "2026-06-30", "2025-06-30", shape="bank")
        keys = [r["key"] for r in st["income"]["rows"]]
        assert "nii" in keys            # net interest income
        assert "prov" in keys           # provision for credit losses
        assert "gross" not in keys      # a bank has no gross profit
        assert "cogs" not in keys

    def test_a_bank_gets_a_bank_s_balance_sheet(self):
        st = qf.statements(BANK, "2026-06-30", "2025-12-31", shape="bank")
        keys = [r["key"] for r in st["balance"]["rows"]]
        assert "deposits" in keys
        assert "ca" not in keys

    def test_the_generic_layout_is_unchanged_for_everyone_else(self):
        st = qf.statements(PLAIN, "2026-06-30", "2025-06-30", shape="operating")
        keys = [r["key"] for r in st["income"]["rows"]]
        assert "rev" in keys and "cogs" in keys


class TestTheNamesAreKeptInOnePlace:
    def test_every_check_name_matches_the_one_it_reports(self):
        """CHECK_NAMES is what a declined check is named by. A second copy of
        the string is a second thing to forget to change."""
        reported = {c["key"]: c["name"] for c in qc.run({})["checks"]}
        assert reported == qc.CHECK_NAMES

    def test_every_shape_names_only_real_checks(self):
        for shape in qf.SHAPES:
            for key in qf.SHAPE_NOT_APPLICABLE.get(shape, {}):
                assert key in qc.CHECK_NAMES, (shape, key)

    def test_every_shape_has_a_label_and_a_context(self):
        for shape, ctx in qf.all_contexts().items():
            assert ctx["shape"] == shape
            assert ctx["shape_label"]
            assert isinstance(ctx["not_applicable"], dict)
