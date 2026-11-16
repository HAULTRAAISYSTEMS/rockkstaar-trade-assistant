"""FMP fallback for foreign issuers with no 10-Q (TSMC, in NT$)."""
import pytest

import quarter_fmp


def _q(date, period, year, rev, eps):
    return {"date": date, "period": period, "fiscalYear": year, "reportedCurrency": "TWD",
            "revenue": rev, "costOfRevenue": rev * 0.4, "operatingExpenses": rev * 0.1,
            "operatingIncome": rev * 0.5, "netIncome": rev * 0.45,
            "weightedAverageShsOutDil": 25_930_000_000, "epsDiluted": eps}


INC = [
    _q("2026-09-30", "Q3", "2026", 1_000e9, 17.0),
    _q("2026-06-30", "Q2", "2026", 950e9, 16.0),
    _q("2026-03-31", "Q1", "2026", 900e9, 15.0),
    _q("2025-12-31", "Q4", "2025", 870e9, 14.0),
    _q("2025-09-30", "Q3", "2025", 760e9, 13.0),
    _q("2025-06-30", "Q2", "2025", 730e9, 12.0),
    _q("2025-03-31", "Q1", "2025", 700e9, 11.0),
    _q("2024-12-31", "Q4", "2024", 690e9, 10.0),
]
BAL = [
    {"date": "2026-09-30", "period": "Q3", "fiscalYear": "2026", "totalCurrentAssets": 3000e9,
     "totalCurrentLiabilities": 1200e9, "accountsReceivables": 300e9,
     "longTermDebt": 900e9, "totalStockholdersEquity": 4500e9},
    {"date": "2025-12-31", "period": "Q4", "fiscalYear": "2025", "totalCurrentAssets": 2800e9,
     "totalCurrentLiabilities": 1100e9, "accountsReceivables": 280e9,
     "longTermDebt": 950e9, "totalStockholdersEquity": 4000e9},
]
CF = [{"date": d, "operatingCashFlow": 500e9, "capitalExpenditure": -300e9}
      for d in ("2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31")]


@pytest.fixture
def fmp(monkeypatch):
    calls = []

    def fake(endpoint, params=None, ticker=""):
        calls.append(endpoint)
        return {"income-statement": INC, "balance-sheet-statement": BAL,
                "cash-flow-statement": CF, "quote": [{"price": 300.0, "pe": 25.0}],
                "profile": [{"companyName": "Taiwan Semiconductor"}]}.get(endpoint)

    monkeypatch.setattr(quarter_fmp, "_get", fake)
    quarter_fmp._CACHE.clear()
    return calls


def test_figures_come_from_the_latest_quarter_and_the_same_quarter_last_year(fmp):
    w = quarter_fmp.walkthrough("tsm")
    f = w["figures"]
    assert w["source"] == "fmp" and w["currency"] == "TWD"
    assert w["period_end"] == "2026-09-30" and w["prior_end"] == "2025-09-30"
    assert f["rev"] == 1_000e9 and f["revP"] == 760e9
    assert f["eps"] == 17.0 and f["epsP"] == 13.0


def test_balance_prior_is_last_fiscal_year_end(fmp):
    f = quarter_fmp.walkthrough("TSM")["figures"]
    assert f["ca"] == 3000e9 and f["caP"] == 2800e9


def test_cash_flow_is_year_to_date_and_matched_to_income(fmp):
    f = quarter_fmp.walkthrough("TSM")["figures"]
    assert f["cfo"] == 1500e9                        # three 2026 quarters
    assert f["capex"] == 900e9                       # made positive
    assert f["niy"] == (1000e9 + 950e9 + 900e9) * 0.45
    assert f["revYtd"] == 2850e9


def test_pe_is_in_usd_and_peg_growth_from_reported_eps(fmp):
    w = quarter_fmp.walkthrough("TSM")
    assert w["ttm_eps"]["value"] == 12.0             # 300 / 25, per US share
    growth = (17 + 16 + 15 + 14) / (13 + 12 + 11 + 10) - 1
    assert round(w["ttm_eps_prior"]["value"], 3) == round(12.0 / (1 + growth), 3)
    assert "TWD" in w["ttm_eps"]["method"]


def test_results_are_cached(fmp):
    quarter_fmp.walkthrough("TSM")
    n = len(fmp)
    quarter_fmp.walkthrough("TSM")
    assert len(fmp) == n


def test_no_fmp_data_returns_none(monkeypatch):
    monkeypatch.setattr(quarter_fmp, "_get", lambda *a, **k: None)
    quarter_fmp._CACHE.clear()
    assert quarter_fmp.walkthrough("ZZZZ") is None


def test_endpoint_falls_back_to_fmp(monkeypatch, fmp):
    import quarter_facts
    import web_app
    monkeypatch.setattr(quarter_facts, "fetch_facts", lambda t: (None, None))
    monkeypatch.setattr(quarter_facts, "explain_gap", lambda *a: "no 10-Q")
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    d = c.get("/api/quarter/walkthrough/TSM").get_json()
    assert d["available"] and d["source"] == "fmp" and d["figures"]["rev"] == 1_000e9


def test_page_never_multiplies_a_foreign_eps_by_four():
    html = open("templates/fundamentals.html").read()
    assert "qdCurrency !== 'USD'" in html and "Financial Modeling Prep" in html
