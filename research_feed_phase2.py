"""Phase 2 query/edit/user-state services for Tradestaar Live Research Feed."""
from __future__ import annotations
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
        if not conn.execute("SELECT 1 FROM research_posts WHERE id=?",(post_id,)).fetchone(): raise core.ResearchValidationError("research post not found")
        conn.execute("""UPDATE research_posts SET ticker=?,company_name=?,headline=?,research_notes=?,category=?,sentiment=?,source_name=?,source_url=?,tradestaar_take=?,take_origin=?,should_notify=?,updated_at=? WHERE id=?""",
        (clean['ticker'],clean['company_name'],clean['headline'],clean['research_notes'],clean['category'],clean['sentiment'],clean['source_name'],clean['source_url'],clean['tradestaar_take'],clean['take_origin'],clean['should_notify'],_now(),post_id))
        _replace_metrics(conn,post_id,metrics); conn.commit()
    except Exception: conn.rollback(); raise
    finally:
        if owns: conn.close()

def delete_post(post_id,actor,conn=None):
    _admin(actor); owns=conn is None; conn=conn or get_db()
    try:
        if not conn.execute("SELECT 1 FROM research_posts WHERE id=?",(post_id,)).fetchone(): raise core.ResearchValidationError("research post not found")
        conn.execute("DELETE FROM research_metrics WHERE post_id=?",(post_id,)); conn.execute("DELETE FROM research_saved_posts WHERE post_id=?",(post_id,)); conn.execute("DELETE FROM research_posts WHERE id=?",(post_id,)); conn.commit()
    except Exception: conn.rollback(); raise
    finally:
        if owns: conn.close()

def _metric_map(conn,ids):
    if not ids:return {}
    ph=','.join('?' for _ in ids); rows=conn.execute(f"SELECT * FROM research_metrics WHERE post_id IN ({ph}) ORDER BY sort_order,id",tuple(ids)).fetchall(); out={x:[] for x in ids}
    for r in rows: d=dict(r); out.setdefault(d['post_id'],[]).append(d)
    return out

def list_admin_posts(actor,limit=100,conn=None):
    _admin(actor); owns=conn is None; conn=conn or get_db()
    try:
        posts=[dict(r) for r in conn.execute("SELECT * FROM research_posts ORDER BY updated_at DESC LIMIT ?",(max(1,min(int(limit),200)),)).fetchall()]; mm=_metric_map(conn,[p['id'] for p in posts])
        for p in posts:p['metrics']=mm.get(p['id'],[])
        return posts
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
