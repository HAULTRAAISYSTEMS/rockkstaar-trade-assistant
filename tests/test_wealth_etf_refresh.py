import json
import os
import unittest
from datetime import date
from unittest.mock import patch
import wealth_etf_refresh as refresh


class RefreshTests(unittest.TestCase):
    def setUp(self):
        refresh.reset_for_tests()
        self.config = {"SPCK": {"url": "https://issuer.example/holdings",
                                "hostname": "issuer.example", "identifier": "ABC",
                                "source": "Issuer"}}

    def test_refresh_caches_without_refetch(self):
        calls = []
        class Response:
            content = b'{}'
            def raise_for_status(self): pass
            def json(self):
                return {"identifier": "ABC", "as_of": date.today().isoformat(),
                        "constituents": [{"ticker": "NVDA", "sector": "Technology",
                                          "weight_pct": 100}]}
        def fetch(*args, **kwargs):
            calls.append(1)
            return Response()
        with patch.dict(os.environ, {"TRADESTAAR_ETF_ISSUER_FEEDS_JSON": json.dumps(self.config)}):
            a, errors, statuses = refresh.refresh(["SPCK"], fetch=fetch, now=100)
            b, errors2, statuses2 = refresh.refresh(["SPCK"], fetch=fetch, now=101)
        self.assertEqual(len(calls), 1)
        self.assertEqual(a, b)
        self.assertEqual(statuses2["SPCK"], "cached_verified_snapshot")

    def test_expired_refresh_failure_does_not_reuse_old_data(self):
        class Response:
            content = b'{}'
            def raise_for_status(self): pass
            def json(self):
                return {"identifier": "ABC", "as_of": date.today().isoformat(),
                        "constituents": [{"ticker": "NVDA", "sector": "Technology",
                                          "weight_pct": 100}]}
        def broken(*args, **kwargs):
            raise ValueError("offline")
        with patch.dict(os.environ, {"TRADESTAAR_ETF_ISSUER_FEEDS_JSON": json.dumps(self.config)}):
            refresh.refresh(["SPCK"], fetch=lambda *a, **k: Response(), now=100)
            data, errors, statuses = refresh.refresh(["SPCK"], fetch=broken, now=3800)
        self.assertEqual(data, {})
        self.assertEqual(statuses["SPCK"], "refresh_failed")


if __name__ == "__main__":
    unittest.main()
