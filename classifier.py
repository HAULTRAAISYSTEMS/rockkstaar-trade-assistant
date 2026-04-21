"""
classifier.py - Auto-classification rules for Rockkstaar Trade Assistant.

Five-bucket workflow — evaluated top-to-bottom, first match wins:

  AVOID / BLOCKED       — do not trade: avoid bias, weak structure, bad R:R
  EXTENDED / CHASE ZONE — ran too far from ideal entry; wait for reset
  A+ READY              — all conditions met: score, status, trend, R:R, not extended
  SETUPS FORMING        — decent setup but one or two things still missing
  TREND WATCH           — strong trend, setup not yet ready

Inputs used (all from stock_data table, no annotate() dependency):
  swing_score, swing_status, daily_trend, trade_bias,
  catalyst_score, momentum_score, risk_reward,
  entry_quality, pct_from_ema20, auto_classify, classify_reason
"""

# Must match DEFAULT_WATCHLISTS in database.py
A_PLUS_READY    = "A+ READY"
SETUPS_FORMING  = "SETUPS FORMING"
TREND_WATCH     = "TREND WATCH"
EXTENDED_ZONE   = "EXTENDED / CHASE ZONE"
AVOID_BLOCKED   = "AVOID / BLOCKED"

# Current 4-mode swing status labels
_READY_STATUSES = {
    "READY — LEVEL HOLDS",
    "PRE-CONFIRMATION",
    # Legacy labels kept for backward compat
    "GOOD SWING CANDIDATE",
    "READY IF LEVEL HOLDS",
}

_AVOID_STATUSES = {
    "AVOID — AT RESISTANCE",
    "AVOID — WEAK STRUCTURE",
    # Legacy labels
    "AVOID AT RESISTANCE",
    "AVOID WEAK STRUCTURE",
}

_EXTENDED_STATUSES = {
    "TOO EXTENDED",
}

_FORMING_STATUSES = {
    "WAIT",
    "WAIT FOR PULLBACK",
    "WAIT FOR 15M CONFIRMATION",
    "NOT ENOUGH EDGE",
    "TREND CONTINUATION",
}

_BULLISH_TRENDS = {"Bullish", "Bullish Lean"}
_BEARISH_TRENDS = {"Bearish", "Bearish Lean"}
_TREND_ALIGNED  = _BULLISH_TRENDS | _BEARISH_TRENDS


def classify_stock(stock: dict) -> tuple:
    """
    Determine which bucket this stock belongs to and why.

    Returns:
        (watchlist_name: str, reason: str)

    Reason strings always start with the bucket name so the dashboard
    badge renderer can map them to the correct CSS class.
    """
    bias         = stock.get("trade_bias") or "Neutral"
    swing_score  = stock.get("swing_score") or 0
    swing_status = (stock.get("swing_status") or "").strip()
    daily_trend  = (stock.get("daily_trend")  or "Neutral").strip()
    cat_sc       = float(stock.get("catalyst_score")  or 0)
    mom_sc       = float(stock.get("momentum_score")  or 0)
    rr           = float(stock.get("risk_reward")     or 0)
    entry_q      = (stock.get("entry_quality") or "").strip()
    pct_ema20    = stock.get("pct_from_ema20")   # may be None

    trend_aligned = daily_trend in _TREND_ALIGNED

    # ── 1. AVOID / BLOCKED ────────────────────────────────────────────────────
    if bias == "Avoid":
        return AVOID_BLOCKED, (
            "AVOID / BLOCKED: Avoid bias — not suitable for active trading"
        )

    if swing_status in _AVOID_STATUSES:
        label = swing_status.replace("AVOID — ", "").replace("AVOID ", "")
        return AVOID_BLOCKED, (
            f"AVOID / BLOCKED: {label} — structure or resistance makes this untradeable"
        )

    if rr > 0 and rr < 1.0:
        return AVOID_BLOCKED, (
            f"AVOID / BLOCKED: R:R too weak ({rr:.1f}:1) — risk does not justify reward"
        )

    if swing_score < 3 and cat_sc < 4 and mom_sc < 4:
        return AVOID_BLOCKED, (
            f"AVOID / BLOCKED: Low signal — swing {swing_score}/10, "
            f"catalyst {cat_sc:.0f}/10, momentum {mom_sc:.0f}/10"
        )

    # ── 2. EXTENDED / CHASE ZONE ──────────────────────────────────────────────
    if swing_status in _EXTENDED_STATUSES:
        return EXTENDED_ZONE, (
            "EXTENDED / CHASE ZONE: Price ran too far — do not chase, wait for reset"
        )

    if entry_q == "Extended":
        pct_note = f" ({pct_ema20:+.1f}% from 20 EMA)" if pct_ema20 is not None else ""
        return EXTENDED_ZONE, (
            f"EXTENDED / CHASE ZONE: Entry quality Extended{pct_note} — wait for pullback"
        )

    if pct_ema20 is not None and pct_ema20 > 8:
        return EXTENDED_ZONE, (
            f"EXTENDED / CHASE ZONE: {pct_ema20:.1f}% above 20 EMA — "
            "too far from ideal entry, wait for reset"
        )

    # ── 3. A+ READY ───────────────────────────────────────────────────────────
    if swing_score >= 7 and swing_status in _READY_STATUSES and trend_aligned:
        gaps = []
        if rr > 0 and rr < 1.5:
            gaps.append(f"R:R only {rr:.1f}:1")
        if not gaps:
            trend_label = daily_trend
            rr_note = f", R:R {rr:.1f}:1" if rr >= 1.5 else ""
            cat_note = f", catalyst {cat_sc:.0f}/10" if cat_sc >= 5 else ""
            return A_PLUS_READY, (
                f"A+ READY: score {swing_score}/10, {swing_status}, "
                f"{trend_label} trend{rr_note}{cat_note} — entry in zone"
            )
        # Near-A+: score and status ready but R:R is marginal → SETUPS FORMING
        return SETUPS_FORMING, (
            f"SETUPS FORMING: Strong setup ({swing_score}/10, {swing_status}) "
            f"but {'; '.join(gaps)} — improve R:R before entry"
        )

    # A+ even without trend if score is very high and in a ready status
    if swing_score >= 9 and swing_status in _READY_STATUSES:
        return A_PLUS_READY, (
            f"A+ READY: Elite score {swing_score}/10, {swing_status} — "
            "trade allowed regardless of trend (very high score)"
        )

    # ── 4. SETUPS FORMING ─────────────────────────────────────────────────────
    if swing_score >= 5:
        gaps = []
        if swing_score < 7:
            gaps.append(f"score {swing_score}/10 (need ≥ 7 for A+)")
        if swing_status not in _READY_STATUSES:
            if swing_status in _FORMING_STATUSES:
                gaps.append(_forming_hint(swing_status))
            elif swing_status:
                gaps.append(f"status: {swing_status}")
        if not trend_aligned:
            gaps.append(f"trend not aligned ({daily_trend})")
        if rr > 0 and rr < 1.5:
            gaps.append(f"R:R {rr:.1f}:1 (need ≥ 1.5)")
        reason_detail = "; ".join(gaps) if gaps else "near key level, watching"
        return SETUPS_FORMING, (
            f"SETUPS FORMING: {reason_detail}"
        )

    # ── 5. TREND WATCH ────────────────────────────────────────────────────────
    if swing_score >= 3 and trend_aligned:
        gaps = []
        if swing_score < 5:
            gaps.append(f"score {swing_score}/10 needs improvement")
        if swing_status in _FORMING_STATUSES:
            gaps.append(_forming_hint(swing_status))
        elif swing_status and swing_status not in _READY_STATUSES:
            gaps.append(f"status: {swing_status}")
        reason_detail = "; ".join(gaps) if gaps else "monitoring for setup development"
        return TREND_WATCH, (
            f"TREND WATCH: {daily_trend} trend, {reason_detail}"
        )

    # ── Catch-all → AVOID / BLOCKED ───────────────────────────────────────────
    return AVOID_BLOCKED, (
        f"AVOID / BLOCKED: Insufficient signal — swing {swing_score}/10, "
        f"trend: {daily_trend}, status: {swing_status or 'none'}"
    )


def _forming_hint(status: str) -> str:
    """Return a plain-English 'needs X' string for a forming/wait status."""
    return {
        "WAIT":                       "needs confirmation",
        "WAIT FOR PULLBACK":          "needs pullback to entry zone",
        "WAIT FOR 15M CONFIRMATION":  "needs 15m entry confirmation",
        "NOT ENOUGH EDGE":            "not enough edge yet",
        "TREND CONTINUATION":         "needs breakout confirmation",
    }.get(status, f"status: {status}")
