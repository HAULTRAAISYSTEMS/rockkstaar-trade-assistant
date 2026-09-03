"""Shared test setup.

The EDGAR extraction is cached per worker so that force-refreshing a scorecard
does not re-download and re-parse tens of megabytes of company facts. Tests all
fetch the same ticker against different stubbed filings, so without this the
second test in a file would score the first one's data.
"""
import os

import pytest

# app.py starts the scanner, the intel alert loop, two cache pre-warms and the
# demo seed at import time. A test that imports it for one function otherwise
# fires all of that: real network calls, writes to whatever database the
# environment points at, and pages of log noise interleaved with the results.
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")
os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")

import fundamentals_engine


@pytest.fixture(autouse=True)
def _clear_edgar_extract_cache():
    fundamentals_engine.clear_edgar_extract_cache()
    yield
    fundamentals_engine.clear_edgar_extract_cache()
