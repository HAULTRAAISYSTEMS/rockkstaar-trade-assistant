"""Tests for the Thesis view engine (thesis.py)."""
import pytest

import thesis


def _data(history, price=100.0, sections=None):
    return {
        "ticker": "TEST",
        "company_name": "Test Corp",
        "sector": "Technology",
        "industry": "Semiconductors",
        "normalized_score": 32.0,
        "verdict": "Good",
        "verdict_class": "good",
        "valuation": {"price": price, "change_pct": 1.5},
        "history": [
            {"revenue_num": r, "net_income_num": ni, "fcf_num": fcf}
            for r, ni, fcf in history
        ],
        "sections": sections or [],
        "red_flags": [],
    }


@pytest.fixture(autouse=True)
def _shares(monkeypatch):
    # 1B shares outstanding; keep Finnhub out of unit tests.
    monkeypatch.setattr(thesis, "_shares_outstanding", lambda ticker, price: 1e9)


def test_targets_order_bear_base_bull():
    d = _data([(100e9, 20e9, 18e9), (90e9, 18e9, 16e9), (80e9, 15e9, 14e9),
               (70e9, 12e9, 11e9), (60e9, 10e9, 9e9)])
    t = thesis.build_targets(d, "TEST")
    assert t["available"] is True
    assert t["bear"] < t["base"] < t["bull"]
    assert t["basis"] == "net income"
    assert t["upside"] == pytest.approx(t["base"] / 100.0 - 1)


def test_target_math_is_transparent():
    # Flat history: growth 0 -> run-rate power, 12x base multiple.
    d = _data([(100e9, 10e9, 9e9)] * 3)
    t = thesis.build_targets(d, "TEST")
    assert t["base"] == pytest.approx(10e9 * 12 / 1e9)
    assert t["bear"] == pytest.approx(10e9 * 0.95 * 7.2 / 1e9)
    assert t["bull"] == pytest.approx(10e9 * 1.05 * 16.2 / 1e9)
    assert t["growth"] == pytest.approx(0.0)


def test_grower_valued_on_run_rate_not_median():
    # Fast grower: the 5-yr median would understate a business earning 72.9B now.
    d = _data([(130e9, 72.9e9, 60e9), (97e9, 55.3e9, 45e9), (61e9, 29.8e9, 26e9),
               (27e9, 4.4e9, 5e9), (17e9, 4.3e9, 3.8e9)])
    t = thesis.build_targets(d, "TEST")
    assert t["power"] == pytest.approx(72.9e9)
    # 67% CAGR clamps to 30% -> 30x base multiple.
    assert t["base"] == pytest.approx(72.9e9 * 1.30 * 30 / 1e9)


def test_decliner_valued_on_cycle_median():
    # Shrinking revenue: peak earnings don't flatter a fading business.
    d = _data([(80e9, 18e9, 16e9), (90e9, 15e9, 14e9), (100e9, 20e9, 18e9)])
    t = thesis.build_targets(d, "TEST")
    assert t["power"] == pytest.approx(18e9)  # median of [18, 15, 20]


def test_negative_recent_earnings_use_run_rate_when_growing():
    # Turnaround case: latest year a loss, revenue still growing -> most
    # recent profitable year, not the loss.
    d = _data([(100e9, -2e9, -1e9), (95e9, 8e9, 7e9), (90e9, 9e9, 8e9)])
    t = thesis.build_targets(d, "TEST")
    assert t["available"] is True
    assert t["basis"] == "net income"
    assert t["power"] == pytest.approx(8e9)


def test_fcf_fallback_when_no_positive_earnings():
    d = _data([(100e9, -5e9, 4e9), (90e9, -3e9, 3e9)])
    t = thesis.build_targets(d, "TEST")
    assert t["available"] is True
    assert t["basis"] == "free cash flow"
    assert t["power"] == pytest.approx(4e9)


def test_sales_fallback_when_no_profits():
    d = _data([(100e9, -5e9, -2e9), (90e9, -3e9, -1e9)])
    t = thesis.build_targets(d, "TEST")
    assert t["available"] is True
    assert t["sales_based"] is True
    # 90B -> 100B revenue growth, growth-scaled base P/S.
    g = 100 / 90 - 1
    assert t["base"] == pytest.approx(100e9 * (1 + g) * (2 + g * 10) / 1e9)


def test_no_history_means_no_targets():
    d = _data([])
    t = thesis.build_targets(d, "TEST")
    assert t["available"] is False
    assert "note" in t


def test_no_shares_means_no_targets(monkeypatch):
    monkeypatch.setattr(thesis, "_shares_outstanding", lambda ticker, price: None)
    d = _data([(100e9, 20e9, 18e9)])
    t = thesis.build_targets(d, "TEST")
    assert t["available"] is False


def test_flags_map_pass_fail_na():
    sections = [{
        "name": "Balance Sheet",
        "rows": [
            {"key": "a", "label": "Good thing", "value": "1.5", "passed": True},
            {"key": "b", "label": "Bad thing", "value": "0.2", "passed": False},
            {"key": "c", "label": "Unknown", "value": "—", "passed": None},
        ],
    }]
    flags = thesis.build_flags(_data([(1, 1, 1)]))
    assert flags == []
    flags = thesis.build_flags(_data([(1, 1, 1)], sections=sections))
    assert [r["status"] for r in flags[0]["rows"]] == ["pass", "fail", "na"]


def test_build_thesis_never_raises():
    out = thesis.build_thesis({"ticker": "X"}, "X")
    assert out["ticker"] == "X"
    assert out["targets"]["available"] is False
    out = thesis.build_thesis(None, "X")
    assert out["ticker"] == "X"


def test_range_bar_geometry():
    d = _data([(100e9, 10e9, 9e9)] * 3)
    t = thesis.build_targets(d, "TEST")
    bar = t["bar"]
    assert bar["bear_pct"] < bar["base_pct"] < bar["bull_pct"]
    assert 0 <= bar["bear_pct"] and bar["bull_pct"] <= 100
    assert bar["price_pct"] is not None


# --- Route-level tests: the /thesis page renders end to end ---------------

def _route_synthetic():
    return {
        "ticker": "NVDA", "company_name": "NVIDIA Corporation",
        "sector": "Technology", "industry": "Semiconductors",
        "normalized_score": 37.5, "verdict": "Great Company",
        "verdict_class": "great",
        "valuation": {"price": 228.86, "change_pct": 1.68},
        "history": [
            {"revenue_num": 130.5e9, "net_income_num": 72.9e9, "fcf_num": 60.9e9},
            {"revenue_num": 96.9e9, "net_income_num": 55.3e9, "fcf_num": 45.0e9},
            {"revenue_num": 60.9e9, "net_income_num": 29.8e9, "fcf_num": 26.0e9},
        ],
        "sections": [{
            "name": "Income Statement",
            "rows": [{"key": "rg", "label": "Revenue growing 3+ years",
                      "value": "$130.5B", "passed": True}],
        }],
        "red_flags": [],
    }


def _thesis_client():
    pytest.importorskip("flask")
    import web_app  # noqa: F401  (registers blueprints as in production)
    import app as legacy
    legacy.app.config["TESTING"] = True
    return legacy, legacy.app.test_client()


def test_thesis_route_renders_targets_and_flags():
    from unittest.mock import patch
    app_module, client = _thesis_client()
    with (
        patch.object(app_module, "_auth_required", return_value=False),
        patch.object(app_module, "get_user_tracked_tickers", return_value=["NVDA"]),
        patch("fundamentals_engine.get_fundamentals", return_value=_route_synthetic()),
        patch.object(thesis, "_shares_outstanding", return_value=24.6e9),
    ):
        resp = client.get("/thesis?ticker=NVDA")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Est. upside" in html
    assert "Bear" in html and "Base" in html and "Bull" in html
    assert "Revenue growing 3+ years" in html
    assert "NVIDIA Corporation" in html
    assert "Open Company Research" in html


def test_thesis_route_start_state_without_ticker():
    from unittest.mock import patch
    app_module, client = _thesis_client()
    with (
        patch.object(app_module, "_auth_required", return_value=False),
        patch.object(app_module, "get_user_tracked_tickers", return_value=[]),
    ):
        resp = client.get("/thesis")
    assert resp.status_code == 200
    assert "Should I own this?" in resp.get_data(as_text=True)


def test_thesis_route_rejects_bad_ticker():
    from unittest.mock import patch
    app_module, client = _thesis_client()
    with (
        patch.object(app_module, "_auth_required", return_value=False),
        patch.object(app_module, "get_user_tracked_tickers", return_value=[]),
    ):
        resp = client.get("/thesis?ticker=<script>")
    assert resp.status_code == 200
    assert "Should I own this?" in resp.get_data(as_text=True)
