"""Live order placement through the Schwab Trader API.

schwab.py is read-only on purpose; everything that can move money lives here,
behind live_trading.py's guardrails. This module only knows how to turn a
validated ticket into Schwab's order JSON and how to talk to the endpoints:

    GET    /accounts/accountNumbers               account number -> hash
    POST   /accounts/{hash}/previewOrder          Schwab's own validation
    POST   /accounts/{hash}/orders                place (201 + Location)
    DELETE /accounts/{hash}/orders/{id}           cancel
    GET    /accounts/{hash}/orders                today's orders

Nothing here retries a POST that may have reached Schwab. The only retry is
after a 401, where Schwab rejected the request before looking at it.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import schwab

logger = logging.getLogger(__name__)

ORDER_TYPES = ("MARKET", "LIMIT", "STOP", "STOP_LIMIT")
TIF_MAP = {"DAY": "DAY", "GTC": "GOOD_TILL_CANCEL",
           "IOC": "IMMEDIATE_OR_CANCEL", "FOK": "FILL_OR_KILL"}
OPEN_STATUSES = {"AWAITING_PARENT_ORDER", "AWAITING_CONDITION", "AWAITING_STOP_CONDITION",
                 "AWAITING_MANUAL_REVIEW", "ACCEPTED", "AWAITING_UR_OUT", "PENDING_ACTIVATION",
                 "QUEUED", "WORKING", "NEW", "AWAITING_RELEASE_TIME", "PENDING_ACKNOWLEDGEMENT"}
MAX_QTY = 100_000
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-/]{0,9}$")


class OrderError(ValueError):
    """A ticket that can't become a valid order. The message is for the user."""


# ── Ticket -> validated dict ─────────────────────────────────────────────────

def _price(v, name: str) -> float | None:
    if v in (None, ""):
        return None
    try:
        p = round(float(v), 4)
    except (TypeError, ValueError):
        raise OrderError(f"{name} isn't a number")
    if p <= 0:
        raise OrderError(f"{name} must be above 0")
    return p


def normalize_ticket(raw: dict) -> dict:
    """Validate what the order ticket sent. Raises OrderError with a plain reason."""
    raw = raw or {}
    sym = str(raw.get("symbol") or "").strip().upper()
    if not _SYMBOL.match(sym):
        raise OrderError("Enter a valid stock symbol")
    side = str(raw.get("side") or "").upper()
    if side not in ("BUY", "SELL"):
        raise OrderError("Pick Buy or Sell")
    try:
        qf = float(raw.get("qty") or 0)
    except (TypeError, ValueError):
        raise OrderError("Quantity must be a whole number of shares")
    if qf != int(qf):
        raise OrderError("Quantity must be a whole number of shares")
    qty = int(qf)
    if qty < 1:
        raise OrderError("Quantity must be at least 1 share")
    if qty > MAX_QTY:
        raise OrderError(f"Quantity is capped at {MAX_QTY:,} shares")
    otype = str(raw.get("order_type") or "").upper()
    if otype not in ORDER_TYPES:
        raise OrderError("Pick an order type")
    tif = str(raw.get("tif") or "DAY").upper()
    if tif not in TIF_MAP:
        raise OrderError("Pick a time in force")
    limit = _price(raw.get("limit"), "Limit price")
    stop = _price(raw.get("stop"), "Stop price")
    extended = bool(raw.get("extended"))

    if otype in ("LIMIT", "STOP_LIMIT") and limit is None:
        raise OrderError("Enter a limit price")
    if otype in ("STOP", "STOP_LIMIT") and stop is None:
        raise OrderError("Enter a stop price")
    if otype in ("MARKET", "STOP"):
        limit = None
    if otype in ("MARKET", "LIMIT"):
        stop = None
    if otype == "MARKET" and tif != "DAY":
        raise OrderError("Market orders are Day only")
    if extended:
        if otype != "LIMIT":
            raise OrderError("Extended hours only takes limit orders")
        if tif not in ("DAY", "GTC"):
            raise OrderError("Extended hours orders must be Day or Good 'til Canceled")
    if otype == "STOP_LIMIT":
        if side == "SELL" and limit > stop:
            raise OrderError("A sell stop-limit's limit should be at or below its stop")
        if side == "BUY" and limit < stop:
            raise OrderError("A buy stop-limit's limit should be at or above its stop")

    target = _price(raw.get("target"), "Take-profit price")
    stop_loss = _price(raw.get("stop_loss"), "Stop-loss price")
    bracket = bool(raw.get("bracket")) or target is not None or stop_loss is not None
    if bracket:
        if side != "BUY":
            raise OrderError("Brackets go on buy orders (they exit the position you're opening)")
        if target is None or stop_loss is None:
            raise OrderError("A bracket needs both a take-profit and a stop-loss")
        ref = limit or stop
        if ref is not None and not (target > ref > stop_loss):
            raise OrderError("Bracket prices must be take-profit above entry, stop-loss below it")
        if target <= stop_loss:
            raise OrderError("Take-profit must be above the stop-loss")
    else:
        target = stop_loss = None

    return {"symbol": sym, "side": side, "qty": qty, "order_type": otype, "tif": tif,
            "limit": limit, "stop": stop, "extended": extended,
            "bracket": bracket, "target": target, "stop_loss": stop_loss}


# ── Validated ticket -> Schwab order JSON ────────────────────────────────────

def _px(p: float) -> str:
    return f"{p:.4f}" if p < 1 else f"{p:.2f}"


def _single(sym, instruction, qty, otype, *, price=None, stop=None,
            session="NORMAL", duration="DAY") -> dict:
    o = {"orderType": otype, "session": session, "duration": duration,
         "orderStrategyType": "SINGLE",
         "orderLegCollection": [{"instruction": instruction, "quantity": qty,
                                 "instrument": {"symbol": sym, "assetType": "EQUITY"}}]}
    if price is not None:
        o["price"] = _px(price)
    if stop is not None:
        o["stopPrice"] = _px(stop)
    return o


def build_payload(t: dict) -> dict:
    """Schwab order JSON for a ticket from normalize_ticket()."""
    session = "SEAMLESS" if t["extended"] else "NORMAL"
    order = _single(t["symbol"], t["side"], t["qty"], t["order_type"],
                    price=t["limit"], stop=t["stop"], session=session,
                    duration=TIF_MAP[t["tif"]])
    if t["bracket"]:
        # Entry triggers a one-cancels-other pair: take profit (limit) and
        # stop loss (stop). Both GTC so the exits outlive the entry's day.
        order["orderStrategyType"] = "TRIGGER"
        order["childOrderStrategies"] = [{
            "orderStrategyType": "OCO",
            "childOrderStrategies": [
                _single(t["symbol"], "SELL", t["qty"], "LIMIT", price=t["target"],
                        duration="GOOD_TILL_CANCEL"),
                _single(t["symbol"], "SELL", t["qty"], "STOP", stop=t["stop_loss"],
                        duration="GOOD_TILL_CANCEL"),
            ]}]
    return order


def describe(t: dict) -> str:
    """One line a person can check before confirming."""
    kind = {"MARKET": "at market", "LIMIT": f"limit {_px(t['limit'])}" if t["limit"] else "",
            "STOP": f"stop {_px(t['stop'])}" if t["stop"] else "",
            "STOP_LIMIT": (f"stop {_px(t['stop'])} / limit {_px(t['limit'])}"
                           if t["stop"] and t["limit"] else "")}[t["order_type"]]
    s = f"{t['side']} {t['qty']:,} {t['symbol']} {kind}, {t['tif']}"
    if t["extended"]:
        s += ", extended hours"
    if t["bracket"]:
        s += f" · take profit {_px(t['target'])} · stop loss {_px(t['stop_loss'])}"
    return s


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _request(method: str, path: str, body: dict | None, user_id: int):
    """(status, headers, parsed body). Retries once only after a 401."""
    import urllib.error
    import urllib.request

    tokens = schwab.load_tokens(user_id)
    if not tokens:
        raise PermissionError("Schwab isn't connected — connect it in Account → Brokers")
    url = f"{schwab._BASE_TRADER}{path}"
    data = json.dumps(body).encode() if body is not None else None

    def _do(token):
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/json",
            **({"Content-Type": "application/json"} if data is not None else {})})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            try:
                parsed = json.loads(raw) if raw else None
            except ValueError:
                parsed = None
            return resp.status, dict(resp.headers), parsed

    try:
        return _do(tokens["access_token"])
    except urllib.error.HTTPError as e:
        if e.code == 401 and tokens.get("refresh_token"):
            with schwab._REFRESH_LOCK:
                latest = schwab._read_stored_tokens(user_id)
                new_tok = schwab.refresh_access_token(latest.get("refresh_token") or tokens["refresh_token"])
                schwab.save_tokens(new_tok, user_id=user_id, is_refresh=True)
                fresh = schwab._read_stored_tokens(user_id)
            try:
                return _do(fresh["access_token"])
            except urllib.error.HTTPError as e2:
                e = e2
        text = e.read().decode(errors="replace")
        try:
            msg = (json.loads(text) or {}).get("message") or text
        except ValueError:
            msg = text
        raise RuntimeError(f"Schwab said no ({e.code}): {str(msg)[:300]}") from e


def account_numbers(user_id: int) -> list[dict]:
    """[{number, hash, last4}] for the user's Schwab accounts."""
    _, _, raw = _request("GET", "/accounts/accountNumbers", None, user_id)
    out = []
    for a in raw or []:
        num, h = str(a.get("accountNumber") or ""), a.get("hashValue")
        if h:
            out.append({"number": num, "hash": h, "last4": num[-4:]})
    return out


def preview(account_hash: str, payload: dict, user_id: int) -> dict:
    """Schwab's own check of the order. Best effort: {} if unavailable."""
    try:
        _, _, raw = _request("POST", f"/accounts/{account_hash}/previewOrder", payload, user_id)
    except Exception as exc:
        logger.info("schwab previewOrder: %s", exc)
        return {"error": str(exc)}
    raw = raw or {}
    val = raw.get("orderValidationResult") or {}
    rejects = [r.get("message") or r.get("activityMessage") or str(r) for r in (val.get("rejects") or [])]
    warns = [r.get("message") or r.get("activityMessage") or str(r) for r in (val.get("warns") or [])]
    bal = (raw.get("orderStrategy") or {}).get("orderBalance") or {}
    return {"rejects": rejects, "warnings": warns,
            "order_value": bal.get("orderValue"), "commission": (raw.get("commissionAndFee") or {}).get("commission")}


def place(account_hash: str, payload: dict, user_id: int) -> str:
    """Send the order. Returns Schwab's order id (or '' if it gave none)."""
    status, headers, _ = _request("POST", f"/accounts/{account_hash}/orders", payload, user_id)
    loc = headers.get("Location") or headers.get("location") or ""
    m = re.search(r"/orders/(\d+)", loc)
    if status not in (200, 201):
        raise RuntimeError(f"Schwab returned {status}")
    return m.group(1) if m else ""


def cancel(account_hash: str, order_id: str, user_id: int) -> None:
    if not re.fullmatch(r"\d{1,20}", str(order_id)):
        raise OrderError("Bad order id")
    _request("DELETE", f"/accounts/{account_hash}/orders/{order_id}", None, user_id)


def todays_orders(account_hash: str, user_id: int) -> list[dict]:
    """Today's orders, newest first, flattened for the ticket's order list."""
    now = datetime.now(timezone.utc)
    _, _, raw = _request("GET", f"/accounts/{account_hash}/orders?" + "&".join([
        "fromEnteredTime=" + (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "toEnteredTime=" + (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "maxResults=50"]), None, user_id)
    out = []

    def walk(o, parent_open=False):
        n = schwab._normalize_order(o)
        n["order_id"] = str(n["order_id"])
        n["stop_price"] = o.get("stopPrice")
        n["open"] = n["status"] in OPEN_STATUSES
        out.append(n)
        for child in o.get("childOrderStrategies") or []:
            walk(child)
    for o in raw or []:
        walk(o)
    out.sort(key=lambda r: r.get("entered") or "", reverse=True)
    return out
