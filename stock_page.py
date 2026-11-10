"""Read-only helpers for the stock page's decision row.

live_quote(ticker)            the shared live print (same feed as the Terminal)
overlay_live(stock, quote)    lay that print over a stock dict before annotate()
price_ladder(stock)           every level that matters, high to low, one list
watch_items(stock)            plain-language "what has to happen" checklist

Nothing here fetches market data on its own; live_quote reads the shared feed
that market_engine keeps warm, and returns None rather than waiting on it.
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def _f(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out > 0 else None


# ── Live print ───────────────────────────────────────────────────────────────

def live_quote(ticker: str) -> dict | None:
    """{price, change_pct, prev_close, source, epoch, as_of} or None."""
    try:
        import market_engine as mkt
        q = (mkt.live_quotes([ticker]) or {}).get(ticker.upper())
    except Exception as exc:
        logger.debug("stock_page live_quote %s: %s", ticker, exc)
        return None
    price = _f((q or {}).get("price"))
    if not price:
        return None
    out = {"price": round(price, 2), "change_pct": q.get("change_pct"),
           "prev_close": _f(q.get("prev_close")), "source": q.get("source") or "",
           "epoch": q.get("epoch"), "as_of": ""}
    if q.get("epoch"):
        try:
            from zoneinfo import ZoneInfo
            out["as_of"] = datetime.fromtimestamp(int(q["epoch"]), ZoneInfo("America/New_York")) \
                .strftime("%-I:%M:%S %p ET")
        except Exception:
            pass
    return out


def overlay_live(stock: dict, quote: dict | None) -> dict:
    """Use the live print for price and day change, so the page, the Terminal
    and the watchlist show one number. Never persisted by the caller."""
    if not quote:
        return stock
    stock["current_price"] = quote["price"]
    if quote.get("prev_close"):
        stock["prev_close"] = quote["prev_close"]
    if quote.get("change_pct") is not None:
        stock["gap_pct"] = round(float(quote["change_pct"]), 2)
    stock["live_quote"] = quote
    return stock


# ── Price ladder ─────────────────────────────────────────────────────────────

_FIBS = (("fib_236", "Fib 23.6%"), ("fib_382", "Fib 38.2%"), ("fib_50", "Fib 50%"),
         ("fib_618", "Fib 61.8% ★"), ("fib_705", "Fib 70.5%"), ("fib_786", "Fib 78.6%"))


def price_ladder(stock: dict) -> list[dict]:
    """Every level a decision rests on, high to low.

    kind: plan (stop / targets / entry zone), ema, fib, swing, zone.
    The "you are here" marker is placed client-side so it can follow the live
    price without re-rendering the list.
    """
    rows: list[dict] = []

    def add(price, label, kind, top=None):
        p = _f(price)
        if p:
            row = {"price": round(p, 2), "label": label, "kind": kind}
            t = _f(top)
            if t and t > p:
                row["top"] = round(t, 2)
            rows.append(row)

    add(stock.get("target_2"), "Target 2", "target")
    add(stock.get("target_1"), "Target 1", "target")
    add(stock.get("entry_zone_low"), "Entry zone", "entry", stock.get("entry_zone_high"))
    add(stock.get("stop_level"), "Stop", "stop")
    add(stock.get("ema_20_daily"), "20 EMA", "ema")
    add(stock.get("ema_50_daily"), "50 EMA", "ema")
    add(stock.get("ema_200_daily"), "200 EMA", "ema")
    add(stock.get("fib_high"), "Swing high", "swing")
    add(stock.get("fib_low"), "Swing low", "swing")
    for key, label in _FIBS:
        add(stock.get(key), label, "fib")
    add(stock.get("nearest_supply_bottom"), "Supply zone", "supply", stock.get("nearest_supply_top"))
    add(stock.get("nearest_demand_bottom"), "Demand zone", "demand", stock.get("nearest_demand_top"))

    # Same price from two sources (swing high = target 1) reads as one row.
    merged: dict[float, dict] = {}
    for r in rows:
        hit = merged.get(r["price"])
        if hit:
            hit["label"] = f'{hit["label"]} · {r["label"]}'
            if r["kind"] in ("target", "stop", "entry"):
                hit["kind"] = r["kind"]
            hit.setdefault("top", r.get("top"))
            if hit.get("top") is None:
                hit.pop("top")
        else:
            merged[r["price"]] = dict(r)
    return sorted(merged.values(), key=lambda r: r.get("top") or r["price"], reverse=True)


# ── What has to happen ───────────────────────────────────────────────────────

def _money(v) -> str:
    return f"${v:,.2f}"


def watch_items(stock: dict) -> list[dict]:
    """Short, factual checklist: {ok: bool, text}. Long-bias reading unless
    the stock is short-biased, in which case the comparisons flip."""
    price = _f(stock.get("current_price"))
    if not price:
        return []
    short = (stock.get("trade_bias") or "").startswith("Short")
    items: list[dict] = []

    ema200 = _f(stock.get("ema_200_daily"))
    if ema200:
        above = price > ema200
        ok = above != short
        if ok:
            items.append({"ok": True, "text": f"{'Above' if above else 'Below'} the 200 EMA ({_money(ema200)})"})
        else:
            items.append({"ok": False, "text": f"{'Reclaim' if not short else 'Lose'} the 200 EMA at {_money(ema200)}"
                                               f" ({abs(price / ema200 - 1) * 100:.1f}% away)"})

    trend = stock.get("daily_trend") or ""
    if stock.get("daily_lh_ll") and not short:
        items.append({"ok": False, "text": "Daily structure is lower highs / lower lows — needs a higher low"})
    elif stock.get("daily_hh_hl") and not short:
        items.append({"ok": True, "text": "Daily structure is higher highs / higher lows"})
    elif trend:
        items.append({"ok": ("Bullish" in trend) != short, "text": f"Daily trend: {trend}"})

    lo, hi = _f(stock.get("entry_zone_low")), _f(stock.get("entry_zone_high"))
    if lo and hi:
        if lo <= price <= hi:
            items.append({"ok": True, "text": f"Price is inside the entry zone {_money(lo)}–{_money(hi)}"})
        elif price > hi:
            items.append({"ok": False, "text": f"Wait for a pullback into {_money(lo)}–{_money(hi)}"
                                               f" ({(price / hi - 1) * 100:.1f}% above)"})
        else:
            items.append({"ok": False, "text": f"Below the entry zone — needs to reclaim {_money(lo)}"})

    stop = _f(stock.get("stop_level"))
    if stop and not short and price <= stop:
        items.append({"ok": False, "text": f"Trading below the stop level {_money(stop)} — plan is invalid"})

    rr = stock.get("risk_reward")
    try:
        rr = float(rr) if rr is not None else None
    except (TypeError, ValueError):
        rr = None
    if rr is not None:
        items.append({"ok": rr >= 2, "text": f"Risk/reward {rr:.1f}:1" + ("" if rr >= 2 else " — 2:1 or better preferred")})

    cat = stock.get("catalyst_score")
    if isinstance(cat, (int, float)):
        items.append({"ok": cat >= 3, "text": f"Catalyst {cat}/10" + ("" if cat >= 3 else " — needs 3+ for an A+ swing")})
    return items
