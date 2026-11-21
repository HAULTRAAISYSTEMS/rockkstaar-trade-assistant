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
    for s in ("Morning<br>Command Center", "Your AI Market Intelligence Briefing", "Game Plan",
              "AI Market Briefing", "A+ Setups Today", "Watchlist Signals", "Economy This Week",
              'id="cmd27-orbit"', 'id="cmd27-money"', 'id="gp-verdict"', "Top 3 for you",
              "command_center_picture.css", "command_center_picture.js"):
        assert s in html, s
    # One home per fact: the index strip, breadth ring and launch cards are gone.
    for gone in ('class="cmd27-kpis"', 'id="cmd27-ring"', 'class="cmd27-launch"', "Market Pulse"):
        assert gone not in html, gone
    # The broken 2027 overlays are gone.
    assert "elite_2027_layout" not in html and "elite_2027_picture" not in html


def test_game_plan_follows_the_trading_engine_gate():
    import command_gameplan as gp
    up = [100 + i * 0.5 for i in range(80)]          # steady uptrend
    down = [140 - i * 0.6 for i in range(80)]        # steady downtrend
    flat_dxy = [100.0] * 10
    on = gp.build_game_plan(up, up, [14.0] * 5, flat_dxy)
    assert on["verdict"] == "RISK-ON" and on["spy"]["trend"] == "bullish"
    caution = gp.build_game_plan(up, up, [22.0] * 5, flat_dxy)
    assert caution["verdict"] == "CAUTION" and "22/30" in caution["rule"]
    off = gp.build_game_plan(down, up, [14.0] * 5, flat_dxy)
    assert off["verdict"] == "RISK-OFF" and "27+/30" in off["rule"]
    vix_off = gp.build_game_plan(up, up, [36.0] * 5, flat_dxy)
    assert vix_off["verdict"] == "RISK-OFF" and vix_off["vix"]["zone"] == "extreme"
    dxy_off = gp.build_game_plan(up, up, [14.0] * 5, [100, 100, 100, 100, 100, 100, 100, 100, 100, 102.0])
    assert dxy_off["verdict"] == "RISK-OFF" and dxy_off["dxy"]["direction"] == "surging"
    assert off["spy"]["streak"] < 0 and any("down days" in r for r in off["reasons"])
    assert gp.build_game_plan([], [], [], [])["verdict"] == "UNKNOWN"


def test_gameplan_endpoint(client, monkeypatch):
    import web_app
    import app as legacy
    up = [100 + i * 0.5 for i in range(80)]
    monkeypatch.setattr(legacy, "_GAMEPLAN_CACHE", {"at": 0.0, "data": None})
    monkeypatch.setattr(legacy._mkt, "_fetch_prices_chart", lambda t, period="90d": {
        "SPY": up, "QQQ": up, "^VIX": [14.0] * 5, "DX-Y.NYB": [100.0] * 10})
    d = client.get("/api/command/gameplan").get_json()
    assert d["ok"] and d["verdict"] == "RISK-ON" and d["levels"]


def test_command_center_has_no_duplicate_facts():
    src = (ROOT / "templates/liquidity.html").read_text(encoding="utf-8")
    # Economy card is economic-only; your earnings live in Watchlist Signals.
    assert "rejectattr('kind', 'equalto', 'EARNINGS')" in src
    assert "selectattr('kind', 'equalto', 'EARNINGS') | selectattr('on_watchlist')" in src
    js = (ROOT / "static/js/command_center_picture.js").read_text(encoding="utf-8")
    assert "/api/wealth/today" in js and "cmd27.money.last" in js and "/api/thesis/zones" in js


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


def test_scene_is_behind_every_page_and_fixed(client):
    base = (ROOT / "templates/base.html").read_text(encoding="utf-8")
    for s in ('ts-scene__sky" data-depth', 'id="ts-scene-floor"', 'ts-scene__far" data-depth', 'ts-scene__near" data-depth',
              "scene3d.js", "scene_2028.css"):
        assert s in base, s
    assert 'id="cc3d-backdrop"' not in base          # the old line-art backdrop is retired
    css = (ROOT / "static/css/scene_2028.css").read_text(encoding="utf-8")
    assert ".ts-scene { position: fixed;" in css    # stays put while the page scrolls
    js = (ROOT / "static/js/scene3d.js").read_text(encoding="utf-8")
    assert "travel +=" in js                        # scrolling moves you over the floor
    home = client.get("/").get_data(as_text=True)
    assert 'class="cc27 cc-scene"' in home          # Command page: no dimming veil
    other = client.get("/watchlists").get_data(as_text=True)
    assert "cc-scene cc-scene-dim" in other
    for f in ("scene_sky.webp", "scene_far.webp", "scene_near.webp"):
        p = ROOT / "static/img" / f
        assert p.exists() and p.stat().st_size < 300_000, f


def test_accent_is_emerald_everywhere():
    import glob
    for f in glob.glob(str(ROOT / "static/css/*.css")) + glob.glob(str(ROOT / "templates/*.html")):
        txt = open(f, encoding="utf-8").read().lower()
        assert "c4f53b" not in txt and "196, 245, 59" not in txt, f


def test_orbit_saves_per_account(client):
    import web_app
    web_app.app.config["WTF_CSRF_ENABLED"] = False
    try:
        _orbit_round_trip(client)
    finally:
        web_app.app.config["WTF_CSRF_ENABLED"] = True


def test_orbit_post_requires_csrf(client):
    assert client.post("/api/command/orbit", json={"tickers": ["NVDA"]}).status_code == 400


def _orbit_round_trip(client):
    assert client.post("/api/command/orbit", json={"tickers": []}).get_json()["tickers"] == []
    assert client.get("/api/command/orbit").get_json()["tickers"] == []
    r = client.post("/api/command/orbit", json={"tickers": ["nvda", "AMD", "NVDA", "bad ticker!", "RKLB"]})
    assert r.status_code == 200 and r.get_json()["tickers"] == ["NVDA", "AMD", "RKLB"]
    assert client.get("/api/command/orbit").get_json()["tickers"] == ["NVDA", "AMD", "RKLB"]
    many = [f"T{i}" for i in range(20)]
    assert len(client.post("/api/command/orbit", json={"tickers": many}).get_json()["tickers"]) == 12
    assert client.post("/api/command/orbit", json={"tickers": "NVDA"}).status_code == 400
    client.post("/api/command/orbit", json={"tickers": []})


def test_orbit_needs_a_signed_in_user():
    import web_app
    c = web_app.app.test_client()
    r = c.get("/api/command/orbit")
    assert r.status_code in (302, 401)


def test_globe_sectors_cover_common_names_and_profiles():
    import globe_sectors as gs
    m = gs.builtin_map()
    for t, etf in (("APLD", "XLK"), ("NBIS", "XLK"), ("CRDO", "SMH"), ("RKLB", "XLI"), ("IREN", "XLK"),
                   ("ALAB", "SMH"), ("COHR", "SMH"), ("NVDA", "XLK"), ("XLF", "XLF")):
        assert m[t] == etf, t
    assert gs.etf_for("Technology", "Semiconductors") == "SMH"
    assert gs.etf_for("Financial Services") == "XLF"
    assert gs.etf_for("Consumer Defensive") == "XLY"
    assert gs.etf_for("Nonsense") == ""


def test_globe_orbit_and_fly_to_are_wired():
    js = (ROOT / "static/js/command_center_3d.js").read_text(encoding="utf-8")
    assert "cmd27:orbit-toggle" in js and "cmd27:focus" in js and "flyTo" in js
    assert "FUT:" in js                              # futures get their own zone
    assert "cands.sort(function (a, b) { return b.pri - a.pri; });" in js   # every pin labelled, by priority
    pj = (ROOT / "static/js/command_center_picture.js").read_text(encoding="utf-8")
    assert "/api/command/orbit" in pj and "cmd27-chip__x" in pj


def test_globe_groups_pins_by_sector(client):
    html = client.get("/").get_data(as_text=True)
    assert 'data-sectors="' in html and "&#34;NVDA&#34;: &#34;XLK&#34;" in html
    js = (ROOT / "static/js/command_center_3d.js").read_text(encoding="utf-8")
    assert "TECH:" in js and "FIN:" in js and "function placeInRegions(" in js
    assert "XLK: 'TECH'" in js and "XLF: 'FIN'" in js


def test_ipad_landscape_keeps_three_columns_and_chips_clear_the_globe():
    css = (ROOT / "static/css/command_center_picture.css").read_text(encoding="utf-8")
    assert "@media (min-width: 1081px) and (max-width: 1400px)" in css   # iPad 1180/1366 landscape
    assert "@media (max-width: 1080px)" in css and "@media (max-width: 1180px)" not in css
    pj = (ROOT / "static/js/command_center_picture.js").read_text(encoding="utf-8")
    assert "Push out until the chip's nearest corner clears the atmosphere" in pj
    assert "rects: rects" in pj
    js = (ROOT / "static/js/command_center_3d.js").read_text(encoding="utf-8")
    assert "safeHalf ? Math.min(R, (safeHalf - 70) / 1.053) : Math.min(R, W * 0.30)" in js
    assert "var tagBoxes = useRegions && zoom < 1.2 ? chipRects.slice() : [];" in js
    assert "if (Pe[2] > 0.3 && ex.live) {" in js     # closed exchanges: beacon only
    nav = (ROOT / "static/css/nav_2027.css").read_text(encoding="utf-8")
    assert "@media (min-width: 761px) and (max-width: 1080px)" in nav


def test_globe_zoom_sector_focus_and_dock():
    js = (ROOT / "static/js/command_center_3d.js").read_text(encoding="utf-8")
    for s in ("cmd27:zoom-cmd", "function focusRegion(", "function sendSector(", "sectorHits.push", "pinch0 = {",
              "host.addEventListener('wheel'", "R *= zoom;", "eBox", "eQuality"):
        assert s in js, s
    # live tape updates refresh the sector panel without cancelling a flight
    assert "if (focusKey) sendSector(focusKey);" in js
    pj = (ROOT / "static/js/command_center_picture.js").read_text(encoding="utf-8")
    assert "cmd27:regions" in pj and "cmd27:sector-open" in pj and "cmd27-sector-list" in pj
    html = (ROOT / "templates/liquidity.html").read_text(encoding="utf-8")
    for s in ('data-zoom="in"', 'data-zoom="out"', 'data-zoom="reset"', 'id="cmd27-sector"', 'id="cmd27-dock"'):
        assert s in html, s


def test_pull_to_refresh_ignores_globe_and_pinches():
    base = (ROOT / "templates/base.html").read_text(encoding="utf-8")
    assert "t.closest('[data-cc3d-orb], [data-no-ptr], canvas" in base
    assert "if(e.touches.length > 1){ pulling = false;" in base
    css = (ROOT / "static/css/command_center_picture.css").read_text(encoding="utf-8")
    assert "touch-action: none; overscroll-behavior: contain;" in css


def test_scene_performance_guards():
    js = (ROOT / "static/js/scene3d.js").read_text(encoding="utf-8")
    assert "var calm = true;" in js and "OX = Math.max(0, (W - vw) / 2);" in js
    css = (ROOT / "static/css/scene_2028.css").read_text(encoding="utf-8")
    assert "backdrop-filter: none !important;" in css



def test_centerpiece_globe_fullscreen_and_edit_mode():
    """Bigger globe spanning the hero, a full-screen mode, and the orbit ×
    only while editing."""
    css = (ROOT / "static/css/command_center_picture.css").read_text(encoding="utf-8")
    assert "grid-column: 1 / -1; grid-row: 1; height: 800px" in css
    assert ".cmd27 .cmd27-hero__globe.is-full { position: fixed !important" in css
    assert ".cmd27-chip__x { display: none; }" in css
    assert ".cmd27-orbit.is-editing .cmd27-chip__x { display: block" in css
    assert "@media (hover: none) { .cmd27-chip__x" not in css
    html = (ROOT / "templates/liquidity.html").read_text(encoding="utf-8")
    assert 'id="cmd27-orbit-edit"' in html and 'id="cmd27-fs"' in html
    assert 'data-mode="portfolio"' in html and 'data-earnings=' in html
    pj = (ROOT / "static/js/command_center_picture.js").read_text(encoding="utf-8")
    assert "orbitEl.classList.toggle('is-editing', editing && mine)" in pj
    assert "box.requestFullscreen || box.webkitRequestFullscreen" in pj
    assert "cmd27:safe" in pj


def test_globe_portfolio_view_shockwaves_and_earnings():
    js = (ROOT / "static/js/command_center_3d.js").read_text(encoding="utf-8")
    # Portfolio: pins sized by position weight, tape ignored while on.
    assert "document.addEventListener('cmd27:portfolio'" in js
    assert "lastTape = e.detail; if (!pMode) setData(e.detail);" in js
    assert "Math.sqrt(nd.weight / maxW)" in js
    # Shockwaves on alert hits / buy-zone entries; earnings ring + countdown.
    assert "document.addEventListener('cmd27:flags'" in js and "waves.push(" in js
    assert "earnDays(" in js and "ctx.setLineDash([3, 3])" in js
    pj = (ROOT / "static/js/command_center_picture.js").read_text(encoding="utf-8")
    assert "cmd27.waves.seen" in pj   # a reload doesn't replay old shockwaves
    assert "'cmd27:portfolio-data'" in pj


def test_page_passes_this_weeks_earnings_to_the_globe(client):
    import html as _html, json, re
    page = client.get("/").get_data(as_text=True)
    m = re.search(r'data-earnings="([^"]*)"', page)
    assert m, "globe is missing its earnings list"
    rows = json.loads(_html.unescape(m.group(1)))
    assert isinstance(rows, list)
    for r in rows:   # ticker + ISO date, nothing invented
        assert set(r) == {"s", "d", "t"} and re.match(r"^\d{4}-\d{2}-\d{2}", r["d"])
    assert 'data-mode="market" aria-pressed="true"' in page
