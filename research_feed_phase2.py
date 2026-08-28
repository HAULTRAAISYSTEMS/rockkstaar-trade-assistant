"""Phase 2 query/edit/user-state services for Tradestaar Live Research Feed."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from database import get_db
import research_feed as core


def _now(): return core._now()
def _admin(actor): return core._assert_admin(actor)

def _replace_metrics(conn, post_id, metrics):
    conn.execute("DELETE FROM research_metrics WHERE post_id=?", (post_id,))
    for i, raw in enumerate(metrics or []):
        m=core.validate_metric(raw,i)
        conn.execute("""INSERT INTO research_metrics
        (id,post_id,metric_type,label,actual_value,expected_value,previous_value,unit,period,comparison,notes,sort_order)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",(str(uuid4()),post_id,m['metric_type'],m['label'],m['actual_value'],m['expected_value'],m['previous_value'],m['unit'],m['period'],m['comparison'],m['notes'],m['sort_order']))

def update_post(post_id,data,actor,metrics=None,conn=None):
    _admin(actor); clean=core.validate_post(data); owns=conn is None; conn=conn or get_db()
    try:
        row=conn.execute("SELECT status FROM research_posts WHERE id=?",(post_id,)).fetchone()
        if not row: raise core.ResearchValidationError("research post not found")
        if row['status'] not in {'draft','published'}: raise core.ResearchValidationError("only drafts or published research can be edited")
        conn.execute("""UPDATE research_posts SET ticker=?,company_name=?,headline=?,research_notes=?,category=?,sentiment=?,source_name=?,source_url=?,tradestaar_take=?,take_origin=?,should_notify=?,priority=?,catalyst_type=?,source_published_at=?,updated_at=? WHERE id=?""",
        (clean['ticker'],clean['company_name'],clean['headline'],clean['research_notes'],clean['category'],clean['sentiment'],clean['source_name'],clean['source_url'],clean['tradestaar_take'],clean['take_origin'],clean['should_notify'],clean['priority'],clean['catalyst_type'],clean['source_published_at'],_now(),post_id))
        _replace_metrics(conn,post_id,metrics); conn.commit()
    except Exception: conn.rollback(); raise
    finally:
        if owns: conn.close()

def delete_post(post_id,actor,conn=None):
    _admin(actor); owns=conn is None; conn=conn or get_db()
    try:
        row=conn.execute("SELECT status FROM research_posts WHERE id=?",(post_id,)).fetchone()
        if not row: raise core.ResearchValidationError("research post not found")
        if row['status']!='draft': raise core.ResearchValidationError("only drafts can be deleted")
        conn.execute("DELETE FROM research_metrics WHERE post_id=?",(post_id,)); conn.execute("DELETE FROM research_saved_posts WHERE post_id=?",(post_id,)); conn.execute("DELETE FROM research_posts WHERE id=?",(post_id,)); conn.commit()
    except Exception: conn.rollback(); raise
    finally:
        if owns: conn.close()

def _metric_map(conn,ids):
    if not ids:return {}
    ph=','.join('?' for _ in ids); rows=conn.execute(f"SELECT * FROM research_metrics WHERE post_id IN ({ph}) ORDER BY sort_order,id",tuple(ids)).fetchall(); out={x:[] for x in ids}
    for r in rows: d=dict(r); out.setdefault(d['post_id'],[]).append(d)
    return out

def _admin_filters(*, query=None, catalyst=None, source=None, priority=None,
                   time_window=None, status=None):
    clauses=[]; params=[]
    if query:
        term='%'+core._text(query)[:100]+'%'; clauses.append('(ticker LIKE ? OR company_name LIKE ? OR headline LIKE ?)'); params.extend([term]*3)
    if catalyst:
        clauses.append('catalyst_type=?'); params.append(core._choice(str(catalyst).upper(),core.CATALYST_TYPES,'catalyst_type'))
    if source:
        clauses.append('source_name LIKE ?'); params.append('%'+core._text(source)[:100]+'%')
    if priority:
        clauses.append('priority=?'); params.append(core._choice(priority,core.PRIORITIES,'priority'))
    if status:
        clauses.append('status=?'); params.append(core._choice(status,core.STATUSES,'status'))
    windows={'1h':1,'6h':6,'24h':24,'7d':168,'30d':720}
    if time_window:
        if time_window not in windows: raise core.ResearchValidationError('invalid time window')
        threshold=(datetime.now(timezone.utc)-timedelta(hours=windows[time_window])).isoformat()
        clauses.append('COALESCE(source_published_at,created_at)>=?'); params.append(threshold)
    return clauses,params

def list_admin_posts(actor,limit=200,conn=None,**filters):
    _admin(actor); owns=conn is None; conn=conn or get_db()
    try:
        clauses,params=_admin_filters(**filters); where=(' WHERE '+' AND '.join(clauses)) if clauses else ''
        params.append(max(1,min(int(limit),500)))
        posts=[dict(r) for r in conn.execute(
            "SELECT * FROM research_posts"+where+
            " ORDER BY CASE priority WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 WHEN 'Medium' THEN 3 ELSE 4 END, "
            "COALESCE(source_published_at,created_at) DESC, updated_at DESC LIMIT ?",tuple(params)).fetchall()]
        mm=_metric_map(conn,[p['id'] for p in posts])
        for p in posts:p['metrics']=mm.get(p['id'],[])
        return posts
    finally:
        if owns:conn.close()

def admin_status_counts(actor,conn=None):
    _admin(actor); owns=conn is None;conn=conn or get_db()
    try:
        counts={status:0 for status in core.STATUSES}
        for row in conn.execute('SELECT status,COUNT(*) AS count FROM research_posts GROUP BY status').fetchall():counts[row['status']]=row['count']
        return counts
    finally:
        if owns:conn.close()

def transition_post(post_id,action,actor,conn=None):
    _admin(actor);owns=conn is None;conn=conn or get_db();action=str(action or '').lower()
    target={'approve':'draft','reject':'rejected'}.get(action)
    if not target:raise core.ResearchValidationError('invalid review action')
    try:
        row=conn.execute('SELECT id,status FROM research_posts WHERE id=?',(post_id,)).fetchone()
        if not row:raise core.ResearchValidationError('research post not found')
        if row['status']!='incoming':raise core.ResearchValidationError('only incoming intelligence can be approved or rejected')
        now=_now();conn.execute('UPDATE research_posts SET status=?,reviewed_at=?,reviewed_by_user_id=?,updated_at=? WHERE id=?',(target,now,int(actor['id']),now,post_id));conn.commit();return target
    except Exception:conn.rollback();raise
    finally:
        if owns:conn.close()

def bulk_transition(post_ids,action,actor,conn=None):
    _admin(actor);ids=list(dict.fromkeys(str(x) for x in (post_ids or []) if str(x)))
    if not ids or len(ids)>100:raise core.ResearchValidationError('select between 1 and 100 posts')
    action=str(action or '').lower();requirements={'approve':('incoming','draft'),'reject':('incoming','rejected'),'publish':('draft','published')}
    if action not in requirements:raise core.ResearchValidationError('invalid bulk action')
    required,target=requirements[action];owns=conn is None;conn=conn or get_db()
    try:
        ph=','.join('?' for _ in ids);rows=conn.execute(f'SELECT id,status,ticker FROM research_posts WHERE id IN ({ph})',tuple(ids)).fetchall()
        if len(rows)!=len(ids):raise core.ResearchValidationError('one or more selected posts were not found')
        invalid=[r['id'] for r in rows if r['status']!=required]
        if invalid:raise core.ResearchValidationError(f'bulk {action} requires {required} posts only')
        now=_now()
        if action=='publish':
            conn.execute(f"UPDATE research_posts SET status='published',published_at=?,updated_at=? WHERE id IN ({ph})",(now,now,*ids))
        else:
            conn.execute(f'UPDATE research_posts SET status=?,reviewed_at=?,reviewed_by_user_id=?,updated_at=? WHERE id IN ({ph})',(target,now,int(actor['id']),now,*ids))
        conn.commit();return {'action':action,'status':target,'posts':[{'id':r['id'],'ticker':r['ticker']} for r in rows]}
    except Exception:conn.rollback();raise
    finally:
        if owns:conn.close()

def list_published(*,ticker=None,category=None,sentiment=None,search=None,watchlist_tickers=None,saved_by_user=None,user_id=None,limit=50,conn=None):
    clauses=["p.status='published'"]; params=[]
    if ticker:clauses.append('p.ticker=?');params.append(core.normalize_ticker(ticker))
    if category:clauses.append('p.category=?');params.append(core._choice(category,core.CATEGORIES,'category'))
    if sentiment:clauses.append('p.sentiment=?');params.append(core._choice(sentiment,core.SENTIMENTS,'sentiment'))
    if search:
        term='%'+core._text(search)[:100]+'%';clauses.append('(p.ticker LIKE ? OR p.company_name LIKE ? OR p.headline LIKE ? OR p.research_notes LIKE ?)');params.extend([term]*4)
    if watchlist_tickers is not None:
        clean=[core.normalize_ticker(t) for t in watchlist_tickers if core._text(t)]
        if not clean:return []
        clauses.append('p.ticker IN ('+','.join('?' for _ in clean)+')');params.extend(clean)
    if saved_by_user is not None:clauses.append('EXISTS (SELECT 1 FROM research_saved_posts s WHERE s.post_id=p.id AND s.user_id=?)');params.append(int(saved_by_user))
    params.append(max(1,min(int(limit),100))); owns=conn is None; conn=conn or get_db()
    try:
        posts=[dict(r) for r in conn.execute(f"SELECT p.* FROM research_posts p WHERE {' AND '.join(clauses)} ORDER BY p.published_at DESC LIMIT ?",tuple(params)).fetchall()]; mm=_metric_map(conn,[p['id'] for p in posts]); saved=set()
        if user_id is not None and posts:
            ids=[p['id'] for p in posts]; ph=','.join('?' for _ in ids); saved={r['post_id'] for r in conn.execute(f"SELECT post_id FROM research_saved_posts WHERE user_id=? AND post_id IN ({ph})",(int(user_id),*ids)).fetchall()}
        for p in posts:p['metrics']=mm.get(p['id'],[]);p['saved']=p['id'] in saved
        return posts
    finally:
        if owns:conn.close()

def set_bookmark(user_id,post_id,saved,conn=None):
    owns=conn is None;conn=conn or get_db()
    try:
        if not conn.execute("SELECT 1 FROM research_posts WHERE id=? AND status='published'",(post_id,)).fetchone():raise core.ResearchValidationError('published research post not found')
        if saved: conn.execute("INSERT OR IGNORE INTO research_saved_posts (user_id,post_id,saved_at) VALUES (?,?,?)",(int(user_id),post_id,_now()))
        else: conn.execute("DELETE FROM research_saved_posts WHERE user_id=? AND post_id=?",(int(user_id),post_id))
        conn.commit()
    except Exception:conn.rollback();raise
    finally:
        if owns:conn.close()

def set_alert_preference(user_id,ticker,enabled,conn=None):
    uid=int(user_id);ticker=core.normalize_ticker(ticker);owns=conn is None;conn=conn or get_db()
    try:
        if conn.execute("SELECT 1 FROM research_alert_preferences WHERE user_id=? AND ticker=?",(uid,ticker)).fetchone():conn.execute("UPDATE research_alert_preferences SET enabled=?,updated_at=? WHERE user_id=? AND ticker=?",(1 if enabled else 0,_now(),uid,ticker))
        else:conn.execute("INSERT INTO research_alert_preferences (user_id,ticker,enabled,created_at,updated_at) VALUES (?,?,?,?,?)",(uid,ticker,1 if enabled else 0,_now(),_now()))
        conn.commit()
    except Exception:conn.rollback();raise
    finally:
        if owns:conn.close()

def get_alert_preferences(user_id,tickers=None,conn=None):
    owns=conn is None;conn=conn or get_db();params=[int(user_id)];clause='user_id=?'
    try:
        if tickers:
            clean=[core.normalize_ticker(t) for t in tickers];clause+=' AND ticker IN ('+','.join('?' for _ in clean)+')';params.extend(clean)
        return {r['ticker']:bool(r['enabled']) for r in conn.execute(f"SELECT ticker,enabled FROM research_alert_preferences WHERE {clause}",tuple(params)).fetchall()}
    finally:
        if owns:conn.close()
