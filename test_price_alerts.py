"""Price alerts fire: the checker, the chart API and re-arming."""
import os

import pytest

os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")
os.environ.setdefault("SECRET_KEY", "test-only-price-alerts")


@pytest.fixture
def db(tmp_path, monkeypatch):
    import database
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    database.init_db()
    return database


@pytest.fixture
def client(db, monkeypatch):
    import app as legacy
    import web_app
    monkeypatch.setattr(legacy, "_MKT_AVAILABLE", False)
    web_app.app.config["WTF_CSRF_ENABLED"] = False
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    yield c
    web_app.app.config["WTF_CSRF_ENABLED"] = True


def test_direction_and_crossing():
    import price_alert_engine as pae
    assert pae.direction_for(110, 100) == "above" and pae.direction_for(90, 100) == "below"
    assert pae.crossed("above", 110, 110) and not pae.crossed("above", 110, 109.99)
    assert pae.crossed("below", 90, 89.5) and not pae.crossed("below", 90, None)


def test_alert_fires_once_and_notifies(db, monkeypatch):
    import price_alert_engine as pae
    import browser_push
    sent = []
    monkeypatch.setattr(browser_push, "send_to_user", lambda uid, payload, conn=None: sent.append((uid, payload)))
    monkeypatch.setattr(pae, "_telegram", lambda *a: None)
    aid = db.create_price_alert(7, "NVDA", "above", 170.0)
    assert pae.check_once(quotes={"NVDA": {"price": 169.9}})["fired"] == []
    assert pae.check_once(quotes={"NVDA": {"price": 170.2}})["fired"] == [aid]
    assert pae.check_once(quotes={"NVDA": {"price": 171}})["fired"] == []      # once only
    assert sent and sent[0][0] == 7 and "NVDA" in sent[0][1]["title"] and sent[0][1]["tag"] == f"price-{aid}"
    row = [a for a in db.get_price_alerts(7) if a["id"] == aid][0]
    assert not row["enabled"] and row["triggered_at"] and row["triggered_price"] == pytest.approx(170.2)
    db.set_price_alert_enabled(aid, 7, True)                                    # resume re-arms
    row = [a for a in db.get_price_alerts(7) if a["id"] == aid][0]
    assert row["enabled"] and row["triggered_at"] is None


def test_chart_api_sets_direction_from_current_price(client):
    r = client.post("/api/alerts/price", json={"ticker": "goog", "price": 300, "current": 297.1, "note": "Line"})
    a = r.get_json()["alert"]
    assert a["direction"] == "above" and a["ticker"] == "GOOG" and a["price"] == 300
    r = client.post("/api/alerts/price", json={"ticker": "GOOG", "price": 290, "current": 297.1})
    assert r.get_json()["alert"]["direction"] == "below"
    # The same line twice re-arms instead of erroring on the unique key.
    assert client.post("/api/alerts/price", json={"ticker": "GOOG", "price": 300, "current": 297.1}).get_json()["ok"]
    got = client.get("/api/alerts/price?ticker=GOOG").get_json()["alerts"]
    assert sorted(x["price"] for x in got) == [290, 300] and got[0]["source"] == "chart"
    assert client.delete(f"/api/alerts/price/{got[0]['id']}").get_json()["ok"]
    assert len(client.get("/api/alerts/price?ticker=GOOG").get_json()["alerts"]) == 1
    assert client.post("/api/alerts/price", json={"ticker": "GOOG", "price": -1}).status_code == 400
    assert client.post("/api/alerts/price", json={"ticker": "<x>", "price": 5}).status_code == 400


def test_chart_has_alert_controls():
    js = open("static/js/chart_tools.js", encoding="utf-8").read()
    assert "data-ct-cmd=\"alert\"" in js and "function ctAlertFromSelected()" in js
    assert "ctDrawAlerts(ctx, dim, axisLabels)" in js and "KeyW" in js
