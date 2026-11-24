"""Earnings strip: your reports in the next few days, then the result."""
from datetime import datetime
from pathlib import Path

import earnings_strip as es

ROOT = Path(__file__).parent


def _row(t, days, code="amc", est=0.9, actual=None, watch=True, date="2026-12-01"):
    return {"kind": "EARNINGS", "ticker": t, "days_away": days, "time_code": code, "eps_est": est,
            "eps_actual": actual, "on_watchlist": watch, "date": date}


def test_only_your_stocks_within_three_days_at_most_five():
    rows = [_row("CRWD", 2), _row("ZZZ", 0, watch=False), _row("FAR", 5),
            {"kind": "ECONOMIC", "title": "CPI", "days_away": 0}] + [_row(f"T{i}", 1) for i in range(6)]
    out = es.build_strip(rows, datetime(2026, 11, 29, 10, 0))
    assert len(out) == 5 and "ZZZ" not in [e["ticker"] for e in out] and "FAR" not in [e["ticker"] for e in out]


def test_states_by_time_of_day():
    rows = [_row("A", 0, "amc"), _row("B", 0, "bmo"), _row("C", 1, "bmo")]
    before = {e["ticker"]: e["state"] for e in es.build_strip(rows, datetime(2026, 12, 1, 7, 0))}
    assert before == {"A": "today", "B": "live", "C": "soon"}
    evening = {e["ticker"]: e["state"] for e in es.build_strip(rows, datetime(2026, 12, 1, 16, 30))}
    assert evening["A"] == "live" and evening["B"] == "today"


def test_result_once_reported_and_nothing_guessed():
    out = es.build_strip([_row("CRWD", 0, est=0.88, actual=0.94), _row("NVDA", 0, est=None, actual=1.1),
                          _row("AMD", 0, est=0.5, actual=0.45)], datetime(2026, 12, 1, 17, 0))
    by = {e["ticker"]: e for e in out}
    assert by["CRWD"]["verdict"] == "beat" and by["CRWD"]["result"] == "Beat · EPS $0.94 vs $0.88"
    assert by["AMD"]["verdict"] == "miss"
    assert by["NVDA"]["state"] == "reported" and by["NVDA"]["result"] is None   # no estimate → no verdict
    assert [e["ticker"] for e in out][:1] in (["CRWD"], ["AMD"], ["NVDA"])


def test_labels():
    out = es.build_strip([_row("A", 0, "bmo"), _row("B", 1), _row("C", 3, date="2026-12-03")], datetime(2026, 11, 30, 21, 0))
    by = {e["ticker"]: e for e in out}
    assert by["A"]["when"] == "Today" and by["A"]["session"] == "before open"
    assert by["B"]["when"] == "Tomorrow" and by["C"]["when"] == "Thu Dec 3"


def test_page_and_setting_wired():
    tpl = (ROOT / "templates/liquidity.html").read_text(encoding="utf-8")
    assert 'id="earn-strip"' in tpl and "js/earnings_strip.js" in tpl
    nav = (ROOT / "templates/_elite_navigation.html").read_text(encoding="utf-8")
    assert 'data-pref="earnings_strip"' in nav and "pref_earn(" in nav
    import app as legacy
    assert legacy._PREF_DEFAULTS["earnings_strip"] is True
    css = (ROOT / "static/css/command_center_picture.css").read_text(encoding="utf-8")
    assert 'html[data-earn-strip="0"] .es' in css
