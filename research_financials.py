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
            start, raw, ytd = fact.get('start'), fact['value'], None
            if key == 'cash':
                start, raw, ytd = _cash_quarter(facts, fact)
            value = _fmt_statement_value(raw, '' if row_key == 'sh' else '$', row_key == 'eps')
            row = {'label': label, 'values': [value], 'period_start': start, 'period_end': end}
            if ytd is not None:
                row['ytd_value'] = _fmt_statement_value(ytd, '$', False)
                row['ytd_start'] = fact.get('start')
            rows.append(row)
        if key == 'cash' and rows:
            quarters = sum(1 for r in rows if 'ytd_value' in r or _span(r) <= 100)
            basis = 'Quarter' if quarters == len(rows) else ('Year to date' if not quarters else 'Quarter where derivable')
        if rows:
            tables.append({'key': key, 'title': title, 'periods': [end], 'basis': basis, 'rows': rows})
    accn = (latest['fields'].get('rev') or {}).get('accn')
    url = f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace("-", "")}/{accn}-index.html' if cik and accn else None
    return {'available': bool(tables), 'period_end': end, 'filed': latest['filed'],
            'form': latest['form'], 'url': url, 'statements': tables}


def _span(row):
    return qf._days({'start': row.get('period_start'), 'end': row.get('period_end')}) or 0


def _cash_quarter(facts, fact):
    """Cash-flow statements in a 10-Q are year to date (6 or 9 months). The
    quarter on its own is this filing's year-to-date figure less the previous
    10-Q's, both on the same tag and fiscal-year start. Returns
    (start, value, ytd_value); ytd_value is None when the figure already is a
    single quarter or when the prior figure is not filed."""
    from datetime import datetime, timedelta
    days = qf._days(fact)
    if days is None or days <= 100:
        return fact.get('start'), fact['value'], None
    start, end = fact.get('start'), fact.get('end')
    prior = [f for f in qf._facts_for(fact.get('tag') or '', facts)
             if f.get('start') == start and (f.get('end') or '') < end
             and f.get('val') is not None and 80 <= (qf._days(f) or 0) <= days - 80]
    if not prior:
        return start, fact['value'], None
    prior.sort(key=lambda f: (f.get('end') or '', f.get('filed') or ''))
    best = prior[-1]
    gap = (datetime.strptime(end, '%Y-%m-%d') - datetime.strptime(best['end'], '%Y-%m-%d')).days
    if not 80 <= gap <= 100:
        return start, fact['value'], None
    q_start = (datetime.strptime(best['end'], '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    return q_start, fact['value'] - best['val'], fact['value']


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
