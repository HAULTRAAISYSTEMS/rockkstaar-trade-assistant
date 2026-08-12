import unittest
from pathlib import Path


class TerminalUiTests(unittest.TestCase):
    def test_chart_tools_are_grouped_and_ema_labels_use_legend(self):
        template = Path("templates/terminal.html").read_text()

        for label in ("Chart", "Overlays", "Indicators", "Draw"):
            self.assertIn(f'<span class="tw-tool-label">{label}</span>', template)
        self.assertIn('id="tw-ema-legend"', template)
        self.assertLess(template.index('id="tw-ema-legend"'), template.index('id="tw-chart-box"'))
        self.assertIn("lastValueVisible:false", template)
        self.assertNotIn("title:'EMA '+period", template)

    def test_command_deck_calculates_session_vwap_from_intraday_bars(self):
        template = Path("templates/terminal.html").read_text()

        self.assertIn("function twSessionVwap(bars,interval)", template)
        self.assertIn("typical*v", template)
        self.assertIn("'tw-intel-vwap'", template)
        self.assertIn("function twLoadSessionVwap(ticker)", template)
        self.assertIn("?interval=5m&range=1d&session=regular", template)


if __name__ == "__main__":
    unittest.main()
