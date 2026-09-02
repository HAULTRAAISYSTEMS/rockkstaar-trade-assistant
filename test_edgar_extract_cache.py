"""The EDGAR extraction cache.

Downloading and parsing a large filer's whole company-facts document is
CPU-bound, so it holds the interpreter lock and stalls every thread in the
worker — health checks included. Render times its health check out after five
seconds, decides the instance is dead and restarts it, which is what the 502s
were. Force refresh is meant to bypass the finished scorecard, not to re-fetch
the filing on every click.
"""
from unittest.mock import patch

import pytest

import fundamentals_engine as fe
from test_edgar_stale_concepts import kla_shaped
from test_edgar_pipeline import _Resp


class CountingGet:
    def __init__(self, payload):
        self.payload, self.calls = payload, 0

    def __call__(self, *a, **kw):
        self.calls += 1
        return _Resp(self.payload)


@pytest.fixture
def counting():
    get = CountingGet(kla_shaped())
    with patch.object(fe, "_edgar_cik", return_value=("0000319201", "KLA")), \
         patch.object(fe._req_module, "get", get):
        yield get


def test_the_filing_is_downloaded_once_for_repeated_lookups(counting):
    for _ in range(5):
        assert fe.fetch_fundamentals_edgar("KLAC") is not None
    assert counting.calls == 1


def test_the_cached_result_matches_a_fresh_one(counting):
    first = fe.fetch_fundamentals_edgar("KLAC")
    second = fe.fetch_fundamentals_edgar("KLAC")
    assert first["revenue"] == second["revenue"]
    assert first["diluted_shares"] == second["diluted_shares"]


def test_callers_cannot_mutate_what_the_next_caller_sees(counting):
    """The TTM layer augments the dict it is handed. The cache has to survive
    that, or the second lookup inherits the first one's injected values."""
    first = fe.fetch_fundamentals_edgar("KLAC")
    first["revenue"][0] = 999
    first["_ttm_metrics"] = {"injected": True}
    second = fe.fetch_fundamentals_edgar("KLAC")
    assert second["revenue"][0] != 999
    assert "_ttm_metrics" not in second


def test_a_new_scorecard_version_refetches(counting):
    """A deploy that changes how facts are read must not be served stale data.
    The cache can never be the reason a shipped fix looks like it did not."""
    fe.fetch_fundamentals_edgar("KLAC")
    assert counting.calls == 1
    with patch.object(fe, "SCORECARD_VERSION", "test-next-version"):
        fe.fetch_fundamentals_edgar("KLAC")
    assert counting.calls == 2


def test_a_different_ticker_is_not_served_another_companys_filing(counting):
    fe.fetch_fundamentals_edgar("KLAC")
    fe.fetch_fundamentals_edgar("AMAT")
    assert counting.calls == 2


def test_clearing_the_cache_forces_a_refetch(counting):
    fe.fetch_fundamentals_edgar("KLAC")
    fe.clear_edgar_extract_cache()
    fe.fetch_fundamentals_edgar("KLAC")
    assert counting.calls == 2


def test_the_cache_is_bounded(counting):
    for i in range(fe._EDGAR_EXTRACT_MAX + 8):
        fe.fetch_fundamentals_edgar(f"T{i:03d}")
    assert len(fe._EDGAR_EXTRACT_CACHE) <= fe._EDGAR_EXTRACT_MAX


def test_a_failed_fetch_is_not_cached():
    """A transient SEC failure must not stick for fifteen minutes."""
    with patch.object(fe, "_edgar_cik", return_value=(None, None)):
        assert fe.fetch_fundamentals_edgar("KLAC") is None
    assert fe._EDGAR_EXTRACT_CACHE == {}
