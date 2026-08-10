import unittest

from mock_data import _swing_defaults


class MockDataContractTests(unittest.TestCase):
    def test_swing_defaults_cover_every_persisted_fibonacci_field(self):
        data = {}
        _swing_defaults(data)
        persisted = {
            "fib_high", "fib_low", "fib_236", "fib_382", "fib_50",
            "fib_618", "fib_65", "fib_705", "fib_786", "fib_confidence",
            "fib_direction", "fib_mode", "macro_fib_high", "macro_fib_low",
            "macro_fib_50", "macro_fib_618", "h4_fib_high", "h4_fib_low",
            "h4_fib_50", "h4_fib_618",
        }
        self.assertEqual(persisted - data.keys(), set())


if __name__ == "__main__":
    unittest.main()
