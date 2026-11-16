"""The 10-Q drill fills every box for KLA's shape of filing.

KLA prints no operating-income subtotal and no total-operating-expenses line,
and its fiscal year starts in July, so the September 10-Q's cash flow covers
exactly three months. Before this, four inputs and two checks stayed empty.
"""
import quarter_facts as qf

Q = ("2026-07-01", "2026-09-30")
QP = ("2025-07-01", "2025-09-30")


def _f(span, val, filed="2026-10-30", form="10-Q"):
    return {"start": span[0], "end": span[1], "val": val, "filed": filed, "form": form}


def _tag(*facts, unit="USD"):
    return {"units": {unit: list(facts)}}


def kla_facts(sga=True):
    g = {
        "RevenueFromContractWithCustomerExcludingAssessedTax":
            _tag(_f(Q, 4_136_015_000), _f(QP, 3_209_696_000)),
        "CostOfRevenue": _tag(_f(Q, 1_577_791_000), _f(QP, 1_243_070_000)),
        "ResearchAndDevelopmentExpense": _tag(_f(Q, 395_096_000), _f(QP, 360_461_000)),
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest":
            _tag(_f(Q, 1_824_082_000), _f(QP, 1_309_476_000)),
        "NetIncomeLoss": _tag(_f(Q, 1_548_588_000), _f(QP, 1_121_040_000)),
        "NetCashProvidedByUsedInOperatingActivities":
            _tag(_f(Q, 1_610_000_000), _f(QP, 995_000_000)),
        "PaymentsToAcquirePropertyPlantAndEquipment":
            _tag(_f(Q, 82_000_000), _f(QP, 71_000_000)),
    }
    if sga:
        g["SellingGeneralAndAdministrativeExpense"] = _tag(_f(Q, 301_458_000), _f(QP, 268_988_000))
    else:
        g["SellingAndMarketingExpense"] = _tag(_f(Q, 200_000_000), _f(QP, 180_000_000))
        g["GeneralAndAdministrativeExpense"] = _tag(_f(Q, 101_458_000), _f(QP, 88_988_000))
    return {"entityName": "KLA CORP", "facts": {"us-gaap": g}}


def test_operating_expenses_and_income_are_computed_from_the_lines():
    f = qf.latest_quarter("KLAC", facts=kla_facts())["fields"]
    assert f["opex"]["value"] == 696_554_000 and f["opex"]["derived"]
    assert f["opexP"]["value"] == 629_449_000
    assert f["opinc"]["value"] == 4_136_015_000 - 1_577_791_000 - 696_554_000
    assert f["opinc"]["derived"] and f["opincP"]["value"] == 1_337_177_000


def test_selling_and_admin_lines_are_added_when_sga_is_not_filed():
    f = qf.latest_quarter("KLAC", facts=kla_facts(sga=False))["fields"]
    assert f["opex"]["value"] == 696_554_000


def test_sga_is_not_double_counted_with_its_own_parts():
    facts = kla_facts()
    facts["facts"]["us-gaap"]["SellingAndMarketingExpense"] = _tag(_f(Q, 200_000_000), _f(QP, 1))
    f = qf.latest_quarter("KLAC", facts=facts)["fields"]
    assert f["opex"]["value"] == 696_554_000


def test_fiscal_first_quarter_cash_flow_fills():
    f = qf.latest_quarter("KLAC", facts=kla_facts())["fields"]
    assert f["cfo"]["value"] == 1_610_000_000
    assert f["capex"]["value"] == 82_000_000
    assert f["niy"]["value"] == 1_548_588_000
    assert f["revYtd"]["value"] == 4_136_015_000


def test_year_to_date_prefers_the_longer_span():
    facts = kla_facts()
    g = facts["facts"]["us-gaap"]
    six = ("2026-04-01", "2026-09-30")
    g["NetIncomeLoss"]["units"]["USD"].append(_f(six, 2_900_000_000))
    f = qf.latest_quarter("KLAC", facts=facts)["fields"]
    assert f["ni"]["value"] == 1_548_588_000          # the quarter
    assert f["niy"]["value"] == 2_900_000_000         # the year to date


def test_statement_shows_computed_rows():
    st = qf.statements(kla_facts(), "2026-09-30", "2025-09-30")
    keys = [r["key"] for r in st["income"]["rows"]]
    assert keys.index("admin") < keys.index("opex") < keys.index("opinc") < keys.index("pretax")
    opinc = next(r for r in st["income"]["rows"] if r["key"] == "opinc")
    assert opinc["derived"] and opinc["now"] == 1_861_670_000
    assert [r["key"] for r in st["cash"]["rows"]][:1] == ["niy"]


def test_banks_never_get_a_rebuilt_operating_income():
    fields = {"rev": {"value": 1, "end": "x"}, "cogs": {"value": 0, "end": "x"},
              "opex": {"value": 0, "end": "x"}}
    qf._derive_operating_income(fields, "bank")
    assert "opinc" not in fields


def test_page_has_the_glance_strip_and_folding_inputs():
    html = open("templates/fundamentals.html").read()
    for needle in ("function qdGlance(", "Growing?", "Real cash?", "PEG",
                   'id="q-fields"', "function qdFold(", "ttm_eps_prior"):
        assert needle in html


def test_peg_is_always_shown_with_a_reason():
    html = open("templates/fundamentals.html").read()
    assert "function qdPegText(" in html
    for reason in ("n/m (EPS falling)", "n/m (losing money)", "under 2 yrs of EPS", "needs price"):
        assert reason in html
