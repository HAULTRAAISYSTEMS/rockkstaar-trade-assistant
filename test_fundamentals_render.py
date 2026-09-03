"""The fundamentals page renders.

Nothing exercised the template. Every scorecard change added markup that
nothing checked, so a stray Jinja expression - a filter on None, a key the
engine stopped emitting - would first show up as a 500 on the live site.
"""
import os
import re
from unittest.mock import patch

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

# web_app, not app: app.py alone has no live_research blueprint, and the
# shared navigation partial builds a url_for('live_research.feed'). The
# Procfile serves web_app:app, so this is what production renders.
import web_app
import app as legacy
import fundamentals_engine as fe
from test_edgar_pipeline import _Resp, facts


def render(data, ticker="KLAC", error=None):
    with web_app.app.test_request_context("/fundamentals"):
        return legacy.render_template("fundamentals.html", ticker=ticker,
                                      data=data, error=error)


@pytest.fixture(scope="module")
def full():
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA Corporation")), \
         patch.object(fe._req_module, "get", return_value=_Resp(facts())):
        raw = fe.fetch_fundamentals_edgar("KLAC")
    raw["_ttm_metrics"] = {"gross_margin_ttm": 0.615,
                           "operating_margin_ttm": 0.415,
                           "net_margin_ttm": 0.355,
                           "period_end": "2026-03-31"}
    return fe.score_fundamentals(raw)


class TestAFullScorecard:
    def test_it_renders(self, full):
        assert len(render(full)) > 2000

    def test_the_verdict_is_on_the_page(self, full):
        assert full["verdict"] in render(full)

    def test_every_section_is_on_the_page(self, full):
        html = render(full)
        for section in full["sections"]:
            assert section["name"] in html

    def test_the_ttm_margins_are_tagged(self, full):
        assert "ttm-tag" in render(full)

    def test_nothing_leaked_an_unrendered_expression(self, full):
        html = render(full)
        assert "{{" not in html and "{%" not in html


class TestThinAndEmptyData:
    """The states that only appear when a filer's data is poor."""

    def test_an_empty_scorecard_renders(self):
        scored = fe.score_fundamentals({"ticker": "NONE", "source": "edgar"})
        html = render(scored, ticker="NONE")
        assert fe.NO_DATA_VERDICT in html

    def test_the_coverage_note_is_shown(self):
        scored = fe.score_fundamentals({
            "ticker": "THIN", "source": "edgar",
            "revenue": [1000.0, 900.0, 800.0, 700.0, 600.0],
            "net_income": [200.0, 170.0, 140.0, 120.0, 100.0],
            "fiscal_period_ends": ["2026-06-30", "2025-06-30", "2024-06-30",
                                   "2023-06-30", "2022-06-30"]})
        html = render(scored, ticker="THIN")
        if scored["coverage_note"]:
            assert "verdict-capped" in html

    def test_no_ticker_renders_the_empty_state(self):
        assert len(render(None, ticker="")) > 500

    def test_an_error_renders(self):
        assert "not found" in render(None, ticker="ZZZZ", error="not found")


def test_every_class_the_template_uses_is_styled():
    """A class added to the markup with no rule renders unstyled."""
    html = open("templates/fundamentals.html").read()
    css = open("static/css/fundamentals.css").read()
    for name in ("ttm-tag", "verdict-capped", "verdict-capped-tag", "verdict-na"):
        assert f'"{name}"' in html or name in css
        assert f".{name}" in css, f"{name} has no CSS rule"
