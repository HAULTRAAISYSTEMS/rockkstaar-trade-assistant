"""Notification inbox: one feed of everything the app alerts about, a bell
with an unread count on every page, and a panel on Today."""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import pytest

import notify_inbox as nx
from database import get_db, set_setting

U = 987654          # a user id no other test touches
T0 = datetime(2026, 11, 10, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean():
    def wipe():
        conn = get_db()
        nx.ensure_tables(conn)
        conn.execute("DELETE FROM notifications WHERE user_id = ? OR title LIKE 'ZZTEST%'", (U,))
        conn.commit()
        conn.close()
        set_setting(nx._read_key(U), "")
    wipe()
    yield
    wipe()


def test_add_dedupes_and_lists_newest_first():
    assert nx.add("setup", "ZZTEST AAA breakout", "body", user_id=U, now=T0)
    assert not nx.add("setup", "ZZTEST AAA breakout", "body", user_id=U, now=T0 + timedelta(minutes=5))
    nx.add("position", "ZZTEST BBB stop hit", "x", user_id=U, severity="danger", now=T0 + timedelta(minutes=1))
    out = nx.items(U)
    mine = [i for i in out["items"] if i["title"].startswith("ZZTEST")]
    assert [i["title"] for i in mine] == ["ZZTEST BBB stop hit", "ZZTEST AAA breakout"]
    assert mine[0]["severity"] == "danger" and mine[0]["icon"] == "🛡" and mine[0]["unread"]


def test_unread_and_mark_read():
    nx.add("research", "ZZTEST thesis", user_id=U, now=T0)
    assert nx.unread_count(U) >= 1
    nx.mark_read(U, now=T0 + timedelta(minutes=1))
    nx.add("research", "ZZTEST later", user_id=U, now=T0 + timedelta(minutes=2))
    mine = {i["title"]: i["unread"] for i in nx.items(U)["items"] if i["title"].startswith("ZZTEST")}
    assert mine == {"ZZTEST later": True, "ZZTEST thesis": False}


def test_kind_filter_and_html_is_stripped():
    nx.add("insider", "ZZTEST <b>buy</b>", "<i>CEO</i> bought", user_id=U, now=T0)
    nx.add("setup", "ZZTEST setup", user_id=U, now=T0)
    only = [i for i in nx.items(U, kind="insider")["items"] if i["title"].startswith("ZZTEST")]
    assert [(i["title"], i["body"]) for i in only] == [("ZZTEST buy", "CEO bought")]


def test_add_never_raises():
    class Broken:
        def execute(self, *a, **k):
            raise RuntimeError("db down")
    assert nx.add("setup", "ZZTEST x", conn=Broken()) is False


def test_scanner_alert_lands_in_the_inbox():
    from database import add_scanner_alert
    add_scanner_alert("ZZT", "GUARDIAN STOP HIT", "ZZTEST guardian message", "high")
    rows = [i for i in nx.items(U, kind="position")["items"] if i["body"] == "ZZTEST guardian message"]
    assert rows and rows[0]["severity"] == "danger" and rows[0]["url"] == "/guardian"
    conn = get_db()
    conn.execute("DELETE FROM notifications WHERE body = 'ZZTEST guardian message'")
    conn.execute("DELETE FROM scanner_alerts WHERE ticker = 'ZZT'")
    conn.commit()
    conn.close()


def test_push_payload_is_recorded_even_with_no_device():
    import browser_push
    browser_push.send_to_user(U, {"title": "ZZTEST Recall: 2 notes due", "body": "CEG: why?",
                                  "url": "/research-memory/review", "tag": "recall-weekly"})
    rows = [i for i in nx.items(U)["items"] if i["title"].startswith("ZZTEST Recall")]
    assert rows and rows[0]["kind"] == "memory" and rows[0]["user_id"] == U


def test_insider_push_is_not_doubled():
    import browser_push
    browser_push.send_to_user(U, {"title": "ZZTEST insider", "body": "b", "tag": "insider-1"})
    assert not [i for i in nx.items(U)["items"] if i["title"] == "ZZTEST insider"]


def test_uptime_down_is_recorded(monkeypatch):
    import uptime_watchdog as uw
    seen = []
    monkeypatch.setattr(nx, "add", lambda *a, **k: seen.append((a, k)) or True)
    uw.run(now=1000.0, checker=lambda url: (False, "HTTP 502"), notify=lambda t: True,
           load=lambda: {"status": "up"}, save=lambda s: None)
    assert seen and seen[0][0][:2] == ("system", "Site down") and seen[0][1]["severity"] == "danger"


# ── API + pages ──────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    import web_app
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    return c


def test_api_lists_counts_and_marks_read(client, monkeypatch):
    monkeypatch.setattr(nx, "items", lambda uid, **k: {"items": [], "unread": 3, "read_at": "", "more": False})
    monkeypatch.setattr(nx, "unread_count", lambda uid, **k: 3)
    marked = []
    monkeypatch.setattr(nx, "mark_read", lambda uid: marked.append(uid) or "2026-11-10T14:00:00+00:00")
    d = client.get("/api/notifications?kind=setup").get_json()
    assert d["unread"] == 3 and any(k["kind"] == "insider" for k in d["kinds"])
    assert client.get("/api/notifications/unread").get_json() == {"unread": 3}
    import web_app
    web_app.app.config["WTF_CSRF_ENABLED"] = False
    try:
        assert client.post("/api/notifications/read").get_json()["ok"] and marked == [1]
    finally:
        web_app.app.config["WTF_CSRF_ENABLED"] = True


def test_api_needs_login():
    import web_app
    c = web_app.app.test_client()
    r = c.get("/api/notifications")
    assert r.status_code in (302, 401)


def test_bell_and_drawer_on_every_page_and_panel_on_today(client):
    page = client.get("/").get_data(as_text=True)
    assert 'id="nx-bell"' in page and 'id="nx-drawer"' in page and "notifications.js" in page
    assert 'id="nx-panel"' in page
    other = client.get("/watchlists").get_data(as_text=True)
    assert 'id="nx-bell"' in other and 'id="nx-panel"' not in other


# ── Market feeds (cron) ──────────────────────────────────────────────────────

import inbox_feeds as feeds

NOW = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)


class Rec:
    def __init__(self):
        self.rows, self.keys = [], set()

    def __call__(self, kind, title, body="", **k):
        key = (k.get("user_id", 0), k.get("key"))
        if key in self.keys:
            return False
        self.keys.add(key)
        self.rows.append({"kind": kind, "title": title, "body": body, **k})
        return True


def test_news_per_follower_fresh_only_and_market_headlines():
    rec = Rec()
    watch = {1: {"CEG", "NVDA"}, 2: {"NVDA"}}
    stories = {
        "NVDA": [{"headline": "Nvidia beats", "published_at": (NOW - timedelta(minutes=20)).isoformat(),
                  "impact": "HIGH", "url": "https://x.com/a", "summary": "Data center up", "source": "Reuters"},
                 {"headline": "Old story", "published_at": (NOW - timedelta(hours=9)).isoformat()}],
        "CEG": [],
    }
    market = [{"headline": "Fed cuts rates", "impact": "CRITICAL", "ticker": "MARKET",
               "published_at": (NOW - timedelta(minutes=5)).isoformat()},
              {"headline": "NVDA something", "impact": "HIGH", "ticker": "NVDA",
               "published_at": (NOW - timedelta(minutes=5)).isoformat()},
              {"headline": "Minor note", "impact": "LOW", "ticker": "MARKET",
               "published_at": (NOW - timedelta(minutes=5)).isoformat()}]
    n = feeds.news(watch, now=NOW, ticker_news=lambda t: stories[t], market_news=lambda: market, add=rec)
    titles = sorted((r["user_id"], r["title"]) for r in rec.rows)
    assert titles == [(0, "Fed cuts rates"), (1, "NVDA · Nvidia beats"), (2, "NVDA · Nvidia beats")]
    assert n == 3 and rec.rows[0]["severity"] == "watch" and rec.rows[0]["url"] == "https://x.com/a"
    # A second run with the same stories stores nothing new.
    assert feeds.news(watch, now=NOW, ticker_news=lambda t: stories[t], market_news=lambda: market, add=rec) == 0


def test_earnings_upcoming_and_results():
    rec = Rec()
    cal = {"today": [{"ticker": "CEG", "date": "2026-11-10", "days_away": 0, "eps_est": 2.6, "eps_actual": 2.9},
                     {"ticker": "XYZ", "date": "2026-11-10", "days_away": 0, "eps_est": 1.0, "eps_actual": 0.8}],
           "tomorrow": [{"ticker": "NVDA", "date": "2026-11-11", "days_away": 1, "eps_est": 1.1,
                         "time_label": "AMC"}]}
    feeds.earnings({1: {"CEG", "NVDA"}}, now=NOW, calendar=cal, add=rec)
    got = sorted((r["user_id"], r["title"], r.get("severity", "info")) for r in rec.rows)
    assert got == [(0, "XYZ missed estimates", "watch"), (1, "CEG beat estimates", "info"),
                   (1, "NVDA reports tomorrow — after the close", "info")]


def test_economy_today_and_the_print():
    rec = Rec()
    ev = [{"event": "CPI MoM", "impact": "HIGH", "days_away": 0, "date": "2026-11-10", "time": "8:30 AM",
           "estimate": 0.3, "unit": "%"},
          {"event": "Retail Sales", "impact": "HIGH", "days_away": 0, "date": "2026-11-10",
           "actual": 0.6, "estimate": 0.4, "previous": 0.2, "unit": "%"},
          {"event": "Jobless Claims", "impact": "MEDIUM", "days_away": 0, "date": "2026-11-10"}]
    feeds.economy(now=NOW, events=ev, add=rec)
    assert [r["title"] for r in rec.rows] == ["Today: CPI MoM at 8:30 AM", "Retail Sales: 0.6%"]
    assert rec.rows[1]["body"] == "Actual 0.6% vs 0.4% expected (prior 0.2%)."


def test_run_throttles_provider_calls():
    store = {}
    calls = {"earn": 0, "econ": 0}

    def cal():
        calls["earn"] += 1
        return {}

    def econ():
        calls["econ"] += 1
        return []
    kw = dict(ticker_news=lambda t: [], market_news=lambda: [], earnings_calendar=cal, economic_calendar=econ,
              add=Rec(), get=store.get, put=store.__setitem__)
    feeds.run(now=NOW, **kw)
    feeds.run(now=NOW + timedelta(minutes=5), **kw)
    feeds.run(now=NOW + timedelta(minutes=31), **kw)
    assert calls == {"earn": 2, "econ": 2}
