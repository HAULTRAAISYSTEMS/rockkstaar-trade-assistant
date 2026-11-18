"""Scoreboard history: splits, flows, time- and money-weighted returns."""
from datetime import date, timedelta

import pytest

import wealth_history as wh
import wealth_ledger as wl


def T(d, t, tk=None, **kw):
    return dict(date=d, type=t, ticker=tk, **kw)


def days(start, n):
    d0 = date.fromisoformat(start)
    return [d0 + timedelta(days=i) for i in range(n)]


def test_split_is_not_a_crash_in_the_history():
    # 1 share at 1,000; 10-for-1 on day 3. The bars are split-adjusted, so the
    # close before the split already reads 100.
    lg = wl.build([T("2026-01-01", "DEPOSIT", amount=1000), T("2026-01-01", "BUY", "KLAC", shares=1, price=1000),
                   T("2026-01-03", "SPLIT", "KLAC", ratio=10)])
    cal = days("2026-01-01", 5)
    closes = {"KLAC": [(d, 100.0) for d in cal]}
    s = wh.build_series([lg], closes, cal)
    assert [round(v) for _, v in s["values"]] == [1000] * 5


def test_deposits_are_not_returns():
    lg = wl.build([T("2026-01-01", "DEPOSIT", amount=1000), T("2026-01-01", "BUY", "A", shares=10, price=100),
                   T("2026-01-03", "DEPOSIT", amount=1000)])
    cal = days("2026-01-01", 4)
    closes = {"A": [(cal[0], 100), (cal[1], 110), (cal[2], 110), (cal[3], 121)]}
    s = wh.build_series([lg], closes, cal)
    sb = wh.scoreboard(s, [(d, 100.0) for d in cal])
    # Stock +21% over the period; the uninvested 1,000 deposit earns nothing,
    # so the time-weighted return is: +10%, 0%, then (1210+1000)/(1100+1000) - 1.
    expect = 1.10 * ((1210 + 1000) / (1100 + 1000)) - 1
    assert sb["twr_pct"] == pytest.approx(expect * 100)
    assert sb["contributed"] == 2000 and sb["gain"] == pytest.approx(210)
    assert sb["spy_pct"] == pytest.approx(0) and sb["excess_pct"] == pytest.approx(expect * 100)


def test_weekend_flow_moves_to_next_trading_day_and_dividends_count_as_cash():
    lg = wl.build([T("2026-01-03", "DEPOSIT", amount=500), T("2026-01-05", "BUY", "A", shares=5, price=100),
                   T("2026-01-06", "DIVIDEND", "A", amount=5)])
    cal = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]
    s = wh.build_series([lg], {"A": [(d, 100.0) for d in cal]}, cal)
    assert s["flows"] == [(date(2026, 1, 5), 500)]
    assert [v for _, v in s["values"]] == [500, 505, 505]


def test_missing_history_is_refused():
    lg = wl.build([T("2026-01-01", "DEPOSIT", amount=100), T("2026-01-01", "BUY", "ZZZ", shares=1, price=100)])
    s = wh.build_series([lg], {}, days("2026-01-01", 3))
    assert not s["ok"] and s["missing"] == ["ZZZ"]


def test_today_uses_live_prices_with_actual_shares():
    lg = wl.build([T("2026-01-01", "DEPOSIT", amount=1000), T("2026-01-01", "BUY", "A", shares=2, price=500),
                   T("2026-01-02", "SPLIT", "A", ratio=2)])
    cal = days("2026-01-01", 2)
    s = wh.build_series([lg], {"A": [(d, 250.0) for d in cal]}, cal, today_prices={"A": 260.0}, today=date(2026, 1, 5))
    assert s["values"][-1] == (date(2026, 1, 5), pytest.approx(4 * 260))


def test_yearly_and_drawdown():
    idx = [(date(2025, 12, 30), 1.0), (date(2025, 12, 31), 1.1), (date(2026, 1, 2), 0.88), (date(2026, 6, 1), 1.21)]
    y = wh.yearly(idx)
    assert y[2025] == pytest.approx(10) and y[2026] == pytest.approx(10)
    lg = wl.build([T("2025-12-30", "DEPOSIT", amount=100)])
    sb = wh.scoreboard({"values": [(d, v * 100) for d, v in idx], "flows": [(date(2025, 12, 30), 100)],
                        "contributions": [(d, 100) for d, _ in idx], "gaps": []}, None)
    assert sb["max_drawdown"]["pct"] == pytest.approx(-20)
