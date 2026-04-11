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

import json as _json
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
# Keyword signal lists  (legacy — used as fallback when catalyst_category absent)
# ===========================================================================

_ANALYST_UPGRADE   = ["upgrade", "raises price target", "price target raised", "outperform",
                       "overweight", "buy rating", "strong buy", "initiated", "reiterate",
                       "maintains buy", "added to conviction", "initiates with buy",
                       "boosts price target", "lifts to outperform"]
_ANALYST_DOWNGRADE = ["downgrade", "underperform", "underweight", "sell rating",
                       "lowers price target", "cut to neutral", "removed from conviction",
                       "cuts price target", "trims price target", "reduced to sell"]
_MAJOR_SIGNALS     = ["sec", "fda", "merger", "acquisition", "buyout", "settlement",
                       "investigation", "recall", "bankruptcy", "delisting", "halt",
                       "indictment", "class action", "takeover", "going private",
                       "government contract", "defense contract", "dod contract",
                       "guidance raised", "guidance cut", "partnership", "joint venture"]
_EARNINGS_SIGNALS  = ["earnings", "eps", "revenue", "beat estimates", "missed estimates",
                       "quarterly results", "q1", "q2", "q3", "q4", "annual results",
                       "guidance", "beat", "miss", "raised guidance", "lowered guidance",
                       "earnings beat", "earnings miss"]


# ===========================================================================
#  CATALYST SCORE — weights and thresholds
# ===========================================================================

# ---- Per-category weights (matches news_fetcher.CATALYST_CATEGORIES) -------
# Used by _score_from_categories() when pre-parsed categories are available.
_CAT_WEIGHTS: dict[str, int] = {
    "earnings_beat":       4,
    "earnings_miss":       3,   # negative result still moves stock
    "analyst_upgrade":     3,
    "analyst_downgrade":   2,
    "partnership_deal":    3,
    "acquisition_merger":  5,
    "government_contract": 4,
    "product_launch":      2,
    "fda":                 5,
    "sec_legal":           3,
    "guidance_raise":      4,
    "guidance_cut":        3,
}

_CAT_DISPLAY: dict[str, str] = {
    "earnings_beat":       "earnings beat",
    "earnings_miss":       "earnings miss",
    "analyst_upgrade":     "analyst upgrade",
    "analyst_downgrade":   "analyst downgrade",
    "partnership_deal":    "partnership/deal",
    "acquisition_merger":  "acquisition/merger",
    "government_contract": "government contract",
    "product_launch":      "product launch",
    "fda":                 "FDA/regulatory approval",
    "sec_legal":           "SEC/legal issue",
    "guidance_raise":      "guidance raise",
    "guidance_cut":        "guidance cut",
}

# ---- Legacy positive weights (used when no pre-parsed categories) ----------
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

# ── Zone-aware entry thresholds ─────────────────────────────────────────────
#   Applied on top of base ENTRY_T logic when zone data is present.
ZONE_ENTRY_T = {
    # Supply directly overhead: supply_bottom within this % of current price.
    # Below this threshold we downgrade a "Perfect" entry to "Okay",
    # because tight stop potential is negated by the overhead resistance.
    "supply_nearby_pct":  0.03,   # 3% — supply within 3% = nearby

    # Deep supply: supply_bottom within 1% — cancel "Perfect", force "Okay"
    # or downgrade to "Extended" only if price is already in the supply zone.
    "supply_immediate_pct": 0.01,  # 1%
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


def _score_from_categories(categories: list[str]) -> tuple[int, list[str]]:
    """
    Score from pre-parsed catalyst_category list (from news_fetcher).
    Returns (total_pts, list_of_display_labels).
    Each category contributes at most once.
    """
    pts    = 0
    labels = []
    seen: set[str] = set()
    for cat in categories:
        if cat in _CAT_WEIGHTS and cat not in seen:
            pts += _CAT_WEIGHTS[cat]
            labels.append(_CAT_DISPLAY.get(cat, cat))
            seen.add(cat)
    return pts, labels


def _scan_keywords(text: str) -> tuple[int, list[str]]:
    """
    Legacy keyword scanner — used when pre-parsed categories are not available.
    Scans catalyst_summary for known signal patterns.
    Returns (total_pts, list_of_labels).  Each category fires at most once.
    """
    t      = text.lower()
    pts    = 0
    labels = []

    if any(w in t for w in _ANALYST_UPGRADE):
        pts += CAT_W["analyst_upgrade"]
        labels.append("analyst upgrade")
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

    high_value = {
        "earnings reported / today", "earnings this week",
        "major event", "earnings data",
        # new category display labels
        "earnings beat", "earnings miss",
        "acquisition/merger", "FDA/regulatory approval",
        "government contract", "guidance raise",
    }
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

    # ---- Keyword / category scoring ----------------------------------------
    # Prefer pre-parsed categories from news_fetcher; fall back to keyword scan.
    raw_cats = data.get("catalyst_category")
    if raw_cats:
        try:
            parsed_cats = _json.loads(raw_cats) if isinstance(raw_cats, str) else list(raw_cats)
            kw_pts, kw_labels = _score_from_categories(parsed_cats)
        except Exception:
            catalyst_text = data.get("catalyst_summary") or ""
            kw_pts, kw_labels = _scan_keywords(catalyst_text)
    else:
        catalyst_text = data.get("catalyst_summary") or ""
        kw_pts, kw_labels = _scan_keywords(catalyst_text)

    raw += kw_pts
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

    # ---- Zone-aware checks (applied before Perfect) ------------------------
    zone_location        = data.get("zone_location") or "BETWEEN ZONES"
    dist_to_supply_pct   = data.get("distance_to_supply_pct")   # % from price to supply bottom (positive = above)
    in_supply_zone       = data.get("in_supply_zone", False)
    in_demand_zone       = data.get("in_demand_zone", False)

    # If price is IN a supply zone on a long setup → DO NOT CHASE (Extended)
    if in_supply_zone and bias == "Long Bias":
        return "Extended"

    # If price is ABOVE SUPPLY on a long setup → Extended (chasing through resistance)
    if zone_location == "ABOVE SUPPLY" and bias == "Long Bias":
        # Only if also extended from VWAP (both conditions must hold)
        if vwap_dist > ENTRY_T["vwap_extended_dist"] * 0.5:
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

    # Zone gate: "Perfect" requires no immediate supply overhead for long setups.
    # If supply is within ZONE_ENTRY_T["supply_nearby_pct"], downgrade to "Okay".
    if near_entry and ob_aligned:
        if bias == "Long Bias" and dist_to_supply_pct is not None:
            if dist_to_supply_pct <= ZONE_ENTRY_T["supply_nearby_pct"] * 100:
                return "Okay"   # Good setup but supply too close — not "Perfect"
        return "Perfect"

    # ---- IN DEMAND bonus ---------------------------------------------------
    # Price pulled back into a demand zone on a long setup — upgrade to Okay
    # even without OB alignment (zone acts as the confirmation).
    if in_demand_zone and bias == "Long Bias":
        return "Okay"   # At worst Okay; never downgraded further for demand entries

    # ---- APPROACHING SUPPLY downgrade for longs ----------------------------
    # Price is within approach_pct of supply — note this but keep as Okay.
    # (Extended is reserved for confirmed supply zone entries, handled above.)

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

    # ── Zone supply gate for LONG setups ─────────────────────────────────────
    # If strong supply sits directly overhead, downgrade TRIGGERED → READY.
    # The trader should see the setup but not be signalled to execute blindly
    # into a known seller zone.
    bias               = data.get("trade_bias") or "Neutral"
    zone_location      = data.get("zone_location") or "BETWEEN ZONES"
    dist_to_supply_pct = data.get("distance_to_supply_pct")   # % gap to supply bottom
    in_supply_zone     = data.get("in_supply_zone", False)

    _supply_cap = False   # will cap exec state to READY when True
    if bias == "Long Bias":
        if in_supply_zone:
            # Price IS inside a supply zone — downgrade all the way to WAIT
            return "WAIT"
        if zone_location == "APPROACHING SUPPLY":
            _supply_cap = True   # supply within 3% — cap at READY, not TRIGGERED
        elif dist_to_supply_pct is not None and 0 < dist_to_supply_pct <= 1.5:
            _supply_cap = True   # supply within 1.5% — immediate resistance

    # ── Momentum Breakout fast path ─────────────────────────────────────────
    # All four live conditions confirmed (3+ candles above ORB high, volume
    # increasing, price > VWAP): signal is valid regardless of entry_quality.
    # Still respect the ORB phase gate: cap at READY during the forming window.
    if data.get("momentum_breakout"):
        if orb_phase == "forming" or _supply_cap:
            return "READY"
        return "TRIGGERED"

    # ── Momentum Runner fast path ────────────────────────────────────────────
    # Lighter breakout (2 candles above ORB, rvol >= 1.0): entry is Extended
    # but the trend is strong — signal is allowed, UI shows warning + reduced size.
    # Only fires when momentum_breakout is False (it's a secondary confirmation).
    if data.get("momentum_runner") and not data.get("momentum_breakout"):
        if orb_phase == "forming" or _supply_cap:
            return "READY"
        return "TRIGGERED"

    # TRIGGERED: all three pillars confirmed — act at the open
    if (orb_ready == "YES"
            and entry_quality == "Perfect"
            and rvol >= EXEC_T["triggered_rvol"]):
        if orb_phase == "forming" or _supply_cap:
            # Range still building or supply overhead — signal forming, not confirmed
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

    # ---- Zone bonus / penalty ------------------------------------------------
    zone_location      = data.get("zone_location") or "BETWEEN ZONES"
    in_demand_zone     = data.get("in_demand_zone", False)
    in_supply_zone     = data.get("in_supply_zone", False)
    dist_to_supply_pct = data.get("distance_to_supply_pct")

    if bias == "Long Bias":
        if in_demand_zone or zone_location in ("IN DEMAND", "IN BULLISH OB"):
            raw += 1
            parts.append("demand zone support")
        elif zone_location == "APPROACHING SUPPLY":
            raw -= 1
            penalties_fired.append("supply zone approaching")
        elif in_supply_zone or zone_location in ("IN SUPPLY", "ABOVE SUPPLY"):
            raw -= 2
            penalties_fired.append("overhead supply — do not chase")
        elif dist_to_supply_pct is not None and 0 < dist_to_supply_pct <= 2.0:
            raw -= 1
            penalties_fired.append(f"supply {dist_to_supply_pct:.1f}% above")

    elif bias == "Short Bias":
        if in_supply_zone or zone_location in ("IN SUPPLY", "IN BEARISH OB"):
            raw += 1
            parts.append("supply zone resistance")
        elif zone_location in ("IN DEMAND",):
            raw -= 1
            penalties_fired.append("demand zone below — short risk")

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

    # News signal keywords / categories
    raw_cats = data.get("catalyst_category")
    if raw_cats:
        try:
            parsed_cats = _json.loads(raw_cats) if isinstance(raw_cats, str) else list(raw_cats)
            _, kw_labels = _score_from_categories(parsed_cats)
            for lbl in kw_labels:
                # Look up weight by reverse-matching display label → key
                cat_key = next((k for k, v in _CAT_DISPLAY.items() if v == lbl), None)
                pts = _CAT_WEIGHTS.get(cat_key, 1) if cat_key else 1
                breakdown.append((lbl.capitalize(), pts))
        except Exception:
            kw_labels = []
    else:
        catalyst_text = data.get("catalyst_summary") or ""
        _, kw_labels  = _scan_keywords(catalyst_text)
        kw_pts_map    = {
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

    return breakdown


# ===========================================================================
# SWING TRADING — Setup Types, Status Labels, Score, Trade Plan
# ===========================================================================

SWING_SETUP_TYPES = [
    "Pullback to 20 EMA",
    "Pullback to 50 EMA",
    "Near 61.8% Retracement",
    "Near 50% Retracement",
    "Order Block Test",
    "Breakout Retest",
    "Trend Continuation",
    "At Resistance — Avoid",
    "Extended — Wait",
    "Weak Structure — Avoid",
    "No Setup",
]

SWING_STATUSES = [
    # ── Current 4-mode labels ─────────────────────────────────────────────────
    "READY — LEVEL HOLDS",        # Mode 2: confirmed at level (15m + vol ≥ 1.2×)
    "PRE-CONFIRMATION",           # Mode 1: near key level, trend aligned, awaiting entry
    "TREND CONTINUATION",         # Mode 3: structure breaking higher/lower, breakout entry
    "WAIT",                       # Mode 4: no valid setup / no edge
    # ── Legacy values (kept for DB backward compat) ───────────────────────────
    "GOOD SWING CANDIDATE",
    "READY IF LEVEL HOLDS",
    "WAIT FOR 15M CONFIRMATION",
    "WAIT FOR PULLBACK",
    "TOO EXTENDED",
    "NOT ENOUGH EDGE",
    "AVOID — AT RESISTANCE",
    "AVOID — WEAK STRUCTURE",
]

# ── Thresholds ───────────────────────────────────────────────────────────────

SWING_T = {
    # EMA proximity thresholds (% from price to EMA)
    "pullback_ema20_pct":     3.0,   # within 3% of 20 EMA → pullback zone
    "pullback_ema50_pct":     4.0,   # within 4% of 50 EMA → deeper pullback
    # Fibonacci proximity threshold
    "fib_proximity_pct":      2.0,   # within 2% of fib level
    # Extension risk thresholds (pct_from_ema20, in trend direction)
    "extended_slight_pct":    8.0,   # 8–12%: slightly extended
    "extended_moderate_pct": 12.0,   # 12–15%: extended
    "extended_heavy_pct":    15.0,   # >15%: heavily extended — do not chase
    # Volume thresholds (relative volume)
    "min_rvol_strong":        1.2,   # strong participation
    "min_rvol_avg":           0.7,   # average participation
    # R:R thresholds
    "rr_excellent":           2.5,   # excellent reward/risk
    "rr_good":                2.0,   # good reward/risk
    "rr_ok":                  1.5,   # acceptable reward/risk
}

# ── Weights (7-category model) ────────────────────────────────────────────────
# Max raw = 2.0+2.0+1.5+1.5+1.0+1.0 = 9.0  |  Min raw (max penalty) = -2.0
# Normalize: score = round(1 + (raw + 2) / 11 * 9), clipped to [1, 10]

SWING_W = {
    # ── Category 1: Trend Quality (max 2.0) ──────────────────────────────────
    "trend_full":       2.0,   # 1D strong + 4H confirms + HH/HL structure
    "trend_partial":    1.5,   # 1D strong + partial 4H or structure
    "trend_lean":       1.0,   # 1D lean / mixed signals
    "trend_neutral":    0.5,   # No directional edge
    "trend_against":    0.0,   # Opposing trend direction
    # ── Category 2: Pullback Quality (max 2.0) ───────────────────────────────
    "pullback_high":    2.0,   # Multi-confluence (zone + fib or zone + EMA)
    "pullback_zone":    1.5,   # In demand/supply zone only
    "pullback_fib618":  1.5,   # Near 61.8% Fibonacci retracement
    "pullback_fib50":   1.0,   # Near 50% Fibonacci retracement
    "pullback_ema20":   1.0,   # Pullback to 20 EMA (within 3%)
    "pullback_ema50":   0.8,   # Pullback to 50 EMA (within 4%)
    "pullback_near":    0.5,   # Approaching demand zone (within 3%)
    "pullback_none":    0.0,   # No pullback edge identified
    # ── Category 3: Confirmation Quality (max 1.5) ───────────────────────────
    "confirm_full":     1.5,   # 15m confirmed: higher low + strong candle
    "confirm_partial":  0.75,  # 15m developing: one signal present
    "confirm_none":     0.0,   # 15m shows weakness or breakdown
    "confirm_default":  0.5,   # No 15m data available (neutral, no penalty)
    # ── Category 4: R:R Quality (max 1.5) ────────────────────────────────────
    "rr_excellent":     1.5,   # R:R >= 2.5:1
    "rr_good":          1.0,   # R:R >= 2.0:1
    "rr_ok":            0.5,   # R:R >= 1.5:1 or unknown (neutral default)
    "rr_weak":          0.0,   # R:R < 1.5:1
    # ── Category 5: Volume / Participation (max 1.0) ─────────────────────────
    "vol_strong":       1.0,   # rvol >= 1.2 — strong participation
    "vol_average":      0.5,   # rvol >= 0.7 — average participation
    "vol_weak":         0.0,   # rvol < 0.7 — thin / absent volume
    # ── Category 6: Market Alignment (max 1.0) ───────────────────────────────
    "mkt_aligned":      1.0,   # Trend direction + catalyst confirms move
    "mkt_neutral":      0.5,   # Trend good but no catalyst confirmation
    "mkt_against":      0.0,   # Catalyst / market fighting the direction
    # ── Category 7: Extension Penalty (max −2.0) ─────────────────────────────
    "ext_none":         0.0,   # Not extended (within 8% of 20 EMA)
    "ext_slight":      -0.5,   # 8–12% from 20 EMA in trend direction
    "ext_moderate":    -1.0,   # 12–15% from 20 EMA
    "ext_heavy":       -2.0,   # >15% from 20 EMA — do not chase
}


def compute_swing_score(data: dict) -> ScoringResult:
    """
    7-category weighted swing score normalized to 1–10.

    Categories and max contribution:
      1. Trend Quality       0–2.0  (1D daily + 4H alignment, HH/HL structure)
      2. Pullback Quality    0–2.0  (at key level: demand zone, fib, EMA)
      3. Confirmation        0–1.5  (15m: higher low + strong reversal candle)
      4. R:R Quality         0–1.5  (computed risk/reward from trade plan)
      5. Volume              0–1.0  (relative volume participation)
      6. Market Alignment    0–1.0  (trend direction + catalyst proxy)
      7. Extension Penalty  −2–0    (% above/below 20 EMA in trend direction)

    Raw score range: −2.0 (worst) to 9.0 (best)
    Normalized:  score = round(1 + (raw + 2) / 11 * 9)  →  clipped to [1, 10]

    Grade thresholds:
      A  →  9–10   (high-conviction setup, act on confirmation)
      B  →  7–8    (solid setup, good patience entry)
      C  →  5–6    (developing, wait for better conditions)
      D  →  1–4    (avoid / insufficient edge)

    IMPORTANT: For R:R to score correctly, compute_swing_trade_plan() must run
    BEFORE compute_swing_score() in the pipeline so risk_reward is available.

    New fields consumed (from fetch_swing_data):
        h4_trend       — "Bullish" | "Bullish Lean" | "Neutral" | "Bearish Lean" | "Bearish"
        h4_hh_hl       — bool: 4H higher highs + higher lows
        m15_confirmation — int 0/1/2: 0=none, 1=developing, 2=confirmed
    """
    bias        = data.get("trade_bias") or "Neutral"
    daily_trend = data.get("daily_trend") or "Neutral"
    h4_trend    = data.get("h4_trend") or "Neutral"
    daily_hh_hl = bool(data.get("daily_hh_hl", False))
    h4_hh_hl    = bool(data.get("h4_hh_hl", False))
    daily_lh_ll = bool(data.get("daily_lh_ll", False))
    pct_ema20   = data.get("pct_from_ema20")
    pct_ema50   = data.get("pct_from_ema50")
    fib_50      = data.get("fib_50")
    fib_618     = data.get("fib_618")
    current     = data.get("current_price") or 0
    in_demand   = data.get("in_demand_zone", False)
    in_supply   = data.get("in_supply_zone", False)
    dist_demand = data.get("distance_to_demand_pct")
    m15_conf    = int(data.get("m15_confirmation") or 0)
    risk_reward = data.get("risk_reward")
    rvol        = data.get("rel_volume") or 0
    cat_score   = data.get("catalyst_score") or 0

    if bias == "Avoid":
        return ScoringResult(1, "Avoid — swing analysis not applicable", "Low")

    raw      = 0.0
    parts    = []
    pen_msgs = []

    # ── Category 1: Trend Quality (0–2.0) ────────────────────────────────────
    if bias == "Long Bias":
        trend_str_1d = daily_trend == "Bullish"
        trend_ok_1d  = daily_trend in ("Bullish", "Bullish Lean")
        trend_ok_4h  = h4_trend in ("Bullish", "Bullish Lean")
        structure    = daily_hh_hl or h4_hh_hl

        if trend_str_1d and trend_ok_4h and structure:
            trend_pts = SWING_W["trend_full"]
            parts.append("strong 1D+4H bullish trend + structure")
        elif trend_str_1d and (trend_ok_4h or structure):
            trend_pts = SWING_W["trend_partial"]
            parts.append("1D bullish + 4H/structure partial")
        elif trend_ok_1d:
            trend_pts = SWING_W["trend_lean"]
            parts.append("bullish lean")
        elif daily_trend == "Neutral":
            trend_pts = SWING_W["trend_neutral"]
        else:
            trend_pts = SWING_W["trend_against"]
            pen_msgs.append("against bearish daily trend")

    elif bias == "Short Bias":
        trend_str_1d = daily_trend == "Bearish"
        trend_ok_1d  = daily_trend in ("Bearish", "Bearish Lean")
        trend_ok_4h  = h4_trend in ("Bearish", "Bearish Lean")
        structure    = daily_lh_ll or (not h4_hh_hl and h4_trend in ("Bearish", "Bearish Lean"))

        if trend_str_1d and trend_ok_4h and structure:
            trend_pts = SWING_W["trend_full"]
            parts.append("strong 1D+4H bearish trend + structure")
        elif trend_str_1d and (trend_ok_4h or structure):
            trend_pts = SWING_W["trend_partial"]
            parts.append("1D bearish + 4H/structure partial")
        elif trend_ok_1d:
            trend_pts = SWING_W["trend_lean"]
            parts.append("bearish lean")
        elif daily_trend == "Neutral":
            trend_pts = SWING_W["trend_neutral"]
        else:
            trend_pts = SWING_W["trend_against"]
            pen_msgs.append("against bullish daily trend")
    else:
        trend_pts = SWING_W["trend_neutral"]

    raw += trend_pts

    # ── Category 2: Pullback Quality (0–2.0) ─────────────────────────────────
    # Priority: multi-confluence > zone > 61.8% fib > 50% fib > 20 EMA > 50 EMA > near demand
    pullback_pts = SWING_W["pullback_none"]

    def _fib_prox(level):
        return current and level and abs(current - level) / current * 100 <= SWING_T["fib_proximity_pct"] * 1.5

    if in_demand and bias == "Long Bias":
        if _fib_prox(fib_618) or _fib_prox(fib_50) or (pct_ema20 is not None and abs(pct_ema20) <= SWING_T["pullback_ema20_pct"]):
            pullback_pts = SWING_W["pullback_high"]
            parts.append("demand zone + fib/EMA confluence")
        else:
            pullback_pts = SWING_W["pullback_zone"]
            parts.append("price in demand zone")

    elif in_supply and bias == "Short Bias":
        if _fib_prox(fib_618) or (pct_ema20 is not None and abs(pct_ema20) <= SWING_T["pullback_ema20_pct"]):
            pullback_pts = SWING_W["pullback_high"]
            parts.append("supply zone + fib/EMA confluence")
        else:
            pullback_pts = SWING_W["pullback_zone"]
            parts.append("price in supply zone")

    elif current and fib_618 and abs(current - fib_618) / current * 100 <= SWING_T["fib_proximity_pct"]:
        pullback_pts = SWING_W["pullback_fib618"]
        parts.append("at 61.8% retracement")

    elif current and fib_50 and abs(current - fib_50) / current * 100 <= SWING_T["fib_proximity_pct"]:
        pullback_pts = SWING_W["pullback_fib50"]
        parts.append("at 50% retracement")

    elif pct_ema20 is not None:
        in_ema20_zone = abs(pct_ema20) <= SWING_T["pullback_ema20_pct"]
        correct_ema20 = (bias == "Long Bias" and pct_ema20 >= -3) or (bias == "Short Bias" and pct_ema20 <= 3)
        if in_ema20_zone and correct_ema20:
            pullback_pts = SWING_W["pullback_ema20"]
            parts.append(f"pullback to 20 EMA ({pct_ema20:+.1f}%)")
        elif pct_ema50 is not None:
            in_ema50_zone = abs(pct_ema50) <= SWING_T["pullback_ema50_pct"]
            correct_ema50 = (bias == "Long Bias" and pct_ema50 >= -4) or (bias == "Short Bias" and pct_ema50 <= 4)
            if in_ema50_zone and correct_ema50:
                pullback_pts = SWING_W["pullback_ema50"]
                parts.append(f"pullback to 50 EMA ({pct_ema50:+.1f}%)")

    # Approaching demand zone (not yet in it) — secondary points
    if pullback_pts == 0 and not in_demand and bias == "Long Bias" \
            and dist_demand is not None and 0 < dist_demand <= 3.0:
        pullback_pts = SWING_W["pullback_near"]
        parts.append(f"approaching demand zone ({dist_demand:.1f}% away)")

    # Supply zone on long = structural headwind (noted only, pullback score unchanged)
    if in_supply and bias == "Long Bias":
        pen_msgs.append("in supply zone — overhead resistance")

    raw += pullback_pts

    # ── Category 3: Confirmation Quality (0–1.5) ──────────────────────────────
    # m15_confirmation: 0 = no data, 1 = one signal (developing), 2 = confirmed
    if m15_conf >= 2:
        conf_pts = SWING_W["confirm_full"]
        parts.append("15m confirmed (higher low + strong candle)")
    elif m15_conf == 1:
        conf_pts = SWING_W["confirm_partial"]
        parts.append("15m developing")
    else:
        # No 15m data available — neutral default, no penalty for missing data
        conf_pts = SWING_W["confirm_default"]

    raw += conf_pts

    # ── Category 4: R:R Quality (0–1.5) ──────────────────────────────────────
    # Requires compute_swing_trade_plan() to have run first in the pipeline.
    if risk_reward is not None and risk_reward > 0:
        if risk_reward >= SWING_T["rr_excellent"]:
            rr_pts = SWING_W["rr_excellent"]
            parts.append(f"excellent R:R {risk_reward:.1f}:1")
        elif risk_reward >= SWING_T["rr_good"]:
            rr_pts = SWING_W["rr_good"]
            parts.append(f"good R:R {risk_reward:.1f}:1")
        elif risk_reward >= SWING_T["rr_ok"]:
            rr_pts = SWING_W["rr_ok"]
        else:
            rr_pts = SWING_W["rr_weak"]
            pen_msgs.append(f"weak R:R {risk_reward:.1f}:1")
    else:
        # Trade plan not yet computed or no valid levels — neutral default
        rr_pts = SWING_W["rr_ok"]

    raw += rr_pts

    # ── Category 5: Volume / Participation (0–1.0) ────────────────────────────
    if rvol >= SWING_T["min_rvol_strong"]:
        vol_pts = SWING_W["vol_strong"]
        parts.append(f"strong volume {rvol:.1f}x avg")
    elif rvol >= SWING_T["min_rvol_avg"]:
        vol_pts = SWING_W["vol_average"]
        parts.append(f"volume {rvol:.1f}x avg")
    else:
        vol_pts = SWING_W["vol_weak"]

    raw += vol_pts

    # ── Category 6: Market Alignment (0–1.0) ──────────────────────────────────
    # Proxy: trend direction + catalyst score (no external market API needed)
    trend_aligned = (
        (bias == "Long Bias"  and daily_trend in ("Bullish", "Bullish Lean")) or
        (bias == "Short Bias" and daily_trend in ("Bearish", "Bearish Lean"))
    )
    if trend_aligned and cat_score >= 3:
        mkt_pts = SWING_W["mkt_aligned"]
        parts.append("catalyst supports move")
    elif trend_aligned:
        mkt_pts = SWING_W["mkt_neutral"]
    else:
        mkt_pts = SWING_W["mkt_against"]

    raw += mkt_pts

    # ── Category 7: Extension Penalty (−2.0–0) ────────────────────────────────
    if pct_ema20 is not None:
        ext_dir = (bias == "Long Bias" and pct_ema20 > 0) or (bias == "Short Bias" and pct_ema20 < 0)
        ext_pct = abs(pct_ema20)
        if ext_dir:
            if ext_pct > SWING_T["extended_heavy_pct"]:
                raw += SWING_W["ext_heavy"]
                pen_msgs.append(f"heavily extended {ext_pct:.1f}% from 20 EMA")
            elif ext_pct > SWING_T["extended_moderate_pct"]:
                raw += SWING_W["ext_moderate"]
                pen_msgs.append(f"extended {ext_pct:.1f}% from 20 EMA")
            elif ext_pct > SWING_T["extended_slight_pct"]:
                raw += SWING_W["ext_slight"]
                pen_msgs.append(f"slightly extended {ext_pct:.1f}% from 20 EMA")

    # ── Normalize raw score to 1–10 ───────────────────────────────────────────
    # Max possible raw = 9.0 | Min possible raw = -2.0
    score_float = 1.0 + (raw + 2.0) / 11.0 * 9.0
    score       = int(round(min(10.0, max(1.0, score_float))))

    # ── Confidence ────────────────────────────────────────────────────────────
    trend_ok   = trend_aligned
    level_ok   = pullback_pts > 0
    signal_ok  = m15_conf >= 1 or rvol >= SWING_T["min_rvol_avg"]
    confidence = _three_factor_confidence(trend_ok, level_ok, signal_ok)

    # ── Grade label ───────────────────────────────────────────────────────────
    if score >= 9:
        label = "A-grade setup"
    elif score >= 7:
        label = "B-grade setup"
    elif score >= 5:
        label = "C-grade setup"
    else:
        label = "No edge"

    explanation = label
    if parts:
        explanation += ": " + ", ".join(parts)
    if pen_msgs:
        explanation += " | " + ", ".join(pen_msgs)

    return ScoringResult(score, explanation, confidence)


def compute_swing_grade(swing_score: int) -> str:
    """
    Return a letter grade for a swing score (1–10).

    A  →  9–10  high-conviction, act on confirmation
    B  →  7–8   solid setup, good patience entry
    C  →  5–6   developing, wait for better conditions
    D  →  1–4   avoid / insufficient edge
    """
    if swing_score >= 9:  return "A"
    if swing_score >= 7:  return "B"
    if swing_score >= 5:  return "C"
    return "D"


def compute_swing_setup_type(data: dict) -> str:
    """
    Classify the dominant swing setup type from price structure.
    Returns one of SWING_SETUP_TYPES.  First match wins.
    """
    bias        = data.get("trade_bias") or "Neutral"
    current     = data.get("current_price") or 0
    pct_ema20   = data.get("pct_from_ema20")
    pct_ema50   = data.get("pct_from_ema50")
    fib_50      = data.get("fib_50")
    fib_618     = data.get("fib_618")
    fib_high    = data.get("fib_high")
    in_demand   = data.get("in_demand_zone", False)
    in_supply   = data.get("in_supply_zone", False)
    daily_trend = data.get("daily_trend") or "Neutral"
    daily_hh_hl = data.get("daily_hh_hl", False)

    if bias == "Avoid":
        return "No Setup"

    # 1. Extended — too far from key levels to enter cleanly
    if pct_ema20 is not None:
        ext_dir = (bias == "Long Bias" and pct_ema20 > 0) or \
                  (bias == "Short Bias" and pct_ema20 < 0)
        if ext_dir and abs(pct_ema20) > SWING_T["extended_slight_pct"]:
            return "Extended — Wait"

    # 2. At resistance on long / at support on short (structural headwind)
    if in_supply and bias == "Long Bias":
        return "At Resistance — Avoid"

    # 3. Order block / demand zone test (highest-quality long pullback)
    if in_demand and bias == "Long Bias":
        return "Order Block Test"
    if in_supply and bias == "Short Bias":
        return "Order Block Test"

    # 4. Breakout retest: price broke to swing highs, pulled back into upper range
    # Detects: above 50% fib, 2–8% below swing high, maintaining HH/HL structure
    if bias == "Long Bias" and current and fib_high and fib_50 and daily_hh_hl:
        pct_below_high = (fib_high - current) / fib_high * 100 if fib_high > 0 else 99
        if current > fib_50 and 2.0 <= pct_below_high <= 8.0:
            return "Breakout Retest"

    # 5. Fibonacci retracement levels (2% proximity window)
    if current and fib_618 and abs(current - fib_618) / current * 100 <= SWING_T["fib_proximity_pct"]:
        return "Near 61.8% Retracement"
    if current and fib_50 and abs(current - fib_50) / current * 100 <= SWING_T["fib_proximity_pct"]:
        return "Near 50% Retracement"

    # 6. EMA pullbacks
    if pct_ema20 is not None:
        in_zone  = abs(pct_ema20) <= SWING_T["pullback_ema20_pct"]
        correct  = (bias == "Long Bias" and pct_ema20 >= -3) or \
                   (bias == "Short Bias" and pct_ema20 <= 3)
        if in_zone and correct:
            return "Pullback to 20 EMA"

    if pct_ema50 is not None:
        in_zone  = abs(pct_ema50) <= SWING_T["pullback_ema50_pct"]
        correct  = (bias == "Long Bias" and pct_ema50 >= -4) or \
                   (bias == "Short Bias" and pct_ema50 <= 4)
        if in_zone and correct:
            return "Pullback to 50 EMA"

    # 7. Trend continuation (good trend, not yet at a specific entry level)
    if bias == "Long Bias"  and daily_trend in ("Bullish", "Bullish Lean"):
        return "Trend Continuation"
    if bias == "Short Bias" and daily_trend in ("Bearish", "Bearish Lean"):
        return "Trend Continuation"

    # 8. Weak / undefined structure
    if daily_trend == "Neutral":
        return "Weak Structure — Avoid"

    return "No Setup"


def compute_swing_status(data: dict) -> str:
    """
    Return the swing trade mode label for a given stock.

    4-mode system evaluated in priority order:

    READY — LEVEL HOLDS   (Mode 2 — green)
        Price at a key level AND 15m candle confirms AND volume ≥ 1.2×.
        Full trade plan is active and entry is valid.

    PRE-CONFIRMATION      (Mode 1 — yellow/orange)
        Price within 2-3% of a key level (50/61.8% fib, 20/50 EMA, demand/supply zone)
        AND trend is aligned.  Trade plan is populated — "Waiting for confirmation."

    TREND CONTINUATION    (Mode 3 — blue)
        Trend has confirmed structure (HH+HL for bulls, LH+LL for bears) and price is
        NOT near a classic pullback level.  Breakout/continuation entry.

    WAIT                  (Mode 4 — gray)
        No valid setup: extended, at resistance, no trend alignment, or no edge.

    Key level proximity window:
        ± 3% of 61.8% fib, ± 3% of 50% fib,
        ± 3% of 20 EMA, ± 4% of 50 EMA, or price in demand/supply zone.
    """
    bias        = data.get("trade_bias") or "Neutral"
    pct_ema20   = data.get("pct_from_ema20")
    pct_ema50   = data.get("pct_from_ema50")
    daily_trend = data.get("daily_trend") or "Neutral"
    daily_hh_hl = bool(data.get("daily_hh_hl", False))
    daily_lh_ll = bool(data.get("daily_lh_ll", False))
    fib_50      = data.get("fib_50")
    fib_618     = data.get("fib_618")
    current     = data.get("current_price") or 0
    in_demand   = data.get("in_demand_zone", False)
    in_supply   = data.get("in_supply_zone", False)
    rvol        = data.get("rel_volume") or 0
    m15_conf    = int(data.get("m15_confirmation") or 0)

    if bias == "Avoid":
        return "WAIT"

    # ── Trend alignment ───────────────────────────────────────────────────────
    trend_bull    = bias == "Long Bias"  and daily_trend in ("Bullish", "Bullish Lean")
    trend_bear    = bias == "Short Bias" and daily_trend in ("Bearish", "Bearish Lean")
    trend_aligned = trend_bull or trend_bear

    # ── Hard disqualifiers → WAIT ─────────────────────────────────────────────
    # At resistance on a long, or at support on a short — structural headwind
    if in_supply and bias == "Long Bias":
        return "WAIT"
    if in_demand and bias == "Short Bias":
        return "WAIT"

    # Heavily extended: >15% from 20 EMA in trend direction — do not chase
    if pct_ema20 is not None:
        ext_dir = (bias == "Long Bias" and pct_ema20 > 0) or \
                  (bias == "Short Bias" and pct_ema20 < 0)
        if ext_dir and abs(pct_ema20) > SWING_T["extended_heavy_pct"]:
            return "WAIT"

    # Trend opposing — no edge
    if not trend_aligned:
        return "WAIT"

    # ── Near key level? (2–3% proximity window) ───────────────────────────────
    _LVL = SWING_T["fib_proximity_pct"] * 1.5   # 3%

    near_fib618 = bool(current and fib_618 and
                       abs(current - fib_618) / current * 100 <= _LVL)
    near_fib50  = bool(current and fib_50  and
                       abs(current - fib_50)  / current * 100 <= _LVL)
    near_ema20  = bool(pct_ema20 is not None and
                       abs(pct_ema20) <= SWING_T["pullback_ema20_pct"])
    near_ema50  = bool(pct_ema50 is not None and
                       abs(pct_ema50) <= SWING_T["pullback_ema50_pct"])
    near_zone   = (in_demand and bias == "Long Bias") or \
                  (in_supply and bias == "Short Bias")

    near_level  = near_fib618 or near_fib50 or near_ema20 or near_ema50 or near_zone

    # ── Mode 2: READY — LEVEL HOLDS ──────────────────────────────────────────
    # At key level + 15m candle confirms + volume ≥ 1.2× average
    if near_level and m15_conf >= 2 and rvol >= SWING_T["min_rvol_strong"]:
        return "READY — LEVEL HOLDS"

    # ── Mode 1: PRE-CONFIRMATION ─────────────────────────────────────────────
    # Near key level, trend aligned — entry forming but not yet confirmed
    if near_level:
        return "PRE-CONFIRMATION"

    # ── Mode 3: TREND CONTINUATION ───────────────────────────────────────────
    # Good trend structure with no pullback level nearby — breakout/continuation entry
    if trend_bull and daily_hh_hl:
        return "TREND CONTINUATION"
    if trend_bear and daily_lh_ll:
        return "TREND CONTINUATION"

    # ── Mode 4: WAIT ─────────────────────────────────────────────────────────
    return "WAIT"


def compute_swing_trade_plan(data: dict) -> dict:
    """
    Compute a COMPLETE swing trade plan for every setup mode.

    A full plan (entry zone, stop, T1, T2, R:R) is ALWAYS generated when
    trade_bias is Long or Short.  The plan_mode field tells the UI how to
    present it:

        "confirmed"        — at level with 15m + vol confirmation (green)
        "pre_confirmation" — approaching key level, not yet confirmed (yellow)
        "continuation"     — trend breakout / continuation (blue)
        "watching"         — valid trend but no specific level yet (neutral)

    Entry priority (longs):
        1. Demand zone          (best: institutional support proven)
        2. 20 EMA               (dynamic support in trend)
        3. 61.8% Fibonacci      (deep pullback to golden pocket)
        4. 50% Fibonacci        (mid-range pullback)
        5. Trend continuation   (above swing high — breakout entry)
        6. Current price zone   (fallback — still gives a plan to watch)

    Stop: below key level support (demand zone / EMA / swing low)
    T1:   previous high / nearest supply zone
    T2:   1.5× the T1 reward extension
    R:R:  reward-to-risk at T1
    """
    bias        = data.get("trade_bias") or "Neutral"
    current     = data.get("current_price") or 0
    e20         = data.get("ema_20_daily")
    e50         = data.get("ema_50_daily")
    fib_50      = data.get("fib_50")
    fib_618     = data.get("fib_618")
    sw_high     = data.get("fib_high")
    sw_low      = data.get("fib_low")
    d_bot       = data.get("nearest_demand_bottom")
    d_top       = data.get("nearest_demand_top")
    s_bot       = data.get("nearest_supply_bottom")
    s_top       = data.get("nearest_supply_top")
    daily_trend = data.get("daily_trend") or "Neutral"
    daily_hh_hl = bool(data.get("daily_hh_hl", False))
    daily_lh_ll = bool(data.get("daily_lh_ll", False))
    rvol        = data.get("rel_volume") or 0
    m15_conf    = int(data.get("m15_confirmation") or 0)
    pct_ema20   = data.get("pct_from_ema20")
    pct_ema50   = data.get("pct_from_ema50")

    out = {
        "entry_zone_low":  None,
        "entry_zone_high": None,
        "stop_level":      None,
        "target_1":        None,
        "target_2":        None,
        "risk_reward":     None,
        "plan_mode":       "none",
    }

    if not current or bias == "Avoid" or bias == "Neutral":
        return out

    # ── Determine plan mode from proximity + confirmation ─────────────────────
    _LVL = SWING_T["fib_proximity_pct"] * 1.5   # 3% window
    _near_level = any([
        current and fib_618 and abs(current - fib_618) / current * 100 <= _LVL,
        current and fib_50  and abs(current - fib_50)  / current * 100 <= _LVL,
        e20 and abs(current - e20) / e20 * 100 <= SWING_T["pullback_ema20_pct"],
        e50 and abs(current - e50) / e50 * 100 <= SWING_T["pullback_ema50_pct"],
        bool(d_bot and d_top and abs(current - d_top) / current < 0.06),
    ])
    _trend_bull = daily_trend in ("Bullish", "Bullish Lean")
    _trend_bear = daily_trend in ("Bearish", "Bearish Lean")
    _confirmed  = _near_level and m15_conf >= 2 and rvol >= SWING_T["min_rvol_strong"]
    _continuation = (
        (bias == "Long Bias"  and _trend_bull and daily_hh_hl and not _near_level) or
        (bias == "Short Bias" and _trend_bear and daily_lh_ll and not _near_level)
    )

    if _confirmed:
        out["plan_mode"] = "confirmed"
    elif _near_level:
        out["plan_mode"] = "pre_confirmation"
    elif _continuation:
        out["plan_mode"] = "continuation"
    elif (_trend_bull and bias == "Long Bias") or (_trend_bear and bias == "Short Bias"):
        out["plan_mode"] = "watching"

    # ─────────────────────────────────────────────────────────────────────────
    if bias == "Long Bias":

        # ── Entry zone ────────────────────────────────────────────────────────
        if _continuation and sw_high:
            # Trend continuation: breakout entry just above swing high
            ez_lo = round(sw_high * 0.998, 2)
            ez_hi = round(sw_high * 1.005, 2)
        elif d_bot and d_top and abs(current - d_top) / current < 0.06:
            ez_lo, ez_hi = d_bot, d_top
        elif e20 and abs(current - e20) / e20 < 0.04:
            buf = e20 * 0.01
            ez_lo, ez_hi = round(e20 - buf, 2), round(e20 + buf, 2)
        elif fib_618 and abs(current - fib_618) / current < 0.03:
            buf = fib_618 * 0.01
            ez_lo, ez_hi = round(fib_618 - buf, 2), round(fib_618 + buf, 2)
        elif fib_50 and abs(current - fib_50) / current < 0.03:
            buf = fib_50 * 0.01
            ez_lo, ez_hi = round(fib_50 - buf, 2), round(fib_50 + buf, 2)
        elif fib_618:
            # Pre-confirm: entry zone IS the upcoming key level even if not there yet
            buf = fib_618 * 0.01
            ez_lo, ez_hi = round(fib_618 - buf, 2), round(fib_618 + buf, 2)
        elif fib_50:
            buf = fib_50 * 0.01
            ez_lo, ez_hi = round(fib_50 - buf, 2), round(fib_50 + buf, 2)
        elif e20:
            buf = e20 * 0.01
            ez_lo, ez_hi = round(e20 - buf, 2), round(e20 + buf, 2)
        else:
            buf = current * 0.015
            ez_lo, ez_hi = round(current - buf, 2), round(current + buf * 0.5, 2)

        out["entry_zone_low"]  = round(ez_lo, 2)
        out["entry_zone_high"] = round(ez_hi, 2)

        # ── Stop ──────────────────────────────────────────────────────────────
        if _continuation and sw_high:
            # Stop below breakout candle (just below the former swing high)
            stop = round(sw_high * 0.975, 2)
        elif d_bot:
            stop = round(d_bot * 0.988, 2)
        elif e20:
            stop = round(e20 * 0.972, 2)
        elif sw_low:
            stop = round(sw_low * 0.99, 2)
        else:
            stop = round(ez_lo * 0.960, 2)
        out["stop_level"] = stop

        # ── Target 1 ──────────────────────────────────────────────────────────
        if s_bot and s_bot > current:
            t1 = round(s_bot * 0.998, 2)
        elif sw_high and not _continuation:
            t1 = round(sw_high, 2)
        else:
            t1 = round(current * 1.07, 2)
        out["target_1"] = t1

        # ── Target 2 (1.5× T1 reward extension) ──────────────────────────────
        reward_1 = t1 - ez_hi
        if reward_1 > 0:
            out["target_2"] = round(ez_hi + reward_1 * 1.5, 2)

        # ── R:R ───────────────────────────────────────────────────────────────
        risk = ez_hi - stop
        if risk > 0 and reward_1 > 0:
            out["risk_reward"] = round(reward_1 / risk, 2)

    elif bias == "Short Bias":

        # ── Entry zone ────────────────────────────────────────────────────────
        if _continuation and sw_low:
            # Trend continuation: breakdown entry just below swing low
            ez_lo = round(sw_low * 0.995, 2)
            ez_hi = round(sw_low * 1.002, 2)
        elif s_bot and s_top and abs(current - s_bot) / current < 0.06:
            ez_lo, ez_hi = s_bot, s_top
        elif e20 and abs(current - e20) / e20 < 0.04:
            buf = e20 * 0.01
            ez_lo, ez_hi = round(e20 - buf, 2), round(e20 + buf, 2)
        elif fib_618:
            buf = fib_618 * 0.01
            ez_lo, ez_hi = round(fib_618 - buf, 2), round(fib_618 + buf, 2)
        elif fib_50:
            buf = fib_50 * 0.01
            ez_lo, ez_hi = round(fib_50 - buf, 2), round(fib_50 + buf, 2)
        else:
            buf = current * 0.015
            ez_lo, ez_hi = round(current - buf * 0.5, 2), round(current + buf, 2)

        out["entry_zone_low"]  = round(ez_lo, 2)
        out["entry_zone_high"] = round(ez_hi, 2)

        # ── Stop ──────────────────────────────────────────────────────────────
        if _continuation and sw_low:
            stop = round(sw_low * 1.025, 2)
        elif s_top:
            stop = round(s_top * 1.012, 2)
        elif e20:
            stop = round(e20 * 1.028, 2)
        elif sw_high:
            stop = round(sw_high * 1.01, 2)
        else:
            stop = round(ez_hi * 1.040, 2)
        out["stop_level"] = stop

        # ── Target 1 ──────────────────────────────────────────────────────────
        if d_top and d_top < current:
            t1 = round(d_top * 1.002, 2)
        elif sw_low and not _continuation:
            t1 = round(sw_low, 2)
        else:
            t1 = round(current * 0.93, 2)
        out["target_1"] = t1

        # ── Target 2 (1.5× T1 reward extension) ──────────────────────────────
        reward_1 = ez_lo - t1
        if reward_1 > 0:
            out["target_2"] = round(ez_lo - reward_1 * 1.5, 2)

        # ── R:R ───────────────────────────────────────────────────────────────
        risk = stop - ez_lo
        if risk > 0 and reward_1 > 0:
            out["risk_reward"] = round(reward_1 / risk, 2)

    return out
