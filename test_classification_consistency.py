"""
test_classification_consistency.py

Regression tests for the single-source-of-truth ticker classification.

Background: before this fix, the dashboard table, the Scanner Buckets
widget, the Best Swing Candidates cards, the top-nav / watchlist-tab
counters, the Avoid/Blocked watchlist view, and the swing-alerts feed
each re-derived a ticker's grade/status/bucket from raw score fields
using their own slightly-different thresholds. That let the same ticker
(NVDA, in the bug report) show up simultaneously as "A+ READY" in one
widget and "AVOID" in another, at the same instant.

classifier.classify() is now the only function allowed to decide bucket
membership, the status badge text, the avoid/blocked flag, and the
letter grade. These tests assert the basic invariant the user cares
about: those fields can never contradict each other for one ticker, and
common real-world inputs (including the exact NVDA repro from the bug
report) land in the bucket a human would expect.

Run with:  python3 -m unittest test_classification_consistency.py -v
"""

import unittest

from classifier import (
    classify,
    is_consistent,
    A_PLUS_READY,
    SETUPS_FORMING,
    TREND_WATCH,
    EXTENDED_ZONE,
    AVOID_BLOCKED,
    ALL_BUCKETS,
)


def make_stock(**overrides) -> dict:
    """A reasonably 'healthy' stock dict; tests override only what they need."""
    base = {
        "ticker":          "TEST",
        "trade_bias":      "Long",
        "swing_score":     5,
        "swing_status":    "WAIT",
        "daily_trend":     "Neutral",
        "catalyst_score":  0,
        "momentum_score":  0,
        "risk_reward":     0,
        "entry_quality":   "",
        "pct_from_ema20":  None,
    }
    base.update(overrides)
    return base


class TestNoContradictions(unittest.TestCase):
    """The exact invariant the bug report asked for: bucket, status badge,
    and avoid/blocked flag must never contradict each other."""

    def test_every_classification_is_internally_consistent(self):
        # A broad sweep of realistic combinations, not just the NVDA repro.
        scenarios = [
            make_stock(trade_bias="Avoid", swing_score=9, swing_status="PRE-CONFIRMATION"),
            make_stock(swing_score=8, swing_status="PRE-CONFIRMATION",
                       daily_trend="Bullish", catalyst_score=4, risk_reward=3.0),
            make_stock(swing_score=8, swing_status="READY — LEVEL HOLDS",
                       daily_trend="Bullish Lean", risk_reward=2.0),
            make_stock(swing_score=2, catalyst_score=1, momentum_score=1),
            make_stock(swing_score=7, swing_status="TOO EXTENDED", daily_trend="Bullish"),
            make_stock(swing_score=7, entry_quality="Extended", daily_trend="Bullish"),
            make_stock(swing_score=6, swing_status="WAIT FOR PULLBACK", daily_trend="Bullish"),
            make_stock(swing_score=4, daily_trend="Bearish"),
            make_stock(swing_score=9, swing_status="READY — LEVEL HOLDS", risk_reward=0.5),
            make_stock(swing_score=9, swing_status="AVOID — AT RESISTANCE", daily_trend="Bullish"),
        ]
        for i, stock in enumerate(scenarios):
            with self.subTest(i=i, stock=stock):
                result = classify(stock)
                self.assertIn(result["bucket"], ALL_BUCKETS)
                self.assertTrue(
                    is_consistent(result),
                    f"Inconsistent classification for {stock}: {result}",
                )
                # The literal contradiction from the bug report: a ticker
                # can never be flagged avoid/blocked while its status badge
                # still reads "A+ READY", and vice versa.
                if result["avoid_blocked"]:
                    self.assertNotEqual(result["status_label"], A_PLUS_READY)
                    self.assertEqual(result["bucket"], AVOID_BLOCKED)
                else:
                    self.assertNotEqual(result["status_label"], AVOID_BLOCKED)

    def test_avoid_bias_always_wins_even_with_elite_score(self):
        """An Avoid-bias ticker must never be classified A+ READY no matter
        how good its raw score looks — this is the literal AVOID + A+ READY
        contradiction from screenshot 4 in the bug report."""
        stock = make_stock(
            trade_bias="Avoid", swing_score=9, swing_status="READY — LEVEL HOLDS",
            daily_trend="Bullish", catalyst_score=8, risk_reward=3.0,
        )
        result = classify(stock)
        self.assertEqual(result["bucket"], AVOID_BLOCKED)
        self.assertTrue(result["avoid_blocked"])
        self.assertEqual(result["status_label"], AVOID_BLOCKED)
        self.assertNotEqual(result["status_label"], A_PLUS_READY)
        self.assertEqual(result["grade"], "D")


class TestNvdaRepro(unittest.TestCase):
    """Reproduces the exact NVDA scenario from the bug report:
    swing_score 8/10, status PRE-CONFIRMATION, Long bias, R:R 3.0:1,
    bullish trend, decent catalyst — this should be unambiguously
    A+ READY everywhere, with no avoid flag anywhere."""

    def setUp(self):
        self.nvda = make_stock(
            ticker="NVDA",
            trade_bias="Long",
            swing_score=8,
            swing_status="PRE-CONFIRMATION",
            daily_trend="Bullish",
            catalyst_score=4,
            risk_reward=3.0,
        )

    def test_bucket_is_a_plus_ready(self):
        result = classify(self.nvda)
        self.assertEqual(result["bucket"], A_PLUS_READY)
        self.assertEqual(result["status_label"], "A+ READY")
        self.assertFalse(result["avoid_blocked"])
        self.assertEqual(result["scanner_key"], "aplus")
        self.assertIn(result["grade"], ("A", "A+"))

    def test_classify_stock_wrapper_matches(self):
        """The backward-compatible classify_stock() wrapper (used by
        run_auto_classification to persist watchlist membership) must agree
        with classify() — they're the same computation now, not two."""
        from classifier import classify_stock
        bucket, reason = classify_stock(self.nvda)
        result = classify(self.nvda)
        self.assertEqual(bucket, result["bucket"])
        self.assertEqual(reason, result["reason"])


class TestScannerBucketGrouping(unittest.TestCase):
    """A ticker can only ever land in exactly one Scanner Buckets quadrant —
    the one matching its canonical bucket. This directly targets the
    bug-report symptom where the A+ READY quadrant showed empty while the
    ticker appeared elsewhere."""

    def test_a_plus_ticker_only_in_aplus_quadrant(self):
        stock = make_stock(
            swing_score=8, swing_status="PRE-CONFIRMATION",
            daily_trend="Bullish", catalyst_score=4, risk_reward=3.0,
        )
        result = classify(stock)
        quadrants = {"aplus", "forming", "chase", "avoid"}
        membership = {q for q in quadrants if result["scanner_key"] == q}
        self.assertEqual(membership, {"aplus"})

    def test_avoid_ticker_only_in_avoid_quadrant(self):
        stock = make_stock(trade_bias="Avoid", swing_score=9)
        result = classify(stock)
        self.assertEqual(result["scanner_key"], "avoid")


class TestAlertsNeverContradictBucket(unittest.TestCase):
    """Reproduces Bug 6: a ticker bucketed A+ READY must never also generate
    a 'PRE-CONFIRMATION' alert, even across repeated scans where the A+
    alert itself would normally be deduped."""

    def setUp(self):
        import alerts
        alerts.clear_alerts()
        self.alerts = alerts

    def test_a_plus_ticker_never_fires_pre_confirm(self):
        stock = make_stock(
            ticker="NVDA", swing_score=8, swing_status="PRE-CONFIRMATION",
            daily_trend="Bullish", catalyst_score=4, risk_reward=3.0,
        )
        # Simulate several consecutive scans (the dedup window would
        # normally suppress a repeated "aplus" alert on later scans —
        # that's exactly the situation that used to leak a contradictory
        # "upgraded to PRE-CONFIRMATION" alert for the same setup).
        all_messages = []
        for _ in range(5):
            fired = self.alerts.generate_alerts([dict(stock)])
            all_messages.extend(a["message"] for a in fired)

        pre_confirm_msgs = [m for m in all_messages if "PRE-CONFIRMATION" in m]
        self.assertEqual(
            pre_confirm_msgs, [],
            "A ticker bucketed A+ READY must never also fire a "
            "PRE-CONFIRMATION alert: " + repr(pre_confirm_msgs),
        )

    def test_avoid_blocked_ticker_fires_no_alerts(self):
        stock = make_stock(
            ticker="BADCO", trade_bias="Avoid", swing_score=9,
            swing_status="READY — LEVEL HOLDS",
        )
        fired = self.alerts.generate_alerts([dict(stock)])
        self.assertEqual(fired, [])


if __name__ == "__main__":
    unittest.main()
