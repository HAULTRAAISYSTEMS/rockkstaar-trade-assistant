import copy
from unittest.mock import patch

from flask import render_template, session

import research_financials as rf
from test_quarter_facts import FIXTURE, facts, duration


def test_latest_quarter_separates_income_from_ytd_cash_and_balance():
    result = rf.build_quarter(FIXTURE, '123')
    assert result['period_end'] == '2026-06-30'
    tables = {s['key']: s for s in result['statements']}
    assert tables['income']['rows'][0]['values'] == ['$60.8K']
    assert tables['income']['rows'][0]['period_start'] == '2026-04-01'
    assert tables['cash']['basis'] == 'Year to date'
    assert tables['cash']['rows'][0]['period_start'] == '2026-01-01'
    assert tables['balance']['rows'][0]['period_start'] is None


def test_q1_cash_flows_are_not_dropped():
    payload = facts(duration('Revenues', [('2026-01-01','2026-03-31',100,'10-Q','2026-05-01')]),
                    duration('NetCashProvidedByUsedInOperatingActivities', [('2026-01-01','2026-03-31',20,'10-Q','2026-05-01')]))
    result = rf.build_quarter(payload, '123')
    cash = next(s for s in result['statements'] if s['key'] == 'cash')
    assert cash['rows'][0]['values'] == ['$20.00']


def test_currency_units_are_not_mixed_or_mutated():
    payload = copy.deepcopy(FIXTURE)
    node = payload['facts']['us-gaap']['RevenueFromContractWithCustomerExcludingAssessedTax']
    node['units']['EUR'] = [dict(node['units']['USD'][0], end='2026-09-30', start='2026-07-01', val=999)]
    assert rf.build_quarter(payload, '123')['period_end'] == '2026-06-30'
    assert 'EUR' in node['units']
    del node['units']['USD']
    assert rf.build_quarter(payload, '123')['available'] is False


def test_refresh_bypasses_cache_and_returns_independent_data():
    rf._CACHE.clear()
    with patch.object(rf.qf, 'fetch_facts', return_value=(FIXTURE, '123')) as fetch:
        rf.get_quarter('TEST')['statements'].clear()
        assert rf.get_quarter('TEST')['statements']
        assert fetch.call_count == 1
        rf.get_quarter('TEST', force=True)
        assert fetch.call_count == 2
    rf._CACHE.clear()


def test_fetch_failure_is_explicit():
    with patch.object(rf.qf, 'fetch_facts', side_effect=RuntimeError('private provider error')):
        result = rf.get_quarter('FAIL', force=True)
    assert not result['available']
    assert 'private' not in result['error']
    rf._CACHE.clear()


def test_quarterly_default_and_annual_view_render_separately():
    import web_app
    from test_company_research_workspace import sample_fundamentals
    research = {'price':None, 'change_pct':None, 'changes':[], 'memory_cards':[], 'source_count':0,
                'latest_filing': {'period_end':'2026-09-30','form':'10-Q','filed':'2026-11-01'}}
    with web_app.app.test_request_context('/research?ticker=TEST'):
        session.update(user_id=7, username='researcher', is_admin=0)
        args = dict(ticker='TEST',data=sample_fundamentals(),error=None,stock={},research=research,suggestions=[],quarter=rf.build_quarter(FIXTURE,'123'))
        html = render_template('research.html', **args)
        assert 'Latest available quarter 2026-06-30' in html
        assert 'A newer financial filing covers 2026-09-30' in html
        assert 'Cash Flow Statement · Year to date' in html
        assert 'Annual figures in' not in html
        html = render_template('research.html', **args, financial_period='annual')
        assert 'Annual figures in' in html
        assert 'Cash Flow Statement · Year to date' not in html


def test_cash_uses_cumulative_span_even_when_quarter_is_last():
    payload = copy.deepcopy(FIXTURE)
    rows = payload['facts']['us-gaap']['NetCashProvidedByUsedInOperatingActivities']['units']['USD']
    rows.append(dict(rows[0], start='2026-04-01', val=7))
    result = rf.build_quarter(payload, '123')
    cash = next(s for s in result['statements'] if s['key'] == 'cash')
    cfo = next(r for r in cash['rows'] if 'operating activities' in r['label'])
    assert cfo['values'] == ['$64.1K']
    assert cfo['period_start'] == '2026-01-01'


def test_route_defaults_to_quarter_and_skips_fetch_for_annual():
    import app
    from test_company_research_workspace import sample_fundamentals
    with patch('fundamentals_engine.get_fundamentals', return_value=sample_fundamentals()), \
         patch.object(app, 'get_stock_data', return_value={}), \
         patch('company_research_data.fetch_company_snapshot', return_value={}), \
         patch.object(app, '_company_research_context', return_value={}), \
         patch.object(app, 'current_user_id', return_value=7), \
         patch.object(app, 'get_user_tracked_tickers', return_value=[]), \
         patch.object(app, 'render_template', side_effect=lambda *a, **kw: kw), \
         patch.object(rf, 'get_quarter', return_value={'available': False}) as fetch:
        with app.app.test_request_context('/research?ticker=ISRG&refresh=1'):
            result = app.research_detail()
        assert result['financial_period'] == 'quarterly'
        fetch.assert_called_once_with('ISRG', force=True)
        fetch.reset_mock()
        with app.app.test_request_context('/research?ticker=ISRG&period=annual'):
            result = app.research_detail()
        assert result['financial_period'] == 'annual'
        fetch.assert_not_called()


def test_quarterly_cash_flow_is_the_quarter_not_year_to_date():
    """CRWV showed -$1.4B net income on Quarterly: that was Jan-Jun. The
    quarter is the 6-month figure less the Q1 10-Q's 3-month figure."""
    payload = copy.deepcopy(FIXTURE)
    cfo = payload['facts']['us-gaap']['NetCashProvidedByUsedInOperatingActivities']['units']['USD']
    cfo.append({"start": "2026-01-01", "end": "2026-03-31", "val": 30000, "form": "10-Q", "filed": "2026-05-01", "accn": "q1"})
    cash = next(s for s in rf.build_quarter(payload, '123')['statements'] if s['key'] == 'cash')
    row = next(r for r in cash['rows'] if 'operating activities' in r['label'])
    assert row['values'] == ['$34.1K'] and row['period_start'] == '2026-04-01'
    assert row['ytd_value'] == '$64.1K' and row['ytd_start'] == '2026-01-01'


def test_cash_falls_back_to_ytd_when_prior_quarter_missing():
    cash = next(s for s in rf.build_quarter(FIXTURE, '123')['statements'] if s['key'] == 'cash')
    row = next(r for r in cash['rows'] if 'operating activities' in r['label'])
    assert row['period_start'] == '2026-01-01' and 'ytd_value' not in row
