"""Price flash: numbers glow green / red when they tick, app-wide, with a setting."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")
import web_app  # noqa: E402

ROOT = Path(__file__).resolve().parent


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(web_app.app.config, "WTF_CSRF_ENABLED", False)
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    return c


def test_every_page_loads_it_and_it_is_on_by_default(client):
    base = (ROOT / "templates/base.html").read_text()
    assert "js/tick_flash.js" in base and "css/tick_flash.css" in base
    client.post("/api/account/prefs", json={"flash_ticks": True})
    html = client.get("/").get_data(as_text=True)
    assert 'data-tick-flash="1"' in html and 'data-pref="flash_ticks"' in html
    client.post("/api/account/prefs", json={"flash_ticks": False})
    assert 'data-tick-flash="0"' in client.get("/").get_data(as_text=True)
    client.post("/api/account/prefs", json={"flash_ticks": True})


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_what_counts_as_a_number():
    js = (ROOT / "static/js/tick_flash.js").read_text()
    harness = """
    global.window = { MutationObserver: function () { this.observe = () => {}; } }; global.document = { documentElement: { getAttribute: () => null }, readyState: 'loading', addEventListener: () => {} };
    global.MutationObserver = function () { this.observe = () => {}; };
    """ + js + """
    const v = window.TSTickFlash.value;
    const out = ['$635.54', '-3.41%', '24H +1.20%', '−22.47', '1,182.00', '$8.3K', '15', '9:26 PM', '15 stocks', 'TECH', '2026'].map(t => { const r = v(t); return r ? r.v : null; });
    console.log(JSON.stringify(out));
    """
    res = subprocess.run(["node", "-e", harness], capture_output=True, text=True, check=True)
    import json
    got = json.loads(res.stdout.strip())
    assert got[:6] == [635.54, -3.41, 1.2, -22.47, 1182.0, 8300.0]
    assert got[6:] == [None, None, None, None, None]   # ranks, times, counts, words, years never flash


def test_the_ticker_name_glows_with_its_number():
    js = (ROOT / "static/js/tick_flash.js").read_text()
    css = (ROOT / "static/css/tick_flash.css").read_text()
    assert "flashName(el, val.v > prev)" in js and "'tf-name-'" in js
    assert "@keyframes tfNameUp" in css and "@keyframes tfNameDn" in css
