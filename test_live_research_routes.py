import unittest
from unittest.mock import patch

try:
    from flask import Flask
    from live_research_routes import create_live_research_blueprint
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False


@unittest.skipUnless(FLASK_AVAILABLE, "Flask unavailable in minimal execution environment")
class LiveResearchRouteContractTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(create_live_research_blueprint(
            require_admin=lambda view: view,
            current_user=lambda: {"id": 1, "is_admin": True},
            tracked_tickers=lambda _user_id: [],
        ))
        self.client = app.test_client()

    @patch("live_research_routes.realtime.announce_published")
    @patch("live_research_routes.svc.transition_post", return_value="draft")
    def test_approve_has_no_public_realtime_event(self, transition, announce):
        response = self.client.post("/api/admin/live-research/posts/incoming-1/approve")
        self.assertEqual(200, response.status_code)
        self.assertEqual("draft", response.get_json()["status"])
        transition.assert_called_once()
        announce.assert_not_called()

    @patch("live_research_routes.realtime.announce_published")
    @patch("live_research_routes.svc.transition_post", return_value="rejected")
    def test_reject_has_no_public_realtime_event(self, _transition, announce):
        response = self.client.post("/api/admin/live-research/posts/incoming-1/reject")
        self.assertEqual(200, response.status_code)
        announce.assert_not_called()

    @patch("live_research_routes.realtime.announce_published")
    @patch("live_research_routes.svc.bulk_transition")
    def test_bulk_only_announces_explicit_publish(self, bulk, announce):
        bulk.return_value = {"action": "approve", "status": "draft", "posts": [{"id": "a", "ticker": "NVDA"}]}
        response = self.client.post("/api/admin/live-research/bulk", json={"action": "approve", "post_ids": ["a"]})
        self.assertEqual(200, response.status_code)
        announce.assert_not_called()

        bulk.return_value = {"action": "publish", "status": "published", "posts": [{"id": "a", "ticker": "NVDA"}]}
        response = self.client.post("/api/admin/live-research/bulk", json={"action": "publish", "post_ids": ["a"]})
        self.assertEqual(200, response.status_code)
        announce.assert_called_once_with("a", ticker="NVDA")


if __name__ == "__main__":
    unittest.main()
