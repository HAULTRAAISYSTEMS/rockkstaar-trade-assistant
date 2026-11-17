"""A short setup must not read A+ READY beside BLOCKED, and the gate must
ask a short for bearish structure, not bullish."""
from app import compute_trade_permission
from classifier import classify, A_PLUS_READY, SETUPS_FORMING
from scoring import structure_for_bias


def short_stock(**kw):
    s = {"ticker": "XYZ", "trade_bias": "Short Bias", "swing_score": 7,
         "swing_status": "PRE-CONFIRMATION", "daily_trend": "Bearish Lean",
         "h4_trend": "Bearish", "daily_hh_hl": False, "h4_hh_hl": False,
         "daily_lh_ll": False, "catalyst_score": 5, "momentum_score": 5,
         "risk_reward": 6.9, "pct_from_ema20": 0.5, "pct_from_ema50": -1.0,
         "current_price": 190.0}
    s.update(kw)
    return s


def test_short_structure_is_lower_highs_not_higher_highs():
    assert structure_for_bias({"daily_lh_ll": True}, "Short Bias")
    assert not structure_for_bias({"daily_hh_hl": True, "h4_hh_hl": True}, "Short Bias")
    assert structure_for_bias({"daily_hh_hl": True}, "Long Bias")


def test_gate_does_not_demand_hh_hl_from_a_short():
    s = short_stock(daily_lh_ll=True)
    p = compute_trade_permission(s, "SWING TRADE")
    assert "HH/HL" not in p["reason"]


def test_short_without_structure_names_lh_ll():
    s = short_stock(h4_trend="Neutral", pct_from_ema20=0.5)
    p = compute_trade_permission(s, "SWING TRADE")
    assert p["permission"] == "BLOCKED" and "LH/LL" in p["reason"]


def test_blocked_gate_never_shows_a_plus():
    s = short_stock(h4_trend="Neutral")
    s["trade_permission"] = compute_trade_permission(s, "SWING TRADE")
    assert s["trade_permission"]["permission"] == "BLOCKED"
    c = classify(s)
    assert c["bucket"] != A_PLUS_READY
    assert c["bucket"] == SETUPS_FORMING and "structure" in c["reason"]


def test_confirmed_short_can_still_be_a_plus():
    s = short_stock(daily_lh_ll=True, daily_trend="Bearish")
    s["trade_permission"] = {"permission": "TRADE ALLOWED", "reason": ""}
    assert classify(s)["bucket"] == A_PLUS_READY
