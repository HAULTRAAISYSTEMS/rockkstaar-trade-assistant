"""Catalyst heat map, earnings reaction replay, explain-this-move."""
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-news-desk")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import news_desk as nd

NOW = datetime(2026, 11, 9, 18, 0, tzinfo=timezone.utc)


def story(h, hours_ago, impact="MEDIUM", summary=""):
    return {"headline": h, "summary": summary, "impact": impact,
            "published_at": (NOW - timedelta(hours=hours_ago)).isoformat(), "url": "https://x/" + h[:5]}


# ── Heat map ─────────────────────────────────────────────────────────────────

def test_tile_scores_recent_impact_weighted_tone():
    t = nd.tile("CEG", [story("CEG soars 10% as guidance raise", 1, "HIGH"),
                        story("CEG beats estimates", 3, "CRITICAL"),
                        story("Old news", 40, "HIGH")], change_pct=9.8, now=NOW)
    assert t["stories"] == 2 and t["label"] == "BULLISH" and t["intensity"] == 60
    assert t["top"] == "CEG beats estimates" and t["change_pct"] == 9.8


def test_tile_bearish_and_empty():
    assert nd.tile("X", [story("X plunges after it misses estimates", 2, "HIGH")], now=NOW)["label"] == "BEARISH"
    empty = nd.tile("Y", [], now=NOW)
    assert empty["stories"] == 0 and empty["intensity"] == 0 and empty["label"] == "NEUTRAL"


def test_heat_map_marks_slow_tickers_pending_and_sorts_by_heat():
    def fetch(t):
        if t == "SLOW":
            time.sleep(1.0)
            return []
        return [story(f"{t} jumps", 1, "HIGH")] * (3 if t == "HOT" else 1)
    out = nd.heat_map(["cool", "HOT", "SLOW"], budget=0.3, fetch=fetch, quotes={"HOT": {"change_pct": 4.2}})
    assert [t["ticker"] for t in out["tiles"]][:2] == ["HOT", "COOL"]
    assert out["pending"] == ["SLOW"]
    assert next(t for t in out["tiles"] if t["ticker"] == "SLOW")["label"] == "PENDING"
    assert out["tiles"][0]["change_pct"] == 4.2
    time.sleep(0.8)


# ── Earnings replay ──────────────────────────────────────────────────────────

def bars_around():
    """Daily bars Nov 3–14 2026 (weekdays); a +8% gap on Nov 10 that holds, fills by Nov 13."""
    days = ["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10",
            "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-14"]
    rows = []
    for d in days:
        rows.append({"time": int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp()),
                     "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000})
    rows[5].update(open=108, high=112, low=107, close=110)     # reaction day
    rows[6].update(open=110, high=111, low=104, close=105)
    rows[7].update(open=105, high=106, low=101, close=102)
    rows[8].update(open=102, high=103, low=99.5, close=100)     # fills the gap
    rows[9].update(open=100, high=101, low=99, close=101)
    return rows


def test_reaction_amc_uses_next_day_and_detects_fill():
    r = nd.reaction(bars_around(), "2026-11-09", "amc")
    assert r["reaction_day"] == "2026-11-10" and r["gap_pct"] == 8.0 and r["day_pct"] == 10.0
    assert r["five_day_pct"] == 1.0 and r["gap_filled"] is True and r["held"] is True


def test_reaction_bmo_same_day_and_unknown_picks_bigger_move():
    assert nd.reaction(bars_around(), "2026-11-10", "bmo")["reaction_day"] == "2026-11-10"
    assert nd.reaction(bars_around(), "2026-11-09", "")["reaction_day"] == "2026-11-10"
    assert nd.reaction(bars_around(), "2027-01-01", "amc") is None


def test_replay_summary():
    dates = [{"date": "2026-11-09", "hour": "amc", "eps": 5.06, "eps_est": 3.72},
             {"date": "2026-11-05", "hour": "bmo", "eps": None, "eps_est": None}]
    out = nd.earnings_replay("CEG", dates=dates, bars=bars_around())
    assert out["ok"] and len(out["reports"]) == 2
    first = out["reports"][0]
    assert first["surprise_pct"] == 36.0 and first["gap_pct"] == 8.0
    s = out["summary"]
    assert s["reports"] == 2 and s["up_days"] == 1 and s["avg_abs_move"] == 5.0


def test_replay_without_history_says_so():
    out = nd.earnings_replay("ZZZ", dates=[], bars=[])
    assert out["ok"] is False and "No past earnings" in out["error"]


def test_earnings_dates_merge_and_skip_future(monkeypatch):
    monkeypatch.setattr(nd, "_edgar_history", lambda t: [])
    monkeypatch.setattr(nd, "_finnhub_history", lambda t: [{"date": "2026-08-05", "hour": "bmo", "eps": 2.7, "eps_est": 2.6},
                                                            {"date": "2099-01-01", "hour": "amc", "eps": None, "eps_est": 1}])
    monkeypatch.setattr(nd, "_fmp_history", lambda t: [{"date": "2026-08-05", "hour": "", "eps": None, "eps_est": None},
                                                        {"date": "2026-05-06", "hour": "", "eps": 2.1, "eps_est": 2.0}])
    rows = nd.earnings_dates("CEG")
    assert [r["date"] for r in rows] == ["2026-08-05", "2026-05-06"]
    assert rows[0]["hour"] == "bmo" and rows[0]["eps"] == 2.7


def test_edgar_8k_item_202_rows_and_hour():
    subs = {"filings": {"recent": {
        "form": ["8-K", "8-K", "10-Q", "8-K"],
        "filingDate": ["2026-08-05", "2026-07-01", "2026-08-06", "2026-05-06"],
        "items": ["2.02,9.01", "5.02", "", "2.02"],
        "acceptanceDateTime": ["2026-08-05T10:31:00.000Z", "2026-07-01T20:00:00.000Z",
                               "2026-08-06T12:00:00.000Z", "2026-05-06T21:05:00.000Z"]}}}
    rows = nd.edgar_report_rows(subs)
    assert [(r["date"], r["hour"]) for r in rows] == [("2026-08-05", "bmo"), ("2026-05-06", "amc")]


def test_earnings_dates_use_edgar_when_vendors_are_thin(monkeypatch):
    monkeypatch.setattr(nd, "_edgar_history", lambda t: [
        {"date": "2026-08-05", "hour": "bmo", "eps": None, "eps_est": None},
        {"date": "2026-05-06", "hour": "amc", "eps": None, "eps_est": None},
        {"date": "2026-02-18", "hour": "bmo", "eps": None, "eps_est": None}])
    # Finnhub free plan: one recent report, a day off from the 8-K.
    monkeypatch.setattr(nd, "_finnhub_history", lambda t: [{"date": "2026-08-06", "hour": "", "eps": 2.7, "eps_est": 2.6}])
    monkeypatch.setattr(nd, "_fmp_history", lambda t: [])
    rows = nd.earnings_dates("CEG")
    assert [r["date"] for r in rows] == ["2026-08-06", "2026-05-06", "2026-02-18"]
    assert rows[0]["eps"] == 2.7 and rows[0]["hour"] == "bmo"


# ── Explain ──────────────────────────────────────────────────────────────────

BASE = {"ticker": "CEG", "change_pct": 9.8, "spy_pct": 0.4, "sector_etf": "XLU", "sector": "Utilities",
        "sector_pct": 0.7, "rel_volume": 3.1, "news": [{"headline": "CEG raises guidance"}]}


def test_rule_explanations_pick_the_driver():
    assert nd.rule_explanation(BASE)["driver"] == "news"
    assert "CEG raises guidance" in nd.rule_explanation(BASE)["detail"]
    sector = dict(BASE, change_pct=-3.0, sector_pct=-2.5, news=[])
    assert nd.rule_explanation(sector)["driver"] == "sector"
    market = dict(BASE, change_pct=-2.5, spy_pct=-1.8, sector_pct=None, sector_etf="", news=[])
    assert nd.rule_explanation(market)["driver"] == "market"
    assert nd.rule_explanation(dict(BASE, news=[], sector_pct=0.1))["driver"] == "flow"
    assert nd.rule_explanation(dict(BASE, change_pct=None))["driver"] == "unknown"


def test_explain_uses_ai_lines_when_available(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    seen = []
    out = nd.explain_move("CEG", facts=BASE, ask=lambda p, k: seen.append(p) or "CEG +9.8% on a beat-and-raise.\nWatch whether it holds VWAP.")
    assert out["ai"] and out["ai_lines"] == ["CEG +9.8% on a beat-and-raise.", "Watch whether it holds VWAP."]
    assert "use ONLY these" in seen[0]
    def boom(*a):
        raise RuntimeError("x")
    fallback = nd.explain_move("CEG", facts=BASE, ask=boom)
    assert fallback["ai"] is False and fallback["driver"] == "news"


# ── Routes / pages ───────────────────────────────────────────────────────────

@pytest.fixture
def client():
    import web_app
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    return c


def test_routes(client, monkeypatch):
    monkeypatch.setattr(nd, "heat_map", lambda tickers: {"ok": True, "tiles": [{"ticker": t} for t in tickers], "pending": []})
    monkeypatch.setattr(nd, "earnings_replay", lambda t: {"ok": True, "ticker": t})
    monkeypatch.setattr(nd, "explain_move", lambda t: {"ok": True, "ticker": t})
    assert client.get("/api/news-desk/heatmap").get_json()["ok"]
    assert client.get("/api/news-desk/replay/ceg").get_json()["ticker"] == "CEG"
    assert client.get("/api/news-desk/explain/CEG").get_json()["ticker"] == "CEG"
    assert client.get("/api/news-desk/explain/%21").status_code in (400, 404)


def test_widgets_are_wired():
    intel = open("templates/intel.html", encoding="utf-8").read()
    assert 'id="nd-grid"' in intel and 'id="nd-explain"' in intel
    assert 'data-move-tools="{{ stock.ticker }}"' in open("templates/stock_detail.html", encoding="utf-8").read()
    assert 'data-move-tools="{{ ticker }}"' in open("templates/research.html", encoding="utf-8").read()
    base = open("templates/base.html", encoding="utf-8").read()
    assert "js/news_desk.js" in base and "css/news_desk.css" in base


def test_heat_map_never_queues_a_ticker_twice_and_caches_empty():
    import threading
    nd._cache.clear()
    calls, gate = [], threading.Event()

    def fetch(t):
        calls.append(t)
        gate.wait(2)
        return []
    nd.heat_map(["AAA", "BBB"], budget=0.05, fetch=fetch, quotes={})
    nd.heat_map(["AAA", "BBB"], budget=0.05, fetch=fetch, quotes={})    # still pending: no new lookups
    assert sorted(calls) == ["AAA", "BBB"]
    gate.set()
    time.sleep(0.2)
    out = nd.heat_map(["AAA", "BBB"], budget=0.5, fetch=fetch, quotes={})
    assert sorted(calls) == ["AAA", "BBB"] and out["pending"] == []     # "no news" was cached
    nd._cache.clear()
