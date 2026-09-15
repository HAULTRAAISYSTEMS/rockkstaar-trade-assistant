"""Opt-in SMS delivery for material changes that match a saved thesis."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import re
import secrets
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet, InvalidToken
import requests

from database import get_db
import research_digest
import research_feed_phase2
import research_memory


class SmsAlertError(ValueError):
    pass


class SmsProviderError(RuntimeError):
    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = str(code or "")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _secret() -> str:
    value = os.environ.get("SMS_ENCRYPTION_KEY") or os.environ.get("SECRET_KEY") or ""
    if not value:
        raise SmsAlertError("SMS encryption is not configured.")
    return value


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(_secret().encode()).digest())
    return Fernet(key)


def normalize_phone(value: str) -> str:
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        normalized = "+" + digits
    elif len(digits) == 10:
        normalized = "+1" + digits
    elif len(digits) == 11 and digits.startswith("1"):
        normalized = "+" + digits
    else:
        normalized = ""
    if not re.fullmatch(r"\+[1-9]\d{9,14}", normalized):
        raise SmsAlertError("Enter a valid phone number, including country code when outside the US.")
    return normalized


def _encrypt(phone: str) -> str:
    return _fernet().encrypt(phone.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(str(ciphertext).encode()).decode()
    except InvalidToken as exc:
        raise SmsAlertError("The saved phone number cannot be decrypted with the configured key.") from exc


def _code_hash(user_id: int, code: str) -> str:
    payload = f"{int(user_id)}:{code}".encode()
    return hmac.new(_secret().encode(), payload, hashlib.sha256).hexdigest()


def provider_ready() -> bool:
    sender = os.environ.get("TWILIO_MESSAGING_SERVICE_SID") or os.environ.get("TWILIO_FROM_NUMBER")
    credentials = ((os.environ.get("TWILIO_API_KEY") and os.environ.get("TWILIO_API_SECRET"))
                   or os.environ.get("TWILIO_AUTH_TOKEN"))
    return bool(os.environ.get("TWILIO_ACCOUNT_SID") and credentials and sender)


def send_message(to: str, body: str, *, timeout: int = 12) -> str:
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    api_key = os.environ.get("TWILIO_API_KEY", "").strip()
    api_secret = os.environ.get("TWILIO_API_SECRET", "").strip()
    service = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "").strip()
    from_number = os.environ.get("TWILIO_FROM_NUMBER", "").strip()
    if not sid or not ((api_key and api_secret) or token) or not (service or from_number):
        raise SmsProviderError("Text delivery is not configured yet.", "not_configured")
    data = {"To": normalize_phone(to), "Body": str(body)[:640]}
    if service:
        data["MessagingServiceSid"] = service
    else:
        data["From"] = normalize_phone(from_number)
    try:
        response = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            data=data, auth=(api_key or sid, api_secret or token), timeout=timeout,
        )
        payload = response.json() if response.content else {}
    except requests.RequestException as exc:
        raise SmsProviderError("The text provider could not be reached.", "network") from exc
    except ValueError:
        payload = {}
    if response.status_code >= 400:
        raise SmsProviderError(payload.get("message") or "The text provider rejected the message.", payload.get("code"))
    return str(payload.get("sid") or "")


def get_preference(user_id: int, conn=None) -> dict:
    owns = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute("SELECT * FROM sms_alert_preferences WHERE user_id=?", (int(user_id),)).fetchone()
        if not row:
            return {"configured": provider_ready(), "exists": False, "verified": False, "enabled": False,
                    "phone_masked": "", "quiet_start": 21, "quiet_end": 7,
                    "timezone": "America/New_York", "daily_cap": 3}
        item = dict(row)
        return {"configured": provider_ready(), "exists": True,
                "verified": bool(item.get("verified_at")), "enabled": bool(item.get("enabled")),
                "phone_masked": "••• ••• " + item.get("phone_last4", ""),
                "quiet_start": int(item.get("quiet_start") or 0), "quiet_end": int(item.get("quiet_end") or 0),
                "timezone": item.get("timezone") or "America/New_York",
                "daily_cap": int(item.get("daily_cap") or 3), "verified_at": item.get("verified_at") or ""}
    finally:
        if owns:
            conn.close()


def begin_verification(user_id: int, phone: str, conn=None) -> None:
    user_id, phone = int(user_id), normalize_phone(phone)
    if not provider_ready():
        raise SmsAlertError("Text delivery is not configured on the server yet.")
    owns = conn is None
    conn = conn or get_db()
    now = _now()
    code = f"{secrets.randbelow(1_000_000):06d}"
    try:
        existing = conn.execute("SELECT created_at FROM sms_alert_preferences WHERE user_id=?", (user_id,)).fetchone()
        created = existing["created_at"] if existing else now.isoformat()
        encrypted = _encrypt(phone)
        if existing:
            conn.execute(
                "UPDATE sms_alert_preferences SET phone_ciphertext=?,phone_last4=?,verified_at=NULL,enabled=0,updated_at=? WHERE user_id=?",
                (encrypted, phone[-4:], now.isoformat(), user_id),
            )
        else:
            conn.execute(
                "INSERT INTO sms_alert_preferences "
                "(user_id,phone_ciphertext,phone_last4,verified_at,enabled,quiet_start,quiet_end,timezone,daily_cap,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (user_id, encrypted, phone[-4:], None, 0, 21, 7, "America/New_York", 3, created, now.isoformat()),
            )
        verification = conn.execute("SELECT 1 FROM sms_alert_verifications WHERE user_id=?", (user_id,)).fetchone()
        verification_values = (_code_hash(user_id, code), (now + timedelta(minutes=10)).isoformat(), now.isoformat(), user_id)
        if verification:
            conn.execute("UPDATE sms_alert_verifications SET code_hash=?,expires_at=?,attempts=0,sent_at=? WHERE user_id=?",
                         verification_values)
        else:
            conn.execute("INSERT INTO sms_alert_verifications (code_hash,expires_at,attempts,sent_at,user_id) VALUES (?,?,0,?,?)",
                         verification_values)
        send_message(phone, f"Tradestaar verification code: {code}. Expires in 10 minutes. Reply STOP to opt out.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def verify_code(user_id: int, code: str, conn=None) -> None:
    user_id = int(user_id)
    code = re.sub(r"\D", "", str(code or ""))
    owns = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute("SELECT * FROM sms_alert_verifications WHERE user_id=?", (user_id,)).fetchone()
        if not row or datetime.fromisoformat(row["expires_at"]) < _now():
            raise SmsAlertError("That verification code expired. Request a new one.")
        if int(row["attempts"] or 0) >= 5:
            raise SmsAlertError("Too many attempts. Request a new verification code.")
        if not hmac.compare_digest(row["code_hash"], _code_hash(user_id, code)):
            conn.execute("UPDATE sms_alert_verifications SET attempts=attempts+1 WHERE user_id=?", (user_id,))
            conn.commit()
            raise SmsAlertError("That verification code is not correct.")
        now = _now().isoformat()
        conn.execute("UPDATE sms_alert_preferences SET verified_at=?,enabled=1,updated_at=? WHERE user_id=?", (now, now, user_id))
        conn.execute("DELETE FROM sms_alert_verifications WHERE user_id=?", (user_id,))
        conn.commit()
    finally:
        if owns:
            conn.close()


def update_preference(user_id: int, *, enabled: bool, quiet_start: int, quiet_end: int,
                      timezone_name: str, daily_cap: int, conn=None) -> None:
    user_id = int(user_id)
    quiet_start, quiet_end, daily_cap = int(quiet_start), int(quiet_end), int(daily_cap)
    if not 0 <= quiet_start <= 23 or not 0 <= quiet_end <= 23:
        raise SmsAlertError("Quiet hours must be between 0 and 23.")
    if not 1 <= daily_cap <= 10:
        raise SmsAlertError("The daily text cap must be between 1 and 10.")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise SmsAlertError("Choose a valid timezone.") from exc
    owns = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute("SELECT verified_at FROM sms_alert_preferences WHERE user_id=?", (user_id,)).fetchone()
        if not row or not row["verified_at"]:
            raise SmsAlertError("Verify your phone number before enabling alerts.")
        conn.execute(
            "UPDATE sms_alert_preferences SET enabled=?,quiet_start=?,quiet_end=?,timezone=?,daily_cap=?,updated_at=? WHERE user_id=?",
            (1 if enabled else 0, quiet_start, quiet_end, timezone_name, daily_cap, _now().isoformat(), user_id),
        )
        conn.commit()
    finally:
        if owns:
            conn.close()


def disable(user_id: int, conn=None) -> None:
    owns = conn is None
    conn = conn or get_db()
    try:
        conn.execute("UPDATE sms_alert_preferences SET enabled=0,updated_at=? WHERE user_id=?", (_now().isoformat(), int(user_id)))
        conn.commit()
    finally:
        if owns:
            conn.close()


def send_test(user_id: int, conn=None) -> str:
    owns = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute("SELECT * FROM sms_alert_preferences WHERE user_id=?", (int(user_id),)).fetchone()
        if not row or not row["verified_at"]:
            raise SmsAlertError("Verify your phone number first.")
        return send_message(_decrypt(row["phone_ciphertext"]),
                            "Tradestaar test: material thesis alerts are connected. Reply STOP to opt out.")
    finally:
        if owns:
            conn.close()


def _is_quiet(row: dict, now: datetime) -> bool:
    try:
        hour = now.astimezone(ZoneInfo(row.get("timezone") or "America/New_York")).hour
    except ZoneInfoNotFoundError:
        hour = now.astimezone(timezone.utc).hour
    start, end = int(row.get("quiet_start") or 0), int(row.get("quiet_end") or 0)
    if start == end:
        return False
    return start <= hour < end if start < end else hour >= start or hour < end


def _tracked_tickers(conn, user_id: int, cards: list[dict]) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT ws.ticker FROM watchlist_stocks ws JOIN watchlists w ON w.id=ws.watchlist_id WHERE w.user_id=?",
        (user_id,),
    ).fetchall()
    return list(dict.fromkeys([str(row["ticker"]).upper() for row in rows]
                              + [str(card["ticker"]).upper() for card in cards if card.get("ticker")]))


def _body(item: dict) -> str:
    impact = str(item.get("thesis_impact") or "uncertain").upper()
    why = " ".join(str(item.get("why") or "").split())
    return (f"Tradestaar thesis alert — {item['ticker']} ({impact})\n"
            f"{item['headline']}\n{why[:180]}\nReply STOP to opt out.")[:640]


def deliver_pending(conn=None, *, now=None) -> dict:
    """Send each qualifying published item at most once per user."""
    if os.environ.get("SMS_ALERTS_ENABLED", "").lower() not in {"1", "true", "yes", "on"}:
        return {"sms_enabled": False, "sms_sent": 0, "sms_skipped": 0, "sms_errors": []}
    if not provider_ready():
        return {"sms_enabled": True, "sms_sent": 0, "sms_skipped": 0,
                "sms_errors": ["provider:not_configured"]}
    now = now or _now()
    owns = conn is None
    conn = conn or get_db()
    result = {"sms_enabled": True, "sms_sent": 0, "sms_skipped": 0, "sms_errors": []}
    try:
        prefs = [dict(row) for row in conn.execute(
            "SELECT * FROM sms_alert_preferences WHERE enabled=1 AND verified_at IS NOT NULL"
        ).fetchall()]
        for pref in prefs:
            user_id = int(pref["user_id"])
            if _is_quiet(pref, now):
                result["sms_skipped"] += 1
                continue
            try:
                local_zone = ZoneInfo(pref.get("timezone") or "America/New_York")
            except ZoneInfoNotFoundError:
                local_zone = timezone.utc
            local_now = now.astimezone(local_zone)
            day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
            sent_today = conn.execute(
                "SELECT COUNT(*) AS n FROM sms_alert_deliveries WHERE user_id=? AND status='sent' AND attempted_at>=?",
                (user_id, day_start),
            ).fetchone()["n"]
            remaining = max(0, int(pref["daily_cap"] or 3) - int(sent_today or 0))
            if not remaining:
                result["sms_skipped"] += 1
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
            for item in [row for row in digest["items"] if row["alert"]][:remaining]:
                if conn.execute("SELECT 1 FROM sms_alert_deliveries WHERE user_id=? AND post_id=?",
                                (user_id, item["id"])).fetchone():
                    continue
                delivery_id = str(uuid4())
                attempted = now.isoformat()
                conn.execute(
                    "INSERT INTO sms_alert_deliveries (id,user_id,post_id,phone_last4,status,attempted_at) VALUES (?,?,?,?,?,?)",
                    (delivery_id, user_id, item["id"], pref["phone_last4"], "sending", attempted),
                )
                conn.commit()  # reserve before the network call; a crash cannot duplicate a text
                try:
                    message_id = send_message(_decrypt(pref["phone_ciphertext"]), _body(item))
                    conn.execute("UPDATE sms_alert_deliveries SET status='sent',provider_message_id=? WHERE id=?",
                                 (message_id, delivery_id))
                    result["sms_sent"] += 1
                except SmsProviderError as exc:
                    conn.execute("UPDATE sms_alert_deliveries SET status='failed',error_code=? WHERE id=?",
                                 (exc.code, delivery_id))
                    if exc.code in {"21610", "30630"}:
                        conn.execute("UPDATE sms_alert_preferences SET enabled=0,updated_at=? WHERE user_id=?",
                                     (attempted, user_id))
                    result["sms_errors"].append(f"user:{user_id}:{exc.code or type(exc).__name__}")
                conn.commit()
        return result
    finally:
        if owns:
            conn.close()
