"""Thesis scorecard, AI bull/bear case, "what changed", weekly recall ping."""
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-memory-edge")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import memory_edge as me

NOW = datetime(2026, 11, 8, 23, 0, tzinfo=timezone.utc)   # Sunday 6 PM ET


@pytest.fixture
def store(monkeypatch):
    data = {}
    monkeypatch.setattr("database.get_setting", lambda k: data.get(k))
    monkeypatch.setattr("database.set_setting", lambda k, v: data.__setitem__(k, v))
    return data


def card(i, impact, days_ago=0, **extra):
    t = (NOW - timedelta(days=days_ago)).isoformat()
    c = {"id": f"c{i}", "ticker": "CEG", "headline": f"Story {i}", "thesis_impact": impact, "why_it_matters": "w",
         "key_change": f"k{i}", "created_at": t, "updated_at": t, "due_at": t, "review_question": f"Q{i}?"}
    c.update(extra)
    return c


# ── Scorecard + AI case ──────────────────────────────────────────────────────

def test_scorecard_tally_and_lean(store):
    cards = [card(1, "strengthened", 9), card(2, "strengthened", 5), card(3, "strengthened", 2), card(4, "weakened", 1)]
    sc = me.thesis_scorecard(1, "ceg", cards)
    assert sc["counts"]["strengthened"] == 3 and sc["net"] == 2 and sc["lean"] == "bull"
    assert [t["id"] for t in sc["timeline"]] == ["c4", "c3", "c2", "c1"]      # newest first
    assert me.thesis_scorecard(1, "CEG", [card(1, "weakened"), card(2, "weakened")])["lean"] == "bear"
    assert me.thesis_scorecard(1, "CEG", [])["lean"] == "none"


def test_ai_case_cached_until_a_card_changes(store, monkeypatch):
    cards = [card(1, "strengthened"), card(2, "weakened")]
    monkeypatch.setattr("research_memory.list_cards", lambda *a, **k: list(cards))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    calls = []

    def ask(prompt, key):
        calls.append(prompt)
        return {"bull": ["EPS beat", "guidance up"], "bear": "valuation\n• power prices", "verdict": "Leans bull.", "watch": ["Q4 guide"]}

    out = me.ai_case(1, "CEG", ask=ask)
    assert out["ok"] and out["case"]["bear"] == ["valuation", "power prices"] and out["case"]["notes"] == 2
    assert "Story 1" in calls[0] and "ONLY these notes" in calls[0]
    assert me.ai_case(1, "CEG", ask=ask)["cached"] is True and len(calls) == 1
    assert me.thesis_scorecard(1, "CEG", cards)["case"]["verdict"] == "Leans bull."
    cards.append(card(3, "strengthened"))
    sc = me.thesis_scorecard(1, "CEG", cards)
    assert sc["case"] is None and sc["case_stale"] is True
    me.ai_case(1, "CEG", ask=ask)
    assert len(calls) == 2


def test_ai_case_needs_notes_and_survives_failure(store, monkeypatch):
    monkeypatch.setattr("research_memory.list_cards", lambda *a, **k: [])
    assert me.ai_case(1, "CEG", ask=lambda *a: {})["ok"] is False
    monkeypatch.setattr("research_memory.list_cards", lambda *a, **k: [card(1, "neutral")])

    def boom(*a):
        raise RuntimeError("timeout")

    out = me.ai_case(1, "CEG", ask=boom)
    assert out["ok"] is False and "could not" in out["error"]


# ── What changed ─────────────────────────────────────────────────────────────

def test_first_look_then_brief_since_previous_visit(store):
    first = me.whats_changed(1, "CEG", now=NOW - timedelta(days=2), price=250.0, news=[], cards=[], earnings_days=None)
    assert first["first_look"] is True
    news = [{"headline": "CEG raises guidance", "published_at": (NOW - timedelta(hours=5)).isoformat(), "source": "Benzinga"},
            {"headline": "Old story", "published_at": (NOW - timedelta(days=3)).isoformat()}]
    cards = [card(1, "strengthened", 0)]
    out = me.whats_changed(1, "CEG", now=NOW, price=275.0, news=news, cards=cards, earnings_days=5, earnings_date="2026-11-14")
    assert out["first_look"] is False and out["since_label"] == "2 days ago"
    assert out["move_pct"] == 10.0 and out["lines"][0].startswith("▲ +10.00%")
    assert "1 new headline" in out["lines"] and "1 new note in your Memory" in out["lines"]
    assert any("Earnings in 5 days" in l for l in out["lines"])
    assert [h["headline"] for h in out["headlines"]] == ["CEG raises guidance"]


def test_reload_within_30_min_keeps_the_same_baseline(store):
    me.whats_changed(1, "CEG", now=NOW - timedelta(days=1), price=100.0, news=[], cards=[], earnings_days=None)
    a = me.whats_changed(1, "CEG", now=NOW, price=110.0, news=[], cards=[], earnings_days=None)
    b = me.whats_changed(1, "CEG", now=NOW + timedelta(minutes=10), price=111.0, news=[], cards=[], earnings_days=None)
    assert a["move_pct"] == 10.0 and b["move_pct"] == 11.0          # both measured from the visit a day ago


def test_nothing_new_says_so(store):
    me.whats_changed(1, "CEG", now=NOW - timedelta(hours=2), price=None, news=[], cards=[], earnings_days=None)
    out = me.whats_changed(1, "CEG", now=NOW, price=None, news=[], cards=[], earnings_days=None)
    assert out["lines"] == ["Nothing material — no price data, news or notes since then."]


# ── Weekly recall ping ──────────────────────────────────────────────────────

def test_recall_ping_sends_once_on_ping_day(store, monkeypatch):
    monkeypatch.setenv("RECALL_PING_WEEKDAYS", "6")
    monkeypatch.setenv("RECALL_PING_HOUR_ET", "18")
    pushed, tg = [], []
    due = [card(1, "strengthened", 3), card(2, "weakened", 1), card(3, "neutral", 2), card(4, "neutral", 0)]
    kw = dict(push=lambda u, p: pushed.append(p) or {"sent": 1}, telegram=lambda t: tg.append(t) or True, due_cards=due)
    out = me.recall_ping(1, now=NOW, **kw)
    assert out["sent"] and out["due"] == 4 and len(out["questions"]) == 3
    assert pushed[0]["url"] == "/research-memory/review" and "+3 more due" in pushed[0]["body"]
    assert "CEG: Q1?" in tg[0]                                       # oldest due first
    assert me.recall_ping(1, now=NOW + timedelta(minutes=5), **kw)["reason"] == "already sent today"
    monday = NOW + timedelta(days=1)
    assert me.recall_ping(1, now=monday, **kw)["reason"] == "not ping time"
    assert me.recall_ping(1, now=NOW - timedelta(hours=3), **kw)["reason"] == "not ping time"   # 3 PM ET


def test_recall_ping_with_nothing_due(store):
    out = me.recall_ping(1, now=NOW, due_cards=[], push=lambda *a: None, telegram=lambda t: True)
    assert out["sent"] is False and out["reason"] == "nothing due"


# ── Routes + pages ──────────────────────────────────────────────────────────

@pytest.fixture
def client():
    import web_app
    app = web_app.app
    old = app.config.get("WTF_CSRF_ENABLED", True)
    app.config["WTF_CSRF_ENABLED"] = False
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    yield c
    app.config["WTF_CSRF_ENABLED"] = old


def test_routes(client, monkeypatch):
    monkeypatch.setattr(me, "thesis_scorecard", lambda u, t: {"ticker": t, "total": 0})
    monkeypatch.setattr(me, "whats_changed", lambda u, t: {"ok": True, "lines": ["x"], "ticker": t})
    monkeypatch.setattr(me, "ai_case", lambda u, t: {"ok": True, "case": {"bull": []}})
    assert client.get("/api/memory/scorecard/ceg").get_json()["ticker"] == "CEG"
    assert client.get("/api/memory/changed/CEG").get_json()["lines"] == ["x"]
    assert client.post("/api/memory/case/CEG").get_json()["ok"]
    assert client.get("/api/memory/changed/!!").status_code == 400


def test_widget_is_on_the_pages_and_runner_pings():
    assert 'data-ticker-brief="{{ ticker }}"' in open("templates/research.html", encoding="utf-8").read()
    assert 'data-ticker-brief="{{ stock.ticker }}"' in open("templates/stock_detail.html", encoding="utf-8").read()
    assert "data-ticker-brief" in open("templates/research_memory.html", encoding="utf-8").read()
    assert "js/ticker_brief.js" in open("templates/base.html", encoding="utf-8").read()
    assert "memory_edge.recall_ping" in open("live_research_runner.py", encoding="utf-8").read()


def test_push_send_to_user_handles_no_devices():
    import sqlite3
    import browser_push
    from migrations import m0007_browser_push
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    m0007_browser_push.upgrade(db)
    assert browser_push.send_to_user(1, {"title": "x"}, conn=db) == {"sent": 0, "expired": 0, "errors": 0}
