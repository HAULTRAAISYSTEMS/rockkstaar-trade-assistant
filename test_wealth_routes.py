"""Wealth Architect routes: privacy, portfolio ownership, import, dashboard, lab."""
import os
import tempfile

import pytest

SCHWAB = '''"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"
"02/10/2026","Buy","KLAC","KLA CORP","2","$1,250.00","","-$2,500.00"
"02/01/2026","MoneyLink Transfer","","Tfr BANK","","","","$3,000.00"
'''


@pytest.fixture
def env(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    import database
    monkeypatch.setattr(database, "DB_PATH", path, raising=False)
    if hasattr(database, "_DB_PATH"):
        monkeypatch.setattr(database, "_DB_PATH", path)
    database.init_db()
    import wealth_routes
    from flask import Flask, session

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = "x" * 32
    from flask_wtf.csrf import CSRFProtect  # noqa: F401  (the real app uses it; tests post without)

    prices = {"KLAC": {"price": 1400.0, "as_of": "2026-11-18T15:00Z", "source": "schwab", "live": True},
              "SPY": {"price": 700.0, "as_of": "2026-11-18T15:00Z", "source": "schwab", "live": True}}
    broker = {"connected": True, "accounts": [
        {"account_number": "12345678", "account_type": "IRA", "cash_balance": 500.0,
         "equity_positions": [{"symbol": "KLAC", "quantity": 2, "avg_price": 1250.0}]}]}
    bp = wealth_routes.create_wealth_blueprint(
        current_user=lambda: {"id": session.get("user_id")} if session.get("user_id") else None,
        price_lookup=lambda ts: {t: prices[t] for t in ts if t in prices},
        bars_lookup=lambda t: {"timestamps": [1767225600, 1769904000, 1770681600], "closes": [600.0, 610.0, 620.0]},
        broker_summary=lambda uid: broker,
        fundamentals_lookup=lambda t: ({"ticker": t, "company_name": t + " Inc", "sector": "Technology", "score_percent": 80,
                                        "scored_metrics": 10, "total_metrics": 10, "red_flags": [], "sections": [],
                                        "valuation": {"pe": 28.0},
                                        "history": [{"year_offset": 0, "revenue_num": 10, "net_income_num": 3},
                                                    {"year_offset": 3, "revenue_num": 5, "net_income_num": 1.5}]},
                                       "2026-11-18T09:00:00"),
        zone_lookup=lambda t, px: {"zone": "accumulate", "zone_label": "Accumulate", "accum_top": 1500, "load_top": 1100},
        ai_draft=lambda t, g, m: ({"score": 7, "evidence": "draft", "risks": "r", "confidence": "low", "check": ["10-K"]}, None))
    app.register_blueprint(bp)

    @app.context_processor
    def _ctx():
        return {"csrf_token": lambda: "t"}

    # base.html needs the app's own endpoints; render pages through the real app instead.
    yield app, path, prices
    os.unlink(path)


def client(app, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = uid
    return c


def make_portfolio(c, name="Roth IRA", kind="roth_ira"):
    d = c.post("/api/wealth/portfolios", json={"name": name, "kind": kind}).get_json()
    assert d["ok"]
    return d["id"]


def test_requires_sign_in(env):
    app, *_ = env
    assert app.test_client().post("/api/wealth/portfolios", json={"name": "x"}).status_code == 401


def test_import_preview_commit_and_dedupe(env):
    app, *_ = env
    c = client(app, 1)
    pid = make_portfolio(c)
    prev = c.post("/api/wealth/import/preview", json={"text": SCHWAB}).get_json()
    assert prev["ok"] and prev["format"] == "schwab" and len(prev["transactions"]) == 2
    r = c.post("/api/wealth/import/commit", json={"portfolio_id": pid, "transactions": prev["transactions"]}).get_json()
    assert r["added"] == 2 and r["duplicates"] == 0
    again = c.post("/api/wealth/import/commit", json={"portfolio_id": pid, "transactions": prev["transactions"]}).get_json()
    assert again["added"] == 0 and again["duplicates"] == 2

    s = c.get(f"/api/wealth/summary?p={pid}").get_json()
    assert s["market_value"] == pytest.approx(2800) and s["cash"] == pytest.approx(500)
    assert s["unrealized"] == pytest.approx(300) and s["net_contributions"] == pytest.approx(3000)
    assert s["total_return"] == pytest.approx(300)
    assert s["holdings"][0]["price_source"] == "schwab"


def test_other_users_cannot_see_or_touch_a_portfolio(env):
    app, *_ = env
    a, b = client(app, 1), client(app, 2)
    pid = make_portfolio(a)
    assert a.post("/api/wealth/transactions", json={"portfolio_id": pid, "date": "2026-01-02", "type": "DEPOSIT",
                                                     "amount": 100}).get_json()["ok"]
    assert b.get(f"/api/wealth/summary?p={pid}").status_code == 404
    assert b.post("/api/wealth/transactions", json={"portfolio_id": pid, "date": "2026-01-02", "type": "DEPOSIT",
                                                     "amount": 100}).status_code == 404
    assert b.delete(f"/api/wealth/portfolios/{pid}").status_code == 404
    tx_id = __import__("wealth_store").list_transactions(1, pid)[0]["id"]
    assert b.delete(f"/api/wealth/transactions/{tx_id}").status_code == 404
    assert b.get("/api/wealth/summary").get_json()["transaction_count"] == 0


def test_bad_transactions_are_rejected_with_reasons(env):
    app, *_ = env
    c = client(app, 1)
    pid = make_portfolio(c)
    r = c.post("/api/wealth/transactions", json={"portfolio_id": pid, "date": "2026-01-02", "type": "BUY",
                                                  "ticker": "KLAC", "shares": 0, "price": 10})
    assert r.status_code == 400 and "Shares must be" in r.get_json()["error"]
    r = c.post("/api/wealth/transactions", json={"portfolio_id": pid, "date": "2026-01-02", "type": "BUY",
                                                  "ticker": "<script>", "shares": 1, "price": 10})
    assert r.status_code == 400


def test_missing_price_is_listed_not_invented(env):
    app, _, prices = env
    c = client(app, 1)
    pid = make_portfolio(c)
    for tx in ({"type": "DEPOSIT", "amount": 1000}, {"type": "BUY", "ticker": "ZZZQ", "shares": 1, "price": 100}):
        c.post("/api/wealth/transactions", json={"portfolio_id": pid, "date": "2026-01-02", **tx})
    s = c.get(f"/api/wealth/summary?p={pid}").get_json()
    assert s["unpriced"] == ["ZZZQ"] and s["total_return"] is None and s["money_weighted_pct"] is None
    assert s["holdings"][0]["market_value"] is None
    bench = c.get(f"/api/wealth/benchmark?p={pid}").get_json()
    assert not bench["ok"] and "no price" in bench["reason"]


def test_benchmark_uses_same_deposit_dates(env):
    app, *_ = env
    c = client(app, 1)
    pid = make_portfolio(c)
    c.post("/api/wealth/transactions", json={"portfolio_id": pid, "date": "2026-01-01", "type": "DEPOSIT", "amount": 600})
    d = c.get(f"/api/wealth/benchmark?p={pid}").get_json()
    assert d["ok"] and d["spy"]["value"] == pytest.approx(700)       # 1 SPY unit bought at 600, worth 700


def test_lab_api(env):
    app, *_ = env
    c = client(app, 1)
    r = c.post("/api/wealth/lab", json={"existing_shares": 10, "existing_avg_cost": 150, "purchase_price": 120,
                                        "invest_amount": 600, "future_price": 180})
    assert r.status_code == 200 and r.get_json()["new_avg_cost"] == pytest.approx(140)
    assert c.post("/api/wealth/lab", json={"purchase_price": 0}).status_code == 400


def test_reconcile_against_schwab_account(env):
    app, *_ = env
    c = client(app, 1)
    pid = make_portfolio(c)
    prev = c.post("/api/wealth/import/preview", json={"text": SCHWAB}).get_json()
    c.post("/api/wealth/import/commit", json={"portfolio_id": pid, "transactions": prev["transactions"]})
    first = c.get(f"/api/wealth/reconcile?p={pid}").get_json()
    assert first["needs_account"] and first["accounts"][0]["label"].endswith("5678")
    rec = c.get(f"/api/wealth/reconcile?p={pid}&account=12345678").get_json()
    assert rec["matched"] == 1 and rec["rows"][0]["status"] == "match" and rec["cash_diff"] == pytest.approx(0)
    # The account choice is remembered for next time.
    assert c.get(f"/api/wealth/reconcile?p={pid}").get_json()["matched"] == 1


def test_pages_render_through_the_real_app(monkeypatch):
    import web_app
    import app as legacy
    import wealth_store
    monkeypatch.setattr(legacy, "_auth_required", lambda: False)
    monkeypatch.setattr(wealth_store, "list_portfolios", lambda uid: [])
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    monkeypatch.setattr(wealth_store, "list_research", lambda uid: {})
    for url in ("/wealth", "/wealth/ledger", "/wealth/lab", "/wealth/scoreboard", "/wealth/research",
                "/wealth/next-dollar", "/wealth/planner"):
        r = c.get(url)
        assert r.status_code == 200, url
        html = r.get_data(as_text=True)
        assert "Wealth Architect" in html
    assert "Start with a portfolio" in c.get("/wealth").get_data(as_text=True)
    # Merged pages: old links land on their new homes.
    r = c.get("/wealth/research/klac")
    assert r.status_code == 302 and r.headers["Location"].endswith("/fundamentals?ticker=KLAC&tab=mcff")
    r = c.get("/wealth/capital?p=7")
    assert r.status_code == 302 and r.headers["Location"].endswith("/wealth/next-dollar?p=7#split")
    # And with a portfolio: every page must build its links.
    monkeypatch.setattr(wealth_store, "list_portfolios", lambda uid: [
        {"id": 7, "name": "Roth IRA", "kind": "roth_ira", "max_position_pct": None, "target_cash_pct": None}])
    monkeypatch.setattr(wealth_store, "get_portfolio", lambda uid, pid: {"id": 7, "name": "Roth IRA", "kind": "roth_ira",
                                                                         "max_position_pct": None, "target_cash_pct": None})
    monkeypatch.setattr(wealth_store, "list_transactions", lambda uid, pid=None: [])
    for url in ("/wealth", "/wealth?p=7", "/wealth/ledger?p=7", "/wealth/lab?p=7", "/wealth/scoreboard?p=7",
                "/wealth/research", "/wealth/next-dollar?p=7", "/wealth/planner"):
        assert c.get(url).status_code == 200, url
    nd = c.get("/wealth/next-dollar?p=7").get_data(as_text=True)
    for tab in ("rank", "split", "journal"):
        assert f'data-nd-tab="{tab}"' in nd
    assert 'id="capital-form"' in nd and 'id="capital-journal"' in nd and "INTELLIGENCE WORKFLOW" not in nd
    lab = c.get("/wealth/lab?p=7").get_data(as_text=True)
    assert "Compare your next dollar" not in lab and "lab-banner" not in lab and "Investment Lab 2.0" not in lab


def test_scoreboard_api(env):
    app, *_ = env
    c = client(app, 1)
    pid = make_portfolio(c)
    for tx in ({"date": "2026-01-01", "type": "DEPOSIT", "amount": 3000},
               {"date": "2026-02-01", "type": "BUY", "ticker": "KLAC", "shares": 2, "price": 600}):
        c.post("/api/wealth/transactions", json={"portfolio_id": pid, **tx})
    d = c.get(f"/api/wealth/scoreboard?p={pid}").get_json()
    # The fixture's bars serve every ticker (KLAC and SPY) from the same closes.
    assert d["ok"] and d["contributed"] == 3000
    assert "twr_pct" in d and "spy_pct" in d and d["chart"]["days"]
    assert d["end_value"] == pytest.approx(1800 + 2 * 1400)       # cash + live KLAC today


def test_mcff_save_status_and_privacy(env):
    app, *_ = env
    a, b = client(app, 1), client(app, 2)
    first = a.get("/api/wealth/mcff/KLAC").get_json()
    assert first["ok"] and first["evaluation"]["status"] == "DO NOT BUY"
    assert first["evaluation"]["gates"]["financials"]["passed"] is True
    g = {"score": 8, "evidence": "e", "origin": "investor"}
    saved = a.post("/api/wealth/mcff/KLAC", json={"moat": g, "ceo": g, "future": g, "buy_high": 1500,
                                                   "assumptions": {"growth": {"base": 0.12}}}).get_json()
    assert saved["evaluation"]["status"] == "BUY ZONE"                      # price 1,400 ≤ 1,500
    base = [s for s in saved["scenarios"] if s["case"] == "base"][0]
    assert base["growth"] == 0.12 and base["annual_return_pct"] == pytest.approx(12, abs=0.01)
    assert b.get("/api/wealth/mcff/KLAC").get_json()["evaluation"]["status"] == "DO NOT BUY"   # not user 2's research
    st = a.get("/api/wealth/mcff-status").get_json()
    assert st["rows"][0]["ticker"] == "KLAC" and st["rows"][0]["status"] == "BUY ZONE"
    d = a.post("/api/wealth/mcff/KLAC/draft", json={"gate": "moat"}).get_json()
    assert d["ok"] and d["draft"]["evidence"] == "draft"
    assert a.post("/api/wealth/mcff/KLAC/draft", json={"gate": "financials"}).status_code == 400
    assert a.get("/api/wealth/mcff/<x>").status_code == 404


def test_next_dollar_api(env):
    app, *_ = env
    c = client(app, 1)
    pid = make_portfolio(c)
    c.post("/api/wealth/transactions", json={"portfolio_id": pid, "date": "2026-01-01", "type": "DEPOSIT", "amount": 5000})
    g = {"score": 8, "origin": "investor"}
    c.post("/api/wealth/mcff/KLAC", json={"moat": g, "ceo": g, "future": g, "buy_high": 1500})
    r = c.post("/api/wealth/next-dollar", json={"portfolio_id": pid, "amount": 500, "tickers": ["KLAC", "SPY"]}).get_json()
    assert r["ok"] and r["rows"][0]["ticker"] == "KLAC" and r["rows"][1]["ticker"] == "CASH"
    assert r["rows"][2]["ticker"] == "SPY" and r["rows"][2]["status"] == "DO NOT BUY"
    assert c.post("/api/wealth/next-dollar", json={"portfolio_id": pid, "amount": 0, "tickers": ["KLAC"]}).status_code == 400


def test_planner_api_and_ira_tax_year_on_deposits(env):
    app, *_ = env
    c = client(app, 1)
    pid = make_portfolio(c)
    ok = c.post("/api/wealth/transactions", json={"portfolio_id": pid, "date": "2027-02-01", "type": "DEPOSIT",
                                                   "amount": 1000, "tax_year": 2026}).get_json()
    assert ok["ok"]
    late = c.post("/api/wealth/transactions", json={"portfolio_id": pid, "date": "2027-05-01", "type": "DEPOSIT",
                                                     "amount": 1000, "tax_year": 2026})
    assert late.status_code == 400 and "April 15" in late.get_json()["error"]
    v = c.post("/api/wealth/plan/2027", json={"entries": [{"portfolio_id": pid, "months": [1, 2, 3], "amount": 2000}]}).get_json()
    ira = {r["tax_year"]: r for r in v["view"]["ira"]}
    assert ira[2027]["planned"] == 6000 and ira[2026]["actual"] == 1000
    assert c.get("/api/wealth/plan/2027").get_json()["plan"]["entries"][0]["amount"] == 2000


def test_today_strip_combines_portfolios_and_uses_official_closes_when_closed(env, monkeypatch):
    """Command Center 'Your money today': all portfolios, last close vs the one
    before when the market is closed (never an after-hours print)."""
    import market_engine
    monkeypatch.setattr(market_engine, "pulse_session", lambda now=None: "closed")
    app, *_ = env
    c = client(app, 1)
    pid = make_portfolio(c)
    prev = c.post("/api/wealth/import/preview", json={"text": SCHWAB}).get_json()
    c.post("/api/wealth/import/commit", json={"portfolio_id": pid, "transactions": prev["transactions"]})
    d = c.get("/api/wealth/today").get_json()
    assert d["ok"] and not d["empty"] and d["source"] == "wealth"
    assert d["value"] == pytest.approx(3300) and d["cash"] == pytest.approx(500)
    # bars: 610 → 620 on 2 shares
    assert d["day_dollars"] == pytest.approx(20) and d["best"]["ticker"] == "KLAC"
    assert d["best"]["pct"] == pytest.approx(1.64, abs=0.01) and d["day_label"]
    assert d["tickers"] == ["KLAC"]
    # Per-holding rows feed the globe's My Portfolio view.
    h = d["holdings"]
    assert [x["ticker"] for x in h] == ["KLAC"]
    assert h[0]["value"] == pytest.approx(2800) and h[0]["weight"] == pytest.approx(100)
    assert h[0]["pct"] == pytest.approx(1.64, abs=0.01) and h[0]["dollars"] == pytest.approx(20)
    assert "sector" in h[0]


def test_today_strip_is_empty_without_portfolios(env):
    app, *_ = env
    d = client(app, 7).get("/api/wealth/today").get_json()
    assert d["ok"] and d["empty"] is True


def test_position_strip_api_uses_ledger_then_schwab_snapshot(env):
    import wealth_store
    app, *_ = env
    c = client(app, 1)
    assert c.get("/api/wealth/position/KLAC").get_json() == {"ok": True, "ticker": "KLAC", "held": False}
    wealth_store.save_broker_snapshot(1, "12345678", {"holdings": [
        {"ticker": "KLAC", "shares": 2, "market_value": 2800.0, "cost_basis_snapshot": 2500.0}]})
    d = c.get("/api/wealth/position/klac").get_json()
    assert d["held"] and d["source"] == "schwab" and d["avg_cost"] == 1250 and d["unrealized"] == 300
    pid = make_portfolio(c)
    for tx in ({"date": "2026-01-01", "type": "DEPOSIT", "amount": 3000},
               {"date": "2026-02-01", "type": "BUY", "ticker": "KLAC", "shares": 1, "price": 1000}):
        c.post("/api/wealth/transactions", json={"portfolio_id": pid, **tx})
    c.post("/api/wealth/capital/journal", json={"ticker": "KLAC", "decision": "WAIT", "amount": 500,
                                               "thesis": "t", "risk": "r", "invalidation": "i"})
    d = c.get("/api/wealth/position/KLAC").get_json()
    assert d["source"] == "ledger" and d["shares"] == 1 and d["unrealized"] == 400
    assert d["last_decision"]["decision"] == "WAIT"
    assert client(app, 2).get("/api/wealth/position/KLAC").get_json()["held"] is False   # private


def test_overview_merges_schwab_snapshot_into_one_holdings_table(monkeypatch):
    import web_app
    import app as legacy
    import wealth_store
    import schwab_account_preferences as prefs
    monkeypatch.setattr(legacy, "_auth_required", lambda: False)
    monkeypatch.setattr(wealth_store, "list_portfolios", lambda uid: [])
    monkeypatch.setattr(prefs, "get_preferences", lambda uid: {})
    monkeypatch.setattr(wealth_store, "list_broker_snapshots", lambda uid: [
        {"account_key": "A1", "synced_at": "2026-11-21T10:00:00", "snapshot": {"holdings": [
            {"ticker": "KLAC", "shares": 2, "market_value": 2800.0, "cost_basis_snapshot": 2500.0},
            {"ticker": "SKHY", "shares": 5, "market_value": 900.0}]}}])
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    html = c.get("/wealth").get_data(as_text=True)
    for gone in ("Brokerage Sync", "Connected Holdings", "Concentration</h2>", "Sector exposure"):
        assert gone not in html
    assert html.count('id="wa-holdings"') == 1 and "2 not in your ledger yet" in html
    assert html.count('class="ov-src is-schwab"') == 2 and "$3,700.00" in html        # snapshot total
    assert html.index(">KLAC<") < html.index(">SKHY<")                                 # largest first
    assert "tab=mcff" in html and "Start with a portfolio" in html


def test_fundamentals_analyzer_has_the_mcff_tab(monkeypatch):
    import web_app
    import app as legacy
    monkeypatch.setattr(legacy, "_auth_required", lambda: False)
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    html = c.get("/fundamentals?tab=mcff").get_data(as_text=True)
    assert "switchTab('mcff', this)\">M.C.F.F.</button>" in html
    pane = html[html.index('id="tab-mcff"'):html.index('>', html.index('id="tab-mcff"'))]
    assert "display:none" not in pane
    assert "Score a company on M.C.F.F." in html
    assert 'id="tab-analyze" class="tab-pane" style="display:none"' in html
