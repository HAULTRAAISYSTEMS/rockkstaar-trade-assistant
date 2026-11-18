"""Wealth Architect ledger: lots, cost basis, splits, sales, income, edge cases."""
from datetime import date

import pytest

import wealth_calc as wc
import wealth_ledger as wl


def T(d, t, tk=None, **kw):
    return dict(date=d, type=t, ticker=tk, **kw)


def test_weighted_average_cost_across_lots_includes_fees():
    lg = wl.build([
        T("2026-01-02", "DEPOSIT", amount=5000),
        T("2026-01-05", "BUY", "KLAC", shares=2, price=800, fees=1),
        T("2026-02-05", "BUY", "KLAC", shares=1, price=700, fees=1),
    ])
    h = lg.holding("KLAC")
    assert h["shares"] == 3
    assert h["cost_basis"] == pytest.approx(2302)          # 1600+1 + 700+1
    assert h["avg_cost"] == pytest.approx(2302 / 3)
    assert lg.cash == pytest.approx(5000 - 2302)
    assert lg.net_contributions == 5000


def test_fifo_sale_realizes_oldest_lot_and_tracks_term():
    lg = wl.build([
        T("2024-01-10", "DEPOSIT", amount=3000),
        T("2024-01-10", "BUY", "NVDA", shares=10, price=50),
        T("2025-06-01", "BUY", "NVDA", shares=10, price=120),
        T("2025-08-01", "SELL", "NVDA", shares=15, price=180, fees=5),
    ])
    gains = lg.realized
    assert [g["term"] for g in gains] == ["long", "short"]
    # Proceeds net of the $5 fee, split by shares: 10/15 and 5/15 of 2695.
    assert gains[0]["gain"] == pytest.approx(2695 * 10 / 15 - 500)
    assert gains[1]["gain"] == pytest.approx(2695 * 5 / 15 - 600)
    h = lg.holding("NVDA")
    assert h["shares"] == 5 and h["cost_basis"] == pytest.approx(600)


def test_one_year_exactly_is_still_short_term():
    lg = wl.build([T("2025-03-01", "DEPOSIT", amount=1000), T("2025-03-01", "BUY", "AAPL", shares=1, price=100),
                   T("2026-03-01", "SELL", "AAPL", shares=1, price=110)])
    assert lg.realized[0]["term"] == "short"
    lg = wl.build([T("2025-03-01", "DEPOSIT", amount=1000), T("2025-03-01", "BUY", "AAPL", shares=1, price=100),
                   T("2026-03-02", "SELL", "AAPL", shares=1, price=110)])
    assert lg.realized[0]["term"] == "long"


def test_split_by_ratio_keeps_total_cost():
    lg = wl.build([T("2026-01-02", "DEPOSIT", amount=10000),
                   T("2026-01-05", "BUY", "KLAC", shares=4, price=1500),
                   T("2026-10-01", "SPLIT", "KLAC", ratio=10)])
    h = lg.holding("KLAC")
    assert h["shares"] == pytest.approx(40)
    assert h["cost_basis"] == pytest.approx(6000)
    assert h["avg_cost"] == pytest.approx(150)


def test_split_from_shares_received_and_same_day_buy_is_after_split():
    lg = wl.build([T("2026-01-02", "DEPOSIT", amount=10000),
                   T("2026-01-05", "BUY", "SNDK", shares=2, price=800),
                   T("2026-11-20", "BUY", "SNDK", shares=1, price=100),     # post-split price, same day
                   T("2026-11-20", "SPLIT", "SNDK", shares=14)])              # 2 held → 16 (8-for-1)
    h = lg.holding("SNDK")
    assert h["shares"] == pytest.approx(17)
    assert h["cost_basis"] == pytest.approx(1700)


def test_reverse_split():
    lg = wl.build([T("2026-01-02", "DEPOSIT", amount=1000), T("2026-01-02", "BUY", "XYZ", shares=50, price=2),
                   T("2026-05-01", "SPLIT", "XYZ", ratio=0.2)])
    assert lg.holding("XYZ")["shares"] == pytest.approx(10)
    assert lg.holding("XYZ")["avg_cost"] == pytest.approx(10)


def test_fractional_shares_and_selling_everything_closes_position():
    lg = wl.build([T("2026-01-02", "DEPOSIT", amount=500),
                   T("2026-01-02", "BUY", "MSFT", shares=1.234567, price=405),
                   T("2026-04-02", "SELL", "MSFT", shares=1.234567, price=420)])
    assert lg.holding("MSFT") is None
    assert "MSFT" not in lg.positions
    assert lg.realized[0]["gain"] == pytest.approx(1.234567 * 15)


def test_oversell_is_reported_not_invented():
    lg = wl.build([T("2026-01-02", "DEPOSIT", amount=500), T("2026-01-02", "BUY", "AMD", shares=1, price=100),
                   T("2026-02-02", "SELL", "AMD", shares=3, price=120)])
    assert any("only 1 shares were held" in i for i in lg.issues)
    assert lg.realized[0]["shares"] == 1


def test_dividends_reinvest_interest_fees_taxes():
    lg = wl.build([
        T("2026-01-02", "DEPOSIT", amount=1000),
        T("2026-01-02", "BUY", "TSM", shares=2, price=200),
        T("2026-03-10", "DIVIDEND", "TSM", amount=3.5),
        T("2026-03-10", "TAX", amount=0.74),
        T("2026-06-10", "REINVEST", "TSM", shares=0.02, price=250),
        T("2026-06-30", "INTEREST", amount=1.1),
        T("2026-07-01", "FEE", amount=2),
    ])
    assert lg.dividends == pytest.approx(3.5 + 5.0)
    assert lg.dividends_by_ticker["TSM"] == pytest.approx(8.5)
    assert lg.holding("TSM")["shares"] == pytest.approx(2.02)
    assert lg.cash == pytest.approx(1000 - 400 + 3.5 - 0.74 + 1.1 - 2)
    assert lg.net_contributions == 1000            # income is not a contribution


def test_transfers_carry_cost_and_count_as_flows():
    lg = wl.build([T("2026-01-02", "TRANSFER_IN", "AAPL", shares=10, price=150, amount=2500),
                   T("2026-05-02", "TRANSFER_OUT", "AAPL", shares=4)])
    h = lg.holding("AAPL")
    assert h["shares"] == 6 and h["avg_cost"] == pytest.approx(150)
    assert lg.transfers_in == 2500 and lg.transfers_out == pytest.approx(600)
    assert lg.realized == []                        # moving shares is not a sale


def test_invalid_rows_are_skipped_with_a_reason():
    lg = wl.build([T("2026-01-02", "BUY", "NVDA", shares=0, price=100), T("bad", "DEPOSIT", amount=5),
                   T("2026-01-02", "TELEPORT", amount=5)])
    assert len(lg.issues) == 3 and lg.positions == {}


def test_negative_cash_is_flagged():
    lg = wl.build([T("2026-01-02", "BUY", "NVDA", shares=1, price=100)])
    assert any("Cash is below zero" in i for i in lg.issues)


def test_zero_share_history_and_shares_on():
    txs = [T("2026-01-02", "DEPOSIT", amount=1000), T("2026-01-02", "BUY", "V", shares=2, price=300),
           T("2026-02-02", "SELL", "V", shares=2, price=310), T("2026-03-02", "BUY", "V", shares=1, price=290)]
    assert wl.shares_on(txs, "V", date(2026, 2, 15)) == 0
    assert wl.shares_on(txs, "V", date(2026, 3, 15)) == 1
    assert wl.build(txs).holding("V")["avg_cost"] == 290


# ── Cost Basis Lab ───────────────────────────────────────────────────────

def test_lab_formula_and_scenarios():
    r = wc.simulate_purchase(existing_shares=10, existing_avg_cost=150, purchase_price=120,
                             invest_amount=600, future_price=180, current_price=120)
    assert r["ok"]
    assert r["new_shares"] == pytest.approx(5)
    assert r["new_avg_cost"] == pytest.approx((1500 + 600) / 15)        # 140
    assert r["avg_cost_change"] == pytest.approx(-10)
    assert r["pl_now_before"] == pytest.approx(-300)
    assert r["pl_now_after"] == pytest.approx(15 * 120 - 2100)
    assert r["pl_future_after"] == pytest.approx(15 * 180 - 2100)
    # The new money earns what the stock does from 120 → 180, nothing more.
    assert r["new_money_gain_future"] == pytest.approx(300)
    assert r["new_money_return_pct"] == pytest.approx(50)
    target = [s for s in r["scenarios"] if s["is_target"]][0]
    assert target["position_pl"] - target["without_purchase_pl"] == pytest.approx(target["new_money_pl"])


def test_lab_whole_shares_leaves_cash_and_needs_one_share():
    r = wc.simulate_purchase(existing_shares=0, existing_avg_cost=0, purchase_price=300,
                             invest_amount=1000, fractional=False, fees=0, cash_available=1500)
    assert r["new_shares"] == 3 and r["left_of_amount"] == pytest.approx(100)
    assert r["remaining_cash"] == pytest.approx(600) and r["old_avg_cost"] is None
    bad = wc.simulate_purchase(existing_shares=0, existing_avg_cost=0, purchase_price=300,
                               invest_amount=200, fractional=False)
    assert not bad["ok"] and "one whole share" in bad["errors"][0]


def test_lab_concentration_and_validation():
    r = wc.simulate_purchase(existing_shares=10, existing_avg_cost=100, purchase_price=100,
                             invest_amount=1000, current_price=100, portfolio_value=10000)
    assert r["weight_before_pct"] == pytest.approx(10) and r["weight_after_pct"] == pytest.approx(20)
    bad = wc.simulate_purchase(existing_shares=5, existing_avg_cost=0, purchase_price=0, invest_amount=-1)
    assert not bad["ok"] and len(bad["errors"]) == 3


# ── Returns ──────────────────────────────────────────────────────────────

def test_xirr_matches_known_answer():
    # 1,000 in, 1,100 out exactly one year later → 10%.
    assert wc.xirr([(date(2025, 1, 1), -1000), (date(2026, 1, 1), 1100)]) == pytest.approx(0.10, abs=1e-3)
    assert wc.xirr([(date(2025, 1, 1), -1000)]) is None


def test_twr_ignores_timing_of_deposits():
    values = [(date(2026, 1, 1), 1000), (date(2026, 1, 2), 1100), (date(2026, 1, 3), 2100), (date(2026, 1, 4), 2310)]
    flows = [(date(2026, 1, 3), 1000)]
    # +10%, then 0% (the 1,000 is a deposit), then +10% → 21%.
    assert wc.twr(values, flows) == pytest.approx(0.21)


def test_annualize_and_drawdown():
    assert wc.annualize(0.21, 730) == pytest.approx(0.1, abs=1e-3)
    assert wc.annualize(0.05, 100) is None
    dd = wc.max_drawdown([(date(2026, 1, d), v) for d, v in ((1, 1.0), (2, 1.2), (3, 0.9), (4, 1.3))])
    assert dd["pct"] == pytest.approx(-25)


def test_effective_holdings_ignores_cash():
    import wealth_service as ws
    lg = wl.build([T("2026-01-02", "DEPOSIT", amount=10000), T("2026-01-02", "BUY", "A", shares=10, price=100),
                   T("2026-01-02", "BUY", "B", shares=10, price=100)])
    prices = {"A": {"price": 100, "live": True}, "B": {"price": 100, "live": True}}
    s = ws.summarize({1: lg}, prices, {}, today=date(2026, 3, 1))
    assert s["effective_holdings"] == pytest.approx(2)
    assert s["cash"] == pytest.approx(8000) and s["sectors"][0]["sector"] == "Cash"
