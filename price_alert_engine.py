"""Price alerts that actually fire.

price_alerts rows were created from the Watchlists page (and now from chart
drawings) but nothing ever checked them: no notification was ever sent. This
module is the checker. The background leader calls check_once() every few
seconds; each enabled, un-fired alert is compared with the shared live quote
(market_engine.live_quotes — the same price the Terminal shows) and fires
once: it is switched off, stamped with the time and price, written to the
notification inbox and pushed to the user's devices.

"above" fires at price >= target, "below" at price <= target. A chart alert
picks its direction from where price is when it is created, so "alert me at
this line" means "when price gets there", from either side.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
MAX_PER_USER = 200
CHECK_EVERY_S = 10
_STATE = {"last_run": 0.0, "last_error": "", "fired": 0, "checked": 0}
_LOCK = threading.Lock()


def direction_for(target: float, current: float | None) -> str:
    """Which way price has to move to reach the target from here."""
    if current is None or current <= 0:
        return "above"
    return "above" if target >= current else "below"


def crossed(direction: str, target: float, price: float | None) -> bool:
    if price is None or price <= 0:
        return False
    return price >= target if direction == "above" else price <= target


def _quotes(tickers: list[str]) -> dict:
    try:
        import market_engine
        return market_engine.live_quotes(tickers) or {}
    except Exception as exc:
        logger.debug("price alerts: quotes failed: %s", exc)
        return {}


def _deliver(alert: dict, price: float, conn) -> None:
    t = alert["ticker"]
    arrow = "↑" if alert["direction"] == "above" else "↓"
    title = f"{t} {arrow} ${alert['target_price']:,.2f}"
    body = f"{t} traded ${price:,.2f} — your alert at ${alert['target_price']:,.2f} " \
           f"({'at or above' if alert['direction'] == 'above' else 'at or below'})."
    if alert.get("note"):
        body += f" Note: {alert['note']}"
    payload = {"title": title, "body": body, "url": f"/terminal?ticker={t}",
               "tag": f"price-{alert['id']}"}
    try:
        import browser_push
        browser_push.send_to_user(int(alert["user_id"]), payload, conn=conn)
    except Exception as exc:
        # No push setup (no VAPID key) must not lose the alert: keep the inbox entry.
        logger.debug("price alert push failed: %s", exc)
        try:
            import notify_inbox
            notify_inbox.add("price", title, body, url=payload["url"], ticker=t, severity="watch",
                             user_id=int(alert["user_id"]), key=f"price-{alert['id']}", conn=conn)
        except Exception:
            pass
    _telegram(alert, title, body)


def _telegram(alert: dict, title: str, body: str) -> None:
    """The Telegram chat is the owner's (the account whose Schwab feeds the
    app), like the Position Guardian's alerts; other users get push + inbox."""
    try:
        from schwab import quotes_user_id
        if int(alert["user_id"]) != int(quotes_user_id() or 0):
            return
        import scanner
        scanner._send_telegram(f"🔔 {title}\n{body}")
    except Exception as exc:
        logger.debug("price alert telegram: %s", exc)


def check_once(conn=None, quotes: dict | None = None, now: datetime | None = None) -> dict:
    """Fire every enabled alert whose target the live price has reached."""
    from database import get_db
    owns = conn is None
    conn = conn or get_db()
    fired = []
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, user_id, ticker, direction, target_price, note FROM price_alerts "
            "WHERE enabled = 1 AND triggered_at IS NULL").fetchall()]
        if not rows:
            return {"checked": 0, "fired": []}
        tickers = sorted({r["ticker"] for r in rows})
        quotes = _quotes(tickers) if quotes is None else quotes
        stamp = (now or datetime.now(ET)).isoformat()
        for r in rows:
            q = quotes.get(r["ticker"]) or {}
            price = q.get("price")
            try:
                price = float(price) if price is not None else None
            except (TypeError, ValueError):
                price = None
            if not crossed(r["direction"], float(r["target_price"]), price):
                continue
            # Claim it first so a second worker or a retry never sends twice.
            cur = conn.execute(
                "UPDATE price_alerts SET enabled = 0, triggered_at = ?, triggered_price = ? "
                "WHERE id = ? AND triggered_at IS NULL", (stamp, price, r["id"]))
            conn.commit()
            if getattr(cur, "rowcount", 1) == 0:
                continue
            _deliver(r, price, conn)
            fired.append(r["id"])
        _STATE["checked"] = len(rows)
        _STATE["fired"] += len(fired)
        return {"checked": len(rows), "fired": fired}
    finally:
        if owns:
            conn.close()


def loop() -> None:
    """Background thread body. Only the leader worker checks."""
    while True:
        try:
            from background_leader import is_leader
            if is_leader():
                with _LOCK:
                    check_once()
                _STATE["last_run"] = time.time()
                _STATE["last_error"] = ""
        except Exception as exc:
            _STATE["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
            logger.warning("price alert loop: %s", exc)
        time.sleep(CHECK_EVERY_S)


def status() -> dict:
    return dict(_STATE)
