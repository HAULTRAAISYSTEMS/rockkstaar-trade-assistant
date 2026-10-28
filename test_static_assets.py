"""Navigation must reuse shell assets without freezing an old deployment."""
from pathlib import Path
import shutil
import subprocess
from urllib.parse import parse_qs, urlsplit

from flask import Flask, url_for
import pytest

from static_assets import install_asset_versions


def test_asset_version_tracks_contents_and_preserves_other_urls(tmp_path):
    css = tmp_path / "shell.css"
    css.write_text("body { color: gold; }")
    app = Flask(__name__, static_folder=str(tmp_path))
    install_asset_versions(app)
    with app.test_request_context():
        first = url_for("static", filename="shell.css")
        assert url_for("static", filename="shell.css") == first
        css.write_text("body { color: white; background: black; }")
        second = url_for("static", filename="shell.css")
        assert first != second
        assert len(parse_qs(urlsplit(second).query)["asset"][0]) == 20
        assert "asset=" not in url_for("static", filename="missing.js")
        assert "asset=" not in url_for("static", filename="logo.png")
        assert "asset=" not in url_for("static", filename="../outside.css")


def test_service_worker_navigation_cache_contract():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for service worker execution test")
    script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const handlers = {};
const store = new Map();
let calls = 0, status = 200, offline = false, cacheFailure = false;
const context = {
  URL, Response,
  self: {location: {origin: 'https://app.test'},
    addEventListener: (name, fn) => handlers[name] = fn},
  caches: {open: async () => {
    if (cacheFailure) throw Error('storage unavailable');
    return {match: async r => store.get(r.url)?.clone(),
      put: async (r, response) => store.set(r.url, response)};
  }},
  fetch: async () => {
    calls++;
    if (offline) throw Error('offline');
    return new Response('network-' + calls, {status});
  }
};
vm.runInNewContext(fs.readFileSync('static/service-worker.js', 'utf8'), context);
async function load(path, mode = 'cors') {
  let response;
  handlers.fetch({request: {url: 'https://app.test' + path, method: 'GET', mode},
    respondWith: value => response = value, waitUntil: () => {}});
  return response && (await response).text();
}
(async () => {
  const a = '/static/css/style.css?asset=' + 'a'.repeat(20);
  const b = '/static/css/style.css?asset=' + 'b'.repeat(20);
  assert.equal(await load(a), 'network-1');
  assert.equal(await load(a), 'network-1');
  assert.equal(calls, 1, 'warm versioned CSS never waits for a fetch');
  assert.equal(await load(b), 'network-2', 'changed deployment fetches new CSS');
  assert.equal(await load('/static/js/main.js'), 'network-3');
  assert.equal(await load('/static/js/main.js'), 'network-4', 'unversioned assets check network');
  status = 503;
  assert.equal(await load('/static/js/main.js'), 'network-4', 'server errors cannot poison cache');
  offline = true;
  assert.equal(await load(b), 'network-2');
  assert.equal(await load('/static/js/main.js'), 'network-4');
  offline = false; status = 200; cacheFailure = true;
  assert.match(await load(a), /^network-/, 'disabled storage cannot break navigation');
  cacheFailure = false;
  assert.notEqual(await load('/research', 'navigate'), await load('/research', 'navigate'),
    'private pages always load from server');
  const before = calls;
  assert.equal(await load('/api/session-status'), undefined);
  assert.equal(calls, before, 'API responses never enter the shell cache');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", script], cwd=Path(__file__).parent,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
