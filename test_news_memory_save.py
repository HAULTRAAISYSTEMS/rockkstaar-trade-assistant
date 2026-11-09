"""Saving news stories into Research Memory with an AI summary, and per-story scoring."""
import os
import sqlite3
from unittest.mock import patch

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-news-memory")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import news_summarizer as ns
import research_memory as memory
from migrations import m0001_live_research_feed, m0002_live_research_triage, m0005_research_memory
from news_fetcher import CatalystNews
from sentiment_engine import enrich_news_article, score_headline

CEG = {
    "ticker": "CEG",
    "headline": "Constellation Energy earnings analysis: questions answered and next catalysts",
    "summary": "Investing.com -- Constellation Energy Corp (CEG) reported Q3 on Nov 9, 2026. "
               "Adjusted EPS was $5.06 vs $3.72 expected, a 36% beat. Revenue was $9.55B vs $9.21B expected.",
    "url": "https://finance.example.com/ceg-q3",
    "source": "Yahoo",
    "published_at": "2026-11-09T21:00:00+00:00",
}


@pytest.fixture(autouse=True)
def _clear_cache():
    ns._cache.clear()
    yield
    ns._cache.clear()


# ── Summarizer ───────────────────────────────────────────────────────────────

def test_ai_summary_fills_every_field(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    seen = {}

    def ask(item, text, key):
        seen["text"] = text
        return {"summary": "CEG beat Q3 estimates.", "key_points": ["EPS $5.06 vs $3.72", "Revenue $9.55B"],
                "why_it_matters": "Earnings power is ahead of the street.", "key_change": "EPS beat by 36%",
                "thesis_impact": "Strengthened", "disconfirming_evidence": "Q4 guide falls short.",
                "review_question": "What was CEG's Q3 EPS beat?", "tone": "bullish"}

    out = ns.summarize(CEG, fetch=lambda url: "", ask=ask)
    assert out["ok"] and out["ai"] is True
    assert out["thesis_impact"] == "strengthened" and out["tone"] == "BULLISH"
    assert out["key_points"] == ["EPS $5.06 vs $3.72", "Revenue $9.55B"]
    assert out["basis"] == "provider summary"
    assert "36% beat" in seen["text"]


def test_full_article_is_preferred_when_readable(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    article = "Constellation said its nuclear fleet ran at 99.2% capacity. " * 10
    captured = {}
    out = ns.summarize(CEG, fetch=lambda url: article,
                       ask=lambda item, text, key: captured.setdefault("t", text) and {"summary": "x"})
    assert out["basis"] == "full article"
    assert "99.2%" in captured["t"] and "36% beat" in captured["t"]


def test_without_a_key_it_fills_from_the_story_text(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = ns.summarize(CEG, fetch=lambda url: "")
    assert out["ok"] and out["ai"] is False
    assert "No AI key" in out["note"]
    assert any("$5.06" in p for p in out["key_points"])
    assert out["thesis_impact"] == "strengthened"
    assert out["why_it_matters"]


def test_model_failure_falls_back_and_says_so(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    def boom(*a):
        raise RuntimeError("rate limited")

    out = ns.summarize(CEG, fetch=lambda url: "", ask=boom)
    assert out["ok"] and out["ai"] is False and "unavailable" in out["note"]


def test_ai_results_are_cached_but_fresh_bypasses(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    calls = []
    ask = lambda item, text, key: calls.append(1) or {"summary": "s"}
    ns.summarize(CEG, fetch=lambda url: "", ask=ask)
    assert ns.summarize(CEG, fetch=lambda url: "", ask=ask)["cached"] is True
    ns.summarize(CEG, fetch=lambda url: "", ask=ask, fresh=True)
    assert len(calls) == 2


def test_headline_is_required():
    assert ns.summarize({"ticker": "CEG"})["ok"] is False


def test_bad_values_from_the_model_are_cleaned(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    out = ns.summarize(CEG, fetch=lambda url: "",
                       ask=lambda *a: {"thesis_impact": "moon", "tone": "??", "key_points": "a\n• b"})
    assert out["thesis_impact"] in ns.IMPACTS
    assert out["tone"] in ("BULLISH", "BEARISH", "NEUTRAL")
    assert out["key_points"] == ["a", "b"]


def test_article_fetch_refuses_private_addresses():
    for url in ("http://127.0.0.1/admin", "http://169.254.169.254/latest/meta-data",
                "http://localhost:5000/", "file:///etc/passwd", "ftp://example.com/x"):
        assert ns._safe_url(url) is False
        assert ns.fetch_article_text(url) == ""


def test_html_to_text_keeps_paragraphs_drops_scripts():
    page = ("<html><script>var x='Ignore me, a long script string here!!';</script><nav><p>Menu menu menu menu "
            "menu menu menu menu menu</p></nav><p>Short</p><p>Constellation Energy reported "
            "third-quarter adjusted EPS of $5.06 &amp; raised guidance.</p></html>")
    text = ns.html_to_text(page)
    assert "Ignore me" not in text and "Menu" not in text and "Short" not in text
    assert "$5.06 & raised guidance" in text


def test_notes_block_layout():
    block = ns.notes_block("CEG beat.", ["EPS $5.06", " ", "Revenue $9.55B"])
    assert block == "SUMMARY\nCEG beat.\n\nKEY POINTS\n• EPS $5.06\n• Revenue $9.55B"
    assert ns.notes_block("", []) == ""


# ── Research Memory dedupe ───────────────────────────────────────────────────

@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO users(id) VALUES (1),(2)")
    m0001_live_research_feed.upgrade(conn)
    m0002_live_research_triage.upgrade(conn)
    m0005_research_memory.upgrade(conn)
    yield conn
    conn.close()


def _card(**extra):
    data = {"ticker": "CEG", "headline": CEG["headline"], "source_url": CEG["url"],
            "why_it_matters": "Beat and raise.", "thesis_impact": "strengthened"}
    data.update(extra)
    return data


def test_same_story_twice_returns_the_same_card(db):
    first = memory.create_card(1, _card(), conn=db)
    again = memory.create_card(1, _card(why_it_matters="dup"), conn=db)
    by_headline = memory.create_card(1, _card(source_url=""), conn=db)
    assert first == again == by_headline
    assert len(memory.list_cards(1, conn=db)) == 1
    # Another user saving the same story gets their own card.
    assert memory.create_card(2, _card(), conn=db) != first


def test_saved_story_keys_marks_links_and_headlines(db):
    card = memory.create_card(1, _card(), conn=db)
    keys = memory.saved_story_keys(1, conn=db)
    assert keys["urls"][CEG["url"]] == card
    assert keys["headlines"][" ".join(CEG["headline"].lower().split())] == card
    memory.archive_card(1, card, conn=db)
    assert memory.saved_story_keys(1, conn=db)["urls"] == {}


# ── Per-story scoring ────────────────────────────────────────────────────────

def test_lead_text_moves_tone_for_bland_headlines():
    assert score_headline(CEG["headline"])["label"] == "NEUTRAL"
    assert score_headline(CEG["headline"], CEG["summary"])["label"] == "BULLISH"
    enriched = enrich_news_article(dict(CEG, impact="MEDIUM"))
    assert enriched["label"] == "BULLISH"


def test_price_and_guidance_words_score():
    assert score_headline("Constellation Energy Soars 10% as Guidance Raise Shrugs Off Backlash")["label"] == "BULLISH"
    assert score_headline("Retailer plunges after it misses estimates")["label"] == "BEARISH"


def test_each_ticker_story_is_classified_on_its_own_words():
    import intel_engine
    arts = (
        {"headline": "Constellation Energy Soars 10% as Guidance Raise Shrugs Off Data Center Backlash", "summary": ""},
        {"headline": "Constellation Energy Corporation Q3 2026 Earnings Call Summary", "summary": ""},
        {"headline": "Sector Update: Energy Stocks Higher Monday Afternoon", "summary": ""},
        {"headline": "Constellation Energy beat estimates on earnings", "summary": ""},
    )
    news = CatalystNews(headlines=[a["headline"] for a in arts], summary="",
                        categories=["earnings_beat"], freshness_minutes=5, source="finnhub", articles=arts)
    items = intel_engine._news_items_for_ticker("CEG", set(), limit=8, fetch=lambda t: news)
    soars = next(i for i in items if "Soars" in i["headline"])
    assert soars["impact"] == "HIGH"                                # guidance raise
    assert soars["reason"].startswith("Guidance raised")
    call = next(i for i in items if "Earnings Call" in i["headline"])
    assert call["impact"] == "MEDIUM" and "General market news" not in call["reason"]
    sector = next(i for i in items if i["headline"].startswith("Sector Update"))
    # The batch's earnings beat no longer leaks into an unrelated story.
    assert sector["impact"] == "LOW" and sector["reason"] == "Company news"
    beat = next(i for i in items if "beat estimates" in i["headline"])
    assert beat["impact"] == "CRITICAL"


# ── Routes and templates ─────────────────────────────────────────────────────

@pytest.fixture
def client():
    import web_app
    app = web_app.app
    old = app.config.get("WTF_CSRF_ENABLED", True)
    app.config["WTF_CSRF_ENABLED"] = False
    c = app.test_client()
    with c.session_transaction() as session:
        session["user_id"] = 1
    yield c
    app.config["WTF_CSRF_ENABLED"] = old


def test_summarize_route(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch.object(ns, "fetch_article_text", return_value=""):
        resp = client.post("/research-memory/api/summarize", json=CEG)
    data = resp.get_json()
    assert resp.status_code == 200 and data["ok"] and data["why_it_matters"]


def test_create_route_builds_notes_and_reports_duplicates(client):
    payload = dict(CEG, summary="CEG beat.", key_points=["EPS $5.06"], why_it_matters="Beat.",
                   thesis_impact="strengthened", notes="Watch Q4.")
    with patch.object(memory, "find_existing", return_value=None), \
         patch.object(memory, "create_card", return_value="card-1") as create:
        resp = client.post("/research-memory/api/cards", json=payload)
    data = resp.get_json()
    assert data == {"ok": True, "id": "card-1", "duplicate": False,
                    "url": "/research-memory?saved=card-1#card-card-1"}
    sent = create.call_args[0][1]
    assert sent["notes"] == "SUMMARY\nCEG beat.\n\nKEY POINTS\n• EPS $5.06\n\nMY NOTES\nWatch Q4."
    assert sent["source_url"] == CEG["url"] and sent["source_name"] == "Yahoo"
    with patch.object(memory, "find_existing", return_value="card-1"), \
         patch.object(memory, "create_card") as create2:
        dup = client.post("/research-memory/api/cards", json=payload).get_json()
    assert dup["duplicate"] is True and not create2.called


def test_create_route_requires_why(client):
    with patch.object(memory, "find_existing", return_value=None):
        resp = client.post("/research-memory/api/cards", json=dict(CEG, why_it_matters=""))
    assert resp.status_code == 400 and "why this matters" in resp.get_json()["error"]


def test_saved_route(client):
    with patch.object(memory, "saved_story_keys", return_value={"urls": {"u": "c"}, "headlines": {}}):
        data = client.get("/research-memory/api/saved").get_json()
    assert data["ok"] and data["urls"] == {"u": "c"}


def test_api_requires_sign_in():
    import web_app
    c = web_app.app.test_client()
    resp = c.get("/research-memory/api/saved")
    assert resp.status_code in (302, 401)


def test_news_cards_carry_a_save_button():
    import web_app
    from flask import render_template
    item = enrich_news_article(dict(CEG, impact="MEDIUM", reason="Company news"))
    with web_app.app.test_request_context("/intel"):
        html = render_template("_intel_news_items.html", news=[item], intel_status={})
    assert 'class="scanner-save"' in html and "Save to Memory" in html
    assert "data-save='" in html and '"ticker": "CEG"' in html


def test_base_layout_loads_the_save_sheet():
    base = open("templates/base.html", encoding="utf-8").read()
    assert "js/memory_save.js" in base and "css/memory_save.css" in base
    js = open("static/js/memory_save.js", encoding="utf-8").read()
    assert "/research-memory/api/summarize" in js and "/research-memory/api/cards" in js
    assert "tw-save" in open("templates/terminal.html", encoding="utf-8").read()


# ── The Nov 9 outage: page parsing froze the worker ─────────────────────────

def test_page_with_unclosed_tags_parses_fast():
    import time
    page = (("<div>" + "<form action=x>" * 3 + "<p>" + "word " * 30 + "</p>") * 4000)[:828000]
    started = time.time()
    ns.html_to_text(page)
    assert time.time() - started < 2.0          # was 44 seconds with the regex


def test_paragraphs_inside_unclosed_menu_are_skipped_but_later_text_kept():
    text = ns.html_to_text("<nav><p>Menu link menu link menu link menu link menu</p></nav>"
                           "<p>The company reported adjusted EPS of $5.06, ahead of estimates.</p>")
    assert "Menu" not in text and "$5.06" in text


def test_slow_summary_returns_story_text_within_deadline(monkeypatch):
    import time
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(ns, "DEADLINE", 0.3)
    started = time.time()
    out = ns.summarize(CEG, fetch=lambda url: "", ask=lambda *a: time.sleep(1.5) or {"summary": "late"})
    assert time.time() - started < 1.0
    assert out["ok"] and out["ai"] is False and "too long" in out["note"]
    time.sleep(1.3)                              # let the pool thread finish


def test_busy_summarizer_does_not_queue_requests(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    taken = [ns._slots.acquire(blocking=False) for _ in range(ns.MAX_CONCURRENT)]
    try:
        out = ns.summarize(CEG, fetch=lambda url: "", ask=lambda *a: {"summary": "x"})
        assert out["ok"] and out["ai"] is False and "busy" in out["note"]
    finally:
        for ok in taken:
            if ok:
                ns._slots.release()


def test_model_call_has_a_timeout():
    src = open("news_summarizer.py", encoding="utf-8").read()
    assert "timeout=MODEL_TIMEOUT" in src and "max_retries=0" in src
