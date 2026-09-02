"""Geometry behind the fundamentals charts.

A chart is arithmetic wearing a coordinate system. These tests pin the parts
that go wrong silently: bar heights that do not match their values, a baseline
that is not at zero when a year posts a loss, years drawn in the wrong
direction, and a panel rendered from a single data point.
"""
import pytest

import fundamentals_charts as fc

YEARS = ["2026-06-30", "2025-06-30", "2024-06-30", "2023-06-30", "2022-06-30"]


def history(**overrides):
    """Newest-first rows, the order score_fundamentals emits."""
    rows = []
    revenue = [13579e6, 12160e6, 9812e6, 10496e6, 9212e6]
    income = [4831e6, 4062e6, 2762e6, 3387e6, 3322e6]
    fcf = [3770e6, 3670e6, 3010e6, 3310e6, 3040e6]
    for i, end in enumerate(YEARS):
        rows.append({
            "label": "Latest" if i == 0 else f"Year -{i}",
            "period_end": end,
            "revenue_num": revenue[i],
            "net_income_num": income[i],
            "fcf_num": fcf[i],
            "gross_margin_num": 0.61 - i * 0.005,
            "operating_margin_num": 0.41 - i * 0.01,
            "net_margin_num": 0.35 - i * 0.015,
        })
    for key, value in overrides.items():
        rows[0][key] = value
    return rows


def chart(charts, key):
    return next(c for c in charts if c["key"] == key)


def test_three_panels_are_built_from_a_full_history():
    charts = fc.build_charts(history())
    assert [c["key"] for c in charts] == ["revenue_income", "margins", "cash_quality"]


def test_years_read_oldest_to_newest():
    """The history table is newest-first; a chart that copied that order would
    show a growing company as a shrinking one."""
    charts = fc.build_charts(history())
    labels = [lbl["text"] for lbl in chart(charts, "revenue_income")["x_labels"]]
    assert labels == ["FY22", "FY23", "FY24", "FY25", "FY26"]


def test_bar_height_is_proportional_to_its_value():
    bars = chart(fc.build_charts(history()), "revenue_income")["bars"]
    revenue_bars = [b for b in bars if b["color"] == fc.REVENUE_COLOR]
    tallest = max(revenue_bars, key=lambda b: b["h"])
    shortest = min(revenue_bars, key=lambda b: b["h"])
    # FY26 revenue is 13579/9212 = 1.474x FY22's.
    assert tallest["h"] / shortest["h"] == pytest.approx(13579 / 9212, rel=0.01)


def test_a_loss_year_draws_below_the_zero_line():
    rows = history(net_income_num=-1200e6)
    panel = chart(fc.build_charts(rows), "revenue_income")
    loss_bar = [b for b in panel["bars"] if b["color"] == fc.INCOME_COLOR][-1]
    assert loss_bar["y"] == pytest.approx(panel["zero_y"], abs=0.5)
    assert loss_bar["h"] > 0


def test_every_bar_stays_inside_the_plot_area():
    for rows in (history(), history(net_income_num=-1200e6)):
        for panel in fc.build_charts(rows):
            if panel["kind"] != "bars":
                continue
            for bar in panel["bars"]:
                assert bar["y"] >= 0
                assert bar["y"] + bar["h"] <= panel["height"]


def test_margin_lines_carry_one_point_per_year():
    panel = chart(fc.build_charts(history()), "margins")
    assert len(panel["lines"]) == 3
    for line in panel["lines"]:
        assert len(line["points"].split()) == 5


def test_a_series_with_no_data_is_left_out_of_the_legend():
    rows = history()
    for row in rows:
        row["gross_margin_num"] = None
    panel = chart(fc.build_charts(rows), "margins")
    assert [item["name"] for item in panel["legend"]] == ["Operating", "Net"]


def test_a_single_year_draws_nothing():
    """One point is not a trend; an empty frame reads as missing data."""
    assert fc.build_charts(history()[:1]) == []


def test_no_history_draws_nothing():
    assert fc.build_charts(None) == []
    assert fc.build_charts([]) == []


def test_a_panel_whose_series_are_all_blank_is_dropped():
    rows = history()
    for row in rows:
        row["fcf_num"] = None
        row["net_income_num"] = None
    keys = [c["key"] for c in fc.build_charts(rows)]
    assert "cash_quality" not in keys


def test_money_labels_are_readable():
    assert fc.money(13579e6) == "$13.58B"
    assert fc.money(-1200e6) == "-$1.20B"
    assert fc.money(0) == "$0"
    assert fc.money(None) == "N/A"


def test_axis_labels_are_ordered_and_include_zero_for_bars():
    panel = chart(fc.build_charts(history()), "revenue_income")
    ys = [g["y"] for g in panel["gridlines"]]
    assert ys == sorted(ys, reverse=True)
    assert any(g["zero"] for g in panel["gridlines"])


def test_a_cash_panel_without_cash_flow_is_dropped():
    """Rendering "Free cash flow vs net income" with only the income half would
    read as a company whose cash flow is zero."""
    rows = history()
    for row in rows:
        row["fcf_num"] = None
    assert "cash_quality" not in [c["key"] for c in fc.build_charts(rows)]


def test_an_empty_series_leaves_the_bar_legend():
    rows = history()
    for row in rows:
        row["net_income_num"] = None
    panel = chart(fc.build_charts(rows), "revenue_income")
    assert [item["name"] for item in panel["legend"]] == ["Revenue"]


def test_the_roic_panel_appears_when_the_series_is_present():
    """It backs the moat row: the reader can see whether returns held."""
    rows = history()
    for i, row in enumerate(rows):
        row["roic_num"] = 0.40 - i * 0.02
    panel = chart(fc.build_charts(rows), "roic")
    assert len(panel["lines"]) == 1
    assert len(panel["lines"][0]["points"].split()) == 5
    assert [item["name"] for item in panel["legend"]] == ["ROIC"]


def test_no_roic_panel_without_the_series():
    assert "roic" not in [c["key"] for c in fc.build_charts(history())]
