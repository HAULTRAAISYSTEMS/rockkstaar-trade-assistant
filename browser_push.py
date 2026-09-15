"""Free, encrypted Web Push delivery for material saved-thesis changes."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush

from database import get_db
import research_digest
import research_feed_phase2
import research_memory


class BrowserPushError(ValueError):
    pass


P256_ORDER = int("FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", 16)
ALLOWED_PUSH_HOSTS = {
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _secret() -> str:
    value = os.environ.get("WEB_PUSH_ENCRYPTION_KEY") or os.environ.get("SECRET_KEY") or ""
    if not value:
        raise BrowserPushError("Browser push encryption is not configured.")
    return value


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(("push-data:" + _secret()).encode()).digest())
    return Fernet(key)


def _private_key():
    seed = hashlib.sha256(("push-vapid:" + _secret()).encode()).digest()
    value = int.from_bytes(seed, "big") % (P256_ORDER - 1) + 1
    return ec.derive_private_key(value, ec.SECP256R1())


def public_key() -> str:
    raw = _private_key().public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _vapid_private_key() -> str:
    """Return the raw P-256 scalar in the format py-vapid accepts."""
    value = _private_key().private_numbers().private_value
    return base64.urlsafe_b64encode(value.to_bytes(32, "big")).decode().rstrip("=")


def _validate_subscription(value: dict) -> dict:
    if not isinstance(value, dict):
        raise BrowserPushError("The browser did not provide a valid push subscription.")
    endpoint = str(value.get("endpoint") or "").strip()
    keys = value.get("keys") or {}
    auth, p256dh = str(keys.get("auth") or ""), str(keys.get("p256dh") or "")
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").lower()
    allowed = host in ALLOWED_PUSH_HOSTS or host.endswith(".push.apple.com") or host.endswith(".notify.windows.com")
    if parsed.scheme != "https" or not allowed or len(endpoint) > 2048:
        raise BrowserPushError("That push service is not supported.")
    token_pattern = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")
    if not (8 <= len(auth) <= 128 and 40 <= len(p256dh) <= 256
            and token_pattern.fullmatch(auth) and token_pattern.fullmatch(p256dh)):
        raise BrowserPushError("The browser provided invalid subscription keys.")
    return {"endpoint": endpoint, "keys": {"auth": auth, "p256dh": p256dh}}


def _endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()


def _encrypt_subscription(subscription: dict) -> str:
    compact = json.dumps(subscription, separators=(",", ":"), sort_keys=True)
    return _fernet().encrypt(compact.encode()).decode()


def _decrypt_subscription(ciphertext: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(str(ciphertext).encode()).decode())
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise BrowserPushError("A saved browser subscription cannot be decrypted.") from exc


def save_subscription(user_id: int, subscription: dict, *, user_agent: str = "", conn=None) -> dict:
    user_id = int(user_id)
    clean = _validate_subscription(subscription)
    endpoint_hash = _endpoint_hash(clean["endpoint"])
    now = _now().isoformat()
    owns = conn is None
    conn = conn or get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM browser_push_subscriptions WHERE endpoint_hash=?", (endpoint_hash,),
        ).fetchone()
        if existing:
            subscription_id = existing["id"]
            conn.execute(
                "UPDATE browser_push_subscriptions SET user_id=?,subscription_ciphertext=?,user_agent=?,enabled=1,updated_at=?,last_error_code=NULL WHERE id=?",
                (user_id, _encrypt_subscription(clean), str(user_agent or "")[:500], now, subscription_id),
            )
        else:
            subscription_id = str(uuid4())
            conn.execute(
                "INSERT INTO browser_push_subscriptions (id,user_id,endpoint_hash,subscription_ciphertext,user_agent,enabled,created_at,updated_at) VALUES (?,?,?,?,?,1,?,?)",
                (subscription_id, user_id, endpoint_hash, _encrypt_subscription(clean), str(user_agent or "")[:500], now, now),
            )
        pref = conn.execute("SELECT 1 FROM browser_push_preferences WHERE user_id=?", (user_id,)).fetchone()
        if pref:
            conn.execute("UPDATE browser_push_preferences SET enabled=1,updated_at=? WHERE user_id=?", (now, user_id))
        else:
            conn.execute(
                "INSERT INTO browser_push_preferences (user_id,enabled,quiet_start,quiet_end,timezone,daily_cap,created_at,updated_at) VALUES (?,1,21,7,'America/New_York',3,?,?)",
                (user_id, now, now),
            )
        conn.commit()
        return {"id": subscription_id, "enabled": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def remove_subscription(user_id: int, endpoint: str, conn=None) -> None:
    owns = conn is None
    conn = conn or get_db()
    try:
        conn.execute(
            "DELETE FROM browser_push_subscriptions WHERE user_id=? AND endpoint_hash=?",
            (int(user_id), _endpoint_hash(str(endpoint or ""))),
        )
        conn.commit()
    finally:
        if owns:
            conn.close()


def get_settings(user_id: int, conn=None) -> dict:
    owns = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute("SELECT * FROM browser_push_preferences WHERE user_id=?", (int(user_id),)).fetchone()
        devices = conn.execute(
            "SELECT COUNT(*) AS n FROM browser_push_subscriptions WHERE user_id=? AND enabled=1", (int(user_id),),
        ).fetchone()["n"]
        item = dict(row) if row else {}
        return {
            "enabled": bool(item.get("enabled", 1)), "quiet_start": int(item.get("quiet_start", 21)),
            "quiet_end": int(item.get("quiet_end", 7)), "timezone": item.get("timezone") or "America/New_York",
            "daily_cap": int(item.get("daily_cap", 3)), "devices": int(devices or 0),
        }
    finally:
        if owns:
            conn.close()


def update_settings(user_id: int, *, enabled: bool, quiet_start: int, quiet_end: int,
                    timezone_name: str, daily_cap: int, conn=None) -> None:
    user_id = int(user_id)
    quiet_start, quiet_end, daily_cap = int(quiet_start), int(quiet_end), int(daily_cap)
    if not 0 <= quiet_start <= 23 or not 0 <= quiet_end <= 23:
        raise BrowserPushError("Quiet hours must be between 0 and 23.")
    if not 1 <= daily_cap <= 10:
        raise BrowserPushError("The daily notification cap must be between 1 and 10.")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise BrowserPushError("Choose a valid timezone.") from exc
    owns = conn is None
    conn = conn or get_db()
    now = _now().isoformat()
    try:
        exists = conn.execute("SELECT 1 FROM browser_push_preferences WHERE user_id=?", (user_id,)).fetchone()
        if exists:
            conn.execute(
                "UPDATE browser_push_preferences SET enabled=?,quiet_start=?,quiet_end=?,timezone=?,daily_cap=?,updated_at=? WHERE user_id=?",
                (1 if enabled else 0, quiet_start, quiet_end, timezone_name, daily_cap, now, user_id),
            )
        else:
            conn.execute(
                "INSERT INTO browser_push_preferences (user_id,enabled,quiet_start,quiet_end,timezone,daily_cap,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (user_id, 1 if enabled else 0, quiet_start, quiet_end, timezone_name, daily_cap, now, now),
            )
        conn.commit()
    finally:
        if owns:
            conn.close()


def _send(subscription: dict, payload: dict) -> None:
    webpush(
        subscription_info=subscription,
        data=json.dumps(payload, separators=(",", ":")),
        vapid_private_key=_vapid_private_key(),
        vapid_claims={"sub": os.environ.get("WEB_PUSH_VAPID_SUBJECT") or "https://rockkstaar-trade-assistant.onrender.com"},
        ttl=86400,
    )


def _is_quiet(pref: dict, now: datetime) -> bool:
    try:
        hour = now.astimezone(ZoneInfo(pref.get("timezone") or "America/New_York")).hour
    except ZoneInfoNotFoundError:
        hour = now.astimezone(timezone.utc).hour
    start, end = int(pref.get("quiet_start") or 0), int(pref.get("quiet_end") or 0)
    if start == end:
        return False
    return start <= hour < end if start < end else hour >= start or hour < end


def _tracked_tickers(conn, user_id: int, cards: list[dict]) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT ws.ticker FROM watchlist_stocks ws JOIN watchlists w ON w.id=ws.watchlist_id WHERE w.user_id=?",
        (user_id,),
    ).fetchall()
    return list(dict.fromkeys(
        [str(row["ticker"]).upper() for row in rows]
        + [str(card["ticker"]).upper() for card in cards if card.get("ticker")]
    ))


def _payload(item: dict) -> dict:
    impact = str(item.get("thesis_impact") or "uncertain").title()
    why = " ".join(str(item.get("why") or "").split())
    return {
        "title": f"{item['ticker']} thesis change — {impact}",
        "body": f"{item['headline']} — {why[:180]}",
        "url": f"/research-memory?post_id={item['id']}",
        "tag": f"thesis-{item['id']}",
    }


def _status_code(exc: WebPushException) -> int:
    response = getattr(exc, "response", None)
    try:
        return int(getattr(response, "status_code", 0) or 0)
    except (TypeError, ValueError):
        return 0


def send_test(user_id: int, conn=None) -> dict:
    owns = conn is None
    conn = conn or get_db()
    result = {"sent": 0, "expired": 0, "errors": 0}
    try:
        rows = conn.execute(
            "SELECT * FROM browser_push_subscriptions WHERE user_id=? AND enabled=1", (int(user_id),),
        ).fetchall()
        if not rows:
            raise BrowserPushError("Enable notifications on this device first.")
        for raw in rows:
            row = dict(raw)
            try:
                _send(_decrypt_subscription(row["subscription_ciphertext"]), {
                    "title": "Tradestaar notifications are on",
                    "body": "You will only receive material changes that match a saved thesis.",
                    "url": "/notifications", "tag": "tradestaar-push-test",
                })
                conn.execute("UPDATE browser_push_subscriptions SET last_success_at=?,last_error_code=NULL WHERE id=?", (_now().isoformat(), row["id"]))
                result["sent"] += 1
            except WebPushException as exc:
                status = _status_code(exc)
                if status in {404, 410}:
                    conn.execute("UPDATE browser_push_subscriptions SET enabled=0,last_error_code=? WHERE id=?", (str(status), row["id"]))
                    result["expired"] += 1
                else:
                    result["errors"] += 1
        conn.commit()
        return result
    finally:
        if owns:
            conn.close()


def deliver_pending(conn=None, *, now=None) -> dict:
    """Send each qualifying published item once to each opted-in browser."""
    if os.environ.get("BROWSER_PUSH_ENABLED", "1").lower() in {"0", "false", "no", "off"}:
        return {"push_enabled": False, "push_sent": 0, "push_skipped": 0, "push_errors": []}
    now = now or _now()
    owns = conn is None
    conn = conn or get_db()
    result = {"push_enabled": True, "push_sent": 0, "push_skipped": 0, "push_errors": []}
    try:
        prefs = [dict(row) for row in conn.execute(
            "SELECT p.* FROM browser_push_preferences p WHERE p.enabled=1 AND EXISTS "
            "(SELECT 1 FROM browser_push_subscriptions s WHERE s.user_id=p.user_id AND s.enabled=1)"
        ).fetchall()]
        for pref in prefs:
            user_id = int(pref["user_id"])
            if _is_quiet(pref, now):
                result["push_skipped"] += 1
                continue
            try:
                zone = ZoneInfo(pref.get("timezone") or "America/New_York")
            except ZoneInfoNotFoundError:
                zone = timezone.utc
            day_start = now.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
            sent_today = conn.execute(
                "SELECT COUNT(DISTINCT post_id) AS n FROM browser_push_deliveries WHERE user_id=? AND status='sent' AND attempted_at>=?",
                (user_id, day_start),
            ).fetchone()["n"]
            remaining = max(0, int(pref["daily_cap"] or 3) - int(sent_today or 0))
            if not remaining:
                result["push_skipped"] += 1
                continue
            cards = research_memory.list_cards(user_id, limit=500, conn=conn)
            tickers = _tracked_tickers(conn, user_id, cards)
            if not tickers:
                continue
            posts = research_feed_phase2.list_published(
                watchlist_tickers=tickers, user_id=user_id, sort="priority", limit=100, conn=conn,
            )
            digest = research_digest.build_digest(
                posts, cards, reviewed_at=(now - timedelta(hours=24)).isoformat(), now=now, limit=12,
            )
            subscriptions = [dict(row) for row in conn.execute(
                "SELECT * FROM browser_push_subscriptions WHERE user_id=? AND enabled=1", (user_id,),
            ).fetchall()]
            for item in [row for row in digest["items"] if row["alert"]][:remaining]:
                for subscription in subscriptions:
                    if conn.execute(
                        "SELECT 1 FROM browser_push_deliveries WHERE subscription_id=? AND post_id=?",
                        (subscription["id"], item["id"]),
                    ).fetchone():
                        continue
                    delivery_id, attempted = str(uuid4()), now.isoformat()
                    conn.execute(
                        "INSERT INTO browser_push_deliveries (id,user_id,subscription_id,post_id,status,attempted_at) VALUES (?,?,?,?,?,?)",
                        (delivery_id, user_id, subscription["id"], item["id"], "sending", attempted),
                    )
                    conn.commit()
                    try:
                        _send(_decrypt_subscription(subscription["subscription_ciphertext"]), _payload(item))
                        conn.execute("UPDATE browser_push_deliveries SET status='sent' WHERE id=?", (delivery_id,))
                        conn.execute("UPDATE browser_push_subscriptions SET last_success_at=?,last_error_code=NULL WHERE id=?", (attempted, subscription["id"]))
                        result["push_sent"] += 1
                    except (WebPushException, BrowserPushError) as exc:
                        status = _status_code(exc) if isinstance(exc, WebPushException) else 0
                        conn.execute("UPDATE browser_push_deliveries SET status='failed',error_code=? WHERE id=?", (str(status or type(exc).__name__), delivery_id))
                        if status in {404, 410}:
                            conn.execute("UPDATE browser_push_subscriptions SET enabled=0,last_error_code=? WHERE id=?", (str(status), subscription["id"]))
                        result["push_errors"].append(f"user:{user_id}:{status or type(exc).__name__}")
                    conn.commit()
        return result
    finally:
        if owns:
            conn.close()
