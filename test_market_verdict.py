"""One market verdict across the app."""
import re

import command_center as cc
import market_engine
import market_verdict as mv


def test_finalize_names_the_verdict_and_overrides_on_extreme_vix():
    r = mv.finalize(cc.mood(vix=16, spy=0.4, qqq=0.6, regime="RISK_ON"), "RISK_ON")
    assert r["key"] == "RISK_ON" and r["label"] == "Risk-On" and r["trend"] == "Uptrend"
    r = mv.finalize(cc.mood(vix=34, spy=0.4, qqq=0.6, regime="NO_TRADE"), "NO_TRADE")
    assert r["key"] == "NO_TRADE" and r["label"] == "No Trade"
    assert mv.finalize({"known": False, "label": "Unknown"}, None)["key"] is None


def test_context_regime_is_the_verdict_and_trend_moves_aside():
    ctx = {"regime": "RISK_OFF", "regime_label": "Risk-Off", "signal": "Indices below EMAs",
           "longs_ok": False, "shorts_ok": True}
    out = mv.apply(ctx, {"key": "NEUTRAL", "score": 54})
    assert out["regime"] == "NEUTRAL" and out["regime_label"] == "Neutral"
    assert out["trend_regime"] == "RISK_OFF" and out["trend_label"] == "Downtrend"
    assert out["trend_signal"] == "Indices below EMAs" and out["signal"] == mv.NOTES["NEUTRAL"]
    assert out["longs_ok"] is True and out["reduce_size"] is True
    # applying twice keeps the original trend
    assert mv.apply(out, {"key": "RISK_OFF"})["trend_regime"] == "RISK_OFF"


def test_every_market_context_caller_gets_the_verdict(monkeypatch):
    monkeypatch.setattr(market_engine, "_market_context_raw", lambda: {"regime": "RISK_OFF", "regime_label": "Risk-Off"})
    monkeypatch.setattr(market_engine, "_verdict_provider", lambda: {"key": "RISK_ON", "score": 70})
    assert market_engine.get_market_context()["regime_label"] == "Risk-On"
    assert market_engine.get_market_context(raw=True)["regime"] == "RISK_OFF"
    # A provider that itself asks for the context does not recurse.
    monkeypatch.setattr(market_engine, "_verdict_provider",
                        lambda: {"key": market_engine.get_market_context()["regime"]})
    assert market_engine.get_market_context()["regime"] == "RISK_OFF"


def test_dashboard_meter_speaks_the_verdict():
    mt = {"regime": "RISK_OFF", "label": "DEFENSIVE MODE", "css": "mt-risk-off", "meter_score": 20}
    out = mv.apply_temperature(mt, {"key": "NEUTRAL", "score": 54})
    assert out["label"] == "Neutral" and out["meter_score"] == 54 and out["regime"] == "NEUTRAL"


def test_no_second_verdict_vocabulary_left():
    """Only market_verdict may produce Risk-On / Risk-Off wording for display."""
    liq = open("liquidity_engine.py", encoding="utf-8").read()
    assert not re.search(r'status_label\s*=\s*"Risk', liq)
    tpl = open("templates/liquidity.html", encoding="utf-8").read()
    assert "pulse.trend_label" in tpl


def test_pages_show_the_one_verdict(monkeypatch):
    """Trend engine says Risk-Off; the verdict says Neutral. Every page's
    regime chip must read Neutral, and the trend input must read Downtrend."""
    import app as legacy
    import web_app
    monkeypatch.setattr(market_engine, "_market_context_raw", lambda: {
        "regime": "RISK_OFF", "regime_label": "Risk-Off", "spy_1d_pct": -0.3, "qqq_1d_pct": -0.4,
        "vix_level": 19.0, "sectors": [], "leading_sectors": [], "weak_sectors": []})
    monkeypatch.setattr(market_engine, "_verdict_provider",
                        lambda: {"known": True, "key": "NEUTRAL", "label": "Neutral", "score": 54})
    monkeypatch.setattr(legacy, "_MKT_AVAILABLE", True)
    monkeypatch.setattr(legacy, "_get_mkt_ctx", lambda: market_engine.get_market_context())
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
    term = c.get("/terminal").get_data(as_text=True)
    assert re.search(r'id="tw-chip-regime">\s*Neutral\s*<', term)
    intel = c.get("/intel").get_data(as_text=True)
    assert re.search(r"Market regime</small><strong>Neutral</strong>", intel)
    today = c.get("/").get_data(as_text=True)
    assert 'id="cc-regime">Downtrend<' in today
    assert "is-risk-off" not in today.split("</header>")[0]
