"""Yahoo Finance fallback for issuers FMP's plan leaves out (SK hynix, in KRW)."""
import pandas as pd
import pytest

import quarter_fmp
import quarter_yahoo

Q = ["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"]


def _frame(rows, cols):
    return pd.DataFrame({pd.Timestamp(c): {k: v[i] for k, v in rows.items()}
                         for i, c in enumerate(cols)})


INC = _frame({
    "Total Revenue": [24e12, 22e12, 17e12, 19e12, 16e12],
    "Cost Of Revenue": [9e12, 9e12, 7e12, 8e12, 7e12],
    "Operating Expense": [3e12, 3e12, 2.5e12, 2.8e12, 2.4e12],
    "Operating Income": [12e12, 10e12, 7.5e12, 8.2e12, 6.6e12],
    "Net Income": [10e12, 8e12, 6e12, 7e12, 5.5e12],
    "Diluted Average Shares": [690e6] * 5,
    "Diluted EPS": [14500.0, 11600.0, 8700.0, 10100.0, 8000.0],
}, Q)
BAL = _frame({
    "Current Assets": [60e12, 55e12, 50e12, 48e12, 45e12],
    "Current Liabilities": [25e12, 24e12, 23e12, 22e12, 21e12],
    "Accounts Receivable": [15e12, 14e12, 12e12, 13e12, 11e12],
    "Long Term Debt": [18e12, 19e12, 20e12, 21e12, 22e12],
    "Stockholders Equity": [100e12, 92e12, 85e12, 80e12, 75e12],
}, Q)
CF = _frame({
    "Operating Cash Flow": [13e12, 11e12, 9e12, 10e12, 8e12],
    "Capital Expenditure": [-6e12, -5e12, -5e12, -4e12, -4e12],
}, Q)
ANN = _frame({"Diluted EPS": [30000.0, 20000.0]}, ["2025-12-31", "2024-12-31"])


class FakeTicker:
    def __init__(self, sym):
        self.sym = sym
        home = sym == "000660.KS"
        empty = pd.DataFrame()
        self.quarterly_income_stmt = INC if home else empty
        self.quarterly_balance_sheet = BAL if home else empty
        self.quarterly_cashflow = CF if home else empty
        self.income_stmt = ANN if home else empty
        self.info = ({"financialCurrency": "KRW", "longName": "SK hynix Inc."} if home
                     else {"trailingPE": 20.0, "currentPrice": 80.0})


@pytest.fixture
def yahoo(monkeypatch):
    monkeypatch.setattr(quarter_yahoo, "_ticker", FakeTicker)
    quarter_yahoo._CACHE.clear()
    quarter_yahoo._LAST_FAIL.clear()


def test_uses_home_listing_and_matches_year_ago_quarter(yahoo):
    w = quarter_yahoo.walkthrough("skhy")
    assert w["source"] == "yahoo" and w["source_symbol"] == "000660.KS"
    assert w["currency"] == "KRW" and w["company"] == "SK hynix Inc."
    assert w["period_end"] == "2026-09-30" and w["prior_end"] == "2025-09-30"
    f = w["figures"]
    assert f["rev"] == 24e12 and f["revP"] == 16e12
    assert f["eps"] == 14500.0 and f["epsP"] == 8000.0


def test_balance_prior_is_last_fiscal_year_end(yahoo):
    f = quarter_yahoo.walkthrough("SKHY")["figures"]
    assert f["ca"] == 60e12 and f["caP"] == 48e12


def test_cash_flow_is_year_to_date(yahoo):
    f = quarter_yahoo.walkthrough("SKHY")["figures"]
    assert f["cfo"] == 33e12 and f["capex"] == 16e12
    assert f["niy"] == 24e12 and f["revYtd"] == 63e12


def test_pe_from_us_listing_and_growth_from_annual_eps(yahoo):
    w = quarter_yahoo.walkthrough("SKHY")
    assert w["ttm_eps"]["value"] == 4.0
    # Five quarters only, so growth comes from the two fiscal years: +50%.
    assert w["ttm_eps_prior"]["value"] == pytest.approx(4.0 / 1.5, abs=1e-3)


def test_missing_year_ago_stays_blank(monkeypatch, yahoo):
    short = INC[INC.columns[:3]]

    class Short(FakeTicker):
        def __init__(self, sym):
            super().__init__(sym)
            if sym == "000660.KS":
                self.quarterly_income_stmt = short
    monkeypatch.setattr(quarter_yahoo, "_ticker", Short)
    w = quarter_yahoo.walkthrough("SKHY")
    assert w["prior_end"] is None and "revP" not in w["figures"]


def test_no_data_reports_failure(monkeypatch):
    class Empty(FakeTicker):
        def __init__(self, sym):
            super().__init__("none")
    monkeypatch.setattr(quarter_yahoo, "_ticker", Empty)
    quarter_yahoo._CACHE.clear()
    assert quarter_yahoo.walkthrough("ZZZZ") is None
    assert "Yahoo Finance" in quarter_yahoo.last_failure("ZZZZ")


def test_endpoint_falls_back_to_yahoo_after_fmp(monkeypatch, yahoo):
    import quarter_facts
    import web_app
    monkeypatch.setattr(quarter_facts, "fetch_facts", lambda t: (None, None))
    monkeypatch.setattr(quarter_facts, "explain_gap", lambda *a: "no 10-Q")
    monkeypatch.setattr(quarter_fmp, "walkthrough", lambda t: None)
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    d = c.get("/api/quarter/walkthrough/SKHY").get_json()
    assert d["available"] and d["source"] == "yahoo" and d["figures"]["rev"] == 24e12
