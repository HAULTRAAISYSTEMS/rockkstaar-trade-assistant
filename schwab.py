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
    access_token  — expires in 30 minutes (auto-refreshed on 401)
    refresh_token — expires in 7 days (user must re-auth after expiry)
"""

import base64
import hashlib
import json
import logging
import os
import secrets
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
# Refresh 60 s early to avoid edge-case expiry mid-request.
_ACCESS_TTL   = 1800
_REFRESH_EARLY = 60

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


# ── Token storage (uses database get_setting / set_setting) ──────────────────

def save_tokens(token_response: dict) -> None:
    """Persist tokens in the database settings table."""
    from database import set_setting
    now = int(time.time())
    expires_at = now + int(token_response.get("expires_in", _ACCESS_TTL))
    set_setting("schwab_access_token",  token_response["access_token"])
    set_setting("schwab_refresh_token", token_response.get("refresh_token", ""))
    set_setting("schwab_expires_at",    str(expires_at))
    # Refresh token expires in 7 days from Schwab; store absolute epoch
    rt_expires = now + 7 * 86400
    set_setting("schwab_rt_expires_at", str(rt_expires))
    logger.info("schwab  tokens saved  expires_at=%s", expires_at)


def clear_tokens() -> None:
    """Remove stored Schwab tokens (disconnect)."""
    from database import set_setting
    for key in ("schwab_access_token", "schwab_refresh_token",
                "schwab_expires_at",   "schwab_rt_expires_at"):
        set_setting(key, "")
    logger.info("schwab  tokens cleared")


def load_tokens() -> dict | None:
    """
    Load tokens from DB, auto-refresh if access_token is expiring.
    Returns dict with access_token, or None if not connected.
    """
    from database import get_setting
    access_token  = get_setting("schwab_access_token")  or ""
    refresh_token = get_setting("schwab_refresh_token") or ""
    expires_at    = get_setting("schwab_expires_at")    or "0"
    rt_expires_at = get_setting("schwab_rt_expires_at") or "0"

    if not access_token and not refresh_token:
        return None

    now = int(time.time())

    # Refresh token expired — user must re-authenticate
    if refresh_token and int(rt_expires_at) < now:
        logger.warning("schwab  refresh_token expired — user must re-auth")
        return None

    # Access token expiring soon — refresh it now
    if int(expires_at) - now < _REFRESH_EARLY and refresh_token:
        try:
            logger.info("schwab  refreshing access_token")
            new_tokens = refresh_access_token(refresh_token)
            save_tokens(new_tokens)
            return {
                "access_token":  new_tokens["access_token"],
                "refresh_token": new_tokens.get("refresh_token", refresh_token),
                "expires_at":    int(time.time()) + int(new_tokens.get("expires_in", _ACCESS_TTL)),
            }
        except Exception as e:
            logger.error("schwab  token refresh failed: %s", e)
            # Fall back to existing token and hope it still works
            pass

    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "expires_at":    int(expires_at),
    }


def token_status() -> dict:
    """
    Return connection status without making API calls.
    Used for the account page header display.
    """
    from database import get_setting
    access_token  = get_setting("schwab_access_token")  or ""
    refresh_token = get_setting("schwab_refresh_token") or ""
    expires_at    = int(get_setting("schwab_expires_at") or "0")
    rt_expires_at = int(get_setting("schwab_rt_expires_at") or "0")
    now           = int(time.time())

    if not access_token and not refresh_token:
        return {"connected": False, "status": "Not connected", "css": "schwab-disconnected"}

    if refresh_token and rt_expires_at < now:
        return {"connected": False, "status": "Session expired — re-authenticate", "css": "schwab-expired"}

    if expires_at > now:
        mins = (expires_at - now) // 60
        return {"connected": True, "status": f"Connected (token valid ~{mins}m)", "css": "schwab-connected"}

    if refresh_token:
        return {"connected": True, "status": "Connected (refreshing token)", "css": "schwab-connected"}

    return {"connected": False, "status": "Token expired", "css": "schwab-expired"}


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _get(path: str, params: dict = None, *, base: str = _BASE_TRADER) -> dict | list:
    """
    Authenticated GET against Schwab API.
    Handles token refresh on 401 automatically.
    Read-only — this module never issues POST/PUT/DELETE.
    """
    import urllib.request
    import urllib.error
    from urllib.parse import urlencode

    tokens = load_tokens()
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
            # Try a one-shot refresh then retry
            try:
                new_tok = refresh_access_token(tokens["refresh_token"])
                save_tokens(new_tok)
                return _do_request(new_tok["access_token"])
            except Exception as refresh_err:
                raise RuntimeError(f"Schwab auth failed after refresh: {refresh_err}") from refresh_err
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"Schwab API error {e.code}: {body}") from e


# ── Read-only data fetchers ───────────────────────────────────────────────────

def fetch_accounts() -> list[dict]:
    """
    Fetch all linked accounts with positions and balances.
    Returns a list of normalized account dicts.
    """
    raw = _get("/accounts", {"fields": "positions"})
    if not isinstance(raw, list):
        raw = [raw]
    return [_normalize_account(a) for a in raw]


def fetch_orders(account_hash: str, *, days_back: int = 1) -> list[dict]:
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

    # Positions split by type
    positions_raw = acct.get("positions", [])
    equity_positions = []
    option_positions = []

    total_unrealized = 0.0

    for pos in positions_raw:
        instrument = pos.get("instrument", {})
        asset_type = instrument.get("assetType", "EQUITY")
        symbol     = instrument.get("symbol", "")
        qty        = (pos.get("longQuantity") or 0) - (pos.get("shortQuantity") or 0)
        avg_price  = pos.get("averagePrice") or pos.get("averageLongPrice") or 0.0
        mkt_val    = pos.get("marketValue") or 0.0
        day_pnl    = pos.get("currentDayProfitLoss") or 0.0
        day_pnl_pct= pos.get("currentDayProfitLossPercentage") or 0.0
        unrealized = pos.get("longOpenProfitLoss") or 0.0
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
        }

        if asset_type == "OPTION":
            # Parse option description for display: e.g. "AAPL 150C 2026-06-20"
            norm["option_type"]     = instrument.get("putCall", "")
            norm["strike_price"]    = instrument.get("strikePrice") or 0.0
            norm["expiration_date"] = instrument.get("expirationDate", "")
            norm["underlying"]      = instrument.get("underlyingSymbol", symbol[:4])
            norm["contracts"]       = abs(qty)
            norm["cost_basis"]      = round(avg_price * abs(qty) * 100, 2)
            option_positions.append(norm)
        else:
            equity_positions.append(norm)

    # Sort by market value desc
    equity_positions.sort(key=lambda p: abs(p["market_value"]), reverse=True)
    option_positions.sort(key=lambda p: abs(p["market_value"]), reverse=True)

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
        "position_count":    len(equity_positions) + len(option_positions),
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

def get_account_summary() -> dict:
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
        accounts = fetch_accounts()
        total_value   = sum(a["total_value"]      for a in accounts)
        buying_power  = sum(a["buying_power"]     for a in accounts)
        daily_pnl     = sum(a["daily_pnl"] or 0   for a in accounts)
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
        logger.warning("schwab  get_account_summary failed: %s", e)
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
