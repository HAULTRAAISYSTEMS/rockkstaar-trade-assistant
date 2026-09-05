"""The command centre and the macro page share one script.

Splitting the heavy panels off the home page left the shared script still
looking for them there. Every lookup was a bare getElementById, so the first
one returned null and threw — and because renderLiquidityStatus() runs before
renderRiskMeter(), the whole load chain died at the first missing panel. The
browser console filled with the same four TypeErrors on every refresh cycle,
on the page the user opens most.

A renderer whose panel is not on this page must stand down quietly. A loader
whose panel is not on this page must not make the request at all.
"""
import re
from pathlib import Path

import pytest

SCRIPT = Path("templates/_liq_scripts.html").read_text()

# Every id the script reaches for without going through panelEl().
DIRECT_LOOKUPS = set(re.findall(r"document\.getElementById\(['\"]([\w-]+)['\"]\)", SCRIPT))


@pytest.fixture(scope="module")
def pages():
    from unittest.mock import patch
    import web_app, app as _app
    client = web_app.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "test"
        sess["logged_in"] = True
    out = {}
    with patch.object(_app, "_auth_required", lambda *a, **k: False):
        for path in ("/", "/opportunity", "/macro"):
            html = client.get(path).get_data(as_text=True)
            out[path] = set(re.findall(r'id="([\w-]+)"', html))
    return out


class TestTheSplitPagesDisagreeAboutWhatExists:
    def test_each_page_is_missing_some_of_the_ids_the_script_looks_up(self, pages):
        """The premise: this is not a hypothetical."""
        for path, ids in pages.items():
            assert DIRECT_LOOKUPS - ids, f"{path} has every panel; the guard tests are moot"


class TestRenderersStandDown:
    def test_the_liquidity_panel_returns_before_it_touches_anything(self):
        body = SCRIPT[SCRIPT.index("function renderLiquidityStatus(d) {"):][:900]
        assert body.index("if (!badge) return;") < body.index("badge.textContent")

    def test_the_risk_meter_still_draws_when_the_yield_table_is_absent(self):
        """The meter is on both pages; the tables under it are not."""
        meter = SCRIPT.index("panelEl('liq-risk-fill')")
        bail  = SCRIPT.index("if (!grid) return;                 // meter is drawn")
        assert meter < bail

    def test_the_regime_box_write_is_guarded(self):
        assert "if (regime && d.status) {" in SCRIPT

    def test_money_flow_scan_and_alerts_all_bail(self):
        for guard in ("if (!grid) return;\n  if (!mflow.length)",
                      "const content = document.getElementById('liq-scan-content');\n  if (!content) return;",
                      "if (!list) return;"):
            assert guard in SCRIPT

    def test_the_scanner_input_is_never_read_off_null(self):
        """This one threw uncaught, not into a console.warn."""
        assert "document.getElementById('extra-tickers-input').value" not in SCRIPT


class TestLoadersDoNotFetchForPanelsThatAreNotThere:
    @pytest.mark.parametrize("fn,gate", [
        ("async function loadMoneyFlow() {", "liq-flow-grid"),
        ("async function loadAlerts() {",    "liq-opp-alerts-list"),
        ("async function runScan() {",       "liq-scan-content"),
    ])
    def test_the_gate_is_the_first_thing_in_the_function(self, fn, gate):
        body = SCRIPT[SCRIPT.index(fn):][:400]
        assert gate in body
        # runScan builds its URL before awaiting; either way the gate is first.
        call = body.index("fetch(") if "fetch(" in body else len(body)
        assert body.index(gate) < call

    def test_the_scan_warmup_post_is_gated_too(self):
        """/api/opportunity/refresh kicked a scan the home page never showed."""
        init = SCRIPT[SCRIPT.index("document.addEventListener('DOMContentLoaded'"):][:600]
        assert "if (document.getElementById('liq-scan-content')) {" in init
