import os
from unittest.mock import patch

from flask import render_template, session

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")

import app


def sample_fundamentals():
    return {
        "ticker": "AMD", "company_name": "Advanced Micro Devices", "sector": "Technology",
        "industry": "Semiconductors", "normalized_score": 82, "verdict": "Strong Company",
        "verdict_reason": "Cash generation and returns are improving.", "coverage_note": "",
        "ttm_period_end": "2026-06-27", "scored_metrics": 18, "total_metrics": 20,
        "history": [{"period_end": "2026-06-27", "label": "Latest", "revenue": "$11.5B",
                     "net_income": "$2.3B", "fcf": "$4.1B", "gross_margin": "54.0%",
                     "operating_margin": "22.1%", "fcf_over_ni": 1.78, "margins_are_ttm": True}],
        "sections": [{"name": "Cash Flow", "earned": 9, "possible": 10}],
        "valuation": {"available": True, "rows": [{"label": "Trailing P/E", "value": "31.2x", "tone": "watch", "note": "Above history"}]},
        "filing_signals": {"available": True, "signals": [], "lookback_days": 1095},
    }


def test_company_context_joins_changes_fundamentals_and_private_thesis():
    post = {"id": "post-1", "ticker": "AMD", "headline": "AMD raises guidance",
            "tradestaar_take": "The demand outlook improved.", "source_name": "AMD IR",
            "source_url": "https://example.com/release", "source_published_at": "2026-09-14T18:00:00+00:00",
            "sentiment": "Bullish", "priority": "High", "catalyst_type": "EARNINGS"}
    card = {"ticker": "AMD", "headline": "Data center thesis", "why_it_matters": "Growth is durable.",
            "thesis_impact": "strengthened", "updated_at": "2026-09-13T00:00:00+00:00"}
    news = {"ticker": "AMD", "headline": "New AMD accelerator ships", "source": "Wire",
            "url": "https://example.com/news", "published_at": "2026-09-14T17:00:00+00:00"}
    stock = {"current_price": 210.5, "change_pct": 2.4, "updated_at": "2026-09-14T18:05:00+00:00",
             "company_description": "AMD designs high-performance processors."}

    with (
        patch("research_feed_phase2.list_published", return_value=[post]) as feed,
        patch("research_memory.list_cards", return_value=[card]) as memory,
        patch.object(app, "get_user_setting", return_value="2026-09-14T16:00:00+00:00"),
        patch.object(app._intel, "get_intel_summary", return_value={
            "market_news": [news], "earnings": {"coming_up": [{"ticker": "AMD", "date": "2026-10-27", "time_label": "After close", "source": "Company IR"}]},
        }),
    ):
        result = app._company_research_context(9, "AMD", sample_fundamentals(), stock)

    assert feed.call_args.kwargs["user_id"] == 9
    memory.assert_called_once_with(9, ticker="AMD", limit=50)
    assert result["description"].startswith("AMD designs")
    assert result["new_count"] == 2
    assert result["changes"][0]["headline"] == "AMD raises guidance"
    assert result["earnings"]["date"] == "2026-10-27"


def test_unsafe_provider_link_is_not_rendered():
    with (
        patch("research_feed_phase2.list_published", return_value=[]),
        patch("research_memory.list_cards", return_value=[]),
        patch.object(app, "get_user_setting", return_value=""),
        patch.object(app._intel, "get_intel_summary", return_value={
            "market_news": [{"ticker": "AMD", "headline": "Bad link", "url": "javascript:alert(1)"}],
        }),
    ):
        result = app._company_research_context(2, "AMD", None, {})

    assert result["headlines"][0]["url"] == ""


def test_review_checkpoint_is_saved_for_only_the_current_user():
    with app.app.test_request_context("/research/AMD/reviewed", method="POST"):
        with patch.object(app, "current_user_id", return_value=42), patch.object(app, "set_user_setting") as save:
            response = app.company_research_reviewed("AMD")

    save.assert_called_once()
    assert save.call_args.args[0] == 42
    assert save.call_args.args[1] == "company_research_reviewed:AMD"
    assert response.status_code == 302


def test_unified_company_workspace_renders_all_five_research_steps():
    import web_app

    research = {
        "price": 210.5, "change_pct": 2.4, "market_as_of": "2026-09-14T18:05:00Z",
        "description": "AMD designs processors.", "earnings": None, "new_count": 0,
        "reviewed_at": "", "changes": [], "memory_cards": [], "fundamentals_as_of": "2026-06-27",
        "source_count": 1,
    }
    with web_app.app.test_request_context("/research?ticker=AMD"):
        session.update(user_id=7, username="researcher", is_admin=0)
        html = render_template("research.html", ticker="AMD", data=sample_fundamentals(), error=None,
                               stock={}, research=research, suggestions=[])

    for heading in ("Business quality and price", "What changed", "Financial history",
                    "Filing integrity signals", "Your AMD thesis"):
        assert heading in html
    assert "Open detailed fundamentals" in html
    assert "What the company does" in html


def test_wide_financial_table_cannot_force_mobile_page_overflow():
    css = open("static/css/company_research.css", encoding="utf-8").read()

    assert ".cr-shell{" in css and "width:100%;min-width:0" in css
    assert ".cr-section{min-width:0" in css
    assert ".cr-table-wrap{width:100%;min-width:0;max-width:100%;overflow:auto" in css
