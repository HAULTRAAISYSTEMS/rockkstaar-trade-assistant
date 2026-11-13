"""Daily value-zone scan: which watchlist names just moved into a buy zone.

Runs from the cron runner once per trading day after the close. For every
user's tracked tickers it rebuilds the Thesis value zones, compares each with
the zone stored from the last scan, and sends ONE digest per user listing the
names that moved into Accumulate or Load the boat (and any that now have the
full Load-the-boat signal: buy zone plus a confirmed daily trend). Push +
inbox for everyone; Telegram for the owner.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from database import get_db, get_user_setting, set_user_setting

logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
SCAN_AFTER = (16, 20)          # ET, after the close
DAY_KEY = "value_zone_scan_day"
BUY = ("accumulate", "load")
RANK = {"pricey": 0, "fair": 1, "accumulate": 2, "load": 3}


def _now() -> datetime:
    return datetime.now(NY)


def due(now: datetime | None = None) -> bool:
    now = now or _now()
    return now.weekday() < 5 and (now.hour, now.minute) >= SCAN_AFTER


def _watch_by_user(conn) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for r in conn.execute("SELECT w.user_id AS uid, ws.ticker AS t FROM watchlist_stocks ws "
                          "JOIN watchlists w ON w.id = ws.watchlist_id").fetchall():
        if r["uid"] is not None and r["t"]:
            out.setdefault(int(r["uid"]), set()).add(str(r["t"]).upper())
    return out


def _zone_for_ticker(ticker: str) -> dict:
    from fundamentals_engine import get_fundamentals
    from market_data import fetch_chart_bars
    from thesis import zone_summary
    try:
        bars, _ = fetch_chart_bars(ticker, "1d", "5y")
    except Exception:
        bars = None
    data = get_fundamentals(ticker)
    # After the close the last daily candle is today's official close —
    # fresher than the price cached with the fundamentals (up to a day old).
    closes = [c for c in ((bars or {}).get("closes") or []) if c]
    if closes:
        val = dict(data.get("valuation") or {})
        old = val.get("price")
        if isinstance(old, (int, float)) and old > 0:
            for k in ("pe", "pfcf", "ps"):
                if isinstance(val.get(k), (int, float)) and val[k] > 0:
                    val[k] = val[k] * closes[-1] / old
        val["price"] = float(closes[-1])
        data = dict(data, valuation=val)
    return zone_summary(data, ticker, bars)


def changes(prev: str | None, z: dict) -> str | None:
    """'entered' when a name moves into (or deeper into) a buy zone, 'signal'
    when it is in one with a confirmed trend for the first time, else None."""
    if not z.get("available"):
        return None
    zone = z.get("zone")
    sig = (z.get("signal") or {}).get("key")
    prev_zone, _, prev_sig = (prev or "").partition("|")
    if zone in BUY and RANK.get(zone, 0) > RANK.get(prev_zone, -1):
        return "entered"
    if zone in BUY and sig in ("load", "buy") and prev_sig not in ("load", "buy"):
        return "signal"
    return None


def _line(z: dict, kind: str) -> str:
    sig = z.get("signal") or {}
    head = ("⚓ " if sig.get("key") in ("load", "buy") else "") + f"{z['ticker']} → {z['zone_label']}"
    tail = f" (${z['price']:,.2f}; buy under ${z['accum_top']:,.2f}, load under ${z['load_top']:,.2f})"
    if kind == "signal" or sig.get("key") in ("load", "buy"):
        tail += f" — {sig.get('title')}"
    return head + tail


def run(now: datetime | None = None, *, budget_s: float = 90.0, zone_fn=None, send_fn=None) -> dict:
    now = now or _now()
    if not due(now):
        return {"skipped": "not due"}
    zone_fn = zone_fn or _zone_for_ticker
    today = now.strftime("%Y-%m-%d")
    conn = get_db()
    try:
        watch = _watch_by_user(conn)
    finally:
        conn.close()
    users = [u for u in watch if get_user_setting(u, DAY_KEY) != today]
    if not users:
        return {"skipped": "done today"}
    started, zones, sent = time.monotonic(), {}, 0
    for t in sorted({t for u in users for t in watch[u]}):
        if time.monotonic() - started > budget_s:
            break                     # the next 5-minute run picks up the rest
        try:
            zones[t] = zone_fn(t)
        except Exception as exc:
            logger.debug("value zone scan %s: %s", t, exc)
            zones[t] = {"available": False}
    finished_users = []
    for u in users:
        if not all(t in zones for t in watch[u]):
            continue
        hits = []
        for t in sorted(watch[u]):
            z = zones[t]
            if not z.get("available"):
                continue
            key = f"value_zone_last:{t}"
            kind = changes(get_user_setting(u, key), z)
            set_user_setting(u, key, f"{z['zone']}|{(z.get('signal') or {}).get('key') or ''}")
            if kind:
                hits.append(_line(z, kind))
        set_user_setting(u, DAY_KEY, today)
        finished_users.append(u)
        if hits:
            title = (f"⚓ {len(hits)} name{'s' if len(hits) != 1 else ''} in a buy zone")
            body = "\n".join(hits)
            (send_fn or _send)(u, title, body)
            sent += 1
    return {"users": len(finished_users), "tickers": len(zones), "digests": sent}


def _send(user_id: int, title: str, body: str) -> None:
    payload = {"title": title, "body": body, "url": "/watchlists",
               "tag": f"zone-{user_id}"}
    try:
        import browser_push
        browser_push.send_to_user(int(user_id), payload)
    except Exception as exc:
        logger.debug("value zone push: %s", exc)
    try:
        from schwab import quotes_user_id
        if int(user_id) == int(quotes_user_id() or 0):
            import scanner
            scanner._send_telegram(f"{title}\n{body}")
    except Exception as exc:
        logger.debug("value zone telegram: %s", exc)
