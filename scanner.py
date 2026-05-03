"""
scanner.py — Real-time Momentum Scanner for Rockkstaar Trade Assistant.

Phase 1: curated 18-ticker universe scanned every 15 s during market hours.

Signal tags (first match wins):
  MOMENTUM SPIKE   — +1.5%+ intraday · vol ≥1.5× · above VWAP · near HOD
  BREAKOUT WATCH   — near HOD · day gain ≥1.5% · elevated volume
  VOLUME SPIKE     — vol ≥2× avg · directional move ≥0.5%
  HOT RUNNER       — +4%+ on the day

Alerts are persisted to the scanner_alerts DB table (seen/unseen).
Phone alerts (Telegram/Discord) are disabled in Phase 1.
Duplicate (ticker, tag) events suppressed for 15 minutes.
"""
from __future__ import annotations

import logging
import os
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# ── ET helpers ────────────────────────────────────────────────────────────────

def _et_now() -> datetime:
    try:
        import zoneinfo
        return datetime.now(zoneinfo.ZoneInfo("America/New_York"))
    except Exception:
        from datetime import timezone
        return datetime.now(timezone(timedelta(hours=-4)))


def _market_hours() -> bool:
    """True if within the scannable window (8:30 AM – 5:00 PM ET, weekdays)."""
    now = _et_now()
    if now.weekday() >= 5:
        return False
    h, m = now.hour, now.minute
    return (8, 30) <= (h, m) <= (17, 0)


# ── Scan universe (Phase 1 — curated high-activity names) ────────────────────

SCAN_UNIVERSE: list[str] = [
    # Mega-cap tech
    "NVDA", "META", "AMZN", "AMD", "TSLA", "AAPL", "MSFT", "GOOGL",
    # Semis
    "MU", "MRVL", "AVGO",
    # Other active names
    "DELL", "RKLB", "PLTR", "SMCI", "TSM",
    # ETFs
    "QQQ", "SPY",
]

# ── Module-level state (thread-safe) ──────────────────────────────────────────

_scan_lock       = threading.Lock()
_scanner_started = False

_scan_state: dict = {
    "last_scan":     None,   # ET timestamp string
    "opportunities": [],     # list[dict] — current findings, sorted by score
    "scan_count":    0,
}

# Average daily volume cache — populated lazily, 24-hour TTL
_avg_vol_lock   = threading.Lock()
_avg_vol_cache: dict[str, dict] = {}   # {ticker: {"vol": float, "ts": float}}
_AVG_VOL_TTL    = 86_400   # 24 h

# Alert dedup — suppress same (ticker, tag) within 15 minutes
_notif_lock   = threading.Lock()
_notif_fired: dict[tuple, float] = {}   # {(ticker, tag): epoch_fired}
_NOTIF_DEDUP  = 900   # 15 minutes


# ── Volume average helpers ────────────────────────────────────────────────────

def _get_avg_volume(ticker: str) -> float | None:
    """Return cached avg daily volume, fetching from Yahoo if stale/missing."""
    now = _time.time()
    with _avg_vol_lock:
        entry = _avg_vol_cache.get(ticker)
        if entry and now - entry["ts"] < _AVG_VOL_TTL:
            return entry["vol"]

    try:
        from data_fetcher import _fetch_ohlcv_via_chart_api
        data = _fetch_ohlcv_via_chart_api(ticker, interval="1d", range_str="1mo")
        if not data or len(data["volumes"]) < 5:
            return None
        recent = [v for v in data["volumes"][-20:] if v and v > 0]
        if not recent:
            return None
        avg = sum(recent) / len(recent)
        with _avg_vol_lock:
            _avg_vol_cache[ticker] = {"vol": avg, "ts": now}
        return avg
    except Exception as _e:
        logger.debug("avg_volume fetch failed %s: %s", ticker, _e)
        return None


def _prefetch_avg_volumes(tickers: list[str]) -> None:
    """Batch-populate avg volume cache on scanner startup (background)."""
    now = _time.time()
    missing = []
    with _avg_vol_lock:
        for t in tickers:
            entry = _avg_vol_cache.get(t)
            if not entry or now - entry["ts"] >= _AVG_VOL_TTL:
                missing.append(t)

    if not missing:
        return

    logger.info("scanner: prefetching avg volumes for %d tickers", len(missing))
    with ThreadPoolExecutor(max_workers=15) as ex:
        futures = {ex.submit(_get_avg_volume, t): t for t in missing}
        for f in as_completed(futures, timeout=90):
            try:
                f.result()
            except Exception:
                pass


# ── Per-ticker scan logic ─────────────────────────────────────────────────────

def _scan_ticker(ticker: str) -> dict | None:
    """
    Fetch today's 2-minute intraday bars for one ticker and apply
    momentum detection rules. Returns an opportunity dict or None.
    """
    try:
        from data_fetcher import _fetch_ohlcv_via_chart_api
        data = _fetch_ohlcv_via_chart_api(ticker, interval="2m", range_str="1d")
        if not data or len(data["closes"]) < 5:
            return None

        closes  = data["closes"]
        opens   = data["opens"]
        highs   = data["highs"]
        lows    = data["lows"]
        volumes = data["volumes"]
        n       = len(closes)

        current_price = closes[-1]
        today_open    = opens[0] if opens[0] else current_price
        intraday_high = max(highs)
        vols_clean    = [v if v else 0 for v in volumes]
        total_vol     = sum(vols_clean)

        if current_price <= 0 or today_open <= 0:
            return None

        # Day % change from today's open
        day_chg_pct = (current_price - today_open) / today_open * 100

        # Cumulative VWAP (typical-price weighted)
        typ   = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
        denom = sum(vols_clean)
        vwap  = (sum(tp * v for tp, v in zip(typ, vols_clean)) / denom
                 if denom > 0 else current_price)
        above_vwap = current_price > vwap

        # Short-term momentum: change over last ~15 min (8 bars × 2m = 16 min)
        lookback = min(8, n - 2)
        mom_pct = (
            (closes[-1] - closes[-1 - lookback]) / closes[-1 - lookback] * 100
            if lookback > 0 else day_chg_pct
        )

        # Breaking intraday high (within 0.15% tolerance)
        breaking_high = current_price >= intraday_high * 0.9985

        # Volume ratio vs 20-day average (project today's partial volume to full day)
        avg_vol   = _avg_vol_cache.get(ticker, {}).get("vol")
        vol_ratio = 1.0
        if avg_vol and avg_vol > 0:
            # 2m bars: ~195 bars in a 6.5h session
            projected_vol = total_vol * (195 / max(n, 1))
            vol_ratio = projected_vol / avg_vol

        # ── Classification (first match wins for primary tag) ─────────────────
        # MOMENTUM SPIKE: +1.5%+ intraday · vol ≥1.5× · above VWAP · near HOD
        if day_chg_pct >= 1.5 and vol_ratio >= 1.5 and above_vwap and breaking_high:
            primary_tag = "MOMENTUM SPIKE"
            scan_score  = 10
        # BREAKOUT WATCH: near HOD · day gain ≥1.5% · elevated volume
        elif breaking_high and day_chg_pct >= 1.5 and vol_ratio >= 1.2:
            primary_tag = "BREAKOUT WATCH"
            scan_score  = 7
        # VOLUME SPIKE: vol ≥2× avg · price moving
        elif vol_ratio >= 2.0 and abs(day_chg_pct) >= 0.5:
            primary_tag = "VOLUME SPIKE"
            scan_score  = 5
        # HOT RUNNER: strong day gain
        elif day_chg_pct >= 4.0:
            primary_tag = "HOT RUNNER"
            scan_score  = 6
        else:
            return None

        # Tight entry zone centered on current price
        e_low  = round(current_price * 0.998, 2)
        e_high = round(current_price * 1.003, 2)

        return {
            "ticker":        ticker,
            "price":         round(current_price, 2),
            "day_chg_pct":   round(day_chg_pct, 2),
            "momentum_pct":  round(mom_pct, 2),
            "volume_ratio":  round(vol_ratio, 1),
            "above_vwap":    above_vwap,
            "vwap":          round(vwap, 2),
            "breaking_high": breaking_high,
            "intraday_high": round(intraday_high, 2),
            "primary_tag":   primary_tag,
            "scan_score":    scan_score,
            "entry_zone":    f"${e_low}–${e_high}",
            "reason":        _build_reason(
                primary_tag, ticker, day_chg_pct, mom_pct, vol_ratio, above_vwap, breaking_high
            ),
            "scanned_at": _et_now().strftime("%I:%M %p").lstrip("0") + " ET",
        }

    except Exception as _e:
        logger.debug("scan_ticker %s: %s", ticker, _e)
        return None


def _build_reason(
    tag: str, ticker: str, day_chg: float, mom: float,
    vol_r: float, above_vwap: bool, breaking: bool,
) -> str:
    parts = [f"{ticker}"]
    if tag == "MOMENTUM SPIKE":
        parts.append(f"momentum spike: +{day_chg:.1f}%")
        if mom >= 1.0:
            parts.append(f"+{mom:.1f}% in 15 min")
    elif tag in ("BREAKOUT WATCH", "HOT RUNNER"):
        parts.append(f"+{day_chg:.1f}% on day")
    elif tag == "VOLUME SPIKE":
        chg_txt = f"{day_chg:+.1f}%"
        parts.append(f"volume spike {chg_txt}")
    if vol_r >= 1.5:
        parts.append(f"rel vol {vol_r:.1f}×")
    if above_vwap:
        parts.append("above VWAP")
    if breaking:
        parts.append("near HOD")
    return ", ".join(parts[1:]) if len(parts) > 1 else f"{day_chg:+.1f}% on day"


# ── Notification helpers ──────────────────────────────────────────────────────

def _should_notify(ticker: str, tag: str) -> bool:
    """Return True and record the fire time if this (ticker, tag) is not in cooldown."""
    key = (ticker, tag)
    now = _time.time()
    with _notif_lock:
        if now - _notif_fired.get(key, 0) < _NOTIF_DEDUP:
            return False
        _notif_fired[key] = now
    return True


def _send_telegram(msg: str) -> None:
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        import requests as _req
        _req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=6,
        )
    except Exception as _e:
        logger.debug("telegram send failed: %s", _e)


def _send_discord(msg: str) -> None:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        return
    try:
        import requests as _req
        _req.post(webhook, json={"content": msg}, timeout=6)
    except Exception as _e:
        logger.debug("discord send failed: %s", _e)


def _persist_alert(opp: dict) -> None:
    """Write a scanner-detected opportunity to the DB scanner_alerts table."""
    tag      = opp["primary_tag"]
    ticker   = opp["ticker"]
    severity = "high" if tag == "MOMENTUM SPIKE" else (
               "medium" if tag in ("BREAKOUT WATCH", "HOT RUNNER") else "low"
    )
    message  = (
        f"{ticker} {tag}: {opp['reason']} "
        f"(${opp['price']}, entry {opp['entry_zone']})"
    )
    try:
        from database import add_scanner_alert
        add_scanner_alert(ticker, tag, message, severity)
    except Exception as _e:
        logger.debug("persist_alert failed %s: %s", ticker, _e)


# ── Core scan cycle ───────────────────────────────────────────────────────────

def _run_scan(extra_tickers: list[str] | None = None) -> list[dict]:
    """
    Scan the full universe concurrently. Returns detected opportunities
    sorted by scan_score desc, day_chg_pct desc.
    extra_tickers are merged into the universe (e.g. user watchlist).
    """
    universe = list(SCAN_UNIVERSE)
    if extra_tickers:
        seen = set(universe)
        for t in extra_tickers:
            if t not in seen:
                universe.append(t)
                seen.add(t)

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(_scan_ticker, t): t for t in universe}
        for f in as_completed(futures, timeout=22):
            try:
                opp = f.result()
                if opp:
                    results.append(opp)
            except Exception:
                pass

    results.sort(key=lambda x: (x["scan_score"], x["day_chg_pct"]), reverse=True)
    return results


def _scanner_loop() -> None:
    """
    Daemon loop — runs every 25 seconds. Skips quietly outside market hours.
    On first iteration pre-populates the avg-volume cache so vol_ratio is
    available from cycle 1 onward.
    """
    logger.info("scanner: daemon loop started")

    first_run = True
    cycle_s   = 15
    tick_s    = 5

    while True:
        if _market_hours():
            if first_run:
                # Pre-populate avg volumes in a bg thread so the first scan
                # cycle starts immediately while volumes load concurrently.
                threading.Thread(
                    target=_prefetch_avg_volumes,
                    args=(list(SCAN_UNIVERSE),),
                    daemon=True,
                ).start()
                first_run = False

            try:
                opps = _run_scan()
                ts   = _et_now().strftime("%I:%M %p").lstrip("0") + " ET"

                with _scan_lock:
                    _scan_state["opportunities"] = opps
                    _scan_state["last_scan"]     = ts
                    _scan_state["scan_count"]   += 1

                # Persist new alerts to DB (dedup-guarded, fire-and-forget)
                for opp in opps:
                    if _should_notify(opp["ticker"], opp["primary_tag"]):
                        threading.Thread(
                            target=_persist_alert, args=(opp,), daemon=True
                        ).start()

                logger.info(
                    "scanner: cycle=%d  found=%d  time=%s",
                    _scan_state["scan_count"], len(opps), ts,
                )
            except Exception as _e:
                logger.warning("scanner: cycle error: %s", _e)
        else:
            first_run = True   # reset so prefetch fires again next market open
            with _scan_lock:
                if _scan_state["opportunities"]:
                    _scan_state["opportunities"] = []

        # Sleep in short ticks so the daemon exits quickly on interpreter shutdown
        elapsed = 0
        while elapsed < cycle_s:
            _time.sleep(tick_s)
            elapsed += tick_s


# ── Public API ────────────────────────────────────────────────────────────────

def get_scan_results() -> dict:
    """Return a thread-safe snapshot of the current scanner state."""
    with _scan_lock:
        return {
            "opportunities": list(_scan_state["opportunities"]),
            "last_scan":     _scan_state["last_scan"],
            "scan_count":    _scan_state["scan_count"],
            "market_hours":  _market_hours(),
        }


def start_scanner() -> None:
    """Spawn the background scanner daemon thread (safe to call multiple times)."""
    global _scanner_started
    if _scanner_started:
        return
    _scanner_started = True
    threading.Thread(
        target=_scanner_loop,
        name="momentum-scanner",
        daemon=True,
    ).start()
    logger.info("scanner: started")
