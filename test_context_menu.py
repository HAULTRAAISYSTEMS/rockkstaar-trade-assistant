"""Right-click menus: the ticker-menu API, and the menu wired into every page."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent


@pytest.fixture()
def c(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    import database
    monkeypatch.setattr(database, "DB_PATH", path, raising=False)
    if hasattr(database, "_DB_PATH"):
        monkeypatch.setattr(database, "_DB_PATH", path)
    database.init_db()
    import web_app
    import app as legacy
    monkeypatch.setattr(legacy, "_auth_required", lambda: False)
    monkeypatch.setattr(legacy, "_onboard_ticker_bg", lambda *a, **k: None)
    monkeypatch.setattr(legacy, "_MKT_AVAILABLE", False)
    web_app.app.config["WTF_CSRF_ENABLED"] = False
    client = web_app.app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = 1
    yield client
    web_app.app.config["WTF_CSRF_ENABLED"] = True
    os.unlink(path)


def _mine(c):
    import database
    wl = database.create_watchlist("Menu Test", user_id=1)
    return wl


def test_menu_info_lists_and_orbit(c):
    wl = _mine(c)
    c.post("/api/command/orbit", json={"tickers": ["NVDA"]})
    d = c.get("/api/ticker-menu/nvda").get_json()
    assert d["ok"] and d["ticker"] == "NVDA" and d["in_orbit"] is True
    row = [l for l in d["lists"] if l["id"] == wl][0]
    assert row["has"] is False and row["auto"] is False
    assert c.get("/api/ticker-menu/AMD").get_json()["in_orbit"] is False


def test_add_and_remove_from_a_list(c):
    wl = _mine(c)
    r = c.post("/api/ticker-menu/AMD/watchlist", json={"watchlist_id": wl, "action": "add"}).get_json()
    assert r["ok"] and r["has"] is True
    assert [l for l in c.get("/api/ticker-menu/AMD").get_json()["lists"] if l["id"] == wl][0]["has"] is True
    r = c.post("/api/ticker-menu/AMD/watchlist", json={"watchlist_id": wl, "action": "remove"}).get_json()
    assert r["ok"] and r["has"] is False
    assert [l for l in c.get("/api/ticker-menu/AMD").get_json()["lists"] if l["id"] == wl][0]["has"] is False


def test_rejects_bad_input_and_other_users_lists(c):
    import database
    theirs = database.create_watchlist("Not Yours", user_id=2)
    assert c.get("/api/ticker-menu/<x>").status_code in (400, 404)
    assert c.post("/api/ticker-menu/AMD/watchlist", json={"watchlist_id": theirs, "action": "add"}).status_code == 404
    wl = _mine(c)
    assert c.post("/api/ticker-menu/AMD/watchlist", json={"watchlist_id": wl, "action": "nuke"}).status_code == 400
    assert c.post("/api/ticker-menu/AMD/watchlist", json={"action": "add"}).status_code == 400


def test_every_page_loads_the_menu():
    base = (ROOT / "templates/base.html").read_text()
    assert "js/context_menu.js" in base and "css/context_menu.css" in base


def test_chart_and_globe_hooks():
    ct = (ROOT / "static/js/chart_tools.js").read_text()
    assert "TSMenuQueue" in ct and "ctMenuChartSpec" in ct and "ctMenuDrawingSpec" in ct
    pic = (ROOT / "static/js/command_center_picture.js").read_text()
    for ev in ("cmd27:orbit-set", "cmd27:orbit-reset", "cmd27:orbit-edit", "data-ticker"):
        assert ev in pic
    js = (ROOT / "static/js/context_menu.js").read_text()
    # Text boxes, selections and Shift keep the browser's own menu.
    assert "ev.shiftKey" in js and "editable(ev.target)" in js and "hasSelection" in js


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_scripts_parse():
    for f in ("static/js/context_menu.js", "static/js/chart_tools.js", "static/js/command_center_picture.js"):
        subprocess.run(["node", "--check", str(ROOT / f)], check=True)
