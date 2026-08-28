"""
schwab.py — Charles Schwab Trader API client  (Phase 1: read-only)

═══════════════════════════════════════════════════════════════════
PHASE 1 SCOPE — READ-ONLY ACCOUNT VISIBILITY
  ✓ OAuth 2.0 PKCE authorization flow
  ✓ Token storage in database (encrypted at rest via DB)
  ✓ Account balances + buying power
  ✓ Open equity + option positions
  ✓ Today's orders and status
  ✓ Daily P&L
  ✗ Order placement (Phase 2)
  ✗ Order cancellation (Phase 2)
  ✗ Any write operations (Phase 2+)
═══════════════════════════════════════════════════════════════════

SETUP INSTRUCTIONS
──────────────────
1. Register an app at https://developer.schwab.com
2. Set callback URL to:  https://<your-domain>/schwab/callback
   (or http://localhost:5000/schwab/callback for local dev)
3. Copy your App Key and App Secret
4. Set environment variables:

   SCHWAB_CLIENT_ID      = <your App Key>
   SCHWAB_CLIENT_SECRET  = <your App Secret>
   SCHWAB_REDIRECT_URI   = https://<your-domain>/schwab/callback

Never hardcode credentials in this file or commit them to git.

OAUTH FLOW
──────────
  GET /schwab/auth
    → redirect to Schwab authorize URL (stores PKCE state in session)

  GET /schwab/callback?code=...&state=...
    → exchange code for access_token + refresh_token
    → store tokens in schwab_tokens DB table
    → redirect to /schwab/account

  Tokens:
    access_token  — expires in 30 minutes (auto-refreshed before expiry / on 401)
    refresh_token — expires in 7 days (user must re-auth after expiry)
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# ── API constants ─────────────────────────────────────────────────────────────
_BASE_AUTH    = "https://api.schwabapi.com/v1/oauth"
_BASE_TRADER  = "https://api.schwabapi.com/trader/v1"
_TOKEN_URL    = f"{_BASE_AUTH}/token"
_AUTH_URL     = f"{_BASE_AUTH}/authorize"

# Access token TTL from Schwab is 1800 s (30 min).
# Refresh 2 minutes early so page/API requests do not race the token boundary.
_ACCESS_TTL    = 1800
_REFRESH_EARLY = 120

# Prevent multiple simultaneous requests from refreshing the same token at once.
# This matters in production where Home, Account, and background requests can hit
# Schwab nearly simultaneously when an access token reaches its expiry window.
_REFRESH_LOCK = threading.Lock()

# Read-only scopes required for Phase 1
_SCOPES = "readonly"


def _client_id() -> str:
    v = os.environ.get("SCHWAB_CLIENT_ID", "")
    if not v:
        raise RuntimeError("SCHWAB_CLIENT_ID env var is not set")
    return v


def _client_secret() -> str:
    v = os.environ.get("SCHWAB_CLIENT_SECRET", "")
    if not v:
        raise RuntimeError("SCHWAB_CLIENT_SECRET env var is not set")
    return v


def _redirect_uri() -> str:
    return os.environ.get(
        "SCHWAB_REDIRECT_URI",
        "http://localhost:5000/schwab/callback",
    )


def is_configured() -> bool:
    """Return True if required env vars are present."""
    return bool(
        os.environ.get("SCHWAB_CLIENT_ID") and
        os.environ.get("SCHWAB_CLIENT_SECRET")
    )


# ── PKCE helpers ──────────────────────────────────────────────────────────────

def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier  = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


# ── OAuth URL builders ────────────────────────────────────────────────────────

def build_auth_url(state: str, code_challenge: str) -> str:
    """Build the Schwab OAuth authorization URL."""
    params = {
        "response_type":         "code",
        "client_id":             _client_id(),
        "redirect_uri":          _redirect_uri(),
        "scope":                 _SCOPES,
        "state":                 state,
        "code_challenge":        code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{_AUTH_URL}?{urlencode(params)}"


# ── Token exchange ────────────────────────────────────────────────────────────

def exchange_code_for_tokens(code: str, code_verifier: str) -> dict:
    """
    Exchange an authorization code for access + refresh tokens.
    Returns the full token response dict from Schwab.
    Raises RuntimeError on failure.
    """
    import urllib.request
    import urllib.error

    credentials = base64.b64encode(
        f"{_client_id()}:{_client_secret()}".encode()
    ).decode()

    payload = urlencode({
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  _redirect_uri(),
        "code_verifier": code_verifier,
    }).encode()

    req = urllib.request.Request(
        _TOKEN_URL,
        data=payload,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"Token exchange failed {e.code}: {body}") from e


def refresh_access_token(refresh_token: str) -> dict:
    """
    Use a refresh_token to obtain a new access_token.
    Returns the new token response dict.
    Raises RuntimeError on failure.
    """
    import urllib.request
    import urllib.error

    credentials = base64.b64encode(
        f"{_client_id()}:{_client_secret()}".encode()
    ).decode()

    payload = urlencode({
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
    }).encode()

    req = urllib.request.Request(
        _TOKEN_URL,
        data=payload,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"Token refresh failed {e.code}: {body}") from e


# ── Token storage (per-user, stored in user_settings table) ──────────────────

def _safe_int(value, default: int = 0) -> int:
    """Parse persisted numeric token metadata without letting bad data crash auth."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    """Normalize optional Schwab numeric fields without dropping the account."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def save_tokens(token_response: dict, user_id: int = 1, *, is_refresh: bool = False) -> None:
    """
    Persist Schwab tokens for the given user.

    Critical refresh behavior:
    Schwab refresh responses are not guaranteed to return a refresh_token every
    time. During a refresh we therefore preserve the currently stored refresh
    token instead of replacing it with an empty string. We also preserve the
    original refresh-token expiry unless Schwab explicitly returns a refresh
    token expiry value.
    """
    from database import get_user_setting, set_user_setting

    access_token = token_response.get("access_token")
    if not access_token:
        raise RuntimeError("Schwab token response did not include access_token")

    now = int(time.time())
    expires_at = now + _safe_int(token_response.get("expires_in"), _ACCESS_TTL)

    old_refresh = get_user_setting(user_id, "schwab_refresh_token") or ""
    old_rt_exp  = _safe_int(get_user_setting(user_id, "schwab_rt_expires_at"), 0)
    new_refresh = token_response.get("refresh_token") or ""

    # Initial authorization must establish a refresh token. Refresh operations
    # keep the old one if Schwab only sends a replacement access token.
    refresh_token = new_refresh or (old_refresh if is_refresh else "")

    set_user_setting(user_id, "schwab_access_token", access_token)
    set_user_setting(user_id, "schwab_refresh_token", refresh_token)
    set_user_setting(user_id, "schwab_expires_at", str(expires_at))

    explicit_rt_ttl = _safe_int(
        token_response.get("refresh_token_expires_in")
        or token_response.get("refresh_expires_in"),
        0,
    )
    if explicit_rt_ttl > 0:
        rt_expires = now + explicit_rt_ttl
    elif is_refresh and old_rt_exp > 0:
        # Do not accidentally extend Schwab's absolute refresh-token lifetime.
        rt_expires = old_rt_exp
    else:
        rt_expires = now + 7 * 86400

    set_user_setting(user_id, "schwab_rt_expires_at", str(rt_expires))
    logger.info(
        "schwab tokens saved user_id=%s access_expires_at=%s refresh_expires_at=%s refresh_preserved=%s",
        user_id,
        expires_at,
        rt_expires,
        bool(is_refresh and not new_refresh and old_refresh),
    )


def clear_tokens(user_id: int = 1) -> None:
    """Remove stored Schwab tokens for a user (disconnect)."""
    from database import set_user_setting
    for key in ("schwab_access_token", "schwab_refresh_token",
                "schwab_expires_at",   "schwab_rt_expires_at"):
        set_user_setting(user_id, key, "")
    logger.info("schwab tokens cleared user_id=%s", user_id)


def _read_stored_tokens(user_id: int) -> dict:
    """Read the current token snapshot from persistent storage."""
    from database import get_user_setting
    return {
        "access_token":  get_user_setting(user_id, "schwab_access_token") or "",
        "refresh_token": get_user_setting(user_id, "schwab_refresh_token") or "",
        "expires_at":    _safe_int(get_user_setting(user_id, "schwab_expires_at"), 0),
        "rt_expires_at": _safe_int(get_user_setting(user_id, "schwab_rt_expires_at"), 0),
    }


def load_tokens(user_id: int = 1) -> dict | None:
    """
    Load tokens from user_settings and proactively refresh an expiring access token.
    Returns a usable token dict, or None when the Schwab session truly requires
    re-authentication.
    """
    stored = _read_stored_tokens(user_id)
    access_token  = stored["access_token"]
    refresh_token = stored["refresh_token"]
    expires_at    = stored["expires_at"]
    rt_expires_at = stored["rt_expires_at"]

    if not access_token and not refresh_token:
        return None

    now = int(time.time())

    # Refresh token expired — user must re-authenticate.
    if refresh_token and rt_expires_at and rt_expires_at <= now:
        logger.warning("schwab refresh_token expired user_id=%s — must re-auth", user_id)
        return None

    # No usable access token and nothing that can refresh it.
    if not access_token and not refresh_token:
        return None

    # Access token expiring soon — refresh it now. The lock stops simultaneous
    # Home/Account/API requests from racing and invalidating one another.
    if expires_at - now < _REFRESH_EARLY and refresh_token:
        with _REFRESH_LOCK:
            # Another request may already have refreshed while we waited.
            stored = _read_stored_tokens(user_id)
            access_token  = stored["access_token"]
            refresh_token = stored["refresh_token"]
            expires_at    = stored["expires_at"]
            rt_expires_at = stored["rt_expires_at"]
            now = int(time.time())

            if refresh_token and rt_expires_at and rt_expires_at <= now:
                return None

            if expires_at - now < _REFRESH_EARLY and refresh_token:
                try:
                    logger.info("schwab refreshing access_token user_id=%s", user_id)
                    new_tokens = refresh_access_token(refresh_token)
                    save_tokens(new_tokens, user_id=user_id, is_refresh=True)
                    refreshed = _read_stored_tokens(user_id)
                    return {
                        "access_token":  refreshed["access_token"],
                        "refresh_token": refreshed["refresh_token"],
                        "expires_at":    refreshed["expires_at"],
                    }
                except Exception as e:
                    logger.error("schwab token refresh failed user_id=%s: %s", user_id, e)
                    # If the existing access token still has time left, allow the
                    # request to use it. If it is already expired, report auth as
                    # unavailable rather than repeatedly sending a dead token.
                    if access_token and expires_at > now:
                        return {
                            "access_token":  access_token,
                            "refresh_token": refresh_token,
                            "expires_at":    expires_at,
                        }
                    return None

    if not access_token or expires_at <= now:
        # An expired access token with no successful refresh is not usable.
        return None

    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "expires_at":    expires_at,
    }


def is_connected(user_id: int = 1) -> bool:
    """Return True if the user currently has a usable Schwab session."""
    return load_tokens(user_id) is not None


def token_status(user_id: int = 1) -> dict:
    """
    Return connection status from persisted token metadata without exposing
    secrets. An expired access token is still considered connected while a valid
    refresh token exists because the next authenticated request can refresh it.
    """
    stored = _read_stored_tokens(user_id)
    access_token  = stored["access_token"]
    refresh_token = stored["refresh_token"]
    expires_at    = stored["expires_at"]
    rt_expires_at = stored["rt_expires_at"]
    now           = int(time.time())

    if not access_token and not refresh_token:
        return {"connected": False, "status": "Not connected", "css": "schwab-disconnected"}

    if refresh_token and rt_expires_at and rt_expires_at <= now:
        return {"connected": False, "status": "Session expired — re-authenticate", "css": "schwab-expired"}

    if expires_at > now:
        mins = max(0, (expires_at - now) // 60)
        return {"connected": True, "status": f"Connected (token valid ~{mins}m)", "css": "schwab-connected"}

    if refresh_token:
        return {"connected": True, "status": "Connected (refresh ready)", "css": "schwab-connected"}

    return {"connected": False, "status": "Token expired", "css": "schwab-expired"}


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _get(path: str, params: dict = None, *, base: str = _BASE_TRADER,
         user_id: int = 1) -> dict | list:
    """
    Authenticated GET against Schwab API.
    Handles proactive refresh and a one-shot refresh on 401 automatically.
    Read-only — this module never issues POST/PUT/DELETE.
    """
    import urllib.request
    import urllib.error
    from urllib.parse import urlencode

    tokens = load_tokens(user_id)
    if not tokens:
        raise RuntimeError("Not authenticated with Schwab — visit /schwab/auth")

    url = f"{base}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"

    def _do_request(token: str):
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept":        "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())

    try:
        return _do_request(tokens["access_token"])
    except urllib.error.HTTPError as e:
        if e.code == 401 and tokens.get("refresh_token"):
            # One-shot refresh and retry. Preserve the correct user's refresh
            # token instead of writing the retry result into user_id=1.
            try:
                with _REFRESH_LOCK:
                    latest = _read_stored_tokens(user_id)
                    refresh_token = latest.get("refresh_token") or tokens["refresh_token"]
                    new_tok = refresh_access_token(refresh_token)
                    save_tokens(new_tok, user_id=user_id, is_refresh=True)
                    refreshed = _read_stored_tokens(user_id)
                return _do_request(refreshed["access_token"])
            except Exception as refresh_err:
                raise RuntimeError(f"Schwab auth failed after refresh: {refresh_err}") from refresh_err
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"Schwab API error {e.code}: {body}") from e


# ── Read-only data fetchers ───────────────────────────────────────────────────

def fetch_accounts(user_id: int = 1) -> list[dict]:
    """
    Fetch all linked accounts with positions and balances.
    Returns a list of normalized account dicts.
    """
    raw = _get("/accounts", {"fields": "positions"}, user_id=user_id)
    if not isinstance(raw, list):
        raw = [raw]
    return [_normalize_account(a) for a in raw]


def fetch_orders(account_hash: str, *, days_back: int = 1, user_id: int = 1) -> list[dict]:
    """
    Fetch recent orders for an account.
    days_back=1 returns today's orders; increase for history.
    Phase 1: read-only display only.
    """
    from_dt = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00Z")
    to_dt   = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%dT23:59:59Z")
    raw = _get(
        f"/accounts/{account_hash}/orders",
        {"fromEnteredTime": from_dt, "toEnteredTime": to_dt, "maxResults": 50},
        user_id=user_id,
    )
    if not isinstance(raw, list):
        raw = []
    return [_normalize_order(o) for o in raw]


# ── Data normalizers ──────────────────────────────────────────────────────────

def _normalize_account(raw: dict) -> dict:
    """
    Flatten a raw Schwab account object into a clean dict.
    Separates equity positions from option positions.
    """
    acct = raw.get("securitiesAccount", raw)

    bal = acct.get("currentBalances", {})
    init_bal = acct.get("initialBalances", {})

    # Daily P&L = current liquidation value minus start-of-day value
    current_value  = bal.get("liquidationValue") or bal.get("totalValue") or 0.0
    open_value     = init_bal.get("liquidationValue") or init_bal.get("totalValue") or 0.0
    daily_pnl      = round(current_value - open_value, 2) if open_value else None
    daily_pnl_pct  = round(daily_pnl / open_value * 100, 2) if open_value and daily_pnl is not None else None

    # Keep the legacy asset-specific lists for the dedicated Schwab page, and a
    # canonical list for consumers that render all open positions together.
    positions_raw = acct.get("positions") or []
    positions = []
    equity_positions = []
    option_positions = []

    total_unrealized = 0.0

    for pos in positions_raw:
        if not isinstance(pos, dict):
            continue
        instrument = pos.get("instrument") or {}
        if not isinstance(instrument, dict):
            continue
        asset_type = str(instrument.get("assetType") or "EQUITY").upper()
        symbol = str(instrument.get("symbol") or "").strip().upper()
        long_qty = _safe_float(pos.get("longQuantity"))
        short_qty = _safe_float(pos.get("shortQuantity"))
        qty = long_qty - short_qty
        # Schwab can retain zero-quantity position records after a close. They
        # are not open positions and must not affect the count or table.
        if not symbol or qty == 0:
            continue

        if qty > 0:
            avg_price = _safe_float(
                pos.get("averagePrice")
                if pos.get("averagePrice") is not None
                else pos.get("averageLongPrice")
            )
            unrealized = _safe_float(pos.get("longOpenProfitLoss"))
        else:
            avg_price = _safe_float(
                pos.get("averagePrice")
                if pos.get("averagePrice") is not None
                else pos.get("averageShortPrice")
            )
            unrealized = _safe_float(pos.get("shortOpenProfitLoss"))

        mkt_val = _safe_float(pos.get("marketValue"))
        day_pnl = _safe_float(pos.get("currentDayProfitLoss"))
        day_pnl_pct = _safe_float(pos.get("currentDayProfitLossPercentage"))
        multiplier = _safe_float(instrument.get("multiplier"), 100.0) if asset_type == "OPTION" else 1.0
        if multiplier <= 0:
            multiplier = 100.0 if asset_type == "OPTION" else 1.0
        units = abs(qty) * multiplier
        cost_basis = abs(avg_price) * units
        last_price = abs(mkt_val) / units if units else 0.0
        unrealized_pct = unrealized / cost_basis * 100 if cost_basis else 0.0
        total_unrealized += unrealized

        norm = {
            "symbol":      symbol,
            "description": instrument.get("description", ""),
            "asset_type":  asset_type,
            "quantity":    qty,
            "avg_price":   round(avg_price, 4),
            "market_value":round(mkt_val, 2),
            "day_pnl":     round(day_pnl, 2),
            "day_pnl_pct": round(day_pnl_pct, 2),
            "unrealized":  round(unrealized, 2),
            "unrealized_pct": round(unrealized_pct, 2),
            "cost_basis": round(cost_basis, 2),
            "last_price": round(last_price, 4),
            "multiplier": multiplier,
        }

        if asset_type == "OPTION":
            # Parse option description for display: e.g. "AAPL 150C 2026-06-20"
            norm["option_type"]     = instrument.get("putCall", "")
            norm["strike_price"]    = instrument.get("strikePrice") or 0.0
            norm["expiration_date"] = instrument.get("expirationDate", "")
            norm["underlying"]      = instrument.get("underlyingSymbol", symbol[:4])
            norm["contracts"]       = abs(qty)
            option_positions.append(norm)
        else:
            equity_positions.append(norm)
        positions.append(norm)

    # Sort by market value desc
    equity_positions.sort(key=lambda p: abs(p["market_value"]), reverse=True)
    option_positions.sort(key=lambda p: abs(p["market_value"]), reverse=True)
    positions.sort(key=lambda p: abs(p["market_value"]), reverse=True)

    return {
        "account_number":    acct.get("accountNumber", ""),
        "account_hash":      raw.get("hashValue") or acct.get("accountNumber", ""),
        "account_type":      acct.get("type", ""),
        "is_day_trader":     acct.get("isDayTrader", False),
        # Balances
        "total_value":       round(current_value, 2),
        "cash_balance":      round(bal.get("cashBalance") or 0.0, 2),
        "buying_power":      round(
            bal.get("buyingPowerNonMarginableTrade")
            or bal.get("availableFunds")
            or bal.get("buyingPower")
            or 0.0, 2
        ),
        "available_funds":   round(bal.get("availableFunds") or 0.0, 2),
        "maintenance_req":   round(bal.get("maintenanceRequirement") or 0.0, 2),
        "day_trading_buying_power": round(
            bal.get("dayTradingBuyingPower") or 0.0, 2
        ),
        # P&L
        "daily_pnl":         daily_pnl,
        "daily_pnl_pct":     daily_pnl_pct,
        "total_unrealized":  round(total_unrealized, 2),
        # Positions
        "equity_positions":  equity_positions,
        "option_positions":  option_positions,
        "positions":         positions,
        "position_count":    len(positions),
    }


def _normalize_order(raw: dict) -> dict:
    """Flatten a raw Schwab order into a display-ready dict."""
    legs = raw.get("orderLegCollection", [])
    symbol = ""
    side   = ""
    if legs:
        first = legs[0]
        symbol = (first.get("instrument") or {}).get("symbol", "")
        side   = first.get("instruction", "")

    qty      = raw.get("quantity") or raw.get("filledQuantity") or 0
    filled   = raw.get("filledQuantity") or 0
    price    = raw.get("price") or raw.get("stopPrice") or 0.0
    status   = raw.get("status", "UNKNOWN")
    entered  = raw.get("enteredTime", "")[:16].replace("T", " ")
    order_id = raw.get("orderId", "")

    _status_css = {
        "FILLED":         "order-filled",
        "WORKING":        "order-working",
        "PENDING_ACTIVATION": "order-pending",
        "QUEUED":         "order-pending",
        "ACCEPTED":       "order-pending",
        "REJECTED":       "order-rejected",
        "CANCELED":       "order-canceled",
        "EXPIRED":        "order-canceled",
        "REPLACED":       "order-canceled",
    }

    return {
        "order_id":   order_id,
        "symbol":     symbol,
        "side":       side,
        "quantity":   qty,
        "filled":     filled,
        "price":      round(float(price), 4) if price else None,
        "status":     status,
        "status_css": _status_css.get(status, "order-unknown"),
        "entered":    entered,
        "order_type": raw.get("orderType", ""),
        "duration":   raw.get("duration", ""),
        "session":    raw.get("session", ""),
    }


# ── Aggregate summary for risk integration ───────────────────────────────────

def get_account_summary(user_id: int = 1) -> dict:
    """
    Fetch all accounts and return a single summary dict.
    Used by the risk engine to override account_size / buying_power.

    Returns:
      {
        "connected":        bool,
        "total_value":      float,   # total portfolio value
        "buying_power":     float,   # available buying power
        "daily_pnl":        float,   # today's realized + unrealized P&L
        "total_unrealized": float,   # open position P&L
        "open_positions":   int,     # number of open positions
        "accounts":         list,    # full normalized account list
        "error":            str|None
      }
    """
    try:
        accounts = fetch_accounts(user_id=user_id)
        total_value   = sum(a["total_value"]       for a in accounts)
        buying_power  = sum(a["buying_power"]      for a in accounts)
        daily_pnl     = sum(a["daily_pnl"] or 0    for a in accounts)
        unrealized    = sum(a["total_unrealized"]  for a in accounts)
        positions     = sum(a["position_count"]    for a in accounts)
        return {
            "connected":        True,
            "total_value":      round(total_value, 2),
            "buying_power":     round(buying_power, 2),
            "daily_pnl":        round(daily_pnl, 2),
            "total_unrealized": round(unrealized, 2),
            "open_positions":   positions,
            "accounts":         accounts,
            "error":            None,
        }
    except Exception as e:
        logger.warning("schwab get_account_summary failed: %s", e)
        return {
            "connected":        False,
            "total_value":      None,
            "buying_power":     None,
            "daily_pnl":        None,
            "total_unrealized": None,
            "open_positions":   0,
            "accounts":         [],
            "error":            str(e),
        }


# ── Trade pair matching ───────────────────────────────────────────────────────

def match_schwab_trades(days_back: int = 30, user_id: int = 1) -> list[dict]:
    """
    Fetch filled orders from Schwab and match BUY + SELL pairs into completed trades.

    Returns a list of trade dicts ready for journal preview:
      ticker, trade_date, direction, entry_price, exit_price, shares,
      pnl_pct, result, import_key, buy_order_id, sell_order_id, already_imported

    Only FILLED equity orders are considered. Options and partial fills
    are skipped to keep the first version simple and safe.

    import_key = "{ticker}:{buy_order_id}:{sell_order_id}" — used for dedup.
    """
    from database import get_schwab_import_keys
    already_imported = get_schwab_import_keys(user_id=user_id)

    try:
        accounts = fetch_accounts(user_id=user_id)
    except Exception as e:
        logger.warning("match_schwab_trades: fetch_accounts failed: %s", e)
        return []

    trades: list[dict] = []

    for acct in accounts:
        account_hash = acct.get("account_hash") or acct.get("hashValue", "")
        if not account_hash:
            continue
        try:
            raw_orders = _get(
                f"/accounts/{account_hash}/orders",
                {
                    "fromEnteredTime": (
                        datetime.utcnow() - timedelta(days=days_back)
                    ).strftime("%Y-%m-%dT00:00:00Z"),
                    "toEnteredTime": (
                        datetime.utcnow() + timedelta(days=1)
                    ).strftime("%Y-%m-%dT23:59:59Z"),
                    "maxResults": 200,
                    "status": "FILLED",
                },
                user_id=user_id,
            )
        except Exception as e:
            logger.warning("match_schwab_trades: fetch orders failed: %s", e)
            continue

        if not isinstance(raw_orders, list):
            continue

        # Group filled orders by ticker
        buys:  dict[str, list] = {}
        sells: dict[str, list] = {}

        for raw in raw_orders:
            if raw.get("status") != "FILLED":
                continue
            legs = raw.get("orderLegCollection") or []
            if not legs:
                continue
            leg       = legs[0]
            instrument= leg.get("instrument") or {}
            asset_type= instrument.get("assetType", "EQUITY")
            if asset_type != "EQUITY":
                continue   # skip options for now

            ticker     = instrument.get("symbol", "")
            instruction= leg.get("instruction", "").upper()  # BUY / SELL / BUY_TO_COVER / SELL_SHORT
            filled_qty = float(raw.get("filledQuantity") or 0)
            avg_price  = float(raw.get("orderActivityCollection", [{}])[0]
                               .get("executionLegs", [{}])[0]
                               .get("price") or raw.get("price") or 0)
            order_id   = str(raw.get("orderId", ""))
            entered    = (raw.get("closeTime") or raw.get("enteredTime") or "")[:10]

            if not ticker or filled_qty <= 0 or avg_price <= 0:
                continue

            item = {
                "ticker":    ticker,
                "qty":       filled_qty,
                "price":     avg_price,
                "order_id":  order_id,
                "date":      entered,
            }

            if "BUY" in instruction:
                buys.setdefault(ticker, []).append(item)
            elif "SELL" in instruction:
                sells.setdefault(ticker, []).append(item)

        # Match buys → sells (FIFO per ticker)
        for ticker in set(buys) & set(sells):
            buy_list  = sorted(buys[ticker],  key=lambda x: x["date"])
            sell_list = sorted(sells[ticker], key=lambda x: x["date"])

            for buy in buy_list:
                for sell in sell_list:
                    if sell["date"] < buy["date"]:
                        continue  # sell must be after buy
                    shares    = min(buy["qty"], sell["qty"])
                    entry     = round(buy["price"], 4)
                    exit_p    = round(sell["price"], 4)
                    pnl_pct   = round((exit_p - entry) / entry * 100, 2) if entry else 0.0
                    result    = "Win" if pnl_pct > 0 else ("Loss" if pnl_pct < 0 else "Break Even")
                    key       = f"{ticker}:{buy['order_id']}:{sell['order_id']}"

                    trades.append({
                        "ticker":           ticker,
                        "trade_date":       sell["date"] or buy["date"],
                        "direction":        "Long",
                        "entry_price":      entry,
                        "exit_price":       exit_p,
                        "shares":           int(shares),
                        "pnl_pct":          pnl_pct,
                        "result":           result,
                        "import_key":       key,
                        "buy_order_id":     buy["order_id"],
                        "sell_order_id":    sell["order_id"],
                        "already_imported": key in already_imported,
                    })
                    break  # one match per buy order

    # Sort: unimported first, then newest first within each group.
    # not already_imported = True for unimported → sorts first with reverse=True.
    trades.sort(key=lambda t: (not t["already_imported"], t["trade_date"]), reverse=True)
    return trades
