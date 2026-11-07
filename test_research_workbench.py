"""Research shell, asynchronous source isolation, and private record regressions."""
import sqlite3
from threading import Event
from unittest.mock import patch

import pytest
import research_workbench as lab


@pytest.fixture
def notebook_db(tmp_path, monkeypatch):
    import database
    path = tmp_path / 'notes.db'
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn
    with connect() as conn:
        conn.execute('CREATE TABLE user_settings(user_id INTEGER, key TEXT, value TEXT, UNIQUE(user_id,key))')
    monkeypatch.setattr(database, 'get_db', connect)
    return connect


def record(**kw):
    return dict(kind='thesis', ticker='ISRG', title='Demand grows', detail='Check installed base and procedure growth', **kw)


def test_record_ownership_and_stale_edit(notebook_db):
    saved = lab.save_record(1, record())
    assert lab._records(2) == []
    with pytest.raises(ValueError, match='not found'):
        lab.save_record(2, saved)
    updated = lab.save_record(1, dict(saved, detail='New evidence'))
    assert updated['created_at'] == saved['created_at']
    with pytest.raises(ValueError, match='another window'):
        lab.save_record(1, saved)
    assert lab._records(1)[0]['detail'] == 'New evidence'


@pytest.mark.parametrize('changes', [
    {'basis': 'disclosed'}, {'kind': 'promise'},
    {'status': 'delivered'}, {'kind': 'exposure'},
    {'source_url': 'javascript:alert(1)'}, {'source_url': 'https://password@example.com'},
    {'source_date': '2026-13-40'}, {'ticker': '../../bad'},
])
def test_evidence_validation(changes):
    raw = record(); raw.update(changes)
    with pytest.raises(ValueError):
        lab.validate_record(raw)


def test_source_job_never_blocks_and_deduplicates(monkeypatch):
    entered, release, done = Event(), Event(), Event()
    def slow(*args):
        entered.set()
        assert release.wait(5)
        return {'available': True}
    monkeypatch.setattr(lab, '_load', slow)
    key = ('LABTEST', 'quarter')
    lab._JOBS.pop(key, None)
    try:
        first = lab.section(*key)
        assert first['status'] == 'loading'
        assert entered.wait(1)
        started = lab._JOBS[key]['started']
        assert lab.section(*key, force=True)['status'] == 'loading'
        assert lab._JOBS[key]['started'] == started
    finally:
        release.set()
    # Queue completion behind the active task without sleeping/polling.
    with lab._LOCK:
        pass
    for _ in range(1000):
        if not lab._JOBS[key].get('running'): break
        done.wait(.001)
    assert lab.section(*key)['data'] == {'available': True}


def test_failed_refresh_preserves_previous_data(monkeypatch):
    key = ('LABFAIL', 'quarter')
    lab._JOBS[key] = {'data': {'period_end': '2026-06-30'}, 'running': True}
    monkeypatch.setattr(lab, '_load', lambda *a: (_ for _ in ()).throw(RuntimeError('provider secret')))
    assert lab._SLOTS.acquire(False)
    lab._run(key, True)
    result = lab.section(*key)
    assert result['data']['period_end'] == '2026-06-30'
    assert result['stale'] and 'secret' not in result['message']


def test_api_requires_identity_and_delete_is_scoped(notebook_db):
    from flask import Flask
    app = Flask(__name__)
    identity = {'id': 1}
    app.register_blueprint(lab.create_blueprint(lambda: identity))
    client = app.test_client()
    saved = lab.save_record(2, record())
    response = client.delete('/api/company-lab-records/' + saved['id'])
    assert response.status_code == 200
    assert len(lab._records(2)) == 1
    identity.clear()
    assert client.get('/api/company-lab/ISRG/quarter').status_code == 401
    assert client.post('/api/company-lab-records', json=record()).status_code == 401


def test_research_shell_does_not_fetch_providers():
    import app
    with patch('fundamentals_engine.get_fundamentals', side_effect=AssertionError('blocking fetch')), \
         patch('company_research_data.fetch_company_snapshot', side_effect=AssertionError('blocking fetch')), \
         patch('research_financials.get_quarter', side_effect=AssertionError('blocking fetch')), \
         patch.object(app, 'render_template', side_effect=lambda name, **kw: (name, kw)):
        with app.app.test_request_context('/research?ticker=ISRG'):
            assert app.research_page() == ('research_workbench.html', {'ticker': 'ISRG'})
