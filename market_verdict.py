"""One market verdict for the whole app.

Four calculations used to put a risk word on screen, and they disagreed: the
Today dial (VIX, indexes, futures, breadth, news, trend) could read "Neutral
54" while the trend regime on Terminal said "Risk-Off", the dashboard meter
said "Defensive Mode" and the liquidity panel said "Risk-On — Liquidity
Expanding". Each was computed correctly; they measured different things with
the same words.

Now the dial is the verdict, and the only thing allowed to say Risk-On,
Neutral, Risk-Off or No Trade. Everything that shows a market regime, sizes a
trade or briefs the user reads it from here. The other readings keep their
numbers but describe themselves in their own words:

  trend      Uptrend / Choppy / Downtrend   (market_engine EMA regime)
  liquidity  Expanding / Mixed / Contracting (liquidity_engine)

Extreme volatility (the trend engine's NO_TRADE, VIX above 30) overrides the
blend: every page then reads No Trade.
"""
from __future__ import annotations

KEYS = ("RISK_ON", "NEUTRAL", "RISK_OFF", "NO_TRADE")
LABELS = {"RISK_ON": "Risk-On", "NEUTRAL": "Neutral", "RISK_OFF": "Risk-Off", "NO_TRADE": "No Trade"}
NOTES = {
    "RISK_ON": "Buyers in control — press A+ setups, still honor stops.",
    "NEUTRAL": "Mixed tape — be selective, smaller size, take profits faster.",
    "RISK_OFF": "Defense first — cash is a position; only the cleanest setups.",
    "NO_TRADE": "Volatility is extreme — no new entries until it settles.",
}
CSS = {"RISK_ON": "mt-risk-on", "NEUTRAL": "mt-caution", "RISK_OFF": "mt-risk-off", "NO_TRADE": "mt-no-trade"}

_TREND_WORDS = {"RISK_ON": "Uptrend", "BULLISH": "Uptrend", "RISK_OFF": "Downtrend", "BEARISH": "Downtrend",
                "NO_TRADE": "Extreme volatility"}


def key_for(label) -> str | None:
    """'Risk-On' / 'risk_on' / 'RISK ON' → 'RISK_ON'; None if not a verdict."""
    k = str(label or "").strip().upper().replace("-", "_").replace(" ", "_")
    return k if k in KEYS else None


def trend_word(regime) -> str:
    """The trend engine's regime in trend words, so it never reads as a verdict."""
    r = str(regime or "").strip().upper().replace("-", "_").replace(" ", "_")
    if not r or r in ("UNKNOWN", "LOADING"):
        return ""
    return _TREND_WORDS.get(r, "Choppy")


def finalize(reading: dict, trend_regime=None) -> dict:
    """Stamp the dial reading as the verdict: key, label, note, NO_TRADE override."""
    reading = dict(reading or {})
    if str(trend_regime or "").upper() == "NO_TRADE":
        reading.update(known=True, label=LABELS["NO_TRADE"])
    key = key_for(reading.get("label")) if reading.get("known") else None
    reading["key"] = key
    if key:
        reading["label"] = LABELS[key]
        reading["note"] = NOTES[key]
    reading["trend"] = trend_word(trend_regime)
    return reading


def flags(key: str) -> dict:
    """The trading switches every engine reads, from the verdict."""
    return {"longs_ok": key not in ("RISK_OFF", "NO_TRADE"),
            "shorts_ok": key != "RISK_ON",
            "reduce_size": key in ("NEUTRAL", "RISK_OFF"),
            "no_trade": key == "NO_TRADE"}


def apply(ctx: dict, verdict: dict | None) -> dict:
    """The market-context dict with its regime replaced by the verdict.

    The trend engine's own regime moves to `trend_regime` / `trend_label`, and
    its signal text to `trend_signal`, so nothing downstream reads it as the
    market's verdict by accident.
    """
    out = dict(ctx or {})
    trend = out.get("trend_regime", out.get("regime"))
    out["trend_regime"] = trend
    out["trend_label"] = trend_word(trend)
    if "trend_signal" not in out:
        out["trend_signal"] = out.get("signal", "")
    key = (verdict or {}).get("key")
    if not key:
        return out
    out.update(regime=key, regime_label=LABELS[key], signal=NOTES[key],
               verdict_score=(verdict or {}).get("score"), **flags(key))
    return out


def apply_temperature(mt: dict, verdict: dict | None) -> dict:
    """The dashboard meter / Trade Coach reading, speaking the verdict."""
    out = dict(mt or {})
    key = (verdict or {}).get("key")
    if not key:
        return out
    out.update(regime=key, label=LABELS[key], css=CSS[key], action_msg=NOTES[key],
               decision_cmd=f"{LABELS[key].upper()} — {NOTES[key]}", **flags(key))
    if (verdict or {}).get("score") is not None:
        out["meter_score"] = verdict["score"]
    return out
