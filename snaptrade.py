"""
snaptrade.py — Robinhood (and 20+ other brokers) via SnapTrade, read-only.

Why this exists, and why it is not robinhood.py
───────────────────────────────────────────────
Robinhood does not publish a brokerage API. Its only official programmatic
surface is the Crypto Trading API, which covers crypto and nothing else — no
equities, no options, no account balances for a stock account. The endpoints
that libraries like robin_stocks call are Robinhood's own private mobile
endpoints: undocumented, unversioned, and reached by handing a third party
your username, password and 2FA code. That is a credential-sharing pattern
this app will not implement, quite apart from what the customer agreement
says about it.

SnapTrade is the supported path. It is an aggregator that holds the broker
relationships; the user authorises it on Robinhood's own OAuth page and this
app never sees a Robinhood credential. Robinhood through SnapTrade is
read-only — positions, balances and orders, no order placement — which
happens to match this app's Schwab phase 1 exactly.

Shape
─────
Deliberately parallel to schwab.py: is_configured(), token_status(),
get_account_summary() and clear_tokens() have the same names, arguments and
return shapes, so the account page and the risk engine consume either broker
without caring which one answered.

The two differ in one place. Schwab hands this app an OAuth token it stores
and refreshes. SnapTrade instead issues a per-user secret at registration
that never expires and is presented on every call, so there is no refresh
cycle and token_status() reports an expiry of None rather than a countdown.

SETUP
─────
1. Create an app at https://dashboard.snaptrade.com — the Starter tier is
   free and allows five connected accounts.
2. Set environment variables:

     SNAPTRADE_CLIENT_ID     = <your client ID>
     SNAPTRADE_CONSUMER_KEY  = <your consumer key>

3. pip install snaptrade-python-sdk  (already in requirements.txt)

The consumer key signs every request. Never hardcode it, and never log it.

FLOW
────
  GET  /brokers/snaptrade/connect
    → register this app user with SnapTrade if new (stores userSecret)
    → ask SnapTrade for a Connection Portal URL
    → redirect the browser there; the user picks Robinhood and signs in
      on Robinhood's own page

  GET  /brokers/snaptrade/callback
    → the portal returns here; accounts are already linked server-side,
      so this only clears the cache and reports the result

  POST /brokers/snaptrade/disconnect
    → delete the SnapTrade user, which revokes every linked connection
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

_SETTING_SECRET = "snaptrade_user_secret"
_SETTING_LINKED = "snaptrade_linked_at"

# SnapTrade identifies users by a string this app chooses. Deriving it from the
# local user id keeps it stable across reconnects — re-registering under a new
# id would orphan the previous registration and its linked brokerages.
_USER_PREFIX = "tradestaar-"


def _client_id() -> str:
    return (os.environ.get("SNAPTRADE_CLIENT_ID") or "").strip()


def _consumer_key() -> str:
    return (os.environ.get("SNAPTRADE_CONSUMER_KEY") or "").strip()


def is_configured() -> bool:
    """True when both credentials are present in the environment."""
    return bool(_client_id() and _consumer_key())


def snaptrade_user_id(user_id: int = 1) -> str:
    return f"{_USER_PREFIX}{user_id}"


def _client():
    """Build an SDK client, or raise a message worth showing a human.

    The SDK is an optional dependency: the app has to boot and the account
    page has to render on a deploy that never installed it.
    """
    if not is_configured():
        raise RuntimeError(
            "SnapTrade is not configured. Set SNAPTRADE_CLIENT_ID and "
            "SNAPTRADE_CONSUMER_KEY."
        )
    try:
        from snaptrade_client import SnapTrade, SnapTradeAuth
    except ImportError as exc:  # pragma: no cover - depends on the deploy
        raise RuntimeError(
            "The snaptrade-python-sdk package is not installed on this deploy."
        ) from exc
    return SnapTrade(
        auth=SnapTradeAuth.commercial_api_key(
            consumer_key=_consumer_key(),
            client_id=_client_id(),
        )
    )


def _body(response):
    """Unwrap an SDK response to plain Python.

    The SDK has returned the payload under .body in every version this app has
    been built against, but it also supports direct indexing, and a bare dict
    turns up in tests and in older releases. Take whichever is there rather
    than pinning the app's correctness to one SDK minor version.
    """
    if response is None:
        return None
    body = getattr(response, "body", None)
    return response if body is None else body


# ── Registration and the connection portal ────────────────────────────────────

def _stored_secret(user_id: int) -> str:
    from database import get_user_setting
    return get_user_setting(user_id, _SETTING_SECRET) or ""


def register_user(user_id: int = 1, *, force: bool = False) -> str:
    """Return this user's SnapTrade secret, registering them if needed.

    Registration is idempotent from this app's side: an existing secret is
    reused, because re-registering issues a new secret and silently strands
    every brokerage the old one had linked.
    """
    existing = _stored_secret(user_id)
    if existing and not force:
        return existing

    from database import set_user_setting

    client = _client()
    body = _body(client.authentication.register_snap_trade_user(
        user_id=snaptrade_user_id(user_id)
    ))
    secret = (body or {}).get("userSecret") or ""
    if not secret:
        raise RuntimeError("SnapTrade registration returned no userSecret")

    set_user_setting(user_id, _SETTING_SECRET, secret)
    logger.info("snaptrade user registered user_id=%s", user_id)
    return secret


def build_portal_url(user_id: int = 1, *, redirect_to: str | None = None,
                     broker: str | None = None) -> str:
    """A one-time URL for SnapTrade's Connection Portal.

    `broker` sends the user straight to one brokerage's login instead of the
    picker — 'ROBINHOOD' for Robinhood. `redirect_to` is where the portal
    returns the browser afterwards.
    """
    secret = register_user(user_id)
    client = _client()

    kwargs = {
        "user_id": snaptrade_user_id(user_id),
        "user_secret": secret,
    }
    if broker:
        kwargs["broker"] = broker
    if redirect_to:
        kwargs["custom_redirect"] = redirect_to

    body = _body(client.authentication.login_snap_trade_user(**kwargs))
    url = (body or {}).get("redirectURI") or ""
    if not url:
        # A user whose brokerage connection is already established still gets a
        # portal URL, so an empty one means the call itself did not do what it
        # was asked, not that there is nothing to connect.
        raise RuntimeError("SnapTrade did not return a connection portal URL")
    return url


def clear_tokens(user_id: int = 1) -> None:
    """Disconnect: delete the SnapTrade user, then forget the secret.

    Deleting the user revokes every brokerage connection it holds. The local
    secret is cleared even when the remote delete fails, because leaving a
    secret behind that the user believes is gone is the worse failure — the
    orphaned SnapTrade user can be removed from the dashboard.
    """
    from database import set_user_setting

    if _stored_secret(user_id):
        try:
            _client().authentication.delete_snap_trade_user(
                user_id=snaptrade_user_id(user_id)
            )
        except Exception as exc:
            logger.warning("snaptrade delete_user failed user_id=%s: %s", user_id, exc)

    set_user_setting(user_id, _SETTING_SECRET, "")
    set_user_setting(user_id, _SETTING_LINKED, "")
    logger.info("snaptrade disconnected user_id=%s", user_id)


def is_connected(user_id: int = 1) -> bool:
    return bool(is_configured() and _stored_secret(user_id))


def token_status(user_id: int = 1) -> dict:
    """Mirror of schwab.token_status() so one template renders either broker.

    Schwab's version returns connected/status/css; those three keys carry the
    same meaning here. `configured` is the extra one this broker needs, because
    SnapTrade can be un-set-up in a way Schwab cannot — the app ships with the
    integration but no keys, and "not connected" and "no keys yet" want
    different words on the page.

    There is no countdown to report: SnapTrade's user secret does not expire,
    so unlike Schwab there is no session to re-authenticate on a timer.
    """
    configured = is_configured()
    if not configured:
        return {"configured": False, "connected": False,
                "status": "Not set up", "css": "schwab-disconnected"}
    if not _stored_secret(user_id):
        return {"configured": True, "connected": False,
                "status": "Not connected", "css": "schwab-disconnected"}
    return {"configured": True, "connected": True,
            "status": "Connected (no expiry)", "css": "schwab-connected"}


# ── Reading accounts ──────────────────────────────────────────────────────────

def _num(value, default=None):
    """Coerce to float, treating anything unparseable as absent.

    Aggregated data is ragged by nature — a field one brokerage always sends is
    null at another — so this returns None rather than 0.0 by default. A zero
    that means "no data" is a lie the P&L columns would repeat.
    """
    if value is None or value == "":
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default          # NaN is not a number here


def _ticker(symbol_field) -> tuple[str, str]:
    """Pull (ticker, description) out of SnapTrade's nested symbol object.

    A position's symbol arrives as {"symbol": {"symbol": "AAPL", ...}} — a
    wrapper around a universal symbol around the string. Brokerages that
    report only a raw ticker skip a level, so walk down rather than index.
    """
    node = symbol_field
    description = ""
    for _ in range(4):
        if isinstance(node, str):
            return node.strip().upper(), description
        if not isinstance(node, dict):
            return "", description
        description = node.get("description") or description
        if isinstance(node.get("symbol"), (str, dict)):
            node = node["symbol"]
            continue
        raw = node.get("raw_symbol") or node.get("ticker")
        return (str(raw).strip().upper() if raw else ""), description
    return "", description


def _option_leg(raw: dict) -> dict:
    """Flatten one option position into the shape the positions table renders."""
    sym = raw.get("symbol") or {}
    opt = sym.get("option_symbol") if isinstance(sym, dict) else None
    opt = opt if isinstance(opt, dict) else (sym if isinstance(sym, dict) else {})

    underlying, _ = _ticker(opt.get("underlying_symbol"))
    ticker = str(opt.get("ticker") or "").strip().upper() or underlying
    qty = _num(raw.get("units"), 0.0) or 0.0
    avg = _num(raw.get("average_purchase_price"), 0.0) or 0.0
    price = _num(raw.get("price"), 0.0) or 0.0

    # SnapTrade quotes options per share; a contract is a hundred of them.
    multiplier = 100.0
    units = abs(qty) * multiplier
    cost_basis = abs(avg) * units
    market_value = price * qty * multiplier
    unrealized = _num(raw.get("open_pnl"))
    if unrealized is None:
        unrealized = market_value - (cost_basis if qty > 0 else -cost_basis)

    return {
        "symbol":         ticker,
        "description":    opt.get("description") or "",
        "asset_type":     "OPTION",
        "quantity":       qty,
        "avg_price":      round(avg, 4),
        "market_value":   round(market_value, 2),
        "day_pnl":        None,
        "day_pnl_pct":    None,
        "unrealized":     round(unrealized, 2),
        "unrealized_pct": round(unrealized / cost_basis * 100, 2) if cost_basis else 0.0,
        "cost_basis":     round(cost_basis, 2),
        "last_price":     round(price, 4),
        "multiplier":     multiplier,
        "option_type":    (opt.get("option_type") or "").upper(),
        "strike_price":   _num(opt.get("strike_price"), 0.0),
        "expiration_date": opt.get("expiration_date") or "",
        "underlying":     underlying or ticker,
        "contracts":      abs(qty),
    }


def _equity_leg(raw: dict) -> dict | None:
    ticker, description = _ticker(raw.get("symbol"))
    qty = _num(raw.get("units"), 0.0) or 0.0
    if not ticker or qty == 0:
        return None                                # closed rows are not holdings

    avg = _num(raw.get("average_purchase_price"), 0.0) or 0.0
    price = _num(raw.get("price"), 0.0) or 0.0
    cost_basis = abs(avg) * abs(qty)
    market_value = price * qty
    unrealized = _num(raw.get("open_pnl"))
    if unrealized is None:
        unrealized = market_value - (avg * qty)

    return {
        "symbol":         ticker,
        "description":    description,
        "asset_type":     "EQUITY",
        "quantity":       qty,
        "avg_price":      round(avg, 4),
        "market_value":   round(market_value, 2),
        # SnapTrade reports no start-of-day mark, so a day P&L would have to be
        # invented. None renders as an em dash; a zero would read as "flat".
        "day_pnl":        None,
        "day_pnl_pct":    None,
        "unrealized":     round(unrealized, 2),
        "unrealized_pct": round(unrealized / cost_basis * 100, 2) if cost_basis else 0.0,
        "cost_basis":     round(cost_basis, 2),
        "last_price":     round(price, 4),
        "multiplier":     1.0,
    }


def _normalize_account(account: dict, holdings: dict) -> dict:
    """Flatten one SnapTrade account into schwab.py's account shape."""
    account = account if isinstance(account, dict) else {}
    holdings = holdings if isinstance(holdings, dict) else {}

    equity_positions = []
    option_positions = []
    for raw in (holdings.get("positions") or []):
        if isinstance(raw, dict):
            leg = _equity_leg(raw)
            if leg:
                equity_positions.append(leg)
    for raw in (holdings.get("option_positions") or []):
        if isinstance(raw, dict) and (_num(raw.get("units"), 0.0) or 0.0) != 0:
            option_positions.append(_option_leg(raw))

    positions = equity_positions + option_positions
    total_unrealized = sum(p["unrealized"] for p in positions)

    balances = [b for b in (holdings.get("balances") or []) if isinstance(b, dict)]
    cash = sum(_num(b.get("cash"), 0.0) or 0.0 for b in balances)
    buying_power = sum(_num(b.get("buying_power"), 0.0) or 0.0 for b in balances)

    # Prefer the brokerage's own total; fall back to cash plus marks.
    balance_node = account.get("balance") or {}
    total_node = balance_node.get("total") if isinstance(balance_node, dict) else None
    total_value = _num((total_node or {}).get("amount")) if isinstance(total_node, dict) else None
    if total_value is None:
        total_value = cash + sum(p["market_value"] for p in positions)

    equity_positions.sort(key=lambda p: abs(p["market_value"]), reverse=True)
    option_positions.sort(key=lambda p: abs(p["market_value"]), reverse=True)
    positions.sort(key=lambda p: abs(p["market_value"]), reverse=True)

    # Which brokerage this is. Named on the account for most, and only on the
    # authorization object for some — this is the label the user reads, so it
    # is worth looking in both places rather than printing "Brokerage".
    auth = account.get("brokerage_authorization")
    auth = auth if isinstance(auth, dict) else {}
    brokerage = auth.get("brokerage")
    brokerage = brokerage if isinstance(brokerage, dict) else {}
    institution = (account.get("institution_name")
                   or brokerage.get("display_name")
                   or brokerage.get("name")
                   or auth.get("name")
                   or "Brokerage")

    meta = account.get("meta")
    account_type = str((meta or {}).get("type") if isinstance(meta, dict) else "")
    account_type = account_type or str(account.get("raw_type") or "")

    return {
        "account_number":    str(account.get("number") or ""),
        "account_hash":      str(account.get("id") or ""),
        "account_type":      account_type,
        "institution":       institution,
        "broker":            "snaptrade",
        "is_day_trader":     False,
        "total_value":       round(total_value, 2),
        "cash_balance":      round(cash, 2),
        "buying_power":      round(buying_power or cash, 2),
        "available_funds":   round(buying_power or cash, 2),
        "maintenance_req":   0.0,
        "day_trading_buying_power": 0.0,
        # No start-of-day value is available through the aggregator.
        "daily_pnl":         None,
        "daily_pnl_pct":     None,
        "total_unrealized":  round(total_unrealized, 2),
        "equity_positions":  equity_positions,
        "option_positions":  option_positions,
        "positions":         positions,
        "position_count":    len(positions),
    }


def fetch_accounts(user_id: int = 1) -> list[dict]:
    """Every linked account, normalized. Raises when the session is unusable."""
    secret = _stored_secret(user_id)
    if not secret:
        raise RuntimeError("No SnapTrade connection for this user")

    client = _client()
    sid = snaptrade_user_id(user_id)
    accounts = _body(client.account_information.list_user_accounts(
        user_id=sid, user_secret=secret
    )) or []

    out = []
    for account in accounts:
        if not isinstance(account, dict) or not account.get("id"):
            continue
        try:
            holdings = _body(client.account_information.get_user_holdings(
                account_id=account["id"], user_id=sid, user_secret=secret
            )) or {}
        except Exception as exc:
            # One brokerage being unreachable must not hide the others.
            logger.warning("snaptrade holdings failed account=%s: %s",
                           account.get("id"), exc)
            holdings = {}
        out.append(_normalize_account(account, holdings))
    return out


def get_account_summary(user_id: int = 1) -> dict:
    """Mirror of schwab.get_account_summary().

    Sums are taken over the values that exist. Day P&L stays None when no
    linked brokerage reports one, rather than collapsing to a confident zero.
    """
    if not is_connected(user_id):
        return _empty_summary(None)
    try:
        accounts = fetch_accounts(user_id=user_id)
        daily = [a["daily_pnl"] for a in accounts if a["daily_pnl"] is not None]
        return {
            "connected":        True,
            "total_value":      round(sum(a["total_value"] for a in accounts), 2),
            "buying_power":     round(sum(a["buying_power"] for a in accounts), 2),
            "daily_pnl":        round(sum(daily), 2) if daily else None,
            "total_unrealized": round(sum(a["total_unrealized"] for a in accounts), 2),
            "open_positions":   sum(a["position_count"] for a in accounts),
            "accounts":         accounts,
            "error":            None,
        }
    except Exception as exc:
        logger.warning("snaptrade get_account_summary failed: %s", exc)
        return _empty_summary(str(exc))


def _empty_summary(error: str | None) -> dict:
    return {
        "connected":        False,
        "total_value":      None,
        "buying_power":     None,
        "daily_pnl":        None,
        "total_unrealized": None,
        "open_positions":   0,
        "accounts":         [],
        "error":            error,
    }
