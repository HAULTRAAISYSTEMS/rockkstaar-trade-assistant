import unittest
from pathlib import Path


class TerminalUiTests(unittest.TestCase):
    def test_chart_tools_are_grouped_and_ema_labels_use_legend(self):
        template = Path("templates/terminal.html").read_text()

        for label in ("Chart", "Overlays", "Indicators", "Draw"):
            self.assertIn(f'<span class="tw-tool-label">{label}</span>', template)
        self.assertIn('id="tw-ema-legend"', template)
        self.assertIn("lastValueVisible:false", template)
        self.assertNotIn("title:'EMA '+period", template)


if __name__ == "__main__":
    unittest.main()
