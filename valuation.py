"""What the company costs, alongside what the company is.

The scorecard answers whether a business is worth owning. It says nothing
about price, and a reader who stops there will buy a great company at any
number. This layer adds the other half — and keeps the same discipline as the
rest of the card: every figure shows its arithmetic, and anything that cannot
be sourced is left blank rather than estimated.

Nothing here is an opinion or a recommendation. "47.9x trailing earnings" is a
fact about the price; whether that is too much is the reader's call.
"""
from __future__ import annotations

# Finnhub's /stock/metric?metric=all payload keys.
_HIGH = "52WeekHigh"
_LOW = "52WeekLow"
_HIGH_DATE = "52WeekHighDate"
_LOW_DATE = "52WeekLowDate"


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


def _pos(value):
    out = _num(value)
    return out if out is not None and out > 0 else None


def _on_one_basis(price, high, low) -> bool:
    """Whether the spot quote and the 52-week range describe the same shares.

    They do not for a foreign company with a US listing. The quote comes back
    for the ADR in dollars while the metric payload states the range and the
    market cap for the primary listing in its home currency, and nothing in
    either payload says so. TSMC rendered as a $428.91 share against a
    "52-week high" of 2,535 and a "52-week low" of 1,145 - a price below its
    own annual low, "-83.1% off the high", "still -63% above it", and a
    market capitalisation of $61.8T. Every one of those is a New Taiwan
    dollar figure wearing a dollar sign.

    A genuine new high or low only breaches the range by a little, so a wide
    breach is a basis mismatch rather than a move.
    """
    if price is None or high is None or low is None:
        return True
    return low * 0.75 <= price <= high * 1.25


def build_valuation(metric: dict | None, quote: dict | None = None,
                    home_symbol: str | None = None) -> dict:
    """Price context from the Finnhub metric payload plus a spot quote.

    ``quote`` is Finnhub's /quote shape: ``c`` current, ``pc`` previous close.
    ``home_symbol`` is the currency symbol the company reports in, used to
    label the provider's home-listing figures when they are not in dollars.
    All are optional; whatever is missing simply does not render.
    """
    metric = metric or {}
    quote = quote or {}

    price = _pos(quote.get("c"))
    prev = _pos(quote.get("pc"))
    high = _pos(metric.get(_HIGH))
    low = _pos(metric.get(_LOW))

    # Ratios survive a basis mismatch - a P/E is the same number whichever
    # currency both halves are stated in. Absolute prices and comparisons
    # between the two bases do not.
    one_basis = _on_one_basis(price, high, low)
    home = (home_symbol or "").strip()
    notice = ""
    if not one_basis:
        notice = (
            "The 52-week range and market cap below come from the company's "
            "primary listing"
            + (f", stated in its home currency ({home.strip()})." if home else ".")
            + " The price is the US listing in dollars. The two are not "
            "comparable, so this card does not measure one against the other."
        )

    def _home(value: float) -> str:
        """A provider figure on the home-listing basis."""
        return f"{home}{value:,.2f}" if home else f"{value:,.2f}"

    rows: list[dict] = []

    if price is not None:
        change = ((price / prev) - 1.0) * 100 if prev else None
        rows.append({
            "key": "price", "label": "Price", "value": f"${price:,.2f}",
            "note": (f"{change:+.2f}% on the day" if change is not None else ""),
            "tone": "neutral" if change is None else ("pos" if change >= 0 else "neg"),
        })

    if high is not None:
        off = ((price / high) - 1.0) * 100 if (price and one_basis) else None
        rows.append({
            "key": "high", "label": "52-week high",
            "value": f"${high:,.2f}" if one_basis else _home(high),
            "note": (f"{_date(metric.get(_HIGH_DATE))}" if metric.get(_HIGH_DATE) else ""),
            "tone": "neutral",
        })
        if off is not None:
            rows.append({
                "key": "off_high", "label": "Off the high", "value": f"{off:.1f}%",
                "note": f"${price:,.2f} against ${high:,.2f}",
                "tone": "neg" if off <= -25 else "neutral",
            })

    if low is not None:
        above = ((price / low) - 1.0) * 100 if (price and one_basis) else None
        rows.append({
            "key": "low", "label": "52-week low",
            "value": f"${low:,.2f}" if one_basis else _home(low),
            # "still -63% above it" is not a sentence. Below the low is a
            # 52-week low, and it is worth saying so plainly.
            "note": ("" if above is None else
                     (f"a new 52-week low" if above <= 0
                      else f"still {above:+.0f}% above it")),
            "tone": "neutral",
        })

    pe = _pos(metric.get("peTTM")) or _pos(metric.get("peExclExtraTTM"))
    if pe is not None:
        rows.append({
            "key": "pe", "label": "Trailing P/E", "value": f"{pe:.1f}x",
            "note": f"${pe:,.2f} of price for every $1 of past-year profit",
            "tone": "warn" if pe >= 30 else "neutral",
        })

    pfcf = _pos(metric.get("pfcfShareTTM"))
    if pfcf is not None:
        rows.append({
            "key": "pfcf", "label": "Price / free cash flow", "value": f"{pfcf:.1f}x",
            "note": f"a {100 / pfcf:.1f}% free cash flow yield at this price",
            "tone": "warn" if pfcf >= 35 else "neutral",
        })

    ps = _pos(metric.get("psTTM"))
    if ps is not None:
        rows.append({"key": "ps", "label": "Price / sales", "value": f"{ps:.1f}x",
                     "note": "", "tone": "neutral"})

    cap = _pos(metric.get("marketCapitalization"))
    if cap is not None:
        rows.append({"key": "cap", "label": "Market cap",
                     "value": (_big(cap * 1e6) if one_basis
                               else _big(cap * 1e6, symbol=home)),
                     "note": "", "tone": "neutral"})

    div = _num(metric.get("currentDividendYieldTTM"))
    if div is not None and div > 0:
        rows.append({"key": "div", "label": "Dividend yield",
                     "value": f"{div:.2f}%", "note": "", "tone": "neutral"})

    return {
        "available": bool(rows),
        "rows": rows,
        "price": price,
        "one_basis": one_basis,
        "notice": notice,
        "off_high_pct": (((price / high) - 1.0) * 100
                         if price and high and one_basis else None),
        "pe": pe,
        "pfcf": pfcf,
    }


def _date(value) -> str:
    text = str(value or "")[:10]
    return text if len(text) == 10 else ""


def _big(value, symbol: str = "$") -> str:
    symbol = symbol or ""
    n = abs(float(value))
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if n >= cutoff:
            return f"{symbol}{n / cutoff:.1f}{suffix}"
    return f"{symbol}{n:,.0f}"
