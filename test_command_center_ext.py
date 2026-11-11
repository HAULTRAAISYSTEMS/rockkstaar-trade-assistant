"""Market mood dial, voice briefing script, live board + home-screen shortcuts."""
import json
import os
from datetime import datetime

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-cc-ext")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import command_center as cc


def test_mood_risk_on_neutral_off():
    on = cc.mood(vix=13, spy=1.2, qqq=1.6, es=0.8, nq=1.1, breadth=0.9, news_tone=40)
    assert on["label"] == "Risk-On" and on["score"] >= 62
    off = cc.mood(vix=31, spy=-2.1, qqq=-2.8, breadth=0.1, news_tone=-50)
    assert off["label"] == "Risk-Off" and off["score"] <= 38
    mid = cc.mood(vix=19, spy=0.1, qqq=-0.2, breadth=0.5)
    assert mid["label"] == "Neutral"
    assert [p["name"] for p in on["parts"]][0] == "VIX"           # heaviest weight first


def test_mood_partial_and_unknown():
    only_vix = cc.mood(vix=12)
    assert only_vix["known"] and only_vix["score"] == 100
    assert cc.mood()["known"] is False


def test_sector_breadth_needs_enough_sectors():
    q = {s: {"change_pct": 0.5} for s in ("XLK", "SMH", "XLF", "XLE", "XLY")}
    q["XLU"] = {"change_pct": -0.2}
    assert cc.sector_breadth(q) == pytest.approx(5 / 6)
    assert cc.sector_breadth({"XLK": {"change_pct": 1}}) is None


def test_briefing_script_reads_naturally():
    s = cc.briefing_script(
        now=datetime(2026, 11, 10, 8, 15), name="",
        pulse={"session": "pre", "es": 0.42, "nq": 0.71, "spy": 0.38, "qqq": 0.66, "vix": 16.4},
        mood_reading=cc.mood(vix=16.4, spy=0.38, qqq=0.66, es=0.42, nq=0.71),
        movers=[{"ticker": "CEG", "change_pct": 9.8}, {"ticker": "NVDA", "change_pct": -2.1}, {"ticker": "AAPL", "change_pct": 0.2}],
        earnings=[{"ticker": "PLTR", "when": "today"}],
        events=[{"event": "CPI", "time": "8:30 AM"}])
    assert s.startswith("Good morning. Here's your Tradestaar briefing for Tuesday, November 10.")
    assert "S and P futures are up 0.4 percent" in s and "in pre-market" in s
    assert "C E G up 9.8 percent" in s and "N V D A down 2.1 percent" in s and "A A P L" not in s
    assert "Earnings to watch: P L T R today." in s and "CPI at 8:30 AM" in s
    assert 60 <= len(s.split()) <= 190                          # about a minute spoken


def test_quiet_watchlist_line():
    s = cc.briefing_script(now=datetime(2026, 11, 10, 18, 0), pulse={}, mood_reading={"known": False},
                           movers=[{"ticker": "AAPL", "change_pct": 0.3}], earnings=[], events=[])
    assert s.startswith("Good evening") and "quiet so far" in s


@pytest.fixture
def client():
    import web_app
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    return c


def test_endpoints(client, monkeypatch):
    import app as legacy
    legacy._MOOD_CACHE.update(at=0.0, value=None)
    monkeypatch.setattr(legacy, "_mood_inputs_now",
                        lambda: ({"vix": 15, "spy": 0.5, "session": "regular"}, cc.mood(vix=15, spy=0.5)))
    d = client.get("/api/mood").get_json()
    assert d["ok"] and d["label"] in ("Risk-On", "Neutral") and d["session"] == "regular"
    v = client.get("/api/briefing/voice").get_json()
    assert v["ok"] and "Tradestaar briefing" in v["script"] and v["words"] > 10
    b = client.get("/api/live-board").get_json()
    assert b["ok"] and [r["ticker"] for r in b["rows"]][:2] == ["SPY", "QQQ"]
    page = client.get("/live-board").get_data(as_text=True)
    assert "board.webmanifest" in page and "Add to Home Screen" in page


def test_today_page_has_dial_and_manifests():
    liq = open("templates/liquidity.html", encoding="utf-8").read()
    assert 'id="cm-needle"' in liq and 'id="cm-play"' in liq and "command_center_ext.js" in liq
    m = json.load(open("static/manifest.webmanifest"))
    assert {s["url"] for s in m["shortcuts"]} >= {"/live-board", "/trade-gate", "/guardian", "/research-memory"}
    board = json.load(open("static/board.webmanifest"))
    assert board["start_url"] == "/live-board" and board["display"] == "standalone"
    js = open("static/js/command_center_ext.js", encoding="utf-8").read()
    assert "speechSynthesis" in js and "/api/briefing/voice" in js


def test_briefing_player_follows_iphone_audio_rules():
    """iPhone: speech must start inside the tap, and the silent switch mutes
    Web Speech unless a media <audio> element plays in the same tap."""
    js = open("static/js/command_center_ext.js", encoding="utf-8").read()
    tap = js[js.index('btn.addEventListener("click"'):]
    assert tap.index("unlockAudio()") < tap.index("speak(script)")      # unlock first, then speak synchronously
    assert "loadScript(); setInterval" in js                             # script is fetched before the tap
    assert "data:audio/wav;base64," in js and "playsinline" in js
    assert 'new SpeechSynthesisUtterance("")' not in js                  # an empty utterance doesn't unlock iOS
    assert "Your phone blocked the audio" in js                          # no silent wave forever


def test_trend_regime_is_one_input_to_the_single_verdict():
    """The page showed "Risk Off" (regime) beside "Neutral 54" (dial). The
    regime now feeds the dial as its Trend part."""
    import command_center as cc
    base = dict(vix=19.5, spy=0.15, qqq=0.21, es=-0.2, nq=-0.39)
    plain = cc.mood(**base)
    off = cc.mood(**base, regime="RISK_OFF")
    assert any(p["name"] == "Trend" for p in off["parts"])
    assert off["score"] < plain["score"]
    assert cc.mood(**base, regime="bogus")["score"] == plain["score"]


def test_today_page_says_each_thing_once():
    html = open("templates/liquidity.html", encoding="utf-8").read()
    assert "Elite news" not in html and "AI Morning Briefing" not in html.replace("AI morning briefing", "")
    assert html.count('id="ai-brief-body"') == 1 and 'class="cc-now' in html
    assert "reject('story_repeat')" in html and "rejectattr('title', 'equalto'" in html


def test_command_center_layout_redesign():
    """Globe is the hero; All/Investing/Trading is gone; body is two columns
    with Setups | Watchlist as tabs and the live feed on the side."""
    html = open("templates/liquidity.html", encoding="utf-8").read()
    assert "liq-mode-toggle" not in html and "setMode(" not in html
    assert "cc-hero-xl" in html and 'class="cc-shortcuts"' in html
    assert "workspace-paths--next" not in html
    assert html.index('class="cc-col-main"') < html.index('class="cc-col-side"')
    assert html.index('class="cc-col-side"') < html.index('id="nx-panel"')
    assert 'data-cc-tab="setups"' in html and 'data-cc-panel="watch" hidden' in html
    scripts = open("templates/_liq_scripts.html", encoding="utf-8").read()
    assert "localStorage.removeItem('cc-mode')" in scripts
    assert "localStorage.getItem('cc-mode')" not in scripts


def test_globe_scales_up_on_big_canvas():
    js = open("static/js/command_center_3d.js", encoding="utf-8").read()
    assert "Math.min(W, H) > 420 ? 0.34 : 0.29" in js
    css = open("static/css/today.css", encoding="utf-8").read()
    assert ".cc-hero-xl > .cc3d-orb" in css and "max-width: 560px" in css
