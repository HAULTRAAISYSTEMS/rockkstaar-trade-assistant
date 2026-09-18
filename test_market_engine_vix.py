from unittest.mock import patch

import market_engine


class _Response:
    status_code = 200

    def json(self):
        return {
            "chart": {"result": [{
                "meta": {
                    "regularMarketPrice": 15.21,
                    "regularMarketTime": 1789722736,
                },
                "timestamp": [],
                "indicators": {"quote": [{"close": []}]},
            }]}
        }


def test_vix_quote_uses_current_metadata_and_preserves_its_timestamp():
    with patch("requests.get", return_value=_Response()):
        quote = market_engine._fetch_vix_quote()

    assert quote["level"] == 15.21
    assert quote["as_of"]
    assert quote["as_of_label"].endswith("ET")
    assert quote["source"] == "Yahoo 5-minute VIX"


def test_market_context_replaces_the_stale_daily_vix_with_the_live_quote():
    prices = {
        "QQQ": list(range(100, 161)),
        "SPY": list(range(200, 261)),
        "^VIX": [16.0, 16.5, 17.7],
    }
    with patch.object(market_engine, "_fetch_prices_chart", return_value=prices), patch.object(
        market_engine, "_fetch_macro", return_value={}
    ), patch.object(market_engine, "_fetch_vix_quote", return_value={
        "level": 15.21,
        "as_of": "2026-09-18T09:12:16+00:00",
        "as_of_label": "5:12 AM ET",
        "source": "Yahoo 5-minute VIX",
    }):
        context = market_engine._build_context()

    assert context["vix_level"] == 15.2
    assert context["vix_as_of_label"] == "5:12 AM ET"
    assert context["vix_source"] == "Yahoo 5-minute VIX"


def test_market_context_refreshes_every_two_minutes():
    assert market_engine._CACHE_TTL_MIN == 2
