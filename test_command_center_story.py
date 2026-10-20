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


# ── /api/account/summary (replaces HTML scraping) ──────────────────────────

def test_account_summary_returns_portfolio_total():
    from unittest.mock import patch
    import web_app  # noqa: F401
    import app as app_module
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    with (
        patch.object(app_module, "_auth_required", return_value=False),
        patch.object(app_module, "_get_portfolio_data",
                     return_value={"connected": True, "total_value": 125000.0, "broker_count": 2}),
    ):
        resp = client.get("/api/account/summary")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["total_value"] == 125000.0


def test_account_summary_no_brokerage_is_not_an_error():
    from unittest.mock import patch
    import web_app  # noqa: F401
    import app as app_module
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    with (
        patch.object(app_module, "_auth_required", return_value=False),
        patch.object(app_module, "_get_portfolio_data",
                     return_value={"connected": False}),
    ):
        resp = client.get("/api/account/summary")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is False


def test_dead_market_story_endpoint_is_gone():
    import web_app  # noqa: F401
    import app as app_module
    assert "api_market_story" not in app_module.app.view_functions


# ── Ticker badge shows the story's subject, not the queried ticker ───────────

def test_enrich_news_article_retags_mislabeled_headline():
    from sentiment_engine import enrich_news_article
    item = enrich_news_article({
        "ticker": "AAPL",
        "headline": "Why Qualcomm (QCOM) Stock Is Down Today",
        "summary": "",
    })
    assert item["ticker"] == "QCOM"


def test_enrich_news_article_keeps_ticker_when_headline_is_silent():
    from sentiment_engine import enrich_news_article
    item = enrich_news_article({
        "ticker": "AAPL",
        "headline": "Apple supplier stocks rally on strong iPhone demand",
        "summary": "",
    })
    assert item["ticker"] == "AAPL"


# ── Trade coach fails closed when the market read is unknown ────────────────

def test_trade_coach_caps_at_watch_when_market_unknown():
    import app as app_module
    stock = {"trade_bias": "Long", "swing_score": 9, "catalyst_score": 8,
             "risk_reward": 3, "daily_trend": "Bullish"}
    plan = {"entry_level": 100, "stop_loss": 95, "target_price": 115}
    market_temp = {"regime": "UNKNOWN", "longs_ok": None, "shorts_ok": None}
    out = app_module.compute_trade_coach(stock, plan, market_temp, {"trading_mode": "SWING TRADE"})
    assert out["coach_status"] == "WATCH"
    assert "unavailable" in out["message"].lower()


def test_trade_coach_still_allows_when_market_known():
    import app as app_module
    stock = {"trade_bias": "Long", "swing_score": 9, "catalyst_score": 8,
             "risk_reward": 3, "daily_trend": "Bullish",
             "trade_permission": {"permission": "TRADE ALLOWED"}}
    plan = {"entry_level": 100, "stop_loss": 95, "target_price": 115}
    market_temp = {"regime": "RISK_ON", "longs_ok": True, "shorts_ok": True}
    out = app_module.compute_trade_coach(stock, plan, market_temp, {"trading_mode": "SWING TRADE"})
    assert out["coach_status"] == "TRADE ALLOWED"


# ── Premarket futures lead (reported 2026-10-20: yesterday's closes at 7:52 AM) ──

def test_market_session_labels():
    assert market_engine.market_session(_et(2026, 10, 20, 7, 52)) == "pre"
    assert market_engine.market_session(_et(2026, 10, 20, 10, 0)) == "regular"
    assert market_engine.market_session(_et(2026, 10, 20, 17, 0)) == "post"
    assert market_engine.market_session(_et(2026, 10, 17, 10, 0)) == "closed"  # Saturday


def test_story_leads_with_futures_before_the_open():
    ctx = _story_ctx(es_1d_chg=-0.09, nq_1d_chg=-0.43)
    with patch.object(market_engine, "market_session", return_value="pre"):
        story = market_engine.generate_market_story(ctx, None)
    assert story["bullets"], "expected bullets"
    assert "Premarket futures" in story["bullets"][0]
    assert "ES -0.09%" in story["bullets"][0]
    assert "NQ -0.43%" in story["bullets"][0]


def test_story_has_no_futures_bullet_during_regular_hours():
    ctx = _story_ctx(es_1d_chg=-0.09, nq_1d_chg=-0.43)
    with patch.object(market_engine, "market_session", return_value="regular"):
        story = market_engine.generate_market_story(ctx, None)
    assert not any("Premarket futures" in b for b in story["bullets"])


# ── Tape: futures ride along permanently, names beside tickers (2026-10-20) ──

def test_tape_carries_futures_and_names():
    ctx = {"spy_price": 766.48, "qqq_price": 718.36,
           "spy_1d_pct": 0.42, "qqq_1d_pct": 1.21,
           "es_futures": 7727.75, "es_1d_chg": -0.09,
           "nq_futures": 29593.75, "nq_1d_chg": -0.43,
           "vix_level": 18.4}
    series = {"DIA": [100.0, 101.0], "IWM": [50.0, 49.0], "^VIX": [18.0, 18.4]}
    with patch.object(market_engine, "get_market_context", return_value=ctx), \
         patch.object(market_engine, "_fetch_prices_chart", return_value=series):
        items = market_engine._build_tape_items()
    by = {i["symbol"]: i for i in items}
    assert {"SPY", "QQQ", "ES", "NQ", "DIA", "IWM", "VIX"} <= set(by)
    assert by["ES"]["price"] == 7727.75 and by["ES"]["change_pct"] == -0.09
    assert by["NQ"]["change_pct"] == -0.43
    assert by["ES"]["name"] == "S&P 500 Futures"
    assert by["SPY"]["name"] == "S&P 500"
    assert by["SPY"]["price"] == 766.48  # ETF close stays — no silent swap


def test_ticker_names_fetch_and_cache():
    market_engine._NAME_CACHE.pop("ZZTEST", None)

    class _Resp:
        def json(self):
            return {"chart": {"result": [{"meta": {"shortName": "Zeta Test Co"}}]}}

    with patch("requests.get", return_value=_Resp()) as mock_get:
        names = market_engine.ticker_names(["ZZTEST"])
        assert names["ZZTEST"] == "Zeta Test Co"
        again = market_engine.ticker_names(["ZZTEST"])
        assert again["ZZTEST"] == "Zeta Test Co"
        assert mock_get.call_count == 1  # fetched once; second call served from cache
    market_engine._NAME_CACHE.pop("ZZTEST", None)
