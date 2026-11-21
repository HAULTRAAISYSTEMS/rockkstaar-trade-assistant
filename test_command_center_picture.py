"""Morning Command Center (picture layout): breadth API and page markup."""
from pathlib import Path

import pytest

ROOT = Path(__file__).parent


@pytest.fixture
def client():
    import web_app
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
    return c


def _fake_quotes(changes):
    def _lq(symbols):
        return {s: {"price": 100.0, "change_pct": changes.get(s)} for s in symbols if s in changes}
    return _lq


def test_breadth_counts_sector_funds(client, monkeypatch):
    import market_engine
    syms = [s for s in market_engine.SECTOR_ETFS if s != "IWM"]
    changes = {s: (1.0 if i < 6 else (-1.0 if i < 9 else 0.0)) for i, s in enumerate(syms)}
    monkeypatch.setattr(market_engine, "live_quotes", _fake_quotes(changes))
    d = client.get("/api/today/breadth").get_json()
    assert d["ok"] is True and d["basis"] == "sector funds"
    assert d["total"] == len(syms)
    assert (d["advancing"], d["declining"], d["unchanged"]) == (6, 3, len(syms) - 9)
    assert d["pct_advancing"] == round(6 / len(syms) * 100)
    assert "IWM" not in [r["symbol"] for r in d["rows"]]
    assert d["rows"][0]["change_pct"] >= d["rows"][-1]["change_pct"]


def test_breadth_says_not_ok_without_quotes(client, monkeypatch):
    import market_engine
    monkeypatch.setattr(market_engine, "live_quotes", _fake_quotes({}))
    d = client.get("/api/today/breadth").get_json()
    assert d["ok"] is False and d["pct_advancing"] is None


def test_page_has_the_picture_sections(client):
    html = client.get("/").get_data(as_text=True)
    for s in ("Morning<br>Command Center", "Your AI Market Intelligence Briefing", "Market Pulse",
              "AI Market Briefing", "A+ Setups Today", "Market Breadth", "Economic Events",
              'id="cmd27-orbit"', 'id="cmd27-ring"', 'class="cmd27-kpis"', 'class="cmd27-launch"',
              "Capital Deployment", "command_center_picture.css", "command_center_picture.js"):
        assert s in html, s
    # Labels the Dow and Russell rows honestly by the funds that price them.
    assert ">DIA<" in html and ">IWM<" in html
    # The broken 2027 overlays are gone.
    assert "elite_2027_layout" not in html and "elite_2027_picture" not in html


def test_live_badge_only_claims_live_when_live():
    src = (ROOT / "templates/liquidity.html").read_text(encoding="utf-8")
    assert "'Live market · real-time' if pulse.live else" in src
    assert "latest available" in src


def test_page_css_loads_after_the_global_sheets():
    base = (ROOT / "templates/base.html").read_text(encoding="utf-8")
    assert base.index("elite_2027_complete.css") < base.index("{% block late_css %}")
    assert (ROOT / "static/css/command_center_picture.css").exists()
    assert (ROOT / "static/js/command_center_picture.js").exists()


def test_globe_uses_the_real_earth_texture():
    js = (ROOT / "static/js/command_center_3d.js").read_text(encoding="utf-8")
    assert "earth.webp" in js and "earth_1024.webp" in js
    assert "var CONTINENTS" not in js   # the hand-drawn outlines are gone
    for f in ("earth.webp", "earth_1024.webp"):
        p = ROOT / "static/img" / f
        assert p.exists() and p.stat().st_size < 400_000
    assert (ROOT / "scripts/build_earth_texture.py").exists()
