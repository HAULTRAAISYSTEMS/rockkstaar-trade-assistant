"""Latest available SEC quarter for Company Research, separate from annual scoring."""
import copy
import time

import quarter_facts as qf
from fundamentals_engine import _fmt_statement_value

_CACHE = {}


def build_quarter(facts, cik):
    # This view supports US-GAAP USD statements only. Do not mix currency or
    # per-share units when companyfacts contains multiple unit arrays.
    facts = copy.deepcopy(facts)
    gaap = (facts.get('facts') or {}).get('us-gaap') or {}
    for tag, node in gaap.items():
        unit = 'USD/shares' if tag.startswith('EarningsPerShare') else ('shares' if 'WeightedAverage' in tag and 'Shares' in tag else 'USD')
        node['units'] = {unit: (node.get('units') or {}).get(unit, [])}
    latest = qf.latest_quarter('', facts=facts)
    if not latest:
        return {'available': False, 'error': 'Quarterly USD statements are unavailable from SEC US-GAAP data for this company. Annual financials remain available.'}
    end = latest['period_end']
    shape = latest['profile']['shape']
    tables = []
    for key, title, definitions, window, basis in (
        ('income', 'Income Statement', qf.INCOME_ROWS_BY_SHAPE.get(shape) or qf.INCOME_ROWS, qf.QUARTER_DAYS, 'Quarter'),
        ('balance', 'Balance Sheet', qf.BALANCE_ROWS_BY_SHAPE.get(shape) or qf.BALANCE_ROWS, None, 'As of'),
        ('cash', 'Cash Flow Statement', qf.CASH_ROWS, (80, 400), 'Year to date'),
    ):
        rows = []
        for row_key, label, _indent, tags in definitions:
            row_facts = facts
            if key == 'cash':
                # When both standalone and cumulative cash flow are tagged,
                # select the longest current fiscal span, independent of order.
                candidates = [f for tag in tags for f in qf._facts_for(tag, facts)
                              if f.get('end') == end and qf._days(f) is not None
                              and 80 <= qf._days(f) <= 400]
                longest = max((qf._days(f) for f in candidates), default=None)
                row_facts = {'facts': {'us-gaap': {
                    tag: {'units': {'USD': [f for f in qf._facts_for(tag, facts)
                                            if qf._days(f) == longest]}}
                    for tag in tags}}}
            fact = qf._pick(row_facts, tags, window=window, end=end)
            if not fact or fact.get('value') is None:
                continue
            value = _fmt_statement_value(fact['value'], '' if row_key == 'sh' else '$', row_key == 'eps')
            rows.append({'label': label, 'values': [value],
                         'period_start': fact.get('start'), 'period_end': end})
        if rows:
            tables.append({'key': key, 'title': title, 'periods': [end], 'basis': basis, 'rows': rows})
    accn = (latest['fields'].get('rev') or {}).get('accn')
    url = f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace("-", "")}/{accn}-index.html' if cik and accn else None
    return {'available': bool(tables), 'period_end': end, 'filed': latest['filed'],
            'form': latest['form'], 'url': url, 'statements': tables}


def get_quarter(ticker, force=False):
    ticker = ticker.upper()
    cached = _CACHE.get(ticker)
    if not force and cached and time.monotonic() - cached[0] < (900 if cached[1]['available'] else 60):
        return copy.deepcopy(cached[1])
    try:
        facts, cik = qf.fetch_facts(ticker)
        result = build_quarter(facts, cik) if facts else {'available': False, 'error': 'Quarterly financials could not be loaded from SEC EDGAR. Try Refresh, or select Annual.'}
    except Exception:
        result = {'available': False, 'error': 'Quarterly financials could not be loaded from SEC EDGAR. Try Refresh, or select Annual.'}
    if len(_CACHE) >= 128:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[ticker] = (time.monotonic(), result)
    return copy.deepcopy(result)
