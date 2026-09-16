"""The morning briefing stays useful when an AI model or provider changes."""
import os

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import app as legacy


def test_provider_json_accepts_a_fenced_object():
    parsed = legacy._briefing_json('```json\n{"macro_bias":"neutral"}\n```')
    assert parsed == {"macro_bias": "neutral"}


def test_anthropic_is_tried_when_nebius_is_not_accessible(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "configured")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured")
    monkeypatch.setattr(legacy, "_generate_nebius_briefing",
                        lambda text: (_ for _ in ()).throw(RuntimeError("403")))
    monkeypatch.setattr(legacy, "_generate_anthropic_briefing",
                        lambda text: {"macro_bias": "risk_off", "provider": "Anthropic"})
    result = legacy._generate_ai_briefing("market data")
    assert result["provider"] == "Anthropic"


def test_the_api_returns_live_data_instead_of_a_raw_provider_error(monkeypatch):
    saved = {}
    monkeypatch.setattr(legacy, "get_ai_briefing", lambda date: None)
    monkeypatch.setattr(legacy, "_build_briefing_market_text", lambda: "market data")
    monkeypatch.setattr(legacy, "_generate_ai_briefing",
                        lambda text: (_ for _ in ()).throw(RuntimeError("403 secret vendor detail")))
    monkeypatch.setattr(legacy, "_live_data_briefing", lambda: {
        "macro_bias": "neutral", "vix_level": "VIX at 17.0",
        "briefing": "Verified live fields.", "tickers_flagged": [],
        "provider": "Live market-data fallback", "degraded": True,
    })
    monkeypatch.setattr(legacy, "save_ai_briefing",
                        lambda date, data: saved.update({"date": date, "data": data}))
    with legacy.app.test_request_context("/api/ai_briefing?refresh=true"):
        response = legacy.api_ai_briefing()
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["briefing"]["provider"] == "Live market-data fallback"
    assert "403" not in payload["briefing"]["briefing"]
    assert saved["data"]["degraded"] is True


def test_live_data_fallback_is_actionable(monkeypatch):
    monkeypatch.setattr(legacy, "_get_mkt_ctx", lambda: {
        "regime": "Caution", "vix_level": 17,
        "spy_1d_pct": -0.46, "qqq_1d_pct": -0.65,
        "leading_sectors": ["Energy", "Materials"],
        "weak_sectors": ["Financials", "Industrials"],
    })
    monkeypatch.setattr(legacy, "get_active_wl_id", lambda: None)
    result = legacy._live_data_briefing()
    assert result["macro_bias"] == "neutral"
    assert "Caution" in result["briefing"]
    assert "SPY -0.5%" in result["briefing"]
    assert result["degraded"] is True
