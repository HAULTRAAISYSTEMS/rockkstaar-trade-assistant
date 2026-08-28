"""Flask Blueprint for Tradestaar Live Research Feed Phase 2/3/4."""
from __future__ import annotations
from flask import Blueprint, jsonify, render_template, request, session
from database import get_user_setting, set_user_setting
import research_feed as rf
import research_feed_phase2 as svc
import live_research_realtime as realtime
import live_research_search as research_search

def create_live_research_blueprint(*, require_admin, current_user, tracked_tickers):
    bp=Blueprint('live_research',__name__)
    def actor(): return current_user() or {'id':session.get('user_id'),'is_admin':session.get('is_admin',0)}
    def uid():
        u=actor()
        if not u or not u.get('id'): raise rf.ResearchPermissionError('authenticated user required')
        return int(u['id'])
    def metrics(data):
        value=data.get('metrics') or []
        if not isinstance(value,list): raise rf.ResearchValidationError('metrics must be a list')
        return value
    @bp.errorhandler(rf.ResearchValidationError)
    def bad(e): return jsonify({'ok':False,'error':str(e)}),400
    @bp.errorhandler(rf.ResearchPermissionError)
    def denied(e): return jsonify({'ok':False,'error':str(e)}),403
    @bp.get('/live-research')
    def feed():
        user=uid(); watched=tracked_tickers(user) if request.args.get('watchlist')=='1' else None
        posts=svc.list_published(ticker=request.args.get('ticker') or None,category=request.args.get('category') or None,sentiment=request.args.get('sentiment') or None,search=request.args.get('q') or None,watchlist_tickers=watched,saved_by_user=user if request.args.get('saved')=='1' else None,user_id=user)
        return render_template('live_research.html',posts=posts,categories=rf.CATEGORIES,sentiments=rf.SENTIMENTS,alert_prefs=svc.get_alert_preferences(user,sorted({p['ticker'] for p in posts})),filters=request.args)
    @bp.get('/api/live-research/posts')
    def posts_api():
        user=uid(); watched=tracked_tickers(user) if request.args.get('watchlist')=='1' else None
        posts=svc.list_published(ticker=request.args.get('ticker') or None,category=request.args.get('category') or None,sentiment=request.args.get('sentiment') or None,search=request.args.get('q') or None,watchlist_tickers=watched,saved_by_user=user if request.args.get('saved')=='1' else None,user_id=user,limit=request.args.get('limit',50))
        tickers=sorted({p['ticker'] for p in posts})
        return jsonify({'ok':True,'posts':posts,'alert_prefs':svc.get_alert_preferences(user,tickers)})
    @bp.get('/api/live-research/updates')
    def updates_api():
        user=uid(); watched=tracked_tickers(user) if request.args.get('watchlist')=='1' else None
        posts=realtime.list_incremental(since=request.args.get('since') or None,user_id=user,watchlist_tickers=watched,limit=request.args.get('limit',50))
        tickers=sorted({p['ticker'] for p in posts}); cursor=max([realtime.published_cursor(p) for p in posts],default=request.args.get('since') or '')
        return jsonify({'ok':True,'posts':posts,'cursor':cursor,'alert_prefs':svc.get_alert_preferences(user,tickers)})
    @bp.post('/api/live-research/posts/<post_id>/bookmark')
    def bookmark(post_id):
        d=request.get_json(silent=True) or {}; saved=bool(d.get('saved',True)); svc.set_bookmark(uid(),post_id,saved); return jsonify({'ok':True,'saved':saved})
    @bp.put('/api/live-research/alerts/<ticker>')
    def alert(ticker):
        enabled=bool((request.get_json(silent=True) or {}).get('enabled')); svc.set_alert_preference(uid(),ticker,enabled); return jsonify({'ok':True,'ticker':rf.normalize_ticker(ticker),'enabled':enabled})
    def admin_page():
        admin=actor();status=request.args.get('status') or 'incoming'
        filters={
            'query':request.args.get('q') or None,'catalyst':request.args.get('catalyst') or None,
            'source':request.args.get('source') or None,'priority':request.args.get('priority') or None,
            'time_window':request.args.get('window') or None,'status':status,
        }
        posts=svc.list_admin_posts(admin,**filters)
        urgent=svc.list_admin_posts(admin,status='incoming',limit=8)
        urgent=[p for p in urgent if p.get('priority') in {'Critical','High'}]
        all_posts=svc.list_admin_posts(admin,limit=500)
        return render_template(
            'admin_live_research.html',posts=posts,urgent_posts=urgent,
            status_counts=svc.admin_status_counts(admin),active_status=status,
            filters={
                'status':status,'q':request.args.get('q') or '',
                'catalyst':request.args.get('catalyst') or '',
                'source':request.args.get('source') or '',
                'priority':request.args.get('priority') or '',
                'time_window':request.args.get('window') or '',
            },
            sources=sorted({p.get('source_name') for p in all_posts if p.get('source_name')}),
            categories=rf.CATEGORIES,sentiments=rf.SENTIMENTS,metric_types=rf.METRIC_TYPES,
            comparisons=rf.COMPARISONS,priorities=rf.PRIORITIES,catalyst_types=rf.CATALYST_TYPES,
            last_review_at=get_user_setting(int(admin['id']),'live_research_last_review_at',''),
        )
    bp.add_url_rule('/admin/live-research','admin_page',require_admin(admin_page),methods=['GET'])
    def research_lookup():
        result=research_search.search(request.args.get('q') or '')
        return jsonify({'ok':True,**result})
    bp.add_url_rule('/api/admin/live-research/research-search','admin_research_search',require_admin(research_lookup),methods=['GET'])
    def research_draft():
        d=request.get_json(force=True) or {}; result=research_search.create_draft_from_result(d,actor())
        return jsonify({'ok':True,**result}),201 if result.get('status')=='draft' else 200
    bp.add_url_rule('/api/admin/live-research/research-search/draft','admin_research_search_draft',require_admin(research_draft),methods=['POST'])
    def create():
        d=request.get_json(force=True) or {}; post_id=rf.create_draft(d,actor(),metrics=metrics(d))
        if d.get('publish_now'):
            if (d.get('take_origin') or 'manual')!='manual': return jsonify({'ok':False,'error':'provider/AI drafts require separate admin review before publishing','post_id':post_id}),409
            rf.publish_post(post_id,actor()); realtime.announce_published(post_id,ticker=rf.normalize_ticker(d.get('ticker')))
        return jsonify({'ok':True,'post_id':post_id}),201
    bp.add_url_rule('/api/admin/live-research/posts','admin_create',require_admin(create),methods=['POST'])
    def update(post_id):
        d=request.get_json(force=True) or {}; svc.update_post(post_id,d,actor(),metrics=metrics(d)); return jsonify({'ok':True})
    bp.add_url_rule('/api/admin/live-research/posts/<post_id>','admin_update',require_admin(update),methods=['PATCH'])
    def publish(post_id): rf.publish_post(post_id,actor()); realtime.announce_published(post_id); return jsonify({'ok':True})
    bp.add_url_rule('/api/admin/live-research/posts/<post_id>/publish','admin_publish',require_admin(publish),methods=['POST'])
    def delete(post_id): svc.delete_post(post_id,actor()); return jsonify({'ok':True})
    bp.add_url_rule('/api/admin/live-research/posts/<post_id>','admin_delete',require_admin(delete),methods=['DELETE'])
    def review(post_id,action):
        status=svc.transition_post(post_id,action,actor());return jsonify({'ok':True,'status':status})
    bp.add_url_rule('/api/admin/live-research/posts/<post_id>/<action>','admin_review',require_admin(review),methods=['POST'])
    def bulk():
        d=request.get_json(force=True) or {};result=svc.bulk_transition(d.get('post_ids'),d.get('action'),actor())
        if result['action']=='publish':
            for post in result['posts']:realtime.announce_published(post['id'],ticker=post['ticker'])
        return jsonify({'ok':True,**result})
    bp.add_url_rule('/api/admin/live-research/bulk','admin_bulk',require_admin(bulk),methods=['POST'])
    def mark_reviewed():
        timestamp=rf._now();set_user_setting(uid(),'live_research_last_review_at',timestamp);return jsonify({'ok':True,'reviewed_at':timestamp})
    bp.add_url_rule('/api/admin/live-research/review-session','admin_mark_reviewed',require_admin(mark_reviewed),methods=['POST'])
    return bp
