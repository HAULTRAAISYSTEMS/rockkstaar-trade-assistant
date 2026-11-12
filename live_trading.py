"""Guardrails, confirmation and audit log for live Schwab orders.

Every live order goes through the same two steps:

  1. preview  — the ticket is validated, checked against the user's risk
                rules and their real Schwab position/buying power, shown to
                Schwab's own previewOrder, and answered with a short-lived
                signed token bound to that exact order.
  2. place    — the token must match the same order, from the same user,
                within CONFIRM_TTL seconds, and must not have been used. All
                checks run again on fresh data before anything is sent.

Live trading is off until the user turns it on (user setting
live_trading_enabled), and LIVE_TRADING_KILL=1 in the environment turns it
off for everyone. Every attempt — placed, blocked or failed — is written to
live_orders.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from datetime import datetime

from database import get_db, get_user_setting, set_user_setting

logger = logging.getLogger(__name__)

CONFIRM_TTL = 90                  # seconds between preview and confirm
DEFAULT_MAX_ORDER_VALUE = 2500.0  # $ per order until the user changes it

_DDL = """
CREATE TABLE IF NOT EXISTS live_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    account_last4 TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    order_type TEXT NOT NULL,
    summary TEXT,
    payload TEXT,
    status TEXT NOT NULL,
    schwab_order_id TEXT,
    nonce TEXT,
    error TEXT
)
"""


def _ensure(conn) -> None:
    from database import ensure_schema
    ensure_schema(conn, 'live_trading', ['live_orders', 'idx_live_orders_user_day'], _create_tables)


def _create_tables(conn) -> None:
    conn.execute(_DDL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_live_orders_user_day ON live_orders(user_id, trade_date)")


# ── Settings ─────────────────────────────────────────────────────────────────

def kill_switch_on() -> bool:
    return os.environ.get("LIVE_TRADING_KILL", "").strip().lower() in ("1", "true", "yes", "on")


def settings(user_id: int) -> dict:
    try:
        mov = float(get_user_setting(user_id, "live_max_order_value") or DEFAULT_MAX_ORDER_VALUE)
    except (TypeError, ValueError):
        mov = DEFAULT_MAX_ORDER_VALUE
    return {"enabled": get_user_setting(user_id, "live_trading_enabled") == "1" and not kill_switch_on(),
            "kill_switch": kill_switch_on(),
            "max_order_value": mov,
            "account": get_user_setting(user_id, "live_trading_account") or ""}


def save_settings(user_id: int, *, enabled=None, max_order_value=None, account=None) -> dict:
    if enabled is not None:
        set_user_setting(user_id, "live_trading_enabled", "1" if enabled else "0")
    if max_order_value is not None:
        v = float(max_order_value)
        if not (1 <= v <= 1_000_000):
            raise ValueError("Max order value must be between $1 and $1,000,000")
        set_user_setting(user_id, "live_max_order_value", f"{v:.2f}")
    if account is not None:
        set_user_setting(user_id, "live_trading_account", str(account))
    return settings(user_id)


# ── Checks ───────────────────────────────────────────────────────────────────

def est_price(t: dict, last: float | None) -> float | None:
    """Price used for size/risk math: the limit, else the stop, else last."""
    return t.get("limit") or t.get("stop") or last


def check(t: dict, *, cfg: dict, risk: dict, last: float | None, position_qty: float,
          buying_power: float | None, opening_today: int, session_locked: bool,
          market_session: str) -> tuple[list[str], list[str], dict]:
    """(blocks, warnings, numbers). Any block stops the order."""
    blocks: list[str] = []
    warns: list[str] = []
    px = est_price(t, last)
    value = round(px * t["qty"], 2) if px else None

    if cfg.get("kill_switch"):
        blocks.append("Live trading is switched off on the server (LIVE_TRADING_KILL)")
    elif not cfg.get("enabled"):
        blocks.append("Live trading is off — turn it on in the ticket's Live settings")
    if session_locked:
        blocks.append("Your trading day is locked on the Risk page")
    if px is None:
        blocks.append("No current price for this symbol — use a limit order")

    if t["order_type"] == "MARKET" and market_session != "regular":
        blocks.append("Market orders only during regular hours (9:30–4:00 ET) — use a limit order")
    if t["order_type"] != "MARKET" and not t["extended"] and market_session in ("pre_market", "after_hours") and t["tif"] in ("IOC", "FOK"):
        warns.append("Outside regular hours an IOC/FOK order will just cancel — tick Extended hours to trade now")
    if market_session in ("pre_market", "after_hours") and not t["extended"]:
        warns.append("Without Extended hours this order waits for the 9:30 open")

    if value is not None and value > cfg["max_order_value"]:
        blocks.append(f"Order value ${value:,.2f} is over your ${cfg['max_order_value']:,.0f} max per order")

    if t["side"] == "BUY":
        if buying_power is not None and value is not None and value > buying_power:
            blocks.append(f"Order value ${value:,.2f} is more than your buying power ${buying_power:,.2f}")
        max_trades = int(risk.get("max_trades_per_day") or 0)
        if max_trades and opening_today >= max_trades:
            blocks.append(f"You've hit your {max_trades} trades for today")
        stop = t.get("stop_loss")
        acct_size = float(risk.get("account_size") or 0)
        risk_pct = float(risk.get("risk_pct") or 0)
        if stop and px:
            per_share = px - stop
            dollars = round(per_share * t["qty"], 2)
            allowed = round(acct_size * risk_pct / 100, 2)
            if allowed and dollars > allowed:
                max_q = int(allowed // per_share) if per_share > 0 else 0
                blocks.append(f"Risk ${dollars:,.2f} is over your {risk_pct:g}% limit (${allowed:,.2f}) — "
                              f"max {max_q} shares at this stop")
        elif t["order_type"] in ("MARKET", "LIMIT", "STOP_LIMIT", "STOP"):
            warns.append("No stop-loss attached — add a bracket to cap the risk")
    else:
        if t["qty"] > (position_qty or 0):
            blocks.append(f"You hold {position_qty:g} {t['symbol']} — the app doesn't short sell"
                          if position_qty else f"You don't hold {t['symbol']} — the app doesn't short sell")

    if last and t.get("limit") and t["order_type"] == "LIMIT":
        gap = (t["limit"] - last) / last
        if t["side"] == "BUY" and gap > 0.03:
            warns.append(f"Limit is {gap:.1%} above the last price — it will likely fill near the market")
        if t["side"] == "SELL" and gap < -0.03:
            warns.append(f"Limit is {-gap:.1%} below the last price — it will likely fill near the market")

    return blocks, warns, {"price": px, "value": value, "last": last}


# ── Confirmation token ───────────────────────────────────────────────────────

def payload_hash(account_hash: str, payload: dict) -> str:
    return hashlib.sha256((account_hash + json.dumps(payload, sort_keys=True)).encode()).hexdigest()


def make_token(secret: str, user_id: int, account_hash: str, payload: dict) -> str:
    from itsdangerous import URLSafeTimedSerializer
    s = URLSafeTimedSerializer(secret, salt="live-order")
    return s.dumps({"u": user_id, "h": payload_hash(account_hash, payload), "n": secrets.token_hex(8)})


def read_token(secret: str, token: str, user_id: int, account_hash: str, payload: dict) -> str:
    """The token's nonce if it's valid for this exact order; raises ValueError."""
    from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
    s = URLSafeTimedSerializer(secret, salt="live-order")
    try:
        d = s.loads(token or "", max_age=CONFIRM_TTL)
    except SignatureExpired:
        raise ValueError("The preview expired — preview the order again")
    except BadSignature:
        raise ValueError("Preview the order before confirming")
    if d.get("u") != user_id or d.get("h") != payload_hash(account_hash, payload):
        raise ValueError("The order changed after the preview — preview it again")
    if nonce_used(d["n"]):
        raise ValueError("This order was already sent")
    return d["n"]


# ── Audit log ────────────────────────────────────────────────────────────────

def nonce_used(nonce: str) -> bool:
    conn = get_db()
    try:
        _ensure(conn)
        return conn.execute("SELECT 1 FROM live_orders WHERE nonce = ?", (nonce,)).fetchone() is not None
    finally:
        conn.close()


def log(user_id: int, t: dict, *, status: str, account_last4="", summary="", payload=None,
        order_id="", nonce=None, error="", trade_date: str) -> None:
    conn = get_db()
    try:
        _ensure(conn)
        conn.execute(
            "INSERT INTO live_orders (user_id, created_at, trade_date, account_last4, symbol, side, qty,"
            " order_type, summary, payload, status, schwab_order_id, nonce, error)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, datetime.now().isoformat(timespec="seconds"), trade_date, account_last4,
             t.get("symbol") or "?", t.get("side") or "?", int(t.get("qty") or 0),
             t.get("order_type") or "?", summary, json.dumps(payload) if payload else None,
             status, order_id, nonce, (error or "")[:500]))
        conn.commit()
    except Exception as exc:
        logger.error("live_orders log failed: %s", exc)
    finally:
        conn.close()


def opening_orders_today(user_id: int, trade_date: str) -> int:
    conn = get_db()
    try:
        _ensure(conn)
        row = conn.execute("SELECT COUNT(*) AS n FROM live_orders WHERE user_id = ? AND trade_date = ?"
                           " AND side = 'BUY' AND status = 'placed'", (user_id, trade_date)).fetchone()
        return int(row["n"] or 0)
    finally:
        conn.close()


def recent(user_id: int, limit: int = 20) -> list[dict]:
    conn = get_db()
    try:
        _ensure(conn)
        rows = conn.execute("SELECT created_at, summary, status, schwab_order_id, error FROM live_orders"
                            " WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
