"""One gunicorn worker owns the app's notification loops.

gunicorn runs two worker processes and each imports app.py, so every
background loop ran twice: the intel alert loop and the scanner's alert
sends fired the same Telegram message from both workers, and the startup
seed ran twice. The first process to take an exclusive lock on a file in
/tmp becomes the leader for its lifetime; the lock is released by the OS
when that process exits, so the next worker (or the recycled one) takes over.

Scans whose results are served from memory still run in every worker —
only things that send messages or write once are gated on is_leader().
"""
from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

LOCK_PATH = os.environ.get("TRADESTAAR_LEADER_LOCK", "/tmp/tradestaar-background.lock")
_lock = threading.Lock()
_handle = None
_leader: bool | None = None


def is_leader() -> bool:
    """True in exactly one process per machine. Re-tried until one wins."""
    global _handle, _leader
    with _lock:
        if _leader:
            return True
        try:
            import fcntl
        except ImportError:          # not POSIX: there is only one process
            _leader = True
            return True
        try:
            handle = open(LOCK_PATH, "a+")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            try:
                handle.close()
            except Exception:
                pass
            _leader = False
            return False
        _handle = handle             # keep it open: closing releases the lock
        _leader = True
        logger.info("background leader: pid %s owns notification loops", os.getpid())
        return True
