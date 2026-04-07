"""
classifier.py - Auto-classification rules for Rockkstaar Trade Assistant.

Classifies each stock into one of the four default watchlists based on
its current momentum, ORB readiness, entry quality, and setup score.

Watchlist hierarchy (evaluated top to bottom — first match wins):
    A+ Momentum    — Full criteria met, trade today
    Secondary Watch — Decent but not full A+ criteria, monitor only
    Swing Watchlist — Weak intraday, some price structure, swing candidate
    Core            — Default / low signal / manually curated names
"""

# Must match DEFAULT_WATCHLISTS in database.py
A_PLUS    = "A+ Momentum"
SECONDARY = "Secondary Watch"
SWING     = "Swing Watchlist"
CORE      = "Core"


def classify_stock(stock: dict) -> tuple:
    """
    Determine which default watchlist this stock belongs to and why.

    Returns:
        (watchlist_name: str, reason: str)

    Classification rules:

    A+ Momentum
        - momentum_score >= 6
        - orb_ready == "YES"
        - entry_quality != "Extended"
        - setup_score >= 7

    Secondary Watch
        - momentum_score >= 4
          OR setup_score in [5, 6]
        (but does NOT meet A+ criteria)

    Swing Watchlist
        - setup_score >= 3
        - momentum_score < 4
        - orb_ready != "YES"
        (some price structure, not an intraday momentum play)

    Core (default catch-all)
        - Everything else: low signal, Avoid bias, or manually curated names
    """
    mom   = stock.get("momentum_score") or 0
    orb   = stock.get("orb_ready")      or "NO"
    entry = stock.get("entry_quality")  or "Okay"
    setup = stock.get("setup_score")    or 0
    bias  = stock.get("trade_bias")     or "Neutral"

    # Avoid-biased stocks go straight to Core regardless of scores
    if bias == "Avoid":
        return CORE, "Placed in Core: Avoid bias — not suitable for active trading today"

    # ── A+ Momentum ───────────────────────────────────────────────────────────
    # Full criteria: strong momentum + ORB confirmed + good entry + high setup
    if mom >= 6 and orb == "YES" and entry != "Extended" and setup >= 7:
        parts = []
        parts.append(f"{'elite' if mom >= 9 else 'strong'} momentum ({mom}/10)")
        parts.append("ORB ready")
        if entry == "Perfect":
            parts.append("perfect entry quality")
        parts.append(f"setup {setup}/10")
        return A_PLUS, "Promoted: " + ", ".join(parts)

    # ── Secondary Watch ───────────────────────────────────────────────────────
    # Moderate momentum or decent setup — worth monitoring but not A+
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
        reason = "Secondary: " + (", ".join(gaps) if gaps else "decent structure, not yet A+ criteria")
        return SECONDARY, reason

    # ── Swing Watchlist ───────────────────────────────────────────────────────
    # Some structure but intraday momentum is weak — potential swing candidate
    if setup >= 3 and mom < 4 and orb != "YES":
        return SWING, (
            f"Swing candidate: low intraday momentum ({mom}/10), "
            f"some price structure (setup {setup}/10) — not an active intraday play"
        )

    # ── Core (default) ────────────────────────────────────────────────────────
    return CORE, f"Core: low signal today (momentum {mom}/10, setup {setup}/10) — manually monitor"
