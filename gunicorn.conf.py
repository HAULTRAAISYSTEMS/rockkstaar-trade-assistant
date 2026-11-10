# Gunicorn reads this file automatically, whatever start command Render uses.
# Command-line flags (the Procfile, or a Start Command set in the Render
# dashboard) override anything here — so this only sets what a flag does not.
#
# Worker recycling. Live prices poll every 3 s from every open page, so the
# old --max-requests 500 restarted each worker every few minutes, and each
# restart is ~12 s with no answer. Recycling is a guard against slow memory
# growth, not a timer: 20000 keeps it at roughly hours. If the Render
# dashboard Start Command still says --max-requests 500, that flag wins —
# change it there to match the Procfile.
max_requests = 20000
max_requests_jitter = 2000


# ── Guards that hold even when the dashboard Start Command overrides flags ──
# Server hooks defined here run whatever the command line says, so the
# recycle floor and the crash diagnostics below cannot be switched off by an
# old --max-requests 500 left in the Render dashboard.
MIN_MAX_REQUESTS = 5000


def on_starting(server):
    cfg = server.cfg
    if 0 < cfg.max_requests < MIN_MAX_REQUESTS:
        server.log.warning(
            "max_requests=%s is too low for 3-second live polling (each recycle is ~12 s "
            "with no answer, the 502s). Raising it to %s. Fix the Render Start Command "
            "to match the Procfile.", cfg.max_requests, max_requests)
        cfg.set("max_requests", max_requests)
        cfg.set("max_requests_jitter", max(cfg.max_requests_jitter, max_requests_jitter))
    server.log.info("gunicorn settings: workers=%s threads=%s timeout=%s max_requests=%s(+%s)",
                    cfg.workers, cfg.threads, cfg.timeout, cfg.max_requests, cfg.max_requests_jitter)


def worker_abort(worker):
    """WORKER TIMEOUT: print every thread's stack so the log shows what hung."""
    import sys
    import threading
    import traceback
    names = {t.ident: t.name for t in threading.enumerate()}
    lines = [f"WORKER TIMEOUT pid={worker.pid} — thread stacks:"]
    for ident, frame in sys._current_frames().items():
        lines.append(f"--- thread {names.get(ident, ident)} ---")
        lines.extend(l.rstrip() for l in traceback.format_stack(frame)[-12:])
    worker.log.error("\n".join(lines))


def worker_exit(server, worker):
    server.log.warning("worker pid=%s exited (requests served: %s)", worker.pid,
                       getattr(worker, "nr", "?"))
