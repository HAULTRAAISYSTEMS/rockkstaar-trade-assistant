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
        broker_summary=lambda uid: broker)
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
    for url in ("/wealth", "/wealth/ledger", "/wealth/lab", "/wealth/scoreboard"):
        r = c.get(url)
        assert r.status_code == 200, url
        html = r.get_data(as_text=True)
        assert "Wealth Architect" in html
    assert "Start with a portfolio" in c.get("/wealth").get_data(as_text=True)


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
