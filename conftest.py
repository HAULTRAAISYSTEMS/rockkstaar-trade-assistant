"""Shared test setup.

The EDGAR extraction is cached per worker so that force-refreshing a scorecard
does not re-download and re-parse tens of megabytes of company facts. Tests all
fetch the same ticker against different stubbed filings, so without this the
second test in a file would score the first one's data.
"""
import pytest

import fundamentals_engine


@pytest.fixture(autouse=True)
def _clear_edgar_extract_cache():
    fundamentals_engine.clear_edgar_extract_cache()
    yield
    fundamentals_engine.clear_edgar_extract_cache()
