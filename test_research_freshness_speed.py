"""Exercise deadlines, freshness, and partial results without live providers."""
import threading
import time
from concurrent.futures import Future
from unittest.mock import patch

import pytest
import company_research_data as cr
import filing_signals as fs


@pytest.fixture(autouse=True)
def sources(monkeypatch):
    cr._CACHE.clear()
    fs.clear_cache()
    monkeypatch.setattr(cr, 'fetch_finnhub_quote', lambda ticker: {'c': 123, 't': 1794056400})
    monkeypatch.setattr(cr, 'fetch_finnhub_metrics', lambda *a, **kw: {'_raw_metric': {}})
    monkeypatch.setattr(cr, '_profile', lambda ticker: {'description': 'Test company'})
    monkeypatch.setattr(cr, '_news', lambda ticker: [])
    monkeypatch.setattr(cr, '_earnings', lambda ticker: [])
    monkeypatch.setattr(cr, 'fetch_filing_signals', lambda *a, **kw: {'available': True, 'signals': []})
    yield
    cr._CACHE.clear()
    fs.clear_cache()


def test_slow_metrics_cannot_hide_a_completed_quote(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(cr, '_FETCH_TIMEOUT', .15)
    monkeypatch.setattr(cr, 'fetch_finnhub_metrics', lambda *a, **k: release.wait(2))
    try:
        start = time.monotonic()
        result = cr.fetch_company_snapshot('TEST')
        assert time.monotonic() - start < 1
        assert result['valuation']['price'] == 123
        assert result['valuation']['quote_as_of']
        assert 'metrics' in result['unavailable']
        # Completion later cannot mutate the response or cached copy.
        release.set()
        assert cr.fetch_company_snapshot('TEST') == result
    finally:
        release.set()


def test_quote_cache_expires_after_thirty_seconds(monkeypatch):
    first = cr.fetch_company_snapshot('TEST')
    monkeypatch.setattr(cr, 'fetch_finnhub_quote', lambda ticker: {'c': 456})
    assert cr.fetch_company_snapshot('TEST')['valuation']['price'] == 123
    stamp, data = cr._CACHE[('TEST', '$')]
    cr._CACHE[('TEST', '$')] = (stamp - 31, data)
    assert cr.fetch_company_snapshot('TEST')['valuation']['price'] == 456
    assert first['valuation']['price'] == 123


def test_force_refresh_propagates_to_filings_and_metrics(monkeypatch):
    with patch.object(cr, 'fetch_filing_signals', return_value={'available': True}) as filings, \
         patch.object(cr, 'fetch_finnhub_metrics', return_value={'_raw_metric': {}}) as metrics:
        cr.fetch_company_snapshot('TEST', force=True)
    filings.assert_called_once_with('TEST', force_refresh=True)
    metrics.assert_called_once_with('TEST', force_refresh=True)


def test_saturated_pool_returns_explicit_unavailable_without_waiting(monkeypatch):
    monkeypatch.setattr(cr, '_submit', lambda fn: None)
    result = cr.fetch_company_snapshot('TEST')
    assert len(result['unavailable']) == 6
    assert result['valuation']['price'] is None
    assert result['filing_signals']['available'] is False


def test_submission_capacity_is_released_on_completion_and_cancel(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    future = Future()
    monkeypatch.setattr(cr, '_SLOTS', slots)
    with patch.object(cr._POOL, 'submit', return_value=future):
        assert cr._submit(lambda: 1) is future
        assert cr._submit(lambda: 2) is None
        future.cancel()
        assert slots.acquire(blocking=False)
        slots.release()


def test_filing_refresh_bypasses_old_metadata(monkeypatch):
    import fundamentals_engine as fe
    from test_filing_signals import submissions
    from test_edgar_pipeline import _Resp
    payload = submissions([('10-Q', '2026-11-01', '')])
    fs._CACHE['TEST'] = (time.time(), {'available': True, 'signals': [], 'marker': 'old'})
    monkeypatch.setattr(fe, '_edgar_cik', lambda _: ('123', 'Test'))
    with patch.object(fe._req_module, 'get', return_value=_Resp(payload)) as get:
        assert fs.fetch_filing_signals('TEST')['marker'] == 'old'
        result = fs.fetch_filing_signals('TEST', force_refresh=True)
    assert 'marker' not in result
    assert get.call_count == 1


def test_new_filing_snapshot_overrides_old_annual_scorecard():
    import app
    with patch('research_feed_phase2.list_published', return_value=[]), \
         patch('research_memory.list_cards', return_value=[]), \
         patch.object(app, 'get_user_setting', return_value=''), \
         patch.object(app._intel, 'get_intel_summary', return_value={}):
        result = app._company_research_context(1, 'TEST',
            {'filing_signals': {'latest_financial_report': {'period_end': '2025-12-31'}}}, {},
            {'filing_signals': {'latest_financial_report': {'period_end': '2026-09-30'}}})
    assert result['latest_filing']['period_end'] == '2026-09-30'
