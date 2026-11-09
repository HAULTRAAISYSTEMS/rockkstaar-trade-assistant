"""Text the owner on Telegram when the site goes down, and when it comes back.

Runs OUTSIDE the web service — from the 5-minute Render cron job
(live_research_runner.py) — because a site that is down cannot report itself.

One check = GET {SITE_URL}/health. A failure is re-checked once after a short
pause so a deploy's few seconds of restart do not page anyone. State (up/down,
since when, last alert) lives in the shared database's settings table, with a
/tmp file as fallback when the database is the thing that is down.

Env:
  UPTIME_URL / SITE_URL     site root (default the onrender.com address)
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   where alerts go
  UPTIME_REMIND_MINUTES     repeat "still down" every N minutes (default 30)
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://rockkstaar-trade-assistant.onrender.com"
STATE_KEY = "uptime_watchdog_state"
STATE_FILE = "/tmp/tradestaar-uptime-state.json"


def site_url() -> str:
    return (os.environ.get("UPTIME_URL") or os.environ.get("SITE_URL") or DEFAULT_URL).rstrip("/")


def probe(url: str, timeout: float = 15.0) -> tuple[bool, str]:
    """(healthy, detail) for one GET of /health."""
    started = time.monotonic()
    try:
        req = urllib.request.Request(url + "/health", headers={"User-Agent": "TradestaarUptime/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ms = int((time.monotonic() - started) * 1000)
            return (resp.status == 200), f"HTTP {resp.status} in {ms} ms"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: no answer within {int(timeout)}s" if "timed out" in str(exc).lower() \
            else f"{type(exc).__name__}: {str(exc)[:120]}"


def check(url: str, *, retry_after: float = 20.0, prober=probe, sleep=time.sleep) -> tuple[bool, str]:
    ok, detail = prober(url)
    if ok:
        return ok, detail
    sleep(retry_after)
    ok2, detail2 = prober(url)
    return ok2, (detail2 if ok2 else f"{detail}; again after {int(retry_after)}s: {detail2}")


def _load() -> dict:
    try:
        from database import get_setting
        raw = get_setting(STATE_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save(state: dict) -> None:
    raw = json.dumps(state)
    try:
        from database import set_setting
        set_setting(STATE_KEY, raw)
    except Exception:
        pass
    try:
        with open(STATE_FILE, "w") as fh:
            fh.write(raw)
    except Exception:
        pass


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        logger.warning("uptime: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; alert not sent: %s", text)
        return False
    body = json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode()
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.warning("uptime: telegram send failed: %s", exc)
        return False


def _minutes(a: float, b: float) -> int:
    return max(1, int(round((b - a) / 60)))


def _clock(ts: float) -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(ts, ZoneInfo("America/New_York")).strftime("%-I:%M %p ET")
    except Exception:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M UTC")


def run(*, now: float | None = None, checker=check, notify=send_telegram, load=_load, save=_save) -> dict:
    """One watchdog pass. Returns what it saw and whether it alerted."""
    now = time.time() if now is None else now
    url = site_url()
    ok, detail = checker(url)
    state = load() or {}
    prev = state.get("status", "up")
    remind = int(os.environ.get("UPTIME_REMIND_MINUTES", "30")) * 60
    alerted = None

    if ok:
        if prev == "down":
            down_since = state.get("since", now)
            alerted = notify(f"🟢 Tradestaar Elite is back UP ({_clock(now)}). "
                             f"It was down about {_minutes(down_since, now)} min. {detail}.\n{url}")
        state = {"status": "up", "since": state.get("since", now) if prev == "up" else now,
                 "last_ok": now, "detail": detail}
    else:
        if prev != "down":
            alerted = notify(f"🔴 Tradestaar Elite is DOWN ({_clock(now)}).\n{detail}\n{url}\n"
                             "Check Render → Events and Logs.")
            state = {"status": "down", "since": now, "last_alert": now, "detail": detail}
        else:
            state["detail"] = detail
            if now - state.get("last_alert", 0) >= remind:
                alerted = notify(f"🔴 Still DOWN — {_minutes(state.get('since', now), now)} min so far "
                                 f"({_clock(now)}). {detail}\n{url}")
                state["last_alert"] = now
    save(state)
    return {"url": url, "ok": ok, "detail": detail, "status": state["status"], "alerted": alerted}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run()))
