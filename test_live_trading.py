"""Live Schwab orders: ticket validation, order JSON, guardrails, confirm
tokens, and the preview → place flow end to end with Schwab stubbed out."""
import os
import time

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-live-trading")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import live_trading as lt
import schwab_orders as so
from schwab_orders import OrderError

BASE = {"symbol": "nvda", "side": "BUY", "qty": "5", "order_type": "LIMIT", "limit": "180", "tif": "DAY"}
RISK = {"account_size": 10000, "risk_pct": 1.0, "max_trades_per_day": 3}
CFG = {"enabled": True, "kill_switch": False, "max_order_value": 2500.0}


def tk(**kw):
    return so.normalize_ticket({**BASE, **kw})


def chk(t, **kw):
    args = dict(cfg=CFG, risk=RISK, last=180.0, position_qty=0, buying_power=5000.0,
                opening_today=0, session_locked=False, market_session="regular")
    args.update(kw)
    return lt.check(t, **args)


# ── Ticket validation ────────────────────────────────────────────────────────

@pytest.mark.parametrize("kw,msg", [
    ({"symbol": "bad sym!"}, "symbol"), ({"side": "SHORT"}, "Buy or Sell"), ({"qty": "1.5"}, "whole"),
    ({"qty": "0"}, "at least 1"), ({"qty": "200000"}, "capped"), ({"order_type": "LIMIT", "limit": ""}, "limit price"),
    ({"order_type": "STOP", "stop": ""}, "stop price"), ({"order_type": "MARKET", "tif": "GTC"}, "Day only"),
    ({"order_type": "MARKET", "extended": True}, "limit orders"), ({"limit": "-3"}, "above 0"),
    ({"side": "SELL", "bracket": True, "target": "200", "stop_loss": "170"}, "buy orders"),
    ({"bracket": True, "target": "200"}, "both"), ({"bracket": True, "target": "175", "stop_loss": "170"}, "above entry"),
    ({"order_type": "STOP_LIMIT", "side": "SELL", "stop": "170", "limit": "171"}, "at or below"),
])
def test_bad_tickets_are_refused_with_a_reason(kw, msg):
    with pytest.raises(OrderError, match=msg):
        tk(**kw)


def test_market_order_drops_stray_prices():
    t = tk(order_type="MARKET", limit="5", stop="4")
    assert t["limit"] is None and t["stop"] is None and t["symbol"] == "NVDA" and t["qty"] == 5


# ── Order JSON ───────────────────────────────────────────────────────────────

def test_limit_payload_matches_schwab_shape():
    p = so.build_payload(tk(tif="GTC", extended=True))
    assert p == {"orderType": "LIMIT", "session": "SEAMLESS", "duration": "GOOD_TILL_CANCEL",
                 "orderStrategyType": "SINGLE", "price": "180.00",
                 "orderLegCollection": [{"instruction": "BUY", "quantity": 5,
                                         "instrument": {"symbol": "NVDA", "assetType": "EQUITY"}}]}


def test_stop_limit_and_penny_prices():
    p = so.build_payload(tk(symbol="ABCD", order_type="STOP_LIMIT", stop="0.5", limit="0.51"))
    assert p["stopPrice"] == "0.5000" and p["price"] == "0.5100" and p["session"] == "NORMAL"


def test_bracket_is_trigger_then_oco_exits():
    p = so.build_payload(tk(bracket=True, target="200", stop_loss="172.5"))
    assert p["orderStrategyType"] == "TRIGGER"
    oco = p["childOrderStrategies"][0]
    assert oco["orderStrategyType"] == "OCO"
    tp, sl = oco["childOrderStrategies"]
    assert (tp["orderType"], tp["price"], tp["orderLegCollection"][0]["instruction"]) == ("LIMIT", "200.00", "SELL")
    assert (sl["orderType"], sl["stopPrice"], sl["duration"]) == ("STOP", "172.50", "GOOD_TILL_CANCEL")
    assert "take profit 200.00" in so.describe(tk(bracket=True, target="200", stop_loss="172.5"))


# ── Guardrails ───────────────────────────────────────────────────────────────

def test_clean_order_passes_with_a_no_stop_warning():
    blocks, warns, nums = chk(tk())
    assert blocks == [] and nums["value"] == 900.0 and any("No stop-loss" in w for w in warns)


@pytest.mark.parametrize("kw,needle", [
    ({"cfg": {**CFG, "enabled": False}}, "Live trading is off"),
    ({"cfg": {**CFG, "kill_switch": True}}, "switched off on the server"),
    ({"session_locked": True}, "locked"),
    ({"buying_power": 500.0}, "buying power"),
    ({"opening_today": 3}, "3 trades"),
])
def test_blocks(kw, needle):
    blocks, _, _ = chk(tk(), **kw)
    assert any(needle in b for b in blocks), blocks


def test_order_value_cap():
    blocks, _, _ = chk(tk(qty="20"))
    assert any("over your $2,500 max" in b for b in blocks)


def test_market_orders_only_in_regular_hours():
    blocks, _, _ = chk(tk(order_type="MARKET"), market_session="pre_market")
    assert any("regular hours" in b for b in blocks)


def test_no_short_selling():
    assert any("don't hold" in b for b in chk(tk(side="SELL"), position_qty=0)[0])
    assert any("hold 3" in b for b in chk(tk(side="SELL"), position_qty=3)[0])
    assert chk(tk(side="SELL"), position_qty=5)[0] == []


def test_risk_per_trade_uses_the_bracket_stop():
    # 1% of 10k = $100. 5 shares x (180-170) = $50 ok; 12 shares = $120 blocked.
    assert chk(tk(bracket=True, target="200", stop_loss="170"))[0] == []
    blocks = chk(tk(qty="12", bracket=True, target="200", stop_loss="170"))[0]
    assert any("max 10 shares" in b for b in blocks), blocks


# ── Confirmation token ───────────────────────────────────────────────────────

def test_token_is_bound_to_user_account_and_order(monkeypatch):
    monkeypatch.setattr(lt, "nonce_used", lambda n: False)
    p = so.build_payload(tk())
    tok = lt.make_token("s3cret", 1, "HASH", p)
    assert lt.read_token("s3cret", tok, 1, "HASH", p)
    for args, msg in [((2, "HASH", p), "changed"), ((1, "OTHER", p), "changed"),
                      ((1, "HASH", so.build_payload(tk(qty="6"))), "changed")]:
        with pytest.raises(ValueError, match=msg):
            lt.read_token("s3cret", tok, *args)
    with pytest.raises(ValueError, match="Preview"):
        lt.read_token("s3cret", "junk", 1, "HASH", p)
    monkeypatch.setattr(lt, "nonce_used", lambda n: True)
    with pytest.raises(ValueError, match="already sent"):
        lt.read_token("s3cret", tok, 1, "HASH", p)


def test_token_expires(monkeypatch):
    monkeypatch.setattr(lt, "nonce_used", lambda n: False)
    monkeypatch.setattr(lt, "CONFIRM_TTL", 1)
    p = so.build_payload(tk())
    tok = lt.make_token("s3cret", 1, "HASH", p)
    time.sleep(2.1)
    with pytest.raises(ValueError, match="expired"):
        lt.read_token("s3cret", tok, 1, "HASH", p)


# ── Routes, Schwab stubbed ───────────────────────────────────────────────────

@pytest.fixture
def live(monkeypatch, tmp_path):
    import app as legacy
    import database
    import schwab
    import web_app
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "live.db"))
    store = {}
    monkeypatch.setattr(lt, "get_user_setting", lambda uid, key, default=None: store.get((uid, key), default))
    monkeypatch.setattr(lt, "set_user_setting", lambda uid, key, value: store.__setitem__((uid, key), value))
    monkeypatch.setattr(legacy, "get_risk_settings", lambda uid: RISK)
    monkeypatch.setattr(legacy, "get_journal_entries_for_date", lambda d, uid: [])
    monkeypatch.setattr(legacy, "get_daily_session", lambda d, uid: {})
    monkeypatch.setattr(legacy, "market_session_now", lambda: "regular")
    monkeypatch.setattr(legacy, "_LIVE_ACCT_CACHE", {})
    monkeypatch.setattr(so, "account_numbers", lambda uid: [{"number": "12345678", "hash": "H1", "last4": "5678"}])
    monkeypatch.setattr(schwab, "fetch_quotes", lambda syms, user_id=None: {"NVDA": {"price": 180.0}})
    monkeypatch.setattr(schwab, "fetch_accounts", lambda user_id=1: [
        {"account_number": "12345678", "buying_power": 5000.0, "positions": [{"symbol": "NVDA", "quantity": 5}]}])
    monkeypatch.setattr(so, "preview", lambda h, p, uid: {"rejects": [], "warnings": [], "commission": 0})
    sent = []
    monkeypatch.setattr(so, "place", lambda h, p, uid: sent.append((h, p)) or "99001")
    cancelled = []
    monkeypatch.setattr(so, "cancel", lambda h, oid, uid: cancelled.append(oid))
    web_app.app.config["WTF_CSRF_ENABLED"] = False
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    yield c, store, sent, cancelled
    web_app.app.config["WTF_CSRF_ENABLED"] = True


def test_off_by_default_and_turning_on_needs_the_word(live):
    c, store, sent, _ = live
    st = c.get("/api/trade/status").get_json()
    assert st["enabled"] is False and st["connected"] is True and st["account"] == "H1"
    d = c.post("/api/trade/preview", json=BASE).get_json()
    assert d["token"] is None and any("Live trading is off" in b for b in d["blocks"])
    r = c.post("/api/trade/settings", json={"enabled": True, "confirm": "yes"})
    assert r.status_code == 400 and store.get((1, "live_trading_enabled")) is None
    assert c.post("/api/trade/settings", json={"enabled": True, "confirm": "live"}).get_json()["enabled"] is True


def test_preview_then_place_sends_exactly_once(live):
    c, store, sent, _ = live
    store[(1, "live_trading_enabled")] = "1"
    d = c.post("/api/trade/preview", json=BASE).get_json()
    assert d["blocks"] == [] and d["token"] and d["summary"].startswith("BUY 5 NVDA limit 180.00")
    r = c.post("/api/trade/place", json={"ticket": BASE, "token": d["token"]})
    assert r.status_code == 200 and r.get_json()["order_id"] == "99001"
    assert len(sent) == 1 and sent[0][0] == "H1" and sent[0][1]["price"] == "180.00"
    again = c.post("/api/trade/place", json={"ticket": BASE, "token": d["token"]})
    assert again.status_code == 409 and len(sent) == 1
    log = c.get("/api/trade/orders").get_json()
    assert log["log"][0]["status"] == "placed" and log["log"][0]["schwab_order_id"] == "99001"


def test_changed_ticket_after_preview_is_refused(live):
    c, store, sent, _ = live
    store[(1, "live_trading_enabled")] = "1"
    tok = c.post("/api/trade/preview", json=BASE).get_json()["token"]
    r = c.post("/api/trade/place", json={"ticket": {**BASE, "qty": "6"}, "token": tok})
    assert r.status_code == 409 and sent == []


def test_place_rechecks_guardrails(live, monkeypatch):
    c, store, sent, _ = live
    store[(1, "live_trading_enabled")] = "1"
    tok = c.post("/api/trade/preview", json=BASE).get_json()["token"]
    store[(1, "live_trading_enabled")] = "0"           # switched off between preview and confirm
    r = c.post("/api/trade/place", json={"ticket": BASE, "token": tok})
    assert r.status_code == 400 and sent == []


def test_sell_more_than_held_is_blocked(live):
    c, store, sent, _ = live
    store[(1, "live_trading_enabled")] = "1"
    d = c.post("/api/trade/preview", json={**BASE, "side": "SELL", "qty": "6"}).get_json()
    assert d["token"] is None and any("hold 5" in b for b in d["blocks"])


def test_cancel_and_sign_in_required(live):
    c, store, sent, cancelled = live
    assert c.post("/api/trade/cancel/123").get_json()["ok"] and cancelled == ["123"]
    with c.session_transaction() as s:
        s.clear()
    assert c.post("/api/trade/preview", json=BASE).status_code in (302, 401)


def test_ticket_ui_is_wired():
    html = open("templates/terminal.html", encoding="utf-8").read()
    js = open("static/js/live_trading.js", encoding="utf-8").read()
    assert "js/live_trading.js" in html and 'id="lt-mode"' in html and 'id="tw-ot-bracket"' in html
    assert "window.LiveTrading.preview()" in html and "/api/trade/place" in js and "Type LIVE" in html


def test_settings_panel_retries_server_errors_and_hides_buttons():
    js = open("static/js/live_trading.js").read()
    html = open("templates/terminal.html").read()
    # A 5xx while checking Schwab is retried, not shown as "not connected".
    assert "(d._status >= 500 || d._status === 0) && attempt < 4" in js and "id=\"lt-retry\"" in js
    # .tw-submit-ghost's display:block used to beat [hidden] (Turn off showed while off).
    assert ".lt-settings [hidden] { display:none !important; }" in html


def test_module_ddl_is_translated_for_postgres(monkeypatch):
    # live_orders and login_failures used SQLite's AUTOINCREMENT without
    # _adapt_ddl, so on Postgres the tables were never created: preview and
    # today's orders failed with a server error, logins were never throttled.
    import database
    import login_throttle
    monkeypatch.setattr(database, "_USE_POSTGRES", True)
    for ddl in (lt._DDL, login_throttle._DDL):
        sql, _ = database._adapt_sql(ddl)
        assert "AUTOINCREMENT" not in sql and "SERIAL PRIMARY KEY" in sql
    assert "REAL" not in login_throttle._DDL  # float4 on Postgres loses the seconds
    sql, _ = database._adapt_sql("SELECT 'INTEGER PRIMARY KEY AUTOINCREMENT'")
    assert "AUTOINCREMENT" in sql  # only CREATE TABLE is touched


def test_schwab_rejection_reaches_the_ticket(live, monkeypatch):
    # A 5xx body is masked by the security headers hook (and can be replaced
    # at Render's edge), so Schwab failures answer 424 with Schwab's words.
    c, store, sent, _ = live
    store[(1, "live_trading_enabled")] = "1"
    def boom(*a, **k):
        raise RuntimeError("Schwab said no (400): insufficient buying power")
    monkeypatch.setattr(so, "place", boom)
    tok = c.post("/api/trade/preview", json=BASE).get_json()["token"]
    r = c.post("/api/trade/place", json={"ticket": BASE, "token": tok})
    assert r.status_code == 424 and r.get_json()["error"] == "Schwab said no (400): insufficient buying power"
    monkeypatch.setattr(schwab_mod(), "fetch_accounts", lambda user_id=1: (_ for _ in ()).throw(TimeoutError("timed out")))
    r = c.post("/api/trade/preview", json=BASE)
    assert r.status_code == 424 and "didn't answer in time" in r.get_json()["error"]


def test_orders_survive_a_broken_order_log(live, monkeypatch):
    c, *_ = live
    monkeypatch.setattr(so, "todays_orders", lambda h, uid: [])
    monkeypatch.setattr(lt, "recent", lambda uid, limit=20: (_ for _ in ()).throw(RuntimeError("no table")))
    d = c.get("/api/trade/orders").get_json()
    assert d["ok"] is True and d["log"] == []


def schwab_mod():
    import schwab
    return schwab


def test_ticket_keeps_market_orders_on_day():
    html = open("templates/terminal.html", encoding="utf-8").read()
    # Market + Good 'til Canceled was refused at Preview; the ticket now
    # disables the other durations for Market and re-checks on load.
    assert "function twTifRules()" in html and 'id="tw-ot-tif-note"' in html
    assert 'id="tw-ot-ext" disabled onchange="twTifRules()"' in html
    assert "addEventListener('DOMContentLoaded',twTifRules)" in html
    assert ".tw-field-row[hidden] { display:none !important; }" in html


def test_market_order_uses_shared_feed_when_schwab_quotes_are_down(live, monkeypatch):
    # Schwab's quote call backs off for 5 min after any error; the ticket
    # showed bid/last/ask from the shared feed but Preview said "No current price".
    import app as legacy
    import schwab
    c, store, sent, _ = live
    store[(1, "live_trading_enabled")] = "1"
    monkeypatch.setattr(schwab, "fetch_quotes", lambda syms, user_id=None: {})
    monkeypatch.setattr(legacy, "_MKT_AVAILABLE", True)
    monkeypatch.setattr(legacy._mkt, "live_quotes", lambda syms: {"NVDA": {"price": 165.37}})
    d = c.post("/api/trade/preview", json={**BASE, "order_type": "MARKET", "limit": "", "qty": "1"}).get_json()
    assert d["token"] and d["est"]["last"] == 165.37
    assert not any("No current price" in b for b in d["blocks"])
