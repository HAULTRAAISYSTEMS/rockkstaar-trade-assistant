"""Request timing, slow-request log and a worker-freeze detector.

* Every request's duration is recorded per endpoint (last 300 per endpoint),
  so /admin/speed can show which pages are slowest (p50 / p95 / max).
* A request over SLOW_MS is logged with its path and duration.
* A watchdog thread wakes every second. If it wakes late by more than
  STALL_SECONDS, something held the interpreter (the GIL) — the Nov 9 freeze
  was exactly this — and it logs how long, plus every request in flight at
  that moment, so the next freeze names its own cause in the Render logs.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from collections import defaultdict, deque

logger = logging.getLogger("perf")

SLOW_MS = int(os.environ.get("PERF_SLOW_MS", "3000"))
STALL_SECONDS = float(os.environ.get("PERF_STALL_SECONDS", "3"))
KEEP = 300

_lock = threading.Lock()
_samples: dict[str, deque] = defaultdict(lambda: deque(maxlen=KEEP))
_counts: dict[str, int] = defaultdict(int)
_errors: dict[str, int] = defaultdict(int)
_active: dict[int, tuple[str, float]] = {}
_stalls: deque = deque(maxlen=50)
_slow: deque = deque(maxlen=100)
_started = time.time()
_watchdog_on = False


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def begin(path: str) -> None:
    with _lock:
        _active[threading.get_ident()] = (path, time.monotonic())


def end(endpoint: str, path: str, status: int) -> float | None:
    now = time.monotonic()
    with _lock:
        started = _active.pop(threading.get_ident(), None)
        if not started:
            return None
        ms = (now - started[1]) * 1000.0
        key = endpoint or path
        _samples[key].append(ms)
        _counts[key] += 1
        if status >= 500:
            _errors[key] += 1
        if ms >= SLOW_MS:
            _slow.append({"at": _stamp(), "path": path, "ms": round(ms), "status": status})
    if ms >= SLOW_MS:
        logger.warning("slow request %s %dms (status %s)", path, ms, status)
    return ms


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = min(len(values) - 1, max(0, int(round(p * (len(values) - 1)))))
    return values[k]


def report() -> dict:
    with _lock:
        rows = []
        for key, dq in _samples.items():
            vals = list(dq)
            rows.append({
                "endpoint": key, "count": _counts[key], "errors": _errors[key],
                "p50_ms": round(_pct(vals, .5)), "p95_ms": round(_pct(vals, .95)),
                "max_ms": round(max(vals) if vals else 0),
            })
        now = time.monotonic()
        active = [{"path": p, "running_ms": round((now - t) * 1000)} for p, t in _active.values()]
        stalls = list(_stalls)
        slow = list(_slow)
    rows.sort(key=lambda r: -r["p95_ms"])
    return {"pid": os.getpid(), "uptime_s": round(time.time() - _started), "endpoints": rows,
            "active": sorted(active, key=lambda a: -a["running_ms"]),
            "stalls": stalls[::-1], "slow": slow[::-1]}


def _watchdog(interval: float = 1.0) -> None:
    last = time.monotonic()
    while True:
        time.sleep(interval)
        now = time.monotonic()
        late = now - last - interval
        last = now
        if late >= STALL_SECONDS:
            with _lock:
                inflight = [f"{p} ({(now - t):.1f}s)" for p, t in _active.values()]
                _stalls.append({"at": _stamp(), "seconds": round(late, 1), "inflight": inflight})
            logger.error("worker %s FROZE for %.1fs — requests in flight: %s",
                         os.getpid(), late, ", ".join(inflight) or "none")


def start_watchdog() -> None:
    global _watchdog_on
    with _lock:
        if _watchdog_on:
            return
        _watchdog_on = True
    threading.Thread(target=_watchdog, daemon=True, name="perf-watchdog").start()


def install(app) -> None:
    """Hook timing into a Flask app and start the freeze detector."""
    from flask import request

    @app.before_request
    def _perf_begin():
        begin(request.path)

    @app.teardown_request
    def _perf_end(exc=None):
        try:
            status = 500 if exc is not None else int(getattr(request, "_perf_status", 200))
            end(request.url_rule.rule if request.url_rule else "", request.path, status)
        except Exception:
            pass

    @app.after_request
    def _perf_status(response):
        try:
            request._perf_status = response.status_code
        except Exception:
            pass
        return response

    start_watchdog()
