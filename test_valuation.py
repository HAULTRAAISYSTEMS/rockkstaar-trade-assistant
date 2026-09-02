"""Price context, held to the same standard as the rest of the card.

The scorecard answers whether a business is worth owning and says nothing
about what it costs. A reader who stops there buys a great company at any
number. Every figure here shows its arithmetic; anything unsourced is left out
rather than estimated, and nothing states an opinion about whether a price is
too high.
"""
import pytest

from valuation import build_valuation

# KLA on 1 Sep 2026, as an independent analyst report presented it.
METRIC = {"52WeekHigh": 307.37, "52WeekLow": 83.22, "52WeekHighDate": "2026-06-30",
          "peTTM": 47.9, "pfcfShareTTM": 59.4, "psTTM": 16.6,
          "marketCapitalization": 225000, "currentDividendYieldTTM": 0.42}
QUOTE = {"c": 171.17, "pc": 175.45}


def cells(result):
    return {r["key"]: r for r in result["rows"]}


def test_it_reproduces_an_independent_analysts_figures():
    """Same inputs, same numbers a human analyst published for KLAC."""
    c = cells(build_valuation(METRIC, QUOTE))
    assert c["price"]["value"] == "$171.17"
    assert c["price"]["note"] == "-2.44% on the day"
    assert c["off_high"]["value"] == "-44.3%"
    assert c["low"]["note"] == "still +106% above it"
    assert c["pe"]["value"] == "47.9x"
    assert c["pfcf"]["value"] == "59.4x"


def test_every_multiple_shows_what_it_means_in_money():
    c = cells(build_valuation(METRIC, QUOTE))
    assert "$47.90 of price for every $1" in c["pe"]["note"]
    assert "1.7% free cash flow yield" in c["pfcf"]["note"]


def test_a_deep_drawdown_is_marked_but_not_called_a_buy():
    """Down 44% is a fact. Whether that is cheap is the reader's call, and the
    card must not imply otherwise."""
    r = build_valuation(METRIC, QUOTE)
    c = cells(r)
    assert c["off_high"]["tone"] == "neg"
    joined = " ".join(x["note"] + x["value"] + x["label"] for x in r["rows"]).lower()
    for word in ("buy", "cheap", "undervalued", "opportunity", "bargain"):
        assert word not in joined


def test_an_expensive_multiple_is_flagged_without_a_verdict():
    c = cells(build_valuation(METRIC, QUOTE))
    assert c["pe"]["tone"] == "warn" and c["pfcf"]["tone"] == "warn"


def test_a_modest_multiple_is_not_flagged():
    c = cells(build_valuation({"peTTM": 12.0, "pfcfShareTTM": 14.0}, {"c": 50.0}))
    assert c["pe"]["tone"] == "neutral" and c["pfcf"]["tone"] == "neutral"


# ── Missing and bad data ─────────────────────────────────────────────────────

def test_no_data_renders_nothing_rather_than_zeros():
    r = build_valuation({}, {})
    assert r["available"] is False and r["rows"] == []


def test_a_missing_price_still_shows_the_multiples():
    """The 52-week range and the ratios do not need a spot quote."""
    c = cells(build_valuation(METRIC, {}))
    assert "pe" in c and "high" in c
    assert "price" not in c and "off_high" not in c


def test_a_missing_quote_argument_is_allowed():
    assert build_valuation(METRIC)["available"] is True


@pytest.mark.parametrize("bad", [None, "", "n/a", 0, -3, float("nan"), float("inf")])
def test_unusable_values_are_dropped_not_rendered(bad):
    """A zero or negative P/E is a loss-making company, not a 0.0x multiple."""
    c = cells(build_valuation({"peTTM": bad, "52WeekHigh": 100.0}, {"c": 50.0}))
    assert "pe" not in c


def test_a_zero_dividend_is_not_shown_as_a_yield():
    c = cells(build_valuation({"currentDividendYieldTTM": 0}, {"c": 50.0}))
    assert "div" not in c


def test_a_price_above_the_52_week_high_reads_positive_not_broken():
    c = cells(build_valuation({"52WeekHigh": 100.0}, {"c": 120.0}))
    assert c["off_high"]["value"] == "20.0%"
    assert c["off_high"]["tone"] == "neutral"


def test_market_cap_is_scaled_from_finnhubs_millions():
    c = cells(build_valuation({"marketCapitalization": 225000}, {"c": 1.0}))
    assert c["cap"]["value"] == "$225.0B"


def test_the_fallback_pe_field_is_used_when_the_primary_is_absent():
    c = cells(build_valuation({"peExclExtraTTM": 21.5}, {"c": 50.0}))
    assert c["pe"]["value"] == "21.5x"
