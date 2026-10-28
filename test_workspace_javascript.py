"""Execute real briefing renderer code against provider failures, not string assertions."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("status,payload,expected", [
    (503, {"ok": False, "error": "secret upstream detail"}, "UNAVAILABLE"),
    (200, {"ok": True, "briefing": {}}, "UNAVAILABLE"),
    (200, {"ok": True, "briefing": {"briefing": "A source-backed summary", "macro_bias": "risk_on"}}, "🟢 RISK ON"),
])
def test_briefing_failure_is_not_rendered_as_neutral(status, payload, expected):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for browser JavaScript regression tests")
    script = Path("templates/_liq_scripts.html").read_text()
    code = script[script.index("function _escB("):script.index("function refreshAIBrief()")]
    harness = f"""
    const assert = require('node:assert/strict');
    const elements = new Map();
    const document = {{getElementById(id) {{
      if (!elements.has(id)) elements.set(id, {{textContent:'',innerHTML:'',style:{{}},classList:{{toggle(){{}}}}}});
      return elements.get(id);
    }}}};
    const fetch = async () => ({{ok:{json.dumps(status < 400)},status:{status},json:async()=>({json.dumps(payload)})}});
    {code}
    loadAIBrief(false);
    setTimeout(() => {{
      assert.equal(elements.get('ai-brief-badge').textContent, {json.dumps(expected)});
      assert.equal(elements.get('ai-brief-refresh-btn').disabled, false);
      assert.ok(!elements.get('ai-brief-body').innerHTML.includes('secret upstream detail'));
    }}, 10);
    """
    subprocess.run([node, "-e", harness], check=True, capture_output=True, text=True, timeout=10)
