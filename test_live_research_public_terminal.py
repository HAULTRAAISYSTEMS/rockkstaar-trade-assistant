import unittest
from pathlib import Path


class PublicResearchTerminalContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent
        cls.template = (root / "templates/live_research.html").read_text()
        cls.script = (root / "static/js/live_research.js").read_text()
        cls.styles = (root / "static/css/live_research.css").read_text()

    def test_template_exposes_terminal_controls_and_safe_source(self):
        for text in ("Breaking / Priority", "Highest Priority", "Watchlist", "Compact", "Detailed", "Original source ↗", "TRADESTAAR TAKE"):
            self.assertIn(text, self.template)
        self.assertIn("stock-research-panel", self.template)
        self.assertNotIn("[ingestion:", self.template)

    def test_realtime_client_inserts_without_reload(self):
        self.assertIn("insertPost(post)", self.script)
        self.assertIn("URLSearchParams(location.search)", self.script)
        self.assertNotIn("location.reload()", self.script)
        self.assertIn("/api/live-research/updates", self.script)

    def test_public_styles_are_scoped_and_responsive(self):
        self.assertIn(".lr-public-shell[data-view=compact]", self.styles)
        self.assertIn("@media(max-width:700px)", self.styles)
        self.assertIn("Published research terminal", self.styles)


if __name__ == "__main__":
    unittest.main()
