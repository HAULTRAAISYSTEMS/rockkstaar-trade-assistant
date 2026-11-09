"""Uptime watchdog, single-leader background loops, request timing and freeze detection."""
import os
import threading
import time

os.environ.setdefault("SECRET_KEY", "test-only-stability")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import perf_monitor as perf
import uptime_watchdog as uw


# ── Uptime watchdog ──────────────────────────────────────────────────────────

class Store:
    def __init__(self, state=None):
        self.state = state or {}

    def load(self):
        return dict(self.state)

    def save(self, state):
        self.state = dict(state)


def _run(store, ok, now, sent):
    return uw.run(now=now, checker=lambda url: (ok, "HTTP 200 in 80 ms" if ok else "HTTP 502"),
                  notify=lambda text: sent.append(text) or True, load=store.load, save=store.save)


def test_down_alerts_once_reminds_then_recovers(monkeypatch):
    monkeypatch.setenv("UPTIME_REMIND_MINUTES", "30")
    store, sent = Store(), []
    _run(store, True, 1000, sent)
    assert sent == [] and store.state["status"] == "up"
    _run(store, False, 2000, sent)
    assert len(sent) == 1 and "DOWN" in sent[0] and "HTTP 502" in sent[0]
    _run(store, False, 2300, sent)                 # 5 min later: no repeat
    assert len(sent) == 1
    _run(store, False, 2000 + 31 * 60, sent)       # 31 min later: reminder
    assert len(sent) == 2 and "Still DOWN" in sent[1]
    out = _run(store, True, 2000 + 40 * 60, sent)
    assert len(sent) == 3 and "back UP" in sent[2] and "40 min" in sent[2]
    assert out["status"] == "up"
    _run(store, True, 2000 + 45 * 60, sent)
    assert len(sent) == 3


def test_a_blip_during_a_deploy_does_not_page():
    calls, waits = [], []
    results = iter([(False, "HTTP 502"), (True, "HTTP 200 in 90 ms")])
    ok, detail = uw.check("https://x", prober=lambda url: calls.append(url) or next(results),
                          sleep=lambda s: waits.append(s))
    assert ok and len(calls) == 2 and waits == [20.0]


def test_two_failures_report_both():
    ok, detail = uw.check("https://x", prober=lambda url: (False, "HTTP 502"), sleep=lambda s: None)
    assert not ok and "again after 20s" in detail


def test_site_url_and_missing_telegram(monkeypatch):
    monkeypatch.delenv("UPTIME_URL", raising=False)
    monkeypatch.delenv("SITE_URL", raising=False)
    assert uw.site_url() == uw.DEFAULT_URL
    monkeypatch.setenv("UPTIME_URL", "https://tradestaarelite.com/")
    assert uw.site_url() == "https://tradestaarelite.com"
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert uw.send_telegram("x") is False


def test_cron_runner_checks_uptime_first():
    src = open("live_research_runner.py", encoding="utf-8").read()
    assert src.index("uptime_watchdog.run()") < src.index("result=run(tickers)")


# ── Leader election ──────────────────────────────────────────────────────────

def test_only_one_process_is_leader(tmp_path, monkeypatch):
    import subprocess, sys
    lock = str(tmp_path / "leader.lock")
    code = ("import os,sys,time;os.environ['TRADESTAAR_LEADER_LOCK']=sys.argv[1];"
            "import background_leader as b;print(b.is_leader(),flush=True);time.sleep(1.5)")
    env = dict(os.environ, PYTHONPATH=os.getcwd())
    a = subprocess.Popen([sys.executable, "-c", code, lock], stdout=subprocess.PIPE, text=True, env=env)
    first = a.stdout.readline().strip()
    b = subprocess.run([sys.executable, "-c", code, lock], capture_output=True, text=True, env=env)
    a.wait()
    assert first == "True" and b.stdout.strip() == "False"
    c = subprocess.run([sys.executable, "-c", code, lock], capture_output=True, text=True, env=env)
    assert c.stdout.strip() == "True"              # lock released when the leader exits


def test_scanner_alerts_only_from_the_leader(monkeypatch):
    import scanner, background_leader
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    posts = []
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: posts.append(a))
    monkeypatch.setattr(background_leader, "is_leader", lambda: False)
    scanner._send_telegram("hi")
    assert posts == []
    monkeypatch.setattr(background_leader, "is_leader", lambda: True)
    scanner._send_telegram("hi")
    assert len(posts) == 1


# ── Timing and freezes ───────────────────────────────────────────────────────

def test_timing_report_ranks_by_p95():
    perf.begin("/fast")
    perf.end("/fast-rule", "/fast", 200)
    perf.begin("/slow")
    with perf._lock:
        tid = threading.get_ident()
        path, _ = perf._active[tid]
        perf._active[tid] = (path, time.monotonic() - 4.0)
    ms = perf.end("/slow-rule", "/slow", 500)
    assert ms >= 4000
    rep = perf.report()
    names = [r["endpoint"] for r in rep["endpoints"]]
    assert names.index("/slow-rule") < names.index("/fast-rule")
    slow = next(r for r in rep["endpoints"] if r["endpoint"] == "/slow-rule")
    assert slow["errors"] == 1
    assert rep["slow"][0]["path"] == "/slow"


def test_freeze_detector_names_the_request_in_flight():
    perf._stalls.clear()
    perf.begin("/research-memory/api/summarize")

    def hog():
        # Hold the GIL in pure Python long enough to look like the Nov 9 freeze.
        end = time.monotonic() + 0.9
        x = 0
        while time.monotonic() < end:
            x += 1

    old = perf.STALL_SECONDS
    perf.STALL_SECONDS = 0.3
    try:
        t = threading.Thread(target=perf._watchdog, args=(0.05,), daemon=True)
        t.start()
        time.sleep(0.1)
        import sys
        old_si = sys.getswitchinterval()
        sys.setswitchinterval(5)        # make the hog keep the interpreter
        try:
            hog()
        finally:
            sys.setswitchinterval(old_si)
        time.sleep(0.2)
    finally:
        perf.STALL_SECONDS = old
        perf.end("", "/research-memory/api/summarize", 200)
    assert perf._stalls, "a held interpreter should be recorded"
    assert any("summarize" in s for s in perf._stalls[-1]["inflight"])


def test_speed_page_is_admin_only_and_renders():
    import web_app
    app = web_app.app
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    assert c.get("/api/admin/speed").status_code == 403
    with c.session_transaction() as s:
        s["user_id"] = 1
        s["is_admin"] = True
    c.get("/health")
    resp = c.get("/admin/speed")
    assert resp.status_code == 200 and "Speed report" in resp.get_data(as_text=True)
    data = c.get("/api/admin/speed").get_json()
    assert "endpoints" in data and "stalls" in data
