from datetime import datetime, timezone
import os
from unittest.mock import patch

import research_digest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")


NOW = datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc)


def post(**overrides):
    row = {
        "id": "p1", "ticker": "AMD", "headline": "AMD raises data-center guidance",
        "priority": "High", "catalyst_type": "EARNINGS", "source_name": "AMD Investor Relations",
        "source_url": "https://ir.amd.com/news", "source_published_at": "2026-09-14T18:00:00+00:00",
        "tradestaar_take": "The outlook moved above the prior range.", "metrics": [],
    }
    row.update(overrides)
    return row


def card(**overrides):
    row = {"ticker": "AMD", "headline": "Data center thesis", "thesis_impact": "strengthened",
           "why_it_matters": "AI accelerator share is the central driver.",
           "updated_at": "2026-09-13T12:00:00+00:00"}
    row.update(overrides)
    return row


def test_primary_thesis_change_becomes_a_material_alert():
    result = research_digest.build_digest([post()], [card()], now=NOW)
    assert result["material_count"] == 1 and result["alert_count"] == 1
    assert result["items"][0]["quality"]["label"] == "Primary source"
    assert "matches saved thesis" in result["items"][0]["reasons"]


def test_low_signal_connected_story_is_suppressed():
    result = research_digest.build_digest([post(priority="Low", catalyst_type="NEWS",
        source_name="Unknown blog", source_url="https://example.com")], [], now=NOW)
    assert result["items"] == []


def test_review_checkpoint_suppresses_already_read_items():
    result = research_digest.build_digest([post()], [card()],
        reviewed_at="2026-09-14T19:00:00+00:00", now=NOW)
    assert result["material_count"] == 0


def test_duplicate_headlines_do_not_create_repeat_alerts():
    second = post(id="p2", headline="AMD raises data center guidance")
    result = research_digest.build_digest([post(), second], [card()], now=NOW)
    assert result["material_count"] == 1


def test_established_reporting_is_not_mislabeled_as_primary():
    quality = research_digest.source_quality(post(source_name="Reuters", source_url="https://reuters.com/x"))
    assert quality == {"rank": 2, "key": "established", "label": "Established reporting"}


def test_undated_items_are_not_called_new():
    result = research_digest.build_digest([post(source_published_at="", published_at="", updated_at="")], [card()], now=NOW)
    assert result["items"] == []


def test_command_digest_includes_saved_thesis_tickers_outside_active_watchlist():
    import app
    with (
        patch.object(app, "get_user_setting", return_value=""),
        patch("research_memory.list_cards", return_value=[card(ticker="MSFT")]),
        patch("research_feed_phase2.list_published", return_value=[]) as feed,
    ):
        result = app._personal_research_digest(7, ["AMD"])
    assert feed.call_args.kwargs["watchlist_tickers"] == ["AMD", "MSFT"]
    assert result["tracked_tickers"] == 2


def test_mark_reviewed_saves_only_the_current_users_checkpoint():
    import app
    with app.app.test_request_context("/research-digest/reviewed", method="POST"):
        with patch.object(app, "current_user_id", return_value=42), patch.object(app, "set_user_setting") as save:
            response = app.research_digest_reviewed()
    save.assert_called_once()
    assert save.call_args.args[:2] == (42, "research_digest_reviewed_at")
    assert response.status_code == 302 and "what-changed" in response.location
