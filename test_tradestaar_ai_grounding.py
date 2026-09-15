import os
from unittest.mock import patch

from flask import render_template, session

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")

import app


def test_ai_context_labels_every_evidence_record_and_scopes_private_memory():
    stock = {"ticker": "AMD", "current_price": 210.5, "updated_at": "2026-09-14T18:00:00Z"}
    news = {"ticker": "AMD", "headline": "AMD launches a new accelerator", "source": "Company IR",
            "url": "https://example.com/news", "published_at": "2026-09-14T17:00:00Z"}
    research = {"ticker": "AMD", "headline": "AMD updates its roadmap", "source_name": "AMD",
                "source_url": "https://example.com/research", "published_at": "2026-09-14T16:00:00Z",
                "research_notes": "The roadmap changed.", "tradestaar_take": "Verify adoption.",
                "sentiment": "Neutral", "priority": "High"}
    memory = {"ticker": "AMD", "headline": "My AMD thesis", "source_name": "Research Memory",
              "updated_at": "2026-09-13T16:00:00Z", "why_it_matters": "Data center growth matters.",
              "key_change": "Raised expectations.", "thesis_impact": "strengthened",
              "disconfirming_evidence": "Share loss."}

    with (
        patch.object(app, "get_active_wl_id", return_value=4),
        patch.object(app, "get_watchlist_stocks", return_value=["AMD"]),
        patch.object(app, "get_all_stock_data", return_value=[stock]),
        patch.object(app, "get_paper_account", return_value={"cash_balance": 1000}),
        patch.object(app, "get_paper_positions", return_value=[]),
        patch.object(app, "get_fundamentals_cache", return_value={
            "company_name": "Advanced Micro Devices", "normalized_score": 82,
            "verdict": "Strong", "history": [{"period_end": "2026-06-27", "revenue": "$11.5B"}],
            "scorecard_version": __import__("fundamentals_engine").SCORECARD_VERSION,
        }),
        patch.object(app._intel, "get_intel_summary", return_value={"market_news": [news]}),
        patch.object(app, "_get_mkt_ctx", return_value={"regime": "risk_on"}),
        patch("research_feed_phase2.list_published", return_value=[research]) as list_research,
        patch("research_memory.list_cards", return_value=[memory]) as list_memory,
    ):
        context = app._tradestaar_ai_context(7, "What is the latest AMD news?")

    assert list_research.call_args.kwargs["user_id"] == 7
    list_memory.assert_called_once_with(7, limit=30)
    assert context["stocks"][0]["source_id"].startswith("S")
    assert context["headlines"][0]["source_id"].startswith("S")
    assert context["published_research"][0]["source_id"].startswith("S")
    assert context["memory_cards"][0]["source_id"].startswith("S")
    assert context["fundamentals"][0]["normalized_score"] == 82
    assert len({source["id"] for source in context["sources"]}) == len(context["sources"])
    assert any(source["kind"] == "private_memory" for source in context["sources"])


def test_common_question_words_are_not_mistaken_for_tickers():
    with (
        patch.object(app, "get_active_wl_id", return_value=None),
        patch.object(app, "get_all_stock_data", return_value=[]),
        patch.object(app, "get_stock_data") as get_stock,
        patch.object(app, "get_paper_account", return_value={"cash_balance": 0}),
        patch.object(app, "get_paper_positions", return_value=[]),
        patch.object(app, "get_fundamentals_cache", return_value=None),
        patch.object(app._intel, "get_intel_summary", return_value={}),
        patch.object(app, "_get_mkt_ctx", return_value={}),
        patch("research_memory.list_cards", return_value=[]),
    ):
        context = app._tradestaar_ai_context(3, "What is the latest news and why does it matter?")

    get_stock.assert_not_called()
    assert context["stocks"] == []


def test_navigation_groups_secondary_tools_and_ai_ui_renders_sources():
    nav = open("templates/_elite_navigation.html", encoding="utf-8").read()
    ai_page = open("templates/tradestaar_ai.html", encoding="utf-8").read()
    base = open("templates/base.html", encoding="utf-8").read()

    assert nav.count('class="nav-menu"') == 3
    assert "Company Research" in nav and "Live Research" in nav and "Market Intel" in nav
    assert "nav-ai-button" in nav
    assert "result.data.sources" in ai_page
    assert "renderAskSources" in base
    assert "private notes are labeled" in ai_page.lower()


def test_ai_workspace_and_grouped_navigation_render_together():
    import web_app

    context = {
        "watchlist": [], "stocks": [], "fundamentals": [], "published_research": [],
        "memory_cards": [], "headlines": [], "paper_account": {"positions": []},
        "market": {}, "as_of": "2026-09-14T18:00:00-04:00", "sources": [],
    }
    with web_app.app.test_request_context("/ai"):
        session.update(user_id=7, username="researcher", is_admin=0)
        html = render_template("tradestaar_ai.html", ai_context=context)

    assert "Tradestaar <em>AI</em>" in html
    assert "Understand the market" in html
    assert "Data status and account menu" in html
