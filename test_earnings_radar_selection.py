"""Earnings Radar ordering.

The radar sorted by date alone, so being on the watchlist only broke ties within
a single day. In earnings season a held ticker reporting Friday lost its slot to
every mega-cap reporting Monday, and the user's own positions fell off the card
during the one week it matters most.
"""
import pytest

import intel_engine as ie

WATCH = {"MRVL", "MU", "NVDA"}


def big(day, i):
    return {"ticker": f"BIG{day}{i:02d}", "days_away": day, "on_watchlist": False,
            "market_cap": 900_000_000_000 - i * 1_000_000_000}


def peak_season(days=7, per_day=20):
    return [big(d, i) for d in range(days) for i in range(per_day)]


def test_watchlist_names_survive_a_crowded_week():
    rows = peak_season() + [
        {"ticker": "MRVL", "days_away": 5, "on_watchlist": True, "market_cap": 90_000_000_000},
        {"ticker": "MU", "days_away": 6, "on_watchlist": True, "market_cap": 120_000_000_000},
    ]
    shown = {r["ticker"] for r in ie.select_radar_rows(rows, 12)}
    assert {"MRVL", "MU"} <= shown, "held tickers must never be crowded off the card"


def test_card_still_reads_as_a_calendar():
    rows = peak_season(days=3, per_day=4) + [
        {"ticker": "MU", "days_away": 6, "on_watchlist": True, "market_cap": 1}]
    days = [r["days_away"] for r in ie.select_radar_rows(rows, 8)]
    assert days == sorted(days), "rows must stay in date order after reservation"


def test_watchlist_cannot_take_over_the_whole_card():
    """A large watchlist must not hide the market-wide prints that move the tape."""
    rows = [{"ticker": f"W{i}", "days_away": 6, "on_watchlist": True, "market_cap": 1}
            for i in range(20)] + peak_season(days=2, per_day=10)
    selected = ie.select_radar_rows(rows, 12)
    watch_count = sum(1 for r in selected if r["on_watchlist"])
    assert watch_count <= 6, "watchlist rows are capped at half the card"
    assert len(selected) == 12


def test_all_watchlist_slate_still_fills_the_card():
    rows = [{"ticker": f"W{i}", "days_away": i, "on_watchlist": True, "market_cap": 1}
            for i in range(8)]
    assert len(ie.select_radar_rows(rows, 6)) == 6


def test_no_watchlist_matches_plain_date_order():
    rows = peak_season(days=3, per_day=5)
    selected = ie.select_radar_rows(rows, 10)
    assert [r["ticker"] for r in selected] == [r["ticker"] for r in sorted(rows, key=ie._radar_sort_key)][:10]


def test_short_slate_is_returned_whole():
    rows = [big(0, 0), big(1, 1)]
    assert len(ie.select_radar_rows(rows, 12)) == 2


@pytest.mark.parametrize("bad", [None, []])
def test_empty_input_is_safe(bad):
    assert ie.select_radar_rows(bad, 6) == []


def test_limit_is_never_below_one():
    rows = peak_season(days=1, per_day=3)
    assert len(ie.select_radar_rows(rows, 0)) == 1
