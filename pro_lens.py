"""Pro lens: the checks a professional analyst runs before trusting a value.

The value zones are deliberately simple: one year of cash flow at a
growth-scaled multiple. That is a good screen and a poor final word on a
fast grower, so this adds the three things an analyst would do next:

1. Owner cash flow. Free cash flow leaves out stock-based pay, a real cost
   that is paid in shares instead of cash. Subtracting it gives the cash an
   owner actually keeps after the employees are paid.
2. A ten-year cash-flow model (DCF). Growth starts where the company is and
   fades in a straight line to 3% by year ten, discounted at the return you
   want (10% by default), plus a terminal value after year ten.
3. What you have to believe. The same model run backwards: the starting
   growth today's price needs, next to the growth the company has delivered.

js/thesis.js mirrors dcf_value() and implied_start_growth() exactly so the
sliders recompute in the browser; the tests hold the two to the same numbers.
Nothing here is advice: these are the standard tools, on the page.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DISCOUNT = 0.10        # the yearly return asked for
TERMINAL_G = 0.03      # growth after year ten, roughly the economy
YEARS = 10
G_MIN, G_MAX = -0.10, 0.60
_SCENARIOS = (("bear", "Bear", -0.10), ("base", "Base", 0.0), ("bull", "Bull", 0.05))


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def growth_path(g0: float, years: int = YEARS, gt: float = TERMINAL_G) -> list[float]:
    """Year-by-year growth: g0 in year one, a straight line to gt in the last."""
    if years <= 1:
        return [gt]
    return [g0 + (gt - g0) * t / (years - 1) for t in range(years)]


def dcf_value(cash: float | None, shares: float | None, g0: float, r: float = DISCOUNT,
              years: int = YEARS, gt: float = TERMINAL_G, cash_ps: float = 0.0) -> float | None:
    """Value per share of `cash` a year, growing along growth_path(g0)."""
    if not cash or cash <= 0 or not shares or shares <= 0 or r <= gt:
        return None
    pv, cf = 0.0, float(cash)
    for t, g in enumerate(growth_path(g0, years, gt), start=1):
        cf *= 1 + g
        pv += cf / (1 + r) ** t
    pv += cf * (1 + gt) / (r - gt) / (1 + r) ** years
    return pv / shares + (cash_ps or 0.0)


def implied_start_growth(price: float | None, cash: float | None, shares: float | None,
                         r: float = DISCOUNT, years: int = YEARS, gt: float = TERMINAL_G,
                         cash_ps: float = 0.0) -> float | None:
    """The year-one growth that makes dcf_value() equal today's price.

    Value rises with growth, so a bisection between -50% and +150% finds it.
    None when the price can't be reached in that range (or no cash to grow).
    """
    if not price or not cash or cash <= 0 or not shares or r <= gt:
        return None
    f = lambda g: dcf_value(cash, shares, g, r, years, gt, cash_ps)
    lo, hi = -0.5, 1.5
    if f(lo) >= price:
        return lo
    if f(hi) < price:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if f(mid) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def belief_tone(implied: float | None, delivered: float | None) -> tuple[str, str]:
    """(tone, word) for how the needed growth compares with the record."""
    if implied is None:
        return "pricey", "Out of reach"
    if delivered is None:
        return "fair", "Unchecked"
    gap = implied - delivered
    if gap <= 0:
        return "load", "Below its record"
    if gap <= 0.08:
        return "fair", "Demanding"
    return "pricey", "Heroic"


def _fmt_money(v):
    if v is None:
        return "—"
    a = abs(v)
    s = f"${a / 1e9:,.2f}B" if a >= 1e9 else f"${a / 1e6:,.0f}M"
    return ("−" if v < 0 else "") + s


def _fmt_price(v):
    return "—" if v is None else f"${v:,.2f}"


def _pct(v, signed=True):
    return "—" if v is None else f"{v * 100:{'+' if signed else ''}.0f}%"


def build_pro_lens(history: list[dict], ttm: dict, price: float | None, shares: float | None,
                   cash_ps: float = 0.0, delivered_growth: float | None = None) -> dict:
    """Everything the Pro lens section shows. Never raises.

    history: newest-first rows with "fcf" and "sbc" (raw USD).
    ttm: {"fcf": ..., "revenue_growth": ...} from the last 12 months, if known.
    delivered_growth: the company's revenue growth (history and TTM blended,
    not capped) — the record the needed growth is compared against.
    """
    try:
        fcf_annual = next((h.get("fcf") for h in history if isinstance(h.get("fcf"), (int, float))), None)
        fcf = ttm.get("fcf") or fcf_annual
        sbc = next((h.get("sbc") for h in history if isinstance(h.get("sbc"), (int, float))), None)
        if not fcf or fcf <= 0 or not shares or not price:
            return {"available": False,
                    "note": "No positive free cash flow to model yet." if price and shares else
                            "Needs a price and a share count."}
        owner = (fcf - sbc) if sbc is not None else None
        g = _clamp(delivered_growth if delivered_growth is not None else 0.0, G_MIN, G_MAX)
        use_owner = owner is not None and owner > 0

        def lens(cash):
            if not cash or cash <= 0:
                return None
            rows = []
            for key, label, adj in _SCENARIOS:
                gs = _clamp(g + adj, G_MIN, G_MAX)
                val = dcf_value(cash, shares, gs, cash_ps=cash_ps)
                rows.append({"key": key, "label": label, "growth": gs, "growth_fmt": _pct(gs),
                             "value": val, "value_fmt": _fmt_price(val),
                             "upside_fmt": _pct(val / price - 1) if val else "—",
                             "tone": "pos" if val and val >= price else "neg"})
            implied = implied_start_growth(price, cash, shares, cash_ps=cash_ps)
            tone, word = belief_tone(implied, g)
            return {"cash": cash, "cash_fmt": _fmt_money(cash), "scenarios": rows,
                    "implied": implied, "implied_fmt": _pct(implied) if implied is not None else "over +150%",
                    "tone": tone, "word": word}

        fcf_lens, owner_lens = lens(fcf), lens(owner)
        sbc_share = (sbc / fcf) if sbc is not None else None
        return {
            "available": True,
            "fcf": fcf, "fcf_fmt": _fmt_money(fcf), "fcf_is_ttm": bool(ttm.get("fcf")),
            "sbc": sbc, "sbc_fmt": _fmt_money(sbc) if sbc is not None else "—",
            "owner": owner, "owner_fmt": _fmt_money(owner),
            "sbc_share": sbc_share, "sbc_share_fmt": _pct(sbc_share, signed=False) if sbc_share is not None else "—",
            "sbc_heavy": bool(sbc_share is not None and sbc_share >= 0.25),
            "delivered": g, "delivered_fmt": _pct(g),
            "fcf_lens": fcf_lens, "owner_lens": owner_lens,
            "default": "owner" if use_owner else "fcf",
            "model": {"fcf": fcf, "owner": owner if use_owner else None, "shares": shares,
                      "cash_ps": cash_ps or 0.0, "price": price, "g": g,
                      "r": DISCOUNT, "years": YEARS, "gt": TERMINAL_G,
                      "basis": "owner" if use_owner else "fcf"},
            "assumptions": (f"Growth starts at the company's own rate and fades in a straight "
                            f"line to {TERMINAL_G:.0%} by year {YEARS}; cash is discounted at "
                            f"{DISCOUNT:.0%} a year, the return you're asking for; net cash per "
                            "share is added on top."),
        }
    except Exception as exc:
        logger.debug("pro lens failed: %s", exc)
        return {"available": False, "note": "Model unavailable."}
