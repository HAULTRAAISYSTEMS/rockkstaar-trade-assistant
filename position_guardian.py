"""Live position guardian: watches your open Schwab positions for you.

For every equity position it checks, in order of danger:
  STOP_HIT     price through your stop                       → danger
  NEAR_STOP    within 1% of your stop                        → watch
  VWAP_LOSS    lost VWAP on heavy volume (≥1.5× normal)      → watch
  BIG_DROP     down 4%+ on the day                           → watch
  EARNINGS     earnings within 3 days                        → watch
  NEWS         a headline in the last hour                   → info
  NO_STOP      no stop set — every position needs one        → info

Stops are set per symbol on the Guardian page and kept in the settings
table. The leader worker runs scan_and_alert() every few minutes in market
hours; each signal alerts once per symbol per day (in-app alert + Telegram).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

SEVERITY = {"STOP_HIT": "danger", "NEAR_STOP": "watch", "VWAP_LOSS": "watch",
            "BIG_DROP": "watch", "EARNINGS": "watch", "NEWS": "info", "NO_STOP": "info"}
ALERTABLE = ("STOP_HIT", "NEAR_STOP", "VWAP_LOSS", "BIG_DROP", "EARNINGS", "NEWS")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── Stops ────────────────────────────────────────────────────────────────────

def _key(user_id: int) -> str:
    return f"guardian_stops:{int(user_id)}"


def get_stops(user_id: int) -> dict:
    try:
        from database import get_setting
        raw = get_setting(_key(user_id))
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def set_stop(user_id: int, symbol: str, stop: float | None, target: float | None = None) -> dict:
    from database import set_setting
    symbol = symbol.upper().strip()
    stops = get_stops(user_id)
    if stop is None or stop <= 0:
        stops.pop(symbol, None)
    else:
        stops[symbol] = {"stop": round(float(stop), 4),
                         "target": round(float(target), 4) if target else None}
    set_setting(_key(user_id), json.dumps(stops))
    return stops


# ── Signals ──────────────────────────────────────────────────────────────────

def evaluate_position(pos: dict, ind: dict, plan: dict | None, news: list[dict] | None = None,
                      now: datetime | None = None) -> dict:
    """Signals for one position. pos = Schwab position, ind = trade_gate indicators."""
    now = now or datetime.now(timezone.utc)
    qty = _f(pos.get("quantity")) or 0
    short = qty < 0
    price = _f(ind.get("price")) or _f(pos.get("last_price"))
    signals: list[dict] = []

    def add(code, text):
        signals.append({"code": code, "severity": SEVERITY[code], "text": text})

    stop = _f((plan or {}).get("stop"))
    if price and stop:
        through = price >= stop if short else price <= stop
        dist = abs(price - stop) / price * 100
        if through:
            add("STOP_HIT", f"Price {price:.2f} is through your stop {stop:.2f}. Exit per plan.")
        elif dist <= 1.0:
            add("NEAR_STOP", f"Price {price:.2f} is {dist:.2f}% from your stop {stop:.2f}.")
    elif not stop:
        add("NO_STOP", "No stop set. Set one so the guardian can protect this position.")

    vwap, rvol = _f(ind.get("vwap")), _f(ind.get("rel_volume"))
    if price and vwap and rvol and rvol >= 1.5:
        lost = price > vwap if short else price < vwap
        if lost:
            add("VWAP_LOSS", f"Lost VWAP {vwap:.2f} on {rvol:.1f}× volume — control has shifted against you.")

    day = _f(pos.get("day_pnl_pct"))
    if day is not None and ((not short and day <= -4) or (short and day >= 4)):
        add("BIG_DROP", f"{day:+.1f}% on the day against the position.")

    days = ind.get("earnings_days")
    if days is not None and 0 <= days <= 3:
        add("EARNINGS", f"Earnings in {days} day{'s' if days != 1 else ''} ({ind.get('earnings_date')}). Decide: hold through, trim, or exit.")

    for item in news or []:
        try:
            ts = datetime.fromisoformat(str(item.get("published_at") or "").replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if now - ts <= timedelta(hours=1):
            add("NEWS", f"News: {item.get('headline')}")
            break

    worst = "ok"
    for s in signals:
        if s["severity"] == "danger":
            worst = "danger"
        elif s["severity"] == "watch" and worst != "danger":
            worst = "watch"
    return {"symbol": pos.get("symbol"), "quantity": qty, "price": price,
            "avg_price": _f(pos.get("avg_price")), "unrealized": _f(pos.get("unrealized")),
            "unrealized_pct": _f(pos.get("unrealized_pct")), "day_pnl_pct": day,
            "stop": stop, "target": _f((plan or {}).get("target")),
            "vwap": vwap, "rel_volume": rvol, "status": worst, "signals": signals}


def positions(user_id: int) -> tuple[list[dict], str | None]:
    """Open equity positions across the user's Schwab accounts."""
    try:
        import schwab
        if not schwab.is_connected(user_id):
            return [], "Schwab is not connected."
        summary = schwab.get_account_summary(user_id)
        if summary.get("error"):
            return [], summary["error"]
        out = []
        for acct in summary.get("accounts") or []:
            out.extend(acct.get("equity_positions") or [])
        return out, None
    except Exception as exc:
        return [], str(exc)


def scan(user_id: int, *, with_news: bool = True) -> dict:
    """The Guardian page's data: every position with its signals."""
    import trade_gate
    pos_list, error = positions(user_id)
    stops = get_stops(user_id)
    rows = []
    for pos in pos_list:
        sym = str(pos.get("symbol") or "").upper()
        if not sym:
            continue
        try:
            ind = trade_gate.gather(sym)
        except Exception:
            ind = {}
        news = []
        if with_news:
            try:
                import intel_engine
                news = intel_engine.fetch_ticker_news(sym, limit=3)
            except Exception:
                news = []
        rows.append(evaluate_position(pos, ind, stops.get(sym), news))
    order = {"danger": 0, "watch": 1, "ok": 2}
    rows.sort(key=lambda r: (order[r["status"]], r["symbol"] or ""))
    return {"ok": error is None, "error": error, "positions": rows,
            "counts": {k: sum(1 for r in rows if r["status"] == k) for k in order},
            "as_of": datetime.now(timezone.utc).isoformat()}


# ── Alerts ───────────────────────────────────────────────────────────────────

def _sent_key(user_id: int) -> str:
    return f"guardian_sent:{int(user_id)}"


def scan_and_alert(user_id: int = 1, *, notify=None, record=None, today: str | None = None) -> list[dict]:
    """Alert each new signal once per symbol per day. Returns what was sent."""
    from database import get_setting, set_setting
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        sent = json.loads(get_setting(_sent_key(user_id)) or "{}")
    except Exception:
        sent = {}
    if sent.get("day") != today:
        sent = {"day": today, "keys": []}
    keys = set(sent.get("keys") or [])
    result = scan(user_id)
    fired = []
    for row in result["positions"]:
        for sig in row["signals"]:
            if sig["code"] not in ALERTABLE:
                continue
            key = f"{row['symbol']}:{sig['code']}"
            if key in keys:
                continue
            keys.add(key)
            text = f"🛡 {row['symbol']} — {sig['text']}"
            fired.append({"symbol": row["symbol"], "code": sig["code"], "text": text})
            try:
                if record:
                    record(row["symbol"], sig, text)
                else:
                    from database import add_scanner_alert
                    sev = {"danger": "high", "watch": "medium", "info": "low"}[sig["severity"]]
                    add_scanner_alert(row["symbol"], "GUARDIAN " + sig["code"].replace("_", " "), text, sev)
            except Exception as exc:
                logger.warning("guardian alert record failed: %s", exc)
            try:
                if notify:
                    notify(text)
                elif sig["severity"] in ("danger", "watch"):
                    import scanner
                    scanner._send_telegram(text)
            except Exception as exc:
                logger.warning("guardian telegram failed: %s", exc)
    set_setting(_sent_key(user_id), json.dumps({"day": today, "keys": sorted(keys)}))
    return fired
