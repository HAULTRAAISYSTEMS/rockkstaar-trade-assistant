"""
classifier.py - Auto-classification rules for Rockkstaar Trade Assistant.

In swing mode (swing_score present), classifies based on swing_score,
daily_trend, and swing_status.  Falls back to legacy day-trading logic
when swing fields are absent (backward compat).

Watchlist hierarchy (evaluated top to bottom — first match wins):
    Swing Ready   — High swing score, actionable status, trend aligned, good R:R
    Pullback Zone — Near key pullback level (20/50 EMA, 50/61.8% fib, demand zone)
    Extended      — Already ran, too far from entry, not good to chase
    Core List     — Default / manually tracked / no actionable data
"""

# Must match DEFAULT_WATCHLISTS in database.py
SWING_READY   = "A+ Swing Setups"
PULLBACK_ZONE = "Secondary Swing Watch"
EXTENDED      = "Extended"
CORE_LIST     = "Core Swing Plays"

_AVOID_STATUSES = {"TOO EXTENDED", "AVOID AT RESISTANCE", "AVOID WEAK STRUCTURE"}
_WAIT_STATUSES  = {"WAIT FOR PULLBACK", "WAIT FOR 15M CONFIRMATION", "NOT ENOUGH EDGE"}
_READY_STATUSES = {"GOOD SWING CANDIDATE", "READY IF LEVEL HOLDS"}


def classify_stock(stock: dict) -> tuple:
    """
    Determine which default watchlist this stock belongs to and why.

    Returns:
        (watchlist_name: str, reason: str)

    Swing mode classification (when swing_score is present):

    Swing Ready
        - swing_score >= 7
        - swing_status in READY_STATUSES
        - daily_trend in ("Bullish", "Bullish Lean") for longs
          OR daily_trend in ("Bearish", "Bearish Lean") for shorts

    Pullback Zone
        - swing_score >= 5
        - swing_status not in AVOID_STATUSES
        (near a key pullback level — not yet fully ready)

    Extended
        - swing_status in AVOID_STATUSES (too extended, at resistance)
        - OR swing_score >= 3 with wait/unfavourable status

    Core List (default)
        - swing_score < 3
        - OR Avoid bias
        - OR no swing data

    Legacy day-trading fallback (when swing_score is absent):
        Swing Ready   : momentum >= 6, ORB ready, entry not Extended, setup >= 7
        Pullback Zone : momentum >= 4 OR setup in [5, 6]
        Extended      : setup >= 3, momentum < 4, ORB not ready
        Core List     : everything else
    """
    bias  = stock.get("trade_bias") or "Neutral"

    # Avoid-biased stocks always go to Core Swing Plays
    if bias == "Avoid":
        return CORE_LIST, "Placed in Core Swing Plays: Avoid bias — not suitable for active trading"

    swing_score  = stock.get("swing_score")
    swing_status = stock.get("swing_status") or ""
    daily_trend  = stock.get("daily_trend")  or "Neutral"

    # ── Swing mode ────────────────────────────────────────────────────────────
    if swing_score:
        trend_ok_long  = daily_trend in ("Bullish", "Bullish Lean")
        trend_ok_short = daily_trend in ("Bearish", "Bearish Lean")
        trend_ok       = trend_ok_long or trend_ok_short

        # Extended: avoid statuses — ran too far or bad structure
        if swing_status in _AVOID_STATUSES:
            return EXTENDED, (
                f"Extended: {swing_status} — already ran or at resistance, "
                "not a valid entry"
            )

        # A+ Swing Setups: high score + actionable status + trend confirmed
        if swing_score >= 7 and swing_status in _READY_STATUSES and trend_ok:
            return SWING_READY, (
                f"A+ Swing Setups: score {swing_score}/10, {swing_status}, "
                f"{daily_trend} daily trend — valid entry zone"
            )

        # A+ even without perfect trend if score is very high
        if swing_score >= 8 and swing_status in _READY_STATUSES:
            return SWING_READY, (
                f"A+ Swing Setups (high score): score {swing_score}/10, {swing_status}"
            )

        # Secondary Swing Watch: decent score, watching for entry at a key level
        if swing_score >= 5 and swing_status not in _AVOID_STATUSES:
            gaps = []
            if swing_score < 7:
                gaps.append(f"score {swing_score}/10 (need ≥ 7 for A+)")
            if swing_status not in _READY_STATUSES:
                gaps.append(f"status: {swing_status}")
            if not trend_ok:
                gaps.append(f"trend: {daily_trend}")
            reason = "Secondary Swing Watch: " + (", ".join(gaps) if gaps else "near key level, watching for entry")
            return PULLBACK_ZONE, reason

        # Extended (low score + avoid/wait status): setup not actionable
        if swing_score >= 3 or swing_status in _WAIT_STATUSES:
            return EXTENDED, (
                f"Extended: score {swing_score}/10, {swing_status} — "
                "setup not ready, do not chase"
            )

        # Core Swing Plays: no usable data — manually tracked
        return CORE_LIST, (
            f"Core Swing Plays: swing score {swing_score}/10, status: {swing_status or 'none'}"
        )

    # ── Legacy day-trading fallback ───────────────────────────────────────────
    mom   = stock.get("momentum_score") or 0
    orb   = stock.get("orb_ready")      or "NO"
    entry = stock.get("entry_quality")  or "Okay"
    setup = stock.get("setup_score")    or 0

    if mom >= 6 and orb == "YES" and entry != "Extended" and setup >= 7:
        parts = []
        parts.append(f"{'elite' if mom >= 9 else 'strong'} momentum ({mom}/10)")
        parts.append("ORB ready")
        if entry == "Perfect":
            parts.append("perfect entry quality")
        parts.append(f"setup {setup}/10")
        return SWING_READY, "A+ Swing Setups: " + ", ".join(parts)

    if entry == "Extended" or (mom < 4 and setup < 3):
        return EXTENDED, (
            f"Extended: entry quality {entry}, momentum {mom}/10 — do not chase"
        )

    if mom >= 4 or (5 <= setup < 7):
        gaps = []
        if orb != "YES":
            gaps.append("ORB not ready yet")
        if entry == "Extended":
            gaps.append("entry too extended")
        if mom < 6:
            gaps.append(f"momentum only {mom}/10 (need ≥ 6)")
        if setup < 7:
            gaps.append(f"setup {setup}/10 (need ≥ 7)")
        reason = "Secondary Swing Watch: " + (", ".join(gaps) if gaps else "near key level, not yet A+")
        return PULLBACK_ZONE, reason

    if setup >= 3 and mom < 4 and orb != "YES":
        return EXTENDED, (
            f"Extended: low momentum ({mom}/10), "
            f"some price structure (setup {setup}/10) — not actionable"
        )

    return CORE_LIST, f"Core Swing Plays: low signal (momentum {mom}/10, setup {setup}/10) — manually monitor"
