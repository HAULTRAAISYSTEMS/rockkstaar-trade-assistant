"""
classifier.py - Single source of truth for ticker classification in the
Rockkstaar Trade Assistant.

This module is the ONLY place that decides:
  - which bucket a ticker belongs to     (bucket)
  - the badge label shown for that ticker (status_label)
  - whether the ticker is avoid/blocked   (avoid_blocked)
  - the letter grade shown for it         (grade)
  - the human-readable explanation        (reason)

Every other part of the app (dashboard table, Scanner Buckets widget,
Best Swing Candidates cards, top-nav / watchlist-tab counters, the
Avoid/Blocked watchlist view, and the swing-alerts feed) MUST read these
five fields from the same classify(stock) call instead of re-deriving
their own grade/status/bucket from raw score fields. That is what
previously let the same ticker show up as e.g. simultaneously "AVOID"
in one widget and "A+ READY" in another — each widget had its own
slightly-different threshold logic. See classify() below.

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

# All valid bucket constants, in priority order (first match wins in classify()).
ALL_BUCKETS = (AVOID_BLOCKED, EXTENDED_ZONE, A_PLUS_READY, SETUPS_FORMING, TREND_WATCH)

# ---------------------------------------------------------------------------
# Canonical display mapping — every badge/label/css/grade shown anywhere in
# the UI for a given bucket comes from THIS table, not from ad hoc per-widget
# logic. This is what guarantees the bucket badge, status badge, and
# avoid/blocked flag can never contradict each other: they're all looked up
# from the same row.
# ---------------------------------------------------------------------------
_BUCKET_DISPLAY = {
    A_PLUS_READY:   {"status_label": "A+ READY",        "badge_css": "sfa-aplus",    "grade_hint": "A+", "scanner_key": "aplus",   "avoid_blocked": False},
    SETUPS_FORMING: {"status_label": "SETUPS FORMING",   "badge_css": "sfa-forming",  "grade_hint": "B",  "scanner_key": "forming", "avoid_blocked": False},
    TREND_WATCH:    {"status_label": "TREND WATCH",      "badge_css": "sfa-watch",    "grade_hint": "C",  "scanner_key": "forming", "avoid_blocked": False},
    EXTENDED_ZONE:  {"status_label": "EXTENDED / CHASE", "badge_css": "sfa-extended", "grade_hint": "B-", "scanner_key": "chase",   "avoid_blocked": False},
    AVOID_BLOCKED:  {"status_label": "AVOID / BLOCKED",  "badge_css": "sfa-rejected", "grade_hint": "D",  "scanner_key": "avoid",   "avoid_blocked": True},
}

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


def _bucket_and_reason(stock: dict) -> tuple:
    """
    Determine which bucket this stock belongs to and why.

    Returns:
        (bucket: str, reason: str)

    Reason strings always start with the bucket name so the dashboard
    badge renderer can map them to the correct CSS class.

    This is the only function in the codebase allowed to decide bucket
    membership from raw score fields. Everything else must call
    classify() and read its "bucket" key.
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


def _grade_for_bucket(bucket: str, swing_score) -> str:
    """
    Letter grade shown next to a ticker. Derived FROM the bucket (not
    independently from swing_score) so the grade can never contradict the
    bucket — e.g. a stock can never be lettered "A+" while bucketed
    AVOID / BLOCKED.

    Within A+ READY / SETUPS FORMING we still use score to pick between the
    two adjacent letters (A+ vs A, B+ vs B) so the grade keeps some
    granularity, but the bucket always wins: the letter is capped to what
    that bucket allows.
    """
    swing_score = swing_score or 0
    if bucket == A_PLUS_READY:
        return "A+" if swing_score >= 9 else "A"
    if bucket == SETUPS_FORMING:
        return "B+" if swing_score >= 6 else "B"
    if bucket == TREND_WATCH:
        return "C"
    if bucket == EXTENDED_ZONE:
        return "B-"
    return "D"  # AVOID_BLOCKED


def classify(stock: dict) -> dict:
    """
    THE canonical per-ticker classification. Call this once per ticker per
    scan/render cycle and have every UI surface read its fields — do not
    re-derive grade/status/bucket from raw score fields anywhere else.

    Returns a dict with:
      bucket          - one of A_PLUS_READY / SETUPS_FORMING / TREND_WATCH /
                         EXTENDED_ZONE / AVOID_BLOCKED
      status_label    - short badge text, derived 1:1 from bucket
                         (e.g. "A+ READY", "AVOID / BLOCKED")
      badge_css       - CSS class for that badge, derived 1:1 from bucket
      grade           - letter grade, derived from (bucket, swing_score) —
                         can never imply a better/worse bucket than `bucket`
      scanner_key     - which Scanner Buckets quadrant this belongs in
                         ("aplus" | "forming" | "chase" | "avoid")
      avoid_blocked   - bool, True iff bucket == AVOID_BLOCKED
      reason          - human-readable explanation (also persisted as
                         classify_reason)
    """
    bucket, reason = _bucket_and_reason(stock)
    display = _BUCKET_DISPLAY[bucket]
    return {
        "bucket":        bucket,
        "status_label":  display["status_label"],
        "badge_css":     display["badge_css"],
        "grade":         _grade_for_bucket(bucket, stock.get("swing_score")),
        "scanner_key":   display["scanner_key"],
        "avoid_blocked": display["avoid_blocked"],
        "reason":        reason,
    }


def classify_stock(stock: dict) -> tuple:
    """
    Backward-compatible wrapper around classify(). Returns (bucket, reason)
    as the original API did. New code should call classify() directly so it
    also gets status_label / badge_css / grade / avoid_blocked from the same
    computation instead of re-deriving them.
    """
    result = classify(stock)
    return result["bucket"], result["reason"]


def is_consistent(classification: dict) -> bool:
    """
    Basic invariant check: the bucket, status label, and avoid/blocked flag
    must never contradict each other for a single classification result.
    Used by tests and can be called defensively wherever a classification
    is rendered.
    """
    bucket = classification.get("bucket")
    if bucket not in ALL_BUCKETS:
        return False

    expected = _BUCKET_DISPLAY[bucket]
    if classification.get("status_label") != expected["status_label"]:
        return False
    if classification.get("avoid_blocked") != expected["avoid_blocked"]:
        return False

    # A ticker flagged avoid/blocked can never show an A+ READY (or any
    # other non-avoid) status label, and vice versa.
    if classification.get("avoid_blocked") and classification.get("status_label") == A_PLUS_READY:
        return False
    if not classification.get("avoid_blocked") and classification.get("status_label") == AVOID_BLOCKED:
        return False

    return True
