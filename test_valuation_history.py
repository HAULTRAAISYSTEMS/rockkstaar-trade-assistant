"""Today's multiple against the company's own recent history.

A single P/E says nothing. The only cheap comparison available is the company
against itself, and that is what turns "down 44% from the high" into a
statement with content — a stock that tripled and then halved can be far off
its peak and still the most expensive it has ever been.

The split is the whole difficulty: price feeds are split-adjusted, filed EPS is
not, and dividing one by the other across a split is wrong by exactly the split
ratio in the direction that makes an expensive stock look cheap.
"""
from datetime import datetime, timezone

import pytest

from valuation_history import build_history, close_on_or_before


def day(text):
    return int(datetime.strptime(text, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp())


def bars(pairs):
    return {"timestamps": [day(d) for d, _ in pairs],
            "closes": [c for _, c in pairs]}


ENDS = ["2026-06-30", "2025-06-30", "2024-06-30"]
EPS = [3.66, 3.04, 2.03]
FCF = [3767e6, 3747e6, 3031e6]
SHARES = [1320e6, 1337e6, 1362e6]
BARS = bars([("2024-06-28", 80.0), ("2025-06-30", 88.8), ("2026-06-30", 175.0)])


# ── Finding the right price ──────────────────────────────────────────────────

def test_it_uses_the_close_on_the_fiscal_year_end():
    assert close_on_or_before(BARS, "2026-06-30") == 175.0


def test_a_year_end_on_a_weekend_falls_back_to_the_last_session():
    assert close_on_or_before(BARS, "2024-06-30") == 80.0


def test_it_refuses_to_reach_back_a_month_for_a_price():
    """A wide gap means the series does not cover that period; a price from
    weeks earlier is not a year-end price."""
    assert close_on_or_before(BARS, "2024-08-15") is None


def test_no_bars_yields_no_price():
    assert close_on_or_before(None, "2026-06-30") is None
    assert close_on_or_before({}, "2026-06-30") is None


# ── The table ────────────────────────────────────────────────────────────────

def history(**over):
    kwargs = dict(period_ends=ENDS, eps=EPS, fcf=FCF, shares=SHARES, bars=BARS,
                  today_price=171.17, today_pe=47.9, today_pfcf=59.4,
                  comparable_years=3)
    kwargs.update(over)
    return build_history(**kwargs)


def test_each_year_gets_a_multiple_from_its_own_price_and_earnings():
    rows = {r["label"]: r for r in history()["rows"]}
    assert rows["FY26"]["pe"] == pytest.approx(175.0 / 3.66, rel=1e-6)
    assert rows["FY25"]["pe"] == pytest.approx(88.8 / 3.04, rel=1e-6)


def test_price_to_free_cash_flow_is_per_share():
    rows = {r["label"]: r for r in history()["rows"]}
    assert rows["FY26"]["pfcf"] == pytest.approx(175.0 / (3767e6 / 1320e6), rel=1e-6)


def test_today_is_the_last_row_and_marked():
    rows = history()["rows"]
    assert rows[-1]["is_today"] is True and rows[-1]["pe"] == 47.9
    assert all(not r["is_today"] for r in rows[:-1])


# ── The reading, which is arithmetic and not an opinion ──────────────────────

def test_it_says_when_today_is_the_most_expensive_on_record():
    """The report's central point: down 44% and still the priciest it has been."""
    note = history()["note"]
    assert "most expensive" in note and "47.9x" in note


def test_it_says_when_today_is_the_cheapest():
    note = history(today_pe=8.0)["note"]
    assert "cheapest" in note


def test_a_middling_multiple_is_placed_not_labelled():
    # History is 47.8x, 29.2x, 39.4x — 35 sits inside that spread.
    note = history(today_pe=35.0)["note"]
    assert "above" in note and "below" in note
    for word in ("cheap", "expensive", "buy", "value"):
        assert word not in note.lower().replace("most expensive", "")


def test_no_verdict_language_anywhere():
    joined = history()["note"].lower()
    for word in ("buy", "sell", "undervalued", "overvalued", "bargain", "opportunity"):
        assert word not in joined


# ── Guarding the split ───────────────────────────────────────────────────────

def test_it_only_covers_the_years_stated_on_one_basis():
    """Reaching past what the newest filing restates mixes a split-adjusted
    price series with as-filed per-share figures."""
    five_ends = ENDS + ["2023-06-30", "2022-06-30"]
    five_eps = EPS + [24.2, 21.9]          # pre-split, ten times the rest
    wide = bars([("2022-06-30", 34.0), ("2023-06-30", 45.0)] +
                [("2024-06-28", 80.0), ("2025-06-30", 88.8), ("2026-06-30", 175.0)])
    r = build_history(period_ends=five_ends, eps=five_eps, fcf=FCF, shares=SHARES,
                      bars=wide, today_pe=47.9, comparable_years=3)
    assert [row["label"] for row in r["rows"] if not row["is_today"]] == \
        ["FY26", "FY25", "FY24"]
    assert r["comparable_years"] == 3


def test_years_without_a_price_are_dropped_not_guessed():
    r = history(bars=bars([("2026-06-30", 175.0)]))
    assert [row["label"] for row in r["rows"] if not row["is_today"]] == ["FY26"]


def test_a_year_with_no_earnings_figure_is_dropped():
    r = history(eps=[3.66, None, None], fcf=[None, None, None])
    assert [row["label"] for row in r["rows"] if not row["is_today"]] == ["FY26"]


def test_a_loss_year_does_not_produce_a_negative_multiple():
    r = history(eps=[3.66, -1.2, 2.03], fcf=[None, None, None])
    labels = [row["label"] for row in r["rows"] if not row["is_today"]]
    assert "FY25" not in labels


# ── Degrading ────────────────────────────────────────────────────────────────

def test_without_price_history_the_section_does_not_render():
    assert history(bars=None)["available"] is False


def test_without_a_current_multiple_the_section_does_not_render():
    assert history(today_pe=None, today_pfcf=None)["available"] is False


def test_one_year_of_history_gives_no_ranking_claim():
    """Two points are not a range."""
    r = history(bars=bars([("2026-06-30", 175.0)]))
    assert r["note"] == ""
