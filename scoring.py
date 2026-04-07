"""
scoring.py — Catalyst scoring, momentum scoring, order block, entry quality,
ORB readiness, and final setup scoring.

All public scoring functions return either a ScoringResult NamedTuple or a plain
string, as documented per function.

TUNING
------
Every weight and threshold is a named constant in the *_W / *_P / *_T dicts at
the top of each section.  Change a number there; the logic and explanation strings
update automatically.

No DB or Flask imports — pure functions only.
"""

from datetime import date, datetime
from typing import NamedTuple


# ===========================================================================
# Return type
# ===========================================================================

class ScoringResult(NamedTuple):
    score:       int    # 1–10
    explanation: str    # short summary for display
    confidence:  str    # "High" | "Medium" | "Low"


# ===========================================================================
# Setup types
# ===========================================================================

SETUP_TYPES = [
    "Momentum Breakout",
    "Momentum Runner",
    "Gap and Go",
    "ORB",
    "VWAP Reclaim",
    "Range Break",
    "Breakdown",
    "No Setup",
]


# ===========================================================================
#  MOMENTUM RUNNER — thresholds
#  Lighter breakout confirmation: 2 consecutive closes above ORB high,
#  rvol >= 1.0, price and VWAP above ORB high.
#  Entry stays Extended (warning shown); signal is not blocked.
# ===========================================================================

MR_T = {
    "rvol_min": 1.0,   # Minimum rvol required for Momentum Runner detection
}


# ===========================================================================
# Keyword signal lists  (used in both scoring and breakdown)
# ===========================================================================

_ANALYST_UPGRADE   = ["upgrade", "raises price target", "price target", "outperform",
                       "overweight", "buy rating", "strong buy", "initiated", "reiterate",
                       "maintains buy", "added to conviction"]
_ANALYST_DOWNGRADE = ["downgrade", "underperform", "underweight", "sell rating",
                       "lowers price target", "cut to neutral", "removed from conviction"]
_MAJOR_SIGNALS     = ["sec", "fda", "merger", "acquisition", "buyout", "settlement",
                       "investigation", "recall", "bankruptcy", "delisting", "halt",
                       "indictment", "class action", "takeover", "going private"]
_EARNINGS_SIGNALS  = ["earnings", "eps", "revenue", "beat estimates", "missed estimates",
                       "quarterly results", "q1", "q2", "q3", "q4", "annual results",
                       "guidance", "beat", "miss", "raised guidance", "lowered guidance"]


# ===========================================================================
#  CATALYST SCORE — weights and thresholds
# ===========================================================================

# ---- Positive weights (added to raw score when signal fires) ---------------
CAT_W = {
    # Earnings proximity  (highest single-signal weight — defines the day's trade)
    "earnings_reported":   5,   # Reported in last 3 days or today/tomorrow
    "earnings_this_week":  4,   # Reporting in 2–7 days
    "earnings_upcoming":   1,   # Reporting within 30 days

    # Major news event (binary: either there is one or not)
    "major_event":         4,   # FDA / SEC / merger / halt / acquisition

    # Analyst action
    "analyst_upgrade":     3,   # Upgrade / initiation / price target raise
    "analyst_downgrade":   2,   # Downgrade (still moves the stock — it's a catalyst)

    # Earnings/guidance data in headlines
    "earnings_data":       2,   # Beat / miss / guidance / EPS in summary

    # Gap size  (medium weight — confirms something moved the stock premarket)
    "gap_extreme":         3,   # |gap| >= CAT_T["gap_extreme_pct"]
    "gap_large":           2,   # |gap| >= CAT_T["gap_large_pct"]
    "gap_moderate":        1,   # |gap| >= CAT_T["gap_moderate_pct"]

    # Relative volume  (high weight — market participation confirms the catalyst is real)
    "rvol_extreme":        3,   # rvol >= CAT_T["rvol_extreme"]
    "rvol_high":           2,   # rvol >= CAT_T["rvol_high"]
    "rvol_moderate":       1,   # rvol >= CAT_T["rvol_moderate"]
}

# ---- Penalty weights (subtracted from raw score when signal fires) ---------
CAT_P = {
    "no_news":            -2,   # No news keywords detected — mystery gap, low trust
    "low_volume":         -2,   # rvol < CAT_T["rvol_low"] — market is not reacting
    "tiny_gap":           -1,   # |gap| < CAT_T["gap_tiny_pct"] — stock barely moved
}

# ---- Thresholds (change these without touching weight dicts) ---------------
CAT_T = {
    "gap_extreme_pct":   10.0,
    "gap_large_pct":      5.0,
    "gap_moderate_pct":   2.0,
    "gap_tiny_pct":       0.5,   # gap below this = tiny_gap penalty
    "rvol_extreme":       5.0,
    "rvol_high":          2.5,
    "rvol_moderate":      1.5,
    "rvol_low":           0.8,   # rvol below this = low_volume penalty
}

# ---- Confidence thresholds -------------------------------------------------
CAT_CONF = {
    "high_min_categories": 3,    # distinct signal categories required for "High"
    "high_min_rvol":       CAT_T["rvol_moderate"],
    "medium_min_categories": 2,  # OR 1 category that is earnings/major
}


# ===========================================================================
#  MOMENTUM SCORE — live price structure (primary) + participation bonus (secondary)
#
#  Structure signals  (price-action only — fire with or without rvol/catalyst):
#    orb_hold            (+3) — price held above ORB high for 2+ candles
#    trend_structure     (+3) — higher highs + higher lows (VWAP not required)
#    strong_candle_bodies(+1) — conviction candles (body > 50% of range)
#    → structure max = 7
#
#  Participation bonuses  (confidence adjusters — low values reduce confidence,
#                          they do NOT block the Momentum Runner signal):
#    price_above_vwap    (+1) — price cleared session VWAP
#    rvol >= 1.0         (+2) — market volume confirming the move
#    → participation max = 3
#
#  Total max = 10.
#
#  structure_momentum_score: computed in mock_data.py from the three structure signals.
#  Momentum Runner threshold: structure_momentum_score >= MOM_T["structure_threshold"]
#  (achievable without rvol or VWAP — low participation reduces confidence only).
# ===========================================================================

MOM_W = {
    # Structure (primary — price action, no rvol gate)
    "orb_hold":            3,   # 2+ consecutive closes above ORB high
    "trend_structure":     3,   # higher highs + higher lows
    "strong_bodies":       1,   # last 3 candles body > 50% of range

    # Participation (secondary — bonuses, not gates)
    "price_above_vwap":    1,   # price cleared session VWAP
    "rvol_ok":             2,   # rel_volume >= MOM_T["rvol_min"]
}

MOM_T = {
    "rvol_min":            1.0,   # Minimum rvol for the volume participation bonus
    "structure_threshold": 5,     # structure_momentum_score >= this → Momentum Runner
                                  # (reachable: orb_hold(3)+trend(3)=6, or orb_hold(3)+bodies(1)... needs 5)
                                  # Minimum: either orb_hold+trend_structure, or just one if bodies fires
}


# ===========================================================================
#  FINAL SETUP SCORE — weights and thresholds
#  Combines momentum + ORB readiness + order block + entry quality.
# ===========================================================================

FINAL_W = {
    "orb_ready":      2,   # ORB conditions met — confirmed in-play setup
    "ob_aligned":     2,   # Order block direction matches trade bias
    "entry_perfect":  1,   # Price at optimal entry zone
}

FINAL_P = {
    "ob_opposed":      -2,  # Order block direction opposed to bias — avoid fade
    "entry_extended":  -3,  # Entry is extended — chasing, poor risk/reward
    "low_momentum":    -2,  # Momentum < FINAL_T["low_momentum"] — weak foundation
}

FINAL_T = {
    "low_momentum": 4,   # Momentum score below this triggers low_momentum penalty
}


# ===========================================================================
#  ENTRY QUALITY — thresholds for Extended / Perfect / Okay
# ===========================================================================

ENTRY_T = {
    "vwap_extended_dist": 0.03,  # > 3% from PM midpoint = extended risk
    "gap_extended":       6.0,   # Gap must also be > this % for "Extended" to fire
    "near_pm_margin":     0.015, # Within 1.5% of PM high/low = near the level
    "vwap_perfect_dist":  0.01,  # Within 1% of PM midpoint = very close to VWAP
    "pm_past_margin":     0.03,  # Price >3% past PM extreme = chasing (Extended)
}


# ===========================================================================
#  ORDER BLOCK — thresholds
# ===========================================================================

OB_T = {
    "rvol_min": 1.5,   # Volume required to confirm an order block
}


# ===========================================================================
#  ORB READINESS — thresholds
# ===========================================================================

ORB_T = {
    "momentum_min":    6,    # Minimum momentum score for ORB readiness
    "rvol_min":        1.5,  # Volume confirmation required
    "inside_gap_limit":2.0,  # Max gap to flag inside-day (no clear direction)
}


# ===========================================================================
#  CONFIDENCE (shared for both momentum and final setup)
# ===========================================================================

SETUP_CONF = {
    "high_requires_all": 3,   # All three confidence flags must be set
    "medium_requires":   2,   # At least two flags
}


# ===========================================================================
# Internal helpers
# ===========================================================================

def _earnings_proximity(earnings_date_str) -> tuple[int, str | None]:
    """Return (points, label) based on how close earnings are to today."""
    if not earnings_date_str:
        return 0, None
    try:
        ed      = datetime.strptime(str(earnings_date_str), "%Y-%m-%d").date()
        days_to = (ed - date.today()).days
        if -3 <= days_to <= 1:
            return CAT_W["earnings_reported"], "earnings reported / today"
        elif days_to <= 7:
            return CAT_W["earnings_this_week"], "earnings this week"
        elif days_to <= 30:
            return CAT_W["earnings_upcoming"], "earnings upcoming"
    except (ValueError, TypeError):
        pass
    return 0, None


def _scan_keywords(text: str) -> tuple[int, list[str]]:
    """
    Scan catalyst_summary for keyword signal categories.
    Returns (total_pts, list_of_labels).
    Each category can only fire once — no double-counting.
    """
    t      = text.lower()
    pts    = 0
    labels = []

    if any(w in t for w in _ANALYST_UPGRADE):
        pts += CAT_W["analyst_upgrade"]
        labels.append("analyst upgrade")

    # Only score downgrade if no upgrade also present
    elif any(w in t for w in _ANALYST_DOWNGRADE):
        pts += CAT_W["analyst_downgrade"]
        labels.append("analyst downgrade")

    if any(w in t for w in _MAJOR_SIGNALS):
        pts += CAT_W["major_event"]
        labels.append("major event")

    if any(w in t for w in _EARNINGS_SIGNALS):
        pts += CAT_W["earnings_data"]
        labels.append("earnings data")

    return pts, labels


def _catalyst_confidence(categories_fired: list[str], rvol: float) -> str:
    """
    Compute confidence for the catalyst score.

    High:   3+ distinct categories AND volume confirms
    Medium: 2+ categories  OR  (1 high-value category like earnings/major + any volume)
    Low:    everything else
    """
    n = len(categories_fired)

    if n >= CAT_CONF["high_min_categories"] and rvol >= CAT_CONF["high_min_rvol"]:
        return "High"

    high_value = {"earnings reported / today", "earnings this week",
                  "major event", "earnings data"}
    has_high_value = bool(high_value.intersection(set(categories_fired)))

    if n >= CAT_CONF["medium_min_categories"]:
        return "Medium"
    if has_high_value and rvol >= CAT_T["rvol_moderate"]:
        return "Medium"

    return "Low"


def _three_factor_confidence(factor_a: bool, factor_b: bool, factor_c: bool) -> str:
    """
    Generic three-factor confidence calculator used by both momentum and final setup.
    High = all three, Medium = two, Low = one or fewer.
    """
    met = sum([factor_a, factor_b, factor_c])
    if met >= SETUP_CONF["high_requires_all"]:
        return "High"
    if met >= SETUP_CONF["medium_requires"]:
        return "Medium"
    return "Low"


# ===========================================================================
# PUBLIC: Catalyst Score  (1–10)
# ===========================================================================

def compute_catalyst_score(data: dict) -> ScoringResult:
    """
    Score 1–10 for how strong and credible the catalyst is.
    Returns ScoringResult(score, explanation, confidence).

    Positive signals (weighted by CAT_W):
      earnings proximity  — highest weight; defines whether the stock is in play
      major news event    — FDA / SEC / merger / acquisition
      analyst upgrade     — medium weight
      gap size            — confirms something moved the stock premarket
      relative volume     — confirms the market is reacting to the catalyst

    Penalties (CAT_P):
      no news detected    — mystery gap; could reverse with no support
      low volume          — market isn't participating; catalyst not believed
      tiny gap            — stock barely moved; catalyst may not be impactful
    """
    raw        = 0
    categories = []   # Distinct category labels (used for confidence)
    signals    = []   # All human-readable signal strings (used for explanation)

    # ---- Earnings proximity ------------------------------------------------
    e_pts, e_label = _earnings_proximity(data.get("earnings_date"))
    if e_pts:
        raw       += e_pts
        categories.append(e_label)
        signals.append(e_label)

    # ---- Gap size ----------------------------------------------------------
    gap = abs(data.get("gap_pct") or 0)
    if gap >= CAT_T["gap_extreme_pct"]:
        raw += CAT_W["gap_extreme"]
        categories.append("gap")
        signals.append(f"extreme gap ({gap:.1f}%)")
    elif gap >= CAT_T["gap_large_pct"]:
        raw += CAT_W["gap_large"]
        categories.append("gap")
        signals.append(f"large gap ({gap:.1f}%)")
    elif gap >= CAT_T["gap_moderate_pct"]:
        raw += CAT_W["gap_moderate"]
        categories.append("gap")
        signals.append(f"gap {gap:.1f}%")

    # ---- Relative volume ---------------------------------------------------
    rvol = data.get("rel_volume") or 0
    if rvol >= CAT_T["rvol_extreme"]:
        raw += CAT_W["rvol_extreme"]
        categories.append("volume")
        signals.append(f"extreme volume ({rvol:.1f}x)")
    elif rvol >= CAT_T["rvol_high"]:
        raw += CAT_W["rvol_high"]
        categories.append("volume")
        signals.append(f"high volume ({rvol:.1f}x)")
    elif rvol >= CAT_T["rvol_moderate"]:
        raw += CAT_W["rvol_moderate"]
        categories.append("volume")
        signals.append(f"volume {rvol:.1f}x avg")

    # ---- Keyword scan (analyst / major event / earnings data) --------------
    catalyst_text         = data.get("catalyst_summary") or ""
    kw_pts, kw_labels     = _scan_keywords(catalyst_text)
    raw                  += kw_pts
    categories.extend(kw_labels)
    signals.extend(kw_labels)

    # ---- Penalties ---------------------------------------------------------
    no_news = (not kw_labels
               and not e_label
               and gap < CAT_T["gap_extreme_pct"])
    if no_news:
        raw += CAT_P["no_news"]
        signals.append("no news detected")

    if rvol < CAT_T["rvol_low"]:
        raw += CAT_P["low_volume"]
        signals.append(f"low volume ({rvol:.1f}x avg)")

    if gap < CAT_T["gap_tiny_pct"]:
        raw += CAT_P["tiny_gap"]
        signals.append("tiny gap")

    # ---- Build result ------------------------------------------------------
    score = min(max(raw, 1), 10)

    unique_cats = list(dict.fromkeys(categories))
    confidence  = _catalyst_confidence(unique_cats, rvol)

    top = ", ".join(s for s in signals if "no news" not in s and "low volume" not in s
                    and "tiny gap" not in s)[:80]
    penalties = ", ".join(s for s in signals if any(
        neg in s for neg in ["no news", "low volume", "tiny gap"]))

    if score >= 8:
        label = "Strong catalyst"
    elif score >= 6:
        label = "Solid catalyst"
    elif score >= 4:
        label = "Moderate catalyst"
    elif score >= 2:
        label = "Weak catalyst"
    else:
        label = "No catalyst"

    explanation = label
    if top:
        explanation += f": {top}"
    if penalties:
        explanation += f" | Penalties: {penalties}"

    return ScoringResult(score, explanation, confidence)


# ===========================================================================
# PUBLIC: Momentum Score  (1–10)
# ===========================================================================

def compute_momentum_score(data: dict) -> ScoringResult:
    """
    Score 1–10 for live intraday momentum.

    Two tiers:

    STRUCTURE (primary — price action only, no rvol/catalyst gate):
      orb_hold            (+3) — 2+ consecutive closes above ORB high
      trend_structure     (+3) — higher highs + higher lows (VWAP not required)
      strong_candle_bodies(+1) — last 3 candles body > 50% of range

    PARTICIPATION BONUS (secondary — reduces confidence if absent, does NOT block signal):
      price_above_vwap    (+1) — price cleared session VWAP
      rvol >= 1.0         (+2) — market volume confirming the move

    Momentum Runner fires when structure_momentum_score >= MOM_T["structure_threshold"],
    computed in mock_data.py.  Low rvol or below-VWAP reduces confidence to Low/Medium
    but does NOT block the Momentum Runner classification — the trader still sees the
    setup, with an entry_note explaining the specific risk (low volume / below VWAP).
    """
    if data.get("trade_bias") == "Avoid":
        return ScoringResult(1, "Avoid — momentum trading not applicable", "Low")

    structure_raw = 0
    bonus_raw     = 0
    parts         = []
    risk_notes    = []

    # ── Structure signals (primary) ──────────────────────────────────────────
    if data.get("orb_hold"):
        structure_raw += MOM_W["orb_hold"]
        parts.append("ORB hold (2+ candles)")
    orb_ok = bool(data.get("orb_hold"))

    if data.get("trend_structure"):
        structure_raw += MOM_W["trend_structure"]
        parts.append("trend structure (HH+HL)")
    trend_ok = bool(data.get("trend_structure"))

    if data.get("strong_candle_bodies"):
        structure_raw += MOM_W["strong_bodies"]
        parts.append("strong candle bodies")
    bodies_ok = bool(data.get("strong_candle_bodies"))

    # ── Participation bonuses (secondary) ────────────────────────────────────
    rvol = data.get("rel_volume") or 0
    if data.get("price_above_vwap"):
        bonus_raw += MOM_W["price_above_vwap"]
        parts.append("above VWAP")
    else:
        risk_notes.append("below VWAP")
    vwap_ok = bool(data.get("price_above_vwap"))

    if rvol >= MOM_T["rvol_min"]:
        bonus_raw += MOM_W["rvol_ok"]
        parts.append(f"volume {rvol:.1f}x avg")
    else:
        risk_notes.append(f"low volume ({rvol:.1f}x avg)")
    rvol_ok = rvol >= MOM_T["rvol_min"]

    # ── Build score ───────────────────────────────────────────────────────────
    raw   = structure_raw + bonus_raw
    score = min(max(raw, 1), 10)

    # Confidence: driven by participation quality, not structure count.
    # Structure alone gives Low confidence (signal present, not confirmed).
    # Participation upgrades it — both factors = High, one = Medium.
    structure_present = structure_raw >= MOM_T["structure_threshold"]
    if structure_present and vwap_ok and rvol_ok:
        confidence = "High"
    elif structure_present and (vwap_ok or rvol_ok):
        confidence = "Medium"
    elif structure_present:
        confidence = "Low"   # valid signal — warn trader, don't block
    else:
        confidence = "Low"

    if score >= 8:
        label = "High momentum"
    elif score >= 6:
        label = "Strong momentum"
    elif score >= 4:
        label = "Moderate momentum"
    elif score >= 2:
        label = "Weak momentum"
    else:
        label = "No momentum"

    explanation = label
    if parts:
        explanation += ": " + ", ".join(parts)
    if risk_notes:
        explanation += " | Caution: " + ", ".join(risk_notes)

    return ScoringResult(score, explanation, confidence)


# ===========================================================================
# PUBLIC: Order Block Status
# ===========================================================================

def compute_order_block(data: dict) -> str:
    """
    Classify the dominant order block as Demand, Supply, or Neutral.

    Order blocks are institutional price zones where large buying (Demand) or
    selling (Supply) previously occurred.  Without tick-level candle data we
    approximate from premarket structure:

    Demand  (bullish): gap up + price cleared prior day high + volume confirms
    Supply  (bearish): gap down + price broke prior day low + volume confirms
    Neutral: everything else (consolidation, inside-day, weak volume)

    TUNING: OB_T["rvol_min"] controls the volume confirmation threshold.
    """
    if data.get("trade_bias") == "Avoid":
        return "Neutral"

    gap      = data.get("gap_pct") or 0
    rvol     = data.get("rel_volume") or 0
    current  = data.get("current_price") or 0
    prev_high = data.get("prev_day_high") or 0
    prev_low  = data.get("prev_day_low") or 0

    # Demand block: gap up, cleared prior day high, volume confirms buyers
    if gap > 0 and prev_high and current > prev_high and rvol >= OB_T["rvol_min"]:
        return "Demand"

    # Supply block: gap down, broke prior day low, volume confirms sellers
    if gap < 0 and prev_low and current < prev_low and rvol >= OB_T["rvol_min"]:
        return "Supply"

    return "Neutral"


# ===========================================================================
# PUBLIC: Entry Quality
# ===========================================================================

def compute_entry_quality(data: dict) -> str:
    """
    Classify entry quality as Perfect, Okay, or Extended.

    Uses the premarket midpoint as a VWAP proxy (no live VWAP without intraday feed).
    Incorporates order block alignment for confirming "Perfect" entries.

    Perfect:  price near VWAP proxy AND aligned with order block, or very close
              to PM extreme in the right direction — tight stop, clear R:R
    Extended: price far from VWAP proxy AND large gap already played — chasing
    Okay:     everything else

    TUNING: See ENTRY_T dict at module level.
    Pre-requisite: data["order_block"] must be set before calling this function.
    """
    if data.get("trade_bias") == "Avoid":
        return "Okay"

    current   = data.get("current_price") or 0
    pm_high   = data.get("premarket_high") or 0
    pm_low    = data.get("premarket_low") or 0
    gap       = abs(data.get("gap_pct") or 0)
    bias      = data.get("trade_bias") or "Neutral"
    ob        = data.get("order_block") or "Neutral"

    if not current:
        return "Okay"

    # VWAP proxy: midpoint of premarket range
    if pm_high and pm_low:
        vwap_proxy = (pm_high + pm_low) / 2
    else:
        vwap_proxy = data.get("prev_close") or current

    vwap_dist = abs(current - vwap_proxy) / current

    # ---- Momentum Breakout override — checked before Extended ----------------
    # If all four breakout conditions are confirmed (price above ORB high for 3+
    # candles, volume increasing, price above VWAP), the "extended" label is
    # misleading — the breakout IS the entry signal. Treat as Okay.
    if data.get("momentum_breakout"):
        return "Okay"

    # ---- Momentum Runner override — force Extended to signal risk ------------
    # Price has cleared ORB high and is running. Entry IS extended by definition.
    # We label it explicitly so the trader sees the warning. The signal is NOT
    # blocked — compute_exec_state has a dedicated fast path for momentum_runner.
    if data.get("momentum_runner"):
        return "Extended"

    # ---- Extended check (worst case — do first) ----------------------------
    # Extended = far from VWAP AND the gap has already played out significantly
    if vwap_dist > ENTRY_T["vwap_extended_dist"] and gap > ENTRY_T["gap_extended"]:
        return "Extended"

    # Extended = price has blown past the PM extreme (chasing beyond the range)
    if pm_high and pm_low:
        if bias == "Long Bias" and current > pm_high * (1 + ENTRY_T["pm_past_margin"]):
            return "Extended"
        if bias == "Short Bias" and current < pm_low * (1 - ENTRY_T["pm_past_margin"]):
            return "Extended"

    # ---- Perfect check -----------------------------------------------------
    # Near PM extreme in the right direction (tight stop available)
    near_pm_high = pm_high and abs(current - pm_high) / pm_high <= ENTRY_T["near_pm_margin"]
    near_pm_low  = pm_low  and abs(current - pm_low)  / pm_low  <= ENTRY_T["near_pm_margin"]

    # Very close to VWAP proxy (could go either way — need OB to confirm direction)
    near_vwap = vwap_dist <= ENTRY_T["vwap_perfect_dist"]

    # Order block aligned with bias
    ob_aligned = (
        (ob == "Demand" and bias == "Long Bias") or
        (ob == "Supply" and bias == "Short Bias")
    )

    near_entry = (
        (bias == "Long Bias"  and near_pm_high) or
        (bias == "Short Bias" and near_pm_low)  or
        near_vwap
    )

    if near_entry and ob_aligned:
        return "Perfect"

    # ---- Okay (default) ----------------------------------------------------
    return "Okay"


# ===========================================================================
# PUBLIC: ORB Readiness
# ===========================================================================

def compute_orb_readiness(data: dict) -> str:
    """
    Return "YES" or "NO" — is this stock ready for an Opening Range Breakout trade?

    ORB requires ALL of:
    1. Momentum ≥ ORB_T["momentum_min"]  (enough energy to break out)
    2. Relative volume ≥ ORB_T["rvol_min"]  (market participation confirmed)
    3. Clean structure (price NOT inside prior day range with weak gap)
    4. Entry NOT extended  (chase risk too high at open)

    Pre-requisites: data["momentum_score"] and data["entry_quality"] must be set.
    """
    if data.get("trade_bias") == "Avoid":
        return "NO"

    momentum_score = data.get("momentum_score") or 0
    rvol           = data.get("rel_volume") or 0
    entry_quality  = data.get("entry_quality") or "Okay"
    current        = data.get("current_price") or 0
    prev_high      = data.get("prev_day_high") or 0
    prev_low       = data.get("prev_day_low") or 0
    gap            = data.get("gap_pct") or 0

    # Must have enough momentum
    if momentum_score < ORB_T["momentum_min"]:
        return "NO"

    # Must have volume confirming the move
    if rvol < ORB_T["rvol_min"]:
        return "NO"

    # Structure must be clear: price stuck inside prior day range with weak gap = no ORB
    if prev_high and prev_low and current:
        inside_day = prev_low < current < prev_high
        if inside_day and abs(gap) < ORB_T["inside_gap_limit"]:
            return "NO"

    # Entry must not be extended — chasing at open destroys R:R
    if entry_quality == "Extended":
        return "NO"

    return "YES"


# ===========================================================================
#  ORB PRICE LEVELS — thresholds
#  Determines proximity of current price to the Opening Range Breakout levels.
#
#  ORB High / Low come from raw market data (9:30–10:00 first-30-min range).
#  In premarket or mock mode, orb_high / orb_low are set explicitly in the data
#  dict before this function is called.
# ===========================================================================

ORB_LEVEL_T = {
    "near_pct":  0.008,  # Within 0.8% of ORB level = "approaching" (NEAR_HIGH / NEAR_LOW)
                         # Tighten to 0.005 for stricter proximity alerts.
}

ORB_STATUSES = ["ABOVE", "NEAR_HIGH", "INSIDE", "NEAR_LOW", "BELOW", "NO_ORB"]


def compute_orb_price_status(data: dict) -> str:
    """
    Classify where the current price sits relative to the ORB levels.

    Returns one of:
      ABOVE      — price above ORB high (breakout confirmed or gap above)
      NEAR_HIGH  — price within ORB_LEVEL_T["near_pct"] below ORB high (approaching)
      INSIDE     — price between ORB low and ORB high (building the range)
      NEAR_LOW   — price within ORB_LEVEL_T["near_pct"] above ORB low (approaching)
      BELOW      — price below ORB low (breakdown confirmed)
      NO_ORB     — orb_high / orb_low not yet established

    Pre-requisite: data["orb_high"] and data["orb_low"] must be set.
    These come from raw market data, not computed — they should be populated by the
    live data feed (9:30–10:00 first 30 minutes) or explicitly set in mock data.

    TUNING: increase ORB_LEVEL_T["near_pct"] to catch approaches earlier.
    """
    orb_high = data.get("orb_high")
    orb_low  = data.get("orb_low")
    current  = data.get("current_price") or 0

    if not orb_high or not orb_low or not current:
        return "NO_ORB"

    # Price cleared ORB high — bullish breakout
    if current >= orb_high:
        return "ABOVE"

    # Price broke below ORB low — bearish breakdown
    if current <= orb_low:
        return "BELOW"

    # Approaching ORB high from below
    dist_to_high = (orb_high - current) / orb_high
    if dist_to_high <= ORB_LEVEL_T["near_pct"]:
        return "NEAR_HIGH"

    # Approaching ORB low from above
    dist_to_low = (current - orb_low) / orb_low
    if dist_to_low <= ORB_LEVEL_T["near_pct"]:
        return "NEAR_LOW"

    return "INSIDE"


# ===========================================================================
#  EXECUTION STATE — thresholds
#  Controls when a setup transitions from watching → ready → actionable.
# ===========================================================================

EXEC_T = {
    # TRIGGERED: entry zone reached, volume confirming, ORB conditions met
    "triggered_rvol":     2.0,   # Minimum rvol for a confirmed trigger

    # READY: ORB formed and setup is strong enough to watch closely
    "ready_momentum":     6,     # Minimum momentum score for READY state

    # Proximity to PM extreme: how close current price must be to PM high/low
    # for "TRIGGERED" to fire (fraction of price, e.g. 0.015 = within 1.5%)
    "trigger_pm_margin":  0.015,
}

EXEC_STATES = ["TRIGGERED", "READY", "WAIT"]


# ===========================================================================
# PUBLIC: Execution State
# ===========================================================================

def compute_exec_state(data: dict) -> str:
    """
    Return the execution state: "TRIGGERED", "READY", or "WAIT".

    This answers the question: "Should I be actively watching this right now?"

    TRIGGERED — all entry conditions met, actionable at the open:
      • ORB ready = YES  (momentum, volume, structure all confirmed)
      • Entry quality = Perfect  (price at optimal zone, tight stop available)
      • Relative volume ≥ EXEC_T["triggered_rvol"]  (market committing to the move)

    READY — setup is formed and valid, watching for the trigger:
      • ORB ready = YES  (structure confirmed)
      • Momentum ≥ EXEC_T["ready_momentum"]  (energy present)
      • Entry not yet perfect — watching for price to reach the level

    WAIT — setup not formed or too early to act:
      • ORB not ready, momentum too weak, or Avoid bias

    Pre-requisites: data["orb_ready"], data["entry_quality"], data["momentum_score"]
    must already be set before calling this function.

    TUNING: Adjust EXEC_T["triggered_rvol"] to make triggers more/less selective.
    A higher threshold filters out low-conviction breakouts.
    """
    if data.get("trade_bias") == "Avoid":
        return "WAIT"

    orb_ready     = data.get("orb_ready") or "NO"
    entry_quality = data.get("entry_quality") or "Okay"
    momentum      = data.get("momentum_score") or 0
    rvol          = data.get("rel_volume") or 0

    # ORB phase gate — execution state cannot exceed what the session allows:
    #   pre_market : ORB range not yet established; cap at WAIT
    #   forming    : range still being built; TRIGGERED is premature, cap at READY
    #   locked / None : no restriction — full scoring applies
    orb_phase = data.get("orb_phase") or "locked"
    if orb_phase == "pre_market":
        return "WAIT"

    # ── Momentum Breakout fast path ─────────────────────────────────────────
    # All four live conditions confirmed (3+ candles above ORB high, volume
    # increasing, price > VWAP): signal is valid regardless of entry_quality.
    # Still respect the ORB phase gate: cap at READY during the forming window.
    if data.get("momentum_breakout"):
        if orb_phase == "forming":
            return "READY"
        return "TRIGGERED"

    # ── Momentum Runner fast path ────────────────────────────────────────────
    # Lighter breakout (2 candles above ORB, rvol >= 1.0): entry is Extended
    # but the trend is strong — signal is allowed, UI shows warning + reduced size.
    # Only fires when momentum_breakout is False (it's a secondary confirmation).
    if data.get("momentum_runner") and not data.get("momentum_breakout"):
        if orb_phase == "forming":
            return "READY"
        return "TRIGGERED"

    # TRIGGERED: all three pillars confirmed — act at the open
    if (orb_ready == "YES"
            and entry_quality == "Perfect"
            and rvol >= EXEC_T["triggered_rvol"]):
        if orb_phase == "forming":
            # Range still building — signal is forming, not confirmed
            return "READY"
        return "TRIGGERED"

    # READY: ORB formed, momentum strong — watching for entry
    if orb_ready == "YES" and momentum >= EXEC_T["ready_momentum"]:
        return "READY"

    # WAIT: conditions not met yet
    return "WAIT"


# ===========================================================================
# PUBLIC: Final Setup Score  (1–10)
# ===========================================================================

def compute_final_setup_score(data: dict) -> ScoringResult:
    """
    Final composite setup score combining all four strategic factors:
      1. Momentum score   — energy foundation (base score)
      2. ORB readiness    — confirms the structure is in play
      3. Order block      — institutional alignment bonus/penalty
      4. Entry quality    — risk/reward at current price

    Returns ScoringResult(score, explanation, confidence).

    Penalties (FINAL_P):
      order block opposed  — institutional pressure against the trade
      entry extended        — chasing = poor risk/reward
      low momentum          — weak foundation undermines the setup

    Pre-requisites: data["momentum_score"], data["orb_ready"],
                    data["order_block"], data["entry_quality"] must be set.
    """
    if data.get("trade_bias") == "Avoid":
        return ScoringResult(1, "Avoid — do not trade this setup", "Low")

    momentum_score = data.get("momentum_score") or 1
    orb_ready      = data.get("orb_ready") or "NO"
    order_block    = data.get("order_block") or "Neutral"
    entry_quality  = data.get("entry_quality") or "Okay"
    bias           = data.get("trade_bias") or "Neutral"
    rvol           = data.get("rel_volume") or 0

    raw   = momentum_score   # Momentum is the foundation
    parts = [f"momentum {momentum_score}/10"]
    penalties_fired = []

    # ---- ORB Readiness -------------------------------------------------------
    if orb_ready == "YES":
        raw += FINAL_W["orb_ready"]
        parts.append("ORB ready")
        orb_ok = True
    else:
        orb_ok = False

    # ---- Order Block alignment -----------------------------------------------
    long_dir  = bias == "Long Bias"
    short_dir = bias == "Short Bias"
    ob_aligned = (long_dir  and order_block == "Demand") or \
                 (short_dir and order_block == "Supply")
    ob_opposed = (long_dir  and order_block == "Supply") or \
                 (short_dir and order_block == "Demand")

    if ob_aligned:
        raw += FINAL_W["ob_aligned"]
        parts.append(f"{order_block} block aligned")
        structure_ok = True
    elif ob_opposed:
        raw += FINAL_P["ob_opposed"]
        penalties_fired.append("order block opposed")
        structure_ok = False
    else:
        structure_ok = False   # Neutral OB = no signal either way

    # ---- Entry quality -------------------------------------------------------
    if entry_quality == "Perfect":
        raw += FINAL_W["entry_perfect"]
        parts.append("perfect entry")
    elif entry_quality == "Extended":
        if data.get("momentum_runner"):
            # Extended is the expected state for a Momentum Runner — the trend is
            # strong and the signal is intentionally allowed. Suppress the score
            # penalty; the UI shows a warning + reduced-size guidance instead.
            parts.append("extended — Momentum Runner (reduced size)")
        else:
            raw += FINAL_P["entry_extended"]
            penalties_fired.append("extended entry — chase risk")

    # ---- Low momentum penalty ------------------------------------------------
    if momentum_score < FINAL_T["low_momentum"]:
        raw += FINAL_P["low_momentum"]
        penalties_fired.append("weak momentum base")

    # ---- Build result --------------------------------------------------------
    score      = min(max(raw, 1), 10)
    volume_ok  = rvol >= MOM_T["rvol_min"]
    confidence = _three_factor_confidence(volume_ok, orb_ok, structure_ok)

    if score >= 8:
        label = "A+ setup"
    elif score >= 6:
        label = "Quality setup"
    elif score >= 4:
        label = "Developing setup"
    else:
        label = "Weak setup"

    explanation = label + ": " + ", ".join(parts)
    if penalties_fired:
        explanation += " | Penalties: " + ", ".join(penalties_fired)

    return ScoringResult(score, explanation, confidence)


# ===========================================================================
# PUBLIC: Setup Type classification
# ===========================================================================

def compute_setup_type(data: dict) -> str:
    """
    Classify the dominant setup type from price / gap / volume patterns.
    Returns one of SETUP_TYPES. First match wins.
    """
    gap        = data.get("gap_pct") or 0
    rvol       = data.get("rel_volume") or 1
    current    = data.get("current_price") or 0
    prev_high  = data.get("prev_day_high") or 0
    prev_low   = data.get("prev_day_low") or 0
    prev_close = data.get("prev_close") or 0
    bias       = data.get("trade_bias") or "Neutral"

    if bias == "Avoid":
        return "No Setup"

    # Momentum Breakout: all four live breakout conditions confirmed — label first
    if data.get("momentum_breakout"):
        return "Momentum Breakout"

    # Momentum Runner: lighter breakout (2 candles above ORB, rvol >= 1.0)
    # Only fires when full Breakout is not confirmed — it's the next tier down.
    if data.get("momentum_runner"):
        return "Momentum Runner"

    # Gap and Go: meaningful gap up, volume confirming, price cleared prior high
    if gap >= 4 and rvol >= 1.5 and prev_high and current > prev_high:
        return "Gap and Go"

    # Breakdown: meaningful gap down with volume confirming selling pressure
    if gap <= -4 and rvol >= 1.5:
        return "Breakdown"

    # Range Break: price sitting on a key prior-day level
    if prev_high and prev_low:
        near_high = abs(current - prev_high) / prev_high < 0.025
        near_low  = abs(current - prev_low)  / prev_low  < 0.025
        if (near_high or near_low) and rvol >= 1.2:
            return "Range Break"

    # VWAP Reclaim: flat/small gap, price very close to prior close
    if prev_close and abs(gap) <= 2.0 and rvol >= 0.8:
        if abs(current - prev_close) / prev_close < 0.015:
            return "VWAP Reclaim"

    # ORB: some directional setup but not yet defined
    if abs(gap) >= 1.5 or rvol >= 1.3:
        return "ORB"

    return "No Setup"


# ===========================================================================
# PUBLIC: Catalyst breakdown  (detail page)
# ===========================================================================

def catalyst_score_breakdown(data: dict) -> list[tuple[str, int]]:
    """
    Return a list of (label, points) tuples showing each signal's exact contribution.
    Uses the same weight constants as compute_catalyst_score so they always match.
    """
    breakdown = []

    # Earnings
    e_pts, e_label = _earnings_proximity(data.get("earnings_date"))
    if e_pts:
        breakdown.append((e_label.capitalize(), e_pts))

    # Gap
    gap = abs(data.get("gap_pct") or 0)
    if gap >= CAT_T["gap_extreme_pct"]:
        breakdown.append((f"Extreme gap ({gap:.1f}%)", CAT_W["gap_extreme"]))
    elif gap >= CAT_T["gap_large_pct"]:
        breakdown.append((f"Large gap ({gap:.1f}%)", CAT_W["gap_large"]))
    elif gap >= CAT_T["gap_moderate_pct"]:
        breakdown.append((f"Gap {gap:.1f}%", CAT_W["gap_moderate"]))

    # Volume
    rvol = data.get("rel_volume") or 0
    if rvol >= CAT_T["rvol_extreme"]:
        breakdown.append((f"Extreme volume ({rvol:.1f}x avg)", CAT_W["rvol_extreme"]))
    elif rvol >= CAT_T["rvol_high"]:
        breakdown.append((f"High volume ({rvol:.1f}x avg)", CAT_W["rvol_high"]))
    elif rvol >= CAT_T["rvol_moderate"]:
        breakdown.append((f"Above-avg volume ({rvol:.1f}x avg)", CAT_W["rvol_moderate"]))

    # Keywords
    catalyst_text     = data.get("catalyst_summary") or ""
    _, kw_labels      = _scan_keywords(catalyst_text)
    kw_pts_map        = {
        "analyst upgrade":   CAT_W["analyst_upgrade"],
        "analyst downgrade": CAT_W["analyst_downgrade"],
        "major event":       CAT_W["major_event"],
        "earnings data":     CAT_W["earnings_data"],
    }
    for lbl in kw_labels:
        breakdown.append((lbl.capitalize(), kw_pts_map.get(lbl, 1)))

    # Penalties
    no_news = (not kw_labels and not _earnings_proximity(data.get("earnings_date"))[0]
               and gap < CAT_T["gap_extreme_pct"])
    if no_news:
        breakdown.append(("No news detected", CAT_P["no_news"]))
    if rvol < CAT_T["rvol_low"]:
        breakdown.append((f"Low volume ({rvol:.1f}x avg)", CAT_P["low_volume"]))
    if gap < CAT_T["gap_tiny_pct"]:
        breakdown.append(("Tiny gap", CAT_P["tiny_gap"]))

    if not breakdown:
        breakdown.append(("No signals detected", 0))

    return breakdown
