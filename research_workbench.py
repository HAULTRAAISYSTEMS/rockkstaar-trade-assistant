"""Bounded public-data jobs and private evidence records for Company Research."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, date
from threading import Lock, BoundedSemaphore
from urllib.parse import urlparse
import copy
import json
import re
import time
import uuid

from flask import Blueprint, request, jsonify, abort

_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix='research-lab')
_SLOTS = BoundedSemaphore(6)
_LOCK = Lock()
_JOBS = {}
_TTL = {'snapshot': 30, 'financials': 3600, 'quarter': 900, 'earnings': 900, 'benchmarks': 30, 'ttm': 900, 'marketcap': 900}


def symbol(value):
    value = str(value or '').strip().upper()
    if not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,11}', value):
        raise ValueError('Enter a valid ticker.')
    return value


def _load(ticker, part, force):
    if part == 'marketcap':
        from company_market_cap import fetch_market_cap
        return fetch_market_cap(ticker)
    if part == 'snapshot':
        from company_research_data import fetch_company_snapshot
        # Home-listing currency is intentionally unknown until the annual
        # statement loads; do not label foreign-listing figures as USD.
        return fetch_company_snapshot(ticker, home_symbol='', force=force)
    if part == 'financials':
        from fundamentals_engine import get_fundamentals
        return get_fundamentals(ticker, force_refresh=force)
    if part == 'quarter':
        from research_financials import get_quarter
        return get_quarter(ticker, force=force)
    from finnhub_ttm import _finnhub_get, fetch_finnhub_quote
    if part == 'benchmarks':
        return {'SPY': fetch_finnhub_quote('SPY'), 'QQQ': fetch_finnhub_quote('QQQ')}
    if part == 'earnings':
        rows = _finnhub_get('/stock/earnings', {'symbol': ticker, 'limit': 8})
        if not isinstance(rows, list):
            return {'available': False, 'rows': [], 'message': 'Reported EPS and consensus are unavailable from the connected provider.'}
        return {'available': bool(rows), 'rows': [r for r in rows if isinstance(r, dict)][:8],
                'source': 'Finnhub reported earnings; estimates are provider consensus, not company guidance.'}
    if part == 'ttm':
        from finnhub_ttm import fetch_finnhub_financials, compute_ttm
        reports = fetch_finnhub_financials(ticker, force_refresh=force)
        if not reports:
            return {'computed': False, 'message': 'Four comparable quarterly reports are unavailable. Annual and quarterly statements remain separate.'}
        return compute_ttm(reports)
    raise ValueError('Unknown section.')


def _run(key, force):
    try:
        data = _load(*key, force)
        if not isinstance(data, dict):
            raise ValueError('Invalid provider response')
        with _LOCK:
            entry = _JOBS[key]
            entry.update(data=data, completed=time.monotonic(), fetched_at=datetime.now(timezone.utc).isoformat(),
                         running=False, error=None)
    except Exception:
        with _LOCK:
            _JOBS[key].update(running=False, error='This section could not be refreshed. Retry shortly.', failed=time.monotonic())
    finally:
        _SLOTS.release()


def section(ticker, part, force=False):
    """Return immediately; never wait for a provider in the HTTP request."""
    key = (symbol(ticker), part)
    if part not in _TTL:
        raise ValueError('Unknown section.')
    now = time.monotonic()
    with _LOCK:
        entry = _JOBS.get(key)
        if entry is None:
            if len(_JOBS) >= 192:
                idle = [k for k, v in _JOBS.items() if not v.get('running')]
                if not idle:
                    return {'status': 'busy', 'message': 'Research is busy. Retrying shortly.'}
                del _JOBS[min(idle, key=lambda k: _JOBS[k].get('started', 0))]
            entry = _JOBS[key] = {}
        stale = now - entry.get('completed', -1e9) >= _TTL[part]
        cooldown = now - entry.get('started', -1e9) < 15 or now - entry.get('failed', -1e9) < 15
        if (force or stale or 'data' not in entry) and not entry.get('running') and not cooldown:
            if _SLOTS.acquire(blocking=False):
                entry.update(running=True, started=now)
                try:
                    _POOL.submit(_run, key, force)
                except Exception:
                    _SLOTS.release()
                    entry.update(running=False, error='Research is busy. Retry shortly.', failed=now)
            elif 'data' not in entry:
                return {'status': 'busy', 'message': 'Research is busy. Retrying shortly.'}
        return {'status': 'ready' if 'data' in entry else ('loading' if entry.get('running') else 'error'),
                'data': copy.deepcopy(entry.get('data')), 'refreshing': bool(entry.get('running')),
                'stale': stale, 'fetched_at': entry.get('fetched_at'), 'message': entry.get('error')}


KINDS = {'thesis', 'promise', 'relationship', 'exposure'}
STATES = {'unreviewed', 'supported', 'challenged', 'delivered', 'partial', 'missed', 'too_early'}


def validate_record(raw):
    if not isinstance(raw, dict):
        raise ValueError('Enter a valid research record.')
    kind = raw.get('kind')
    if kind not in KINDS:
        raise ValueError('Choose a research record type.')
    out = {'kind': kind, 'ticker': symbol(raw.get('ticker'))}
    for field, limit in [('title', 300), ('detail', 4000), ('counter', 2000), ('evidence', 2000), ('category', 100)]:
        out[field] = str(raw.get(field) or '').strip()
        if len(out[field]) > limit:
            raise ValueError(f'{field.capitalize()} is too long.')
    if not out['title'] or not out['detail']:
        raise ValueError('Add a claim and its supporting detail.')
    for field in ('source_url', 'review_url'):
        value = str(raw.get(field) or '').strip()
        if len(value) > 2000 or (value and (urlparse(value).scheme not in ('http', 'https') or not urlparse(value).netloc or urlparse(value).username)):
            raise ValueError('Source links must be public HTTP or HTTPS URLs.')
        out[field] = value
    for field in ('source_date', 'due_date'):
        value = str(raw.get(field) or '')
        if value:
            date.fromisoformat(value)
        out[field] = value
    out['basis'] = raw.get('basis', 'interpretation')
    if out['basis'] not in ('disclosed', 'interpretation'):
        raise ValueError('Choose disclosed fact or interpretation.')
    if out['basis'] == 'disclosed' and not (out['source_url'] and out['source_date']):
        raise ValueError('A disclosed fact needs its source link and publication date.')
    out['status'] = raw.get('status', 'unreviewed')
    if out['status'] not in STATES:
        raise ValueError('Choose a valid review status.')
    if out['status'] in ('delivered', 'partial', 'missed') and not (out['evidence'] and out['review_url']):
        raise ValueError('A management outcome needs review evidence and a source link.')
    if kind == 'promise' and not (out['source_url'] and out['source_date']):
        raise ValueError('Record the original statement, source link, and date.')
    if kind == 'exposure' and not out['category']:
        raise ValueError('Name the shared exposure, such as semiconductor spending.')
    return out


def _records(uid):
    from database import get_db
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM user_settings WHERE user_id=? AND key LIKE 'research_lab:%'", (uid,)).fetchall()
        records = []
        for row in rows:
            try:
                data = json.loads(row['value'])
                if isinstance(data, dict) and data.get('kind') in KINDS:
                    records.append(data)
            except (ValueError, TypeError):
                pass
        return sorted(records, key=lambda r: r.get('updated_at', ''), reverse=True)
    finally:
        conn.close()


def save_record(uid, raw):
    from database import get_db
    value = validate_record(raw)
    record_id = raw.get('id')
    if record_id and not re.fullmatch(r'[a-f0-9]{32}', str(record_id)):
        raise ValueError('Invalid research record.')
    conn = get_db()
    try:
        stamp = datetime.now(timezone.utc).isoformat()
        if record_id:
            row = conn.execute('SELECT value FROM user_settings WHERE user_id=? AND key=?', (uid, 'research_lab:' + record_id)).fetchone()
            if not row:
                raise ValueError('Research record not found.')
            old = json.loads(row['value'])
            if raw.get('updated_at') != old.get('updated_at'):
                raise ValueError('This record changed in another window. Reload it before saving.')
            value.update(id=record_id, created_at=old['created_at'], updated_at=stamp)
            result = conn.execute('UPDATE user_settings SET value=? WHERE user_id=? AND key=? AND value=?',
                                 (json.dumps(value), uid, 'research_lab:' + record_id, row['value']))
            if result.rowcount != 1:
                raise ValueError('This record changed. Reload it before saving.')
        else:
            count = conn.execute("SELECT COUNT(*) AS n FROM user_settings WHERE user_id=? AND key LIKE 'research_lab:%'", (uid,)).fetchone()['n']
            if count >= 250:
                raise ValueError('Your research notebook is full. Remove an older record first.')
            record_id = uuid.uuid4().hex
            value.update(id=record_id, created_at=stamp, updated_at=stamp)
            conn.execute('INSERT INTO user_settings(user_id,key,value) VALUES(?,?,?)', (uid, 'research_lab:' + record_id, json.dumps(value)))
        conn.commit()
        return value
    finally:
        conn.close()


def create_blueprint(current_user):
    bp = Blueprint('research_lab', __name__)

    @bp.before_request
    def identity():
        user = current_user()
        if not user or not user.get('id'):
            abort(401)

    @bp.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.get('/api/company-lab/<ticker>/<part>')
    def public_section(ticker, part):
        try:
            result = section(ticker, part, request.args.get('refresh') == '1')
            return jsonify(result), 200 if result['status'] == 'ready' else 202
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @bp.get('/api/company-lab-records')
    def records():
        uid = int(current_user()['id'])
        from database import get_user_tracked_tickers, get_stock_data, get_company_profile
        holdings = []
        for ticker in get_user_tracked_tickers(uid)[:60]:
            profile = get_company_profile(ticker) or {}
            stock = get_stock_data(ticker) or {}
            holdings.append({'ticker': ticker, 'sector': profile.get('sector') or stock.get('sector') or 'Unclassified',
                             'industry': profile.get('industry') or stock.get('industry') or 'Unavailable'})
        import research_memory
        return jsonify(records=_records(uid), watchlist=holdings,
                       memory=research_memory.list_cards(uid, limit=50))

    @bp.post('/api/company-lab-records')
    def save():
        try:
            return jsonify(record=save_record(int(current_user()['id']), request.get_json(silent=True)))
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400

    @bp.delete('/api/company-lab-records/<record_id>')
    def delete(record_id):
        if not re.fullmatch(r'[a-f0-9]{32}', record_id):
            abort(404)
        from database import get_db
        conn = get_db()
        try:
            conn.execute('DELETE FROM user_settings WHERE user_id=? AND key=?',
                         (int(current_user()['id']), 'research_lab:' + record_id))
            conn.commit()
        finally:
            conn.close()
        return jsonify(ok=True)

    return bp
