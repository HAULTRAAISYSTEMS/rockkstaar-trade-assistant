"""Regression tests for the Morning Command Center data bugs reported 2026-10-19.

The reader's photo showed the page arguing with itself:
- the pulse strip's leading sectors (XLU, XLV = Utilities, Healthcare) did not
  match the read's "Institutional inflows: Energy, Utilities";
- a RISK OFF headline sat above a "VIX calm — execution conditions
  favorable for planned setups" bullet;
- "QQQ down 2.5% today" on a Monday morning, when the move was Friday's.

The strip used to render server-side while the read loaded 800ms later from
its own API call, so two cache generations could disagree. The read is now
written server-side from the same snapshot as the strip.
"""
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import market_engine


def _story_ctx(**over):
    ctx = {
        "regime": "RISK_OFF",
        "vix_level": 18.6,
        "qqq_trend": "Below 20 EMA",
        "spy_trend": "Below 20 EMA",
        "leading_sectors": ["XLU", "XLV"],
        "weak_sectors": ["XLY", "IWM"],
        "longs_ok": False,
        "shorts_ok": True,
        "signal": "Indices below EMAs — defensive posture required",
        "qqq_1d_pct": -2.5,
        "spy_1d_pct": -1.11,
        "qqq_5d_pct": -3.0,
        "yield_10y": None,
        "yield_1d_chg": None,
        "es_1d_chg": None,
        "nq_1d_chg": None,
    }
    ctx.update(over)
    return ctx


# ── Session-day label ─────────────────────────────────────────────────────

def _et(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York"))


def test_day_label_is_friday_on_monday_morning():
    # 2026-10-19 was a Monday; the photo was taken at 8:32 AM ET, pre-open.
    assert market_engine._day_label(_et(2026, 10, 19, 8, 30)) == "Friday"


def test_day_label_is_today_during_market_hours():
    assert market_engine._day_label(_et(2026, 10, 19, 10, 0)) == "today"


def test_day_label_is_friday_on_weekends():
    assert market_engine._day_label(_et(2026, 10, 17, 12, 0)) == "Friday"  # Saturday
    assert market_engine._day_label(_et(2026, 10, 18, 12, 0)) == "Friday"  # Sunday


def test_qqq_bullet_names_the_session_not_today():
    with patch.object(market_engine, "_day_label", return_value="Friday"):
        story = market_engine.generate_market_story(_story_ctx())
    assert any("QQQ down 2.5% Friday" in b for b in story["bullets"])
    assert not any("today" in b for b in story["bullets"] if "QQQ" in b)


# ── Regime-aware VIX bullet ───────────────────────────────────────────────

def test_risk_off_never_calls_a_calm_vix_favorable():
    story = market_engine.generate_market_story(_story_ctx(regime="RISK_OFF", vix_level=18.6))
    assert story["sentiment"] == "bearish"
    assert not any("favorable" in b for b in story["bullets"])
    assert any("VIX at 18.6" in b and "wait" in b for b in story["bullets"])


def test_risk_on_keeps_the_favorable_execution_bullet():
    story = market_engine.generate_market_story(_story_ctx(
        regime="RISK_ON", vix_level=18.6, longs_ok=True, shorts_ok=False))
    assert any("VIX calm at 18.6" in b and "favorable" in b for b in story["bullets"])


def test_danger_zone_vix_unchanged_under_risk_off():
    story = market_engine.generate_market_story(_story_ctx(vix_level=35.0))
    assert any("danger zone" in b for b in story["bullets"])


# ── Strip and read share one snapshot ─────────────────────────────────────

def _mkt_snapshot():
    return {
        "regime": "RISK_OFF",
        "regime_label": "Risk-Off",
        "vix_level": 18.6,
        "vix_as_of_label": "8:17 AM ET",
        "vix_source": "Yahoo 5-minute VIX",
        "spy_1d_pct": -1.11,
        "qqq_1d_pct": -2.50,
        "spy_trend": "Below 20 EMA",
        "qqq_trend": "Below 20 EMA",
        "leading_sectors": ["XLU", "XLV"],
        "weak_sectors": ["XLY", "IWM"],
        "longs_ok": False,
        "shorts_ok": True,
        "signal": "Indices below EMAs — defensive posture required",
        "reduce_size": False,
        "no_trade": False,
    }


def _empty_digest():
    return {"items": [], "material_count": 0, "alert_count": 0,
            "reviewed_at": "", "tracked_theses": 0, "tracked_tickers": 0,
            "first_run": True}


def _command_context():
    import web_app  # noqa: F401  (registers blueprints as in production)
    import app as app_module
    import liquidity_engine
    with (
        patch.object(app_module, "_auth_required", return_value=False),
        patch.object(app_module, "_get_mkt_ctx", return_value=_mkt_snapshot()),
        patch.object(app_module, "_MKT_AVAILABLE", True),
        patch.object(app_module, "_intel") as intel,
        patch.object(app_module, "_command_watchlist", return_value=([], "")),
        patch.object(app_module, "_personal_research_digest", return_value=_empty_digest()),
        patch.object(app_module, "current_user_id", return_value=1),
        patch.object(liquidity_engine, "get_liquidity_status",
                     return_value={"score": 95, "status_label": "Abundant"}),
    ):
        intel.get_intel_summary.return_value = {"market_news": [], "earnings": {}}
        return app_module._command_center_context()


def test_story_and_pulse_agree_on_regime_vix_and_sectors():
    ctx = _command_context()
    assert ctx["pulse"]["regime"] == "RISK_OFF"
    assert ctx["pulse"]["vix"] == 18.6
    assert ctx["story"]["regime"] == ctx["pulse"]["regime"]
    # The sector read names the strip's leading sectors — Utilities and
    # Healthcare (XLU, XLV) — never a different snapshot's "Energy".
    assert any("Utilities, Healthcare" in b for b in ctx["story"]["bullets"])
    assert not any("Energy, Utilities" in b for b in ctx["story"]["bullets"])
    assert any("18.6" in b for b in ctx["story"]["bullets"])


def test_command_center_page_renders_story_server_side():
    import web_app  # noqa: F401
    import app as app_module
    import liquidity_engine
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    with (
        patch.object(app_module, "_auth_required", return_value=False),
        patch.object(app_module, "_get_mkt_ctx", return_value=_mkt_snapshot()),
        patch.object(app_module, "_MKT_AVAILABLE", True),
        patch.object(app_module, "_intel") as intel,
        patch.object(app_module, "_command_watchlist", return_value=([], "")),
        patch.object(app_module, "_personal_research_digest", return_value=_empty_digest()),
        patch.object(app_module, "current_user_id", return_value=1),
        patch.object(liquidity_engine, "get_liquidity_status",
                     return_value={"score": 95, "status_label": "Abundant"}),
    ):
        intel.get_intel_summary.return_value = {"market_news": [], "earnings": {}}
        resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # The read is in the initial HTML — no JS fetch needed.
    assert "Today's read" in html
    assert "Risk-Off" in html
    assert "Utilities, Healthcare" in html
    assert "ms-bullet" in html
    # ...and the strip carries the regime tint class.
    assert "pulse-risk_off" in html
