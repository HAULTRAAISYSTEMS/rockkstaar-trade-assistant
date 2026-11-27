"""Financial and integration regressions for the Portfolio Command Center."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest

import wealth_calc as calc
import wealth_history as history
import wealth_ledger as ledger
import wealth_performance as performance
import wealth_service
import wealth_store
from test_wealth_routes import client, env, make_portfolio  # noqa: F401


def tx(day, kind, **fields):
    return {"date": day.isoformat(), "type": kind, **fields}


def test_transfer_review_preserves_import_identity_and_updates_history(env):
    app, *_ = env
    owner = client(app, 1)
    pid = make_portfolio(owner, "Taxable", "taxable")
    csv = (
        "Date,Action,Symbol,Description,Quantity,Price,Fees & Comm,Amount\n"
        "01/05/2026,Journal,,Transfer,,,,100\n"
    )
    preview = owner.post("/api/wealth/import/preview", json={"text": csv}).json
    request = {"portfolio_id": pid, "transactions": preview["transactions"]}
    assert owner.post("/api/wealth/import/commit", json=request).json["added"] == 1
    row = wealth_store.list_transactions(1, pid)[0]
    url = f"/api/wealth/transactions/{row['id']}/transfer"
    edit = {"mode": "external", "note": "Journal"}
    response = owner.get(f"/api/wealth/performance?p={pid}").json
    assert "verify this cash movement" in response["history"]["reason"]
    assert client(app, 2).patch(url, json=edit).status_code == 409
    assert owner.patch(url, json={"mode": "internal", "note": "Journal"}).status_code == 400
    assert owner.patch(url, json=edit).json["note"] == "Journal external-transfer"
    assert owner.patch(url, json=edit).status_code == 409
    after = wealth_store.list_transactions(1, pid)[0]
    assert (after["fingerprint"], after["amount"]) == (row["fingerprint"], row["amount"])
    assert owner.post("/api/wealth/import/commit", json=request).json["duplicates"] == 1
    assert owner.get(f"/api/wealth/performance?p={pid}").json["history"]["ok"]
    assert owner.patch(url, json={"mode": "pending", "note": after["note"]}).json["note"] == "Journal"


def test_options_history_starts_at_first_fully_valued_close():
    start, opened, closed, end = [date(2026, 6, day) for day in range(1, 5)]
    symbol = "NVDA260918C00100000"
    trades = [
        tx(start, "DEPOSIT", amount=1000),
        tx(start, "BUY", ticker="NVDA", shares=1, price=100),
        tx(opened, "OPTION_BUY", ticker=symbol, shares=2, price=1, fees=1),
    ]
    days = [start, opened, closed, end]
    prices = {"NVDA": [(closed, 100), (end, 110)]}
    with_open_options = ledger.build(trades)
    assert "open option contracts" in history.build_series([with_open_options], prices, days)["reason"]
    balance = wealth_service.performance_balance({1: with_open_options}, [], {"NVDA": {"price": 100}}, opened)
    assert balance["value"] is None and balance["unrealized"] is None
    finished = ledger.build(trades + [tx(closed, "OPTION_SELL", ticker=symbol, shares=2, price=1.5, fees=1)])
    series = history.build_series([finished], prices, days)
    assert series["values"] == [(closed, 1098), (end, 1108)]
    assert series["flows"] == [(closed, 1098)]
    report = performance.analyze(series, [(closed, 100), (end, 105)], {1: finished}, {}, {})
    assert report["partial_ytd"] and report["option_history_trimmed"]
    assert report["gain"] == 10
    assert report["twr_pct"] == pytest.approx(10 / 1098 * 100)
    assert report["periods"][4]["portfolio"] is None
    assert report["periods"][-1]["label"] == "Available Period"
    assert report["years"] == report["spy_years"] == {}
    assert report["heatmap"][2026] == {}
    activity = performance.ledger_activity({1: finished}, {}, end)[2026]
    assert activity["option_count"] == 2
    assert activity["option_realized"] == 98
    assert activity["option_fees"] == activity["fees"] == 2


def test_cash_flow_timing_and_withdrawals_do_not_become_losses():
    start = date(2026, 1, 2)
    last = start + timedelta(days=1)
    flows = [(last, 100), (last, -40)]
    # A $100 opening deposit creates $200 invested; $20 is earned before
    # the $40 closing withdrawal. Netting the flows would use the wrong capital.
    assert calc.twr([(start, 100), (last, 180)], flows) == pytest.approx(.10)
    assert calc.twr([(start, 100), (last, 0)], [(last, -100)]) == pytest.approx(0)
    assert calc.twr([(start, 0), (last, 100)], flows) is None


def test_partial_and_complete_sales_split_fees_reinvestment_and_basis():
    start = date(2025, 1, 2)
    dates = [start + timedelta(days=i) for i in range(6)]
    rows = [
        tx(dates[0], "DEPOSIT", amount=1000),
        tx(dates[0], "BUY", ticker="A", shares=2.5, price=100, fees=2),
        tx(dates[1], "REINVEST", ticker="A", shares=.25, price=120, amount=30),
        tx(dates[2], "SPLIT", ticker="A", ratio=2),
        tx(dates[2], "BASIS_ADJUSTMENT", ticker="A", amount=-12),
        tx(dates[3], "SELL", ticker="A", shares=2.5, price=65, fees=1),
        tx(dates[4], "FEE", amount=5),
        tx(dates[4], "INTEREST", amount=2),
    ]
    active = ledger.build(rows)
    assert active.holding("A")["shares"] == pytest.approx(3)
    assert active.holding("A")["cost_basis"] + active.realized[0]["cost"] == pytest.approx(270)
    current = wealth_service.performance_balance({1: active}, [], {"A": {"price": 70}}, dates[-1])
    assert current["unrealized"] == pytest.approx(210 - active.holding("A")["cost_basis"])
    closed = ledger.build(rows + [tx(dates[5], "SELL", ticker="A", shares=3, price=70, fees=2)])
    assert closed.realized_total == pytest.approx(161.5 + 208 - 270)
    assert closed.cash == pytest.approx(1000 - 252 + 161.5 + 208 - 5 + 2)
    stats = performance.ledger_activity({1: closed}, {}, dates[-1])[2025]
    assert stats["dividends"] == 30 and stats["fees"] == 10 and stats["standalone_fees"] == 5
    assert not closed.positions


def test_tagged_cash_and_stock_transfers_are_eliminated_only_within_scope():
    start, end = date(2025, 2, 3), date(2025, 2, 4)
    a = ledger.build([tx(start, "DEPOSIT", amount=200),
                      tx(start, "BUY", ticker="A", shares=1.5, price=50),
                      tx(end, "WITHDRAWAL", amount=20, note="transfer:cash"),
                      tx(end, "TRANSFER_OUT", ticker="A", shares=.5, note="transfer:stock")])
    b = ledger.build([tx(end, "DEPOSIT", amount=20, note="transfer:cash"),
                      tx(end, "TRANSFER_IN", ticker="A", shares=.5, price=50, note="transfer:stock")])
    grouped = {1: a, 2: b}
    pairs, warnings = performance.transfers(grouped)
    assert len(pairs) == 4 and not warnings
    closes = {"A": [(start, 50), (end, 60)]}
    flows, security, warnings = performance.external_flows(grouped, pairs, closes, {})
    assert flows == [(start, 200)] and not warnings and not security
    built = history.build_series([a, b], closes, [start, end], flows=flows)
    assert calc.twr(built["values"], built["flows"]) == pytest.approx(.075)
    separate, _, _ = performance.external_flows({1: a}, pairs, closes, {})
    assert sorted(separate) == [(start, 200), (end, -30), (end, -20)]
    assert performance.ledger_activity(grouped, pairs, end)[2025]["deposits"] == 200
    assert performance.transfers({1: a})[1][1]


def test_cash_transfer_candidates_are_flagged_and_never_guessed():
    day = date(2025, 2, 3)
    a = ledger.build([tx(day, "DEPOSIT", amount=40)])
    b = ledger.build([tx(day, "WITHDRAWAL", amount=40)])
    c = ledger.build([tx(day, "DEPOSIT", amount=50)])
    pairs, issues = performance.transfers({1: a, 2: b, 3: c})
    assert not pairs
    assert set(issues) == {1, 2}


def test_missing_or_disputed_split_does_not_make_a_historical_loss():
    first, last = date(2025, 2, 3), date(2025, 2, 4)
    before = [tx(first, "DEPOSIT", amount=100), tx(first, "BUY", ticker="A", shares=1, price=100)]
    missing = ledger.build(before)
    splits = {"A": [("A", last, 2)]}
    assert performance.split_issues(missing, splits, last)
    correct = ledger.build(before + [tx(last, "SPLIT", ticker="A", ratio=2)])
    assert not performance.split_issues(correct, splits, last)
    data = history.build_series([correct], {"A": [(first, 50), (last, 50)]}, [first, last],
                                provider_splits=splits)
    assert data["values"] == [(first, 100), (last, 100)]
    assert not history.build_series([correct], {"A": [(first, 50)]}, [first, last])["ok"]


def test_risk_benchmark_alignment_and_no_price_only_substitution():
    start = date(2025, 1, 2)
    days = [start + timedelta(days=i) for i in range(75)]
    benchmark, p = {}, []
    level = portfolio_level = 1.0
    for i, day in enumerate(days):
        r = .006 if i % 3 else -.009
        if i:
            level *= 1 + r
            portfolio_level *= 1 + 2 * r + .0001
        benchmark[day] = level
        p.append((day, portfolio_level))
    del benchmark[days[9]]
    stats = performance.risk(p, benchmark, None)
    assert stats["beta"] == pytest.approx(2)
    assert stats["alpha_pct"] == pytest.approx(2.52)
    assert stats["volatility_pct"] > 0
    assert stats["max_drawdown"]["pct"] < 0
    assert stats["benchmark_max_drawdown"] is None
    assert stats["observations"] == 72
    assert history.benchmark_index(list(benchmark.items()), days)[9][1] is None
    assert not history.benchmark_index(list(benchmark.items()), [days[9], days[10]])


def test_xirr_retains_actual_cash_flow_dates_and_annual_returns_need_a_year():
    first, end = date(2025, 1, 1), date(2026, 1, 1)
    supported = {"values": [(first, 100), (end, 120)], "contributions": [(first, 100), (end, 100)],
                 "flows": [(first, 100)], "dated_flows": [(first - timedelta(days=2), 100)], "gaps": []}
    result = history.scoreboard(supported, None)
    assert result["twr_annual_pct"] == pytest.approx(20)
    assert result["mwr_annual_pct"] == pytest.approx((1.2 ** (365/367) - 1) * 100)
    assert calc.annualize(.20, 364) is None
    assert history._verified_xirr([(first, -100), (end, 230), (date(2027, 1, 1), -132)]) is None


def test_stock_attribution_accounts_for_trades_and_dividends_once():
    start, end = date(2026, 1, 2), date(2026, 1, 5)
    invested = ledger.build([tx(start, "DEPOSIT", amount=1000),
                             tx(start, "BUY", ticker="A", shares=5, price=100),
                             tx(end, "SELL", ticker="A", shares=1.5, price=110, fees=1),
                             tx(end, "DIVIDEND", ticker="A", amount=5)])
    data = history.build_series([invested], {"A": [(start, 100), (end, 110)]}, [start, end])
    result = performance.analyze(data, [(start, 5000), (end, 5010)], {1: invested}, {}, {})
    a = result["attribution"][0]
    assert a["profit"] == pytest.approx(54)
    assert a["contribution_pct"] == pytest.approx(result["twr_pct"])
    assert result["periods"][0]["excess"] == pytest.approx(5.2)


def test_read_only_broker_balance_never_uses_missing_data_as_zero():
    from schwab import _normalize_account
    from wealth_broker_sync import discover
    raw = _normalize_account({"accountNumber": "test-account",
                              "currentBalances": {"liquidationValue": 0, "availableFunds": 0}})
    snapshot = discover({"connected": True, "accounts": [raw]}, [], [])["accounts"][0]
    p = wealth_service.performance_balance({}, [snapshot], {}, date(2026, 11, 1))
    assert p["value"] == p["available_cash"] == p["unrealized"] == 0
    p = wealth_service.performance_balance({}, [{}], {}, date(2026, 11, 1))
    assert p["value"] is None and p["unrealized"] is None and p["cash"] is None


def test_settings_account_strategy_and_user_isolation(env):
    app, *_ = env
    a, other = client(app, 1), client(app, 2)
    first, second = make_portfolio(a, "Equities"), make_portfolio(a, "Tactical")
    endpoint = "/api/wealth/performance"
    assert app.test_client().get(endpoint).status_code == 401
    assert other.put(f"{endpoint}/accounts/{first}", json={"strategy": "active", "history_confirmed": True}).status_code == 404
    assert other.get(f"{endpoint}?p={first}").status_code == 404
    assert a.put(f"{endpoint}/accounts/{first}", json={"strategy": "long_term", "history_confirmed": False}).status_code == 200
    assert a.put(f"{endpoint}/accounts/{second}", json={"strategy": "active", "history_confirmed": False}).status_code == 200
    today = date.today()
    for pid, value in [(first, 1000), (second, 2000)]:
        assert a.post("/api/wealth/transactions", json={
            "date": today.isoformat(), "portfolio_id": pid, "type": "DEPOSIT", "amount": value}).status_code == 200
    goals = f"{endpoint}/goals/{today.year}"
    assert a.put(goals, json={"return_pct": 15, "contribution": 4000}).status_code == 200
    assert a.put(goals, json={"return_pct": -100, "contribution": 4000}).status_code == 400
    got = a.get(endpoint)
    assert got.headers["Cache-Control"] == "private, no-store"
    combined = got.get_json()
    assert combined["position"]["value"] == 3000
    assert combined["selected_goal"]["net"] == 3000
    assert combined["selected_goal"]["remaining"] == 1000
    assert a.get(f"{endpoint}?strategy=long_term").get_json()["position"]["value"] == 1000
    assert a.get(f"{endpoint}?p={second}&view=balance").get_json()["position"]["value"] == 2000
    assert other.get(endpoint).get_json()["selected_goal"]["contribution"] == 25000


def test_missing_index_never_substitutes_calendar_prices(env, monkeypatch):
    import wealth_routes
    from flask import Flask, session
    app, *_ = env
    a = client(app, 1)
    pid = make_portfolio(a)
    assert a.post("/api/wealth/transactions", json={
        "date": "2026-01-01", "portfolio_id": pid, "type": "DEPOSIT", "amount": 1000}).status_code == 200
    isolated = Flask(__name__)
    isolated.secret_key = "fixture"
    actual = []
    def bars(symbol):
        actual.append(symbol)
        if symbol == performance.BENCHMARK:
            return None
        return {"t": ["2026-01-02", "2026-01-05"], "c": [100, 110]}
    isolated.register_blueprint(wealth_routes.create_wealth_blueprint(
        current_user=lambda: {"id": session["user_id"]}, bars_lookup=bars))
    data = client(isolated, 1).get(f"/api/wealth/performance?p={pid}").get_json()
    assert set(actual) == {"SPY", performance.BENCHMARK}
    assert data["history"]["ok"] and data["history"]["twr_pct"] == 0
    assert "spy_pct" not in data["history"] and data["benchmark"]["as_of"] is None
    assert data["history"]["periods"][0]["benchmark"] is None


def test_shifted_overlapping_broker_import_and_concurrent_retries(env):
    import wealth_csv
    import wealth_store
    app, *_ = env
    account = make_portfolio(client(app, 1))
    header = "Date,Action,Symbol,Description,Quantity,Price,Fees & Comm,Amount\n"
    line = "01/02/2026,Buy,A,ABC,2,10,0,-20\n"
    other = "01/05/2026,Buy,A,ABC,1,10,0,-10\n"
    first = wealth_csv.parse(header + line)["transactions"]
    overlap = wealth_csv.parse(header + other + line)["transactions"]
    assert first[0]["fingerprint"] == overlap[0]["fingerprint"]
    assert wealth_store.add_transactions(1, account, first)["added"] == 1
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: wealth_store.add_transactions(1, account, overlap), range(2)))
    assert sum(r["added"] for r in results) == 1
    assert sum(r["duplicates"] for r in results) == 3


def test_incomplete_and_overlapping_schwab_accounts_do_not_invent_history(env, monkeypatch):
    import time
    import app as legacy
    import wealth_store

    app, *_ = env
    c = client(app, 1)
    endpoint = "/api/wealth/performance"
    key = "test-account-hash-only"
    first = make_portfolio(c, "Schwab mapping")
    wealth_store.update_portfolio(1, first, broker_account=key)
    fake = {"connected": True, "accounts": [
        {"account_hash": key, "total_value": 650, "available_funds": 550, "cash_and_sweep": 550,
         "equity_positions": [{"symbol": "KLAC", "quantity": .5, "market_value": 100, "cost_basis": 85}]}
    ]}
    monkeypatch.setattr(legacy, "_schwab_account_cache", {1: {"ts": time.time(), "data": fake}})
    result = c.get(endpoint).get_json()
    assert result["position"]["value"] == 650
    assert result["position"]["unrealized"] == 15
    assert result["position"]["realized"] is None
    assert not result["history"]["ok"]
    assert any("ledger shares differ" in issue for issue in result["warnings"])
    assert key not in str(result)
    second = make_portfolio(c, "Duplicate mapping")
    wealth_store.update_portfolio(1, second, broker_account=key)
    result = c.get(endpoint).get_json()
    assert result["position"]["value"] == 650
    assert any("same Schwab account" in issue for issue in result["warnings"])
    assert c.get(endpoint + "?view=balance").get_json()["position"]["value"] == 650
    assert c.post(endpoint + "/sync").get_json()["ok"]
    assert app.test_client().post(endpoint + "/sync").status_code == 401


def test_reinvestment_and_matching_cash_dividend_needs_reconciliation():
    day = date(2026, 1, 12)
    rows = [tx(day, "DIVIDEND", ticker="A", amount=40),
            tx(day, "REINVEST", ticker="A", amount=40, shares=1, price=40)]
    assert performance.ledger_issues(ledger.build(rows)) == [
        "2026-01-12: check whether the A cash dividend duplicates a recorded reinvestment."]


def test_schwab_picker_uses_verified_suffix_and_ignores_empty_stale_entries(env, monkeypatch):
    import time
    import app as legacy
    import wealth_store

    app, *_ = env
    c = client(app, 1)
    endpoint = "/api/wealth/performance"
    number, hashed = "71324567", "opaque-schwab-key-9999"
    linked = make_portfolio(c, "Schwab Taxable brokerage")
    other_linked = make_portfolio(c, "Schwab Taxable brokerage")
    stale = make_portfolio(c, "Schwab Taxable brokerage")
    wealth_store.update_portfolio(1, linked, broker_account=number)
    wealth_store.update_portfolio(1, other_linked, broker_account="opaque-other-key-8888")
    wealth_store.update_portfolio(1, stale, broker_account="retired-mapping")
    accounts = [
        {"account_hash": hashed, "account_number": number, "total_value": 650, "cash_and_sweep": 650},
        {"account_hash": "opaque-other-key-8888", "account_number": "82900102",
         "total_value": 150, "cash_and_sweep": 150},
    ]
    monkeypatch.setattr(legacy, "_schwab_account_cache", {1: {
        "ts": time.time(), "data": {"connected": True, "accounts": accounts}}})
    all_accounts = c.get(endpoint).get_json()
    assert all_accounts["position"]["value"] == 800
    assert not all_accounts["history"]["ok"]
    assert any("latest Schwab account list" in w for w in all_accounts["warnings"])
    assert [(a["id"], a["label"]) for a in all_accounts["accounts"]] == [
        (linked, "•••• 4567 · Schwab Taxable brokerage"),
        (other_linked, "•••• 0102 · Schwab Taxable brokerage"),
    ]
    assert c.get(f"{endpoint}?p={linked}&view=balance").get_json()["position"]["value"] == 650
    assert c.get(f"{endpoint}?p={other_linked}").get_json()["position"]["value"] == 150
    assert c.get(f"{endpoint}?p={stale}").get_json()["position"]["value"] is None
    assert all(value not in str(all_accounts) for value in (number, hashed, "82900102", "retired-mapping"))
    assert wealth_store.get_portfolio(1, stale) is not None
    assert client(app, 2).get(endpoint).get_json()["accounts"] == []


def test_schwab_empty_stale_entries_are_kept_without_a_fresh_roster(env, monkeypatch):
    import app as legacy
    import wealth_store

    app, *_ = env
    c = client(app, 1)
    endpoint = "/api/wealth/performance"
    linked = make_portfolio(c, "Schwab IRA")
    missing = make_portfolio(c, "Missing account with ledger")
    wealth_store.update_portfolio(1, linked, broker_account="snapshot-key-1212")
    wealth_store.update_portfolio(1, missing, broker_account="unknown-key-3333")
    wealth_store.save_broker_snapshot(1, "snapshot-key-1212", {
        "account_id": "snapshot-key-1212", "account_last_four": "0017",
        "total_value": 100, "cash": 100, "unsupported_positions": 0})
    monkeypatch.setattr(legacy, "_schwab_account_cache", {})
    response = c.get(endpoint).get_json()
    assert response["position"]["value"] is None
    assert "•••• 0017" in response["accounts"][0]["label"]
    assert response["accounts"][1]["label"].endswith("Account ending unavailable")
    assert "3333" not in str(response)
    assert c.get(f"{endpoint}?p={linked}").get_json()["position"]["value"] == 100

    import time
    assert c.post("/api/wealth/transactions", json={
        "portfolio_id": missing, "type": "DEPOSIT", "amount": 40, "date": "2025-01-02"}).status_code == 200
    monkeypatch.setattr(legacy, "_schwab_account_cache", {1: {"ts": time.time(), "data": {
        "connected": True, "accounts": [{"account_hash": "snapshot-key-1212", "account_number": "78210017",
                                          "total_value": 100, "cash_and_sweep": 100}]}}})
    response = c.get(endpoint).get_json()
    assert [a["id"] for a in response["accounts"]] == [linked, missing]
    assert response["position"]["value"] is None
    assert response["history"]["ok"] is False


def test_sync_reuses_exact_number_mapping_and_persists_safe_label(env):
    import wealth_store
    from flask import Flask
    from wealth_routes import create_wealth_blueprint

    flask_app, *_ = env
    c = client(flask_app, 1)
    pid = make_portfolio(c, "Existing Schwab")
    number = "72340007"
    key = "real-schwab-key-9876"
    wealth_store.update_portfolio(1, pid, broker_account=number)
    isolated = Flask(__name__)
    isolated.register_blueprint(create_wealth_blueprint(current_user=lambda: {"id": 1},
        broker_summary=lambda user: {"connected": True, "accounts": [
            {"account_number": number, "account_hash": key, "total_value": 0, "cash_and_sweep": 0},
            {"account_number": "13241010", "account_hash": "another-key", "total_value": 0, "cash_and_sweep": 0},
        ]}))
    response = isolated.test_client().get("/api/wealth/brokerage/discovery").get_json()
    assert response["ok"]
    assert [a["account_id"] for a in response["accounts"]] == ["•••• 0007", "•••• 1010"]
    assert response["accounts"][0]["portfolio_id"] == pid
    assert number not in str(response) and key not in str(response)
    assert len(wealth_store.list_portfolios(1)) == 2
    snapshots = {r["account_key"]: r["snapshot"] for r in wealth_store.list_broker_snapshots(1)}
    assert snapshots[number]["account_last_four"] == "0007"
    assert snapshots["another-key"]["total_value"] == 0
    assert key not in snapshots


def test_sync_recovers_legacy_hash_when_positions_response_only_has_number(env, monkeypatch):
    import time
    import app as legacy
    import schwab
    import wealth_store
    from flask import Flask
    from wealth_routes import create_wealth_blueprint

    flask_app, *_ = env
    pid = make_portfolio(client(flask_app, 1), "Schwab IRA")
    number, key = "71003121", "legacy-hash-4455"
    wealth_store.update_portfolio(1, pid, broker_account=key)
    broker = {"connected": True, "accounts": [
        {"account_number": number, "account_hash": number, "total_value": 55, "cash_and_sweep": 55},
    ]}
    queries = []

    def get(path, *, user_id):
        queries.append((path, user_id))
        return [{"accountNumber": number, "hashValue": key}]

    monkeypatch.setattr(schwab, "_get", get)
    monkeypatch.setattr(schwab, "is_connected", lambda uid: True)
    monkeypatch.setattr(schwab, "get_account_summary", lambda uid: broker)
    monkeypatch.setattr(legacy, "_schwab_account_cache", {})
    isolated = Flask(__name__)
    isolated.register_blueprint(create_wealth_blueprint(current_user=lambda: {"id": 1}))
    c = isolated.test_client()
    endpoint = "/api/wealth/performance"
    assert c.post(endpoint + "/sync").get_json() == {"ok": True, "synced": 1}
    assert queries == [("/accounts/accountNumbers", 1)]
    assert len(wealth_store.list_portfolios(1)) == 1
    # A later position refresh may again omit the hash. The verified mapping persists.
    legacy._schwab_account_cache[1] = {"ts": time.time(), "data": broker}
    result = c.get(f"{endpoint}?p={pid}").get_json()
    assert result["position"]["value"] == 55
    assert result["accounts"][0]["label"] == "•••• 3121 · Schwab IRA"
    assert key not in str(result) and number not in str(result)
    assert c.post(endpoint + "/sync").get_json() == {"ok": True, "synced": 1}
    assert queries == [("/accounts/accountNumbers", 1)]
    import schwab_account_preferences as prefs
    prefs.update_preference(1, key, visible=True, include_wealth=False)
    excluded = c.get(endpoint).get_json()
    assert not excluded["accounts"]
    assert excluded["position"]["value"] is None


def test_next_year_goal_is_planning_only_and_does_not_change_current_progress(env):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    app, *_ = env
    a, b = client(app, 1), client(app, 2)
    pid = make_portfolio(a)
    today = datetime.now(ZoneInfo("America/New_York")).date()
    year = today.year
    endpoint = "/api/wealth/performance"
    assert a.post("/api/wealth/transactions", json={
        "portfolio_id": pid, "date": today.isoformat(), "type": "DEPOSIT", "amount": 1000,
    }).status_code == 200
    data = a.get(endpoint).get_json()
    next_year = next(row for row in data["goals"] if row["year"] == year + 1)
    assert next_year["planned"] is True and next_year["saved"] is False
    assert next_year["net"] is next_year["remaining"] is next_year["actual_return"] is None
    assert a.put(f"{endpoint}/goals/{year + 2}", json={"contribution": 12000, "return_pct": 18}).status_code == 400
    assert a.put(f"{endpoint}/goals/{year + 1}", json={"contribution": 12000, "return_pct": 18}).status_code == 200
    saved = a.get(endpoint).get_json()
    assert saved["selected_goal"]["year"] == year
    assert saved["selected_goal"]["net"] == 1000
    assert saved["selected_goal"]["contribution"] == 25000
    next_year = next(row for row in saved["goals"] if row["year"] == year + 1)
    assert next_year["saved"] and next_year["contribution"] == 12000
    assert next_year["net"] is next_year["remaining"] is next_year["actual_return"] is None
    other = next(row for row in b.get(endpoint).get_json()["goals"] if row["year"] == year + 1)
    assert not other["saved"] and other["contribution"] == 25000


def test_current_holdings_and_account_check_are_available_with_incomplete_history(env, monkeypatch):
    import time
    import app as legacy
    import wealth_store

    app, *_ = env
    c = client(app, 1)
    endpoint = "/api/wealth/performance"
    pid = make_portfolio(c, "Schwab IRA")
    key = "internal-key-9799"
    wealth_store.update_portfolio(1, pid, broker_account=key)
    for row in [
        {"type": "DEPOSIT", "amount": 500},
        {"type": "BUY", "ticker": "KLAC", "shares": 1, "price": 100},
    ]:
        assert c.post("/api/wealth/transactions", json={
            **row, "portfolio_id": pid, "date": "2025-01-02",
        }).status_code == 200
    number = "11120456"
    data = {"connected": True, "accounts": [{"account_hash": key, "account_number": number,
            "cash_and_sweep": 300, "total_value": 560,
            "equity_positions": [{"symbol": "KLAC", "quantity": 2, "market_value": 260, "cost_basis": 200}]}]}
    monkeypatch.setattr(legacy, "_schwab_account_cache", {1: {"ts": time.time(), "data": data}})
    result = c.get(f"{endpoint}?p={pid}").get_json()
    assert not result["history"]["ok"]
    h = result["position"]["holdings"][0]
    assert h["shares"] == 2 and h["market_value"] == 260 and h["unrealized"] == 60
    assert h["weight_pct"] == pytest.approx(260 / 560 * 100)
    check = result["reconciliation"][0]
    assert check["account"] == "•••• 0456 · Schwab IRA"
    assert check["broker_cash"] == 300 and check["ledger_cash"] == 400 and check["cash_difference"] == -100
    assert check["positions"][0]["broker_shares"] == 2 and check["positions"][0]["ledger_shares"] == 1
    assert key not in str(result) and number not in str(result)
    assert c.get(f"{endpoint}?p={pid}&view=balance").get_json()["reconciliation"] == result["reconciliation"]


def test_contribution_plan_dates_and_verified_end_of_year_projection():
    plan = performance.clean_contribution_plan(2028, {
        "cadence": "monthly", "amount": 125.25, "start_date": "2028-01-31",
    })
    forecast = performance.contribution_projection(2028, plan, 2000, 500, date(2028, 2, 1))
    assert forecast["next_date"] == "2028-02-29"
    assert forecast["count"] == 12 and forecast["total"] == 1503
    assert forecast["remaining_count"] == 11 and forecast["remaining"] == 1377.75
    assert forecast["year_end"] == 1877.75 and forecast["gap"] == -122.25
    past_february = performance.contribution_projection(2028, plan, 2000, 500, date(2028, 2, 29))
    assert past_february["next_date"] == "2028-03-31"
    for actual, all_accounts in [(None, True), (500, False)]:
        incomplete = performance.contribution_projection(2028, plan, 2000, actual, date(2028, 2, 1),
                                                          all_accounts=all_accounts)
        assert incomplete["remaining"] == 1377.75
        assert incomplete["year_end"] is incomplete["gap"] is None
    weekly = performance.clean_contribution_plan(2028, {
        "cadence": "weekly", "amount": 100, "start_date": "2028-01-01",
    })
    next_year = performance.contribution_projection(2028, weekly, 5000, None, date(2027, 12, 31))
    assert next_year["count"] == next_year["remaining_count"] == 53
    assert next_year["year_end"] == 5300 and next_year["gap"] == 300


def test_contribution_plan_preview_and_save_leave_ledger_untouched(env):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    app, *_ = env
    c = client(app, 1)
    other = client(app, 2)
    endpoint = "/api/wealth/performance"
    year = datetime.now(ZoneInfo("America/New_York")).year + 1
    pid = make_portfolio(c)
    path = f"{endpoint}/goals/{year}"
    plan = {"cadence": "monthly", "amount": 500, "start_date": f"{year}-01-01"}
    goal = {"return_pct": 15, "contribution": 10000, "contribution_plan": plan}
    assert app.test_client().post(path + "/preview", json=goal).status_code == 401
    assert c.post(path + "/preview", json=goal).get_json()["projection"]["year_end"] == 6000
    assert c.put(path, json=goal | {"contribution_plan": plan | {"amount": 50.125}}).status_code == 400
    assert c.put(path, json=goal | {"contribution_plan": plan | {"start_date": "2020-01-01"}}).status_code == 400

    def planned_goal(account):
        return next(row for row in account.get(endpoint).get_json()["goals"] if row["year"] == year)

    assert planned_goal(c)["projection"] is None
    assert c.put(path, json=goal).status_code == 200
    assert planned_goal(c)["projection"]["remaining"] == 6000
    assert planned_goal(c)["net"] is None
    assert planned_goal(other)["projection"] is None
    assert c.put(path, json={"return_pct": 18, "contribution": 11000}).status_code == 200
    assert planned_goal(c)["projection"]["gap"] == -5000
    assert c.put(path, json=goal | {"contribution_plan": None}).status_code == 200
    assert planned_goal(c)["projection"] is None
    import wealth_store
    assert not wealth_store.list_transactions(1, pid)


def test_mixed_contribution_plan_with_end_date_and_one_time_payment():
    from datetime import date

    plan = performance.clean_contribution_plan(2027, [
        {"cadence": "weekly", "amount": 300, "start_date": "2027-01-01", "end_date": "2027-01-08"},
        {"cadence": "biweekly", "amount": 200, "start_date": "2027-01-15", "end_date": "2027-02-01"},
        {"cadence": "monthly", "amount": 50.25, "start_date": "2027-01-31", "end_date": "2027-03-01"},
        {"cadence": "once", "amount": 400, "start_date": "2027-02-18"},
    ])
    result = performance.contribution_projection(2027, plan, 2000, 600, date(2027, 1, 8))
    assert result == {"total": 1500.50, "count": 7, "remaining": 900.50, "remaining_count": 5,
                      "next_date": "2027-01-15", "year_end": 1500.50, "gap": -499.50}
    assert performance.clean_contribution_plan(2027, []) is None
    with pytest.raises(ValueError, match="last contribution"):
        performance.clean_contribution_plan(2027, plan[0] | {"end_date": "2026-12-31"})


def test_unreconciled_broker_view_can_show_recorded_ledger_activity(env, monkeypatch):
    import time
    import app as legacy
    import wealth_store
    from datetime import datetime
    from zoneinfo import ZoneInfo

    app, *_ = env
    c = client(app, 1)
    pid = make_portfolio(c, "Schwab")
    key = "fixture-schwab"
    wealth_store.update_portfolio(1, pid, broker_account=key)
    today = datetime.now(ZoneInfo("America/New_York")).date()
    for kind, amount in (("DEPOSIT", 300), ("DIVIDEND", 20), ("FEE", 5)):
        result = c.post("/api/wealth/transactions", json={
            "portfolio_id": pid, "type": kind, "amount": amount, "ticker": "KLAC" if kind == "DIVIDEND" else None,
            "date": today.isoformat(),
        })
        assert result.status_code == 200
    monkeypatch.setattr(legacy, "_schwab_account_cache", {1: {"ts": time.time(), "data": {
        "connected": True, "accounts": [{"account_hash": key, "cash_and_sweep": 400, "total_value": 400}],
    }}})
    data = c.get(f"/api/wealth/performance?p={pid}").get_json()
    assert not data["history"]["ok"]
    assert data["position"]["dividends_ytd"] is None
    assert data["selected_goal"]["net"] is None
    assert data["position"]["recorded_activity_ytd"] == {
        "deposits": 300, "dividends": 20, "fees": 5, "withdrawals": 0, "interest": 0}


def test_contribution_chart_shows_cumulative_plan_and_covered_actual_months_only():
    plan = performance.clean_contribution_plan(2026, [
        {"cadence": "monthly", "amount": 500, "start_date": "2026-01-01"},
        {"cadence": "once", "amount": 250, "start_date": "2026-03-15"},
    ])
    rows = performance.contribution_chart(2026, plan, 12000, date(2026, 4, 15),
        {(2026, 2): 600, (2026, 3): -100}, date(2026, 2, 12))
    assert rows[:5] == [
        {"month": 1, "target": 1000, "planned": 500, "actual": None},
        {"month": 2, "target": 2000, "planned": 1000, "actual": 600},
        {"month": 3, "target": 3000, "planned": 1750, "actual": 500},
        {"month": 4, "target": 4000, "planned": 2250, "actual": 500},
        {"month": 5, "target": 5000, "planned": 2750, "actual": None},
    ]
    assert rows[-1] == {"month": 12, "target": 12000, "planned": 6250, "actual": None}
    unverified = performance.contribution_chart(2026, plan, 12000, date(2026, 4, 15))
    assert all(row["actual"] is None for row in unverified)
    assert unverified[-1]["planned"] == 6250
