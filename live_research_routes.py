"""Flask Blueprint for Tradestaar Live Research Feed Phase 2/3."""
from __future__ import annotations
from flask import Blueprint, jsonify, render_template, request, session
import research_feed as rf
import research_feed_phase2 as svc

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
    @bp.post('/api/live-research/posts/<post_id>/bookmark')
    def bookmark(post_id):
        d=request.get_json(silent=True) or {}; saved=bool(d.get('saved',True)); svc.set_bookmark(uid(),post_id,saved); return jsonify({'ok':True,'saved':saved})
    @bp.put('/api/live-research/alerts/<ticker>')
    def alert(ticker):
        enabled=bool((request.get_json(silent=True) or {}).get('enabled')); svc.set_alert_preference(uid(),ticker,enabled); return jsonify({'ok':True,'ticker':rf.normalize_ticker(ticker),'enabled':enabled})
    def admin_page(): return render_template('admin_live_research.html',posts=svc.list_admin_posts(actor()),categories=rf.CATEGORIES,sentiments=rf.SENTIMENTS,metric_types=rf.METRIC_TYPES,comparisons=rf.COMPARISONS)
    bp.add_url_rule('/admin/live-research','admin_page',require_admin(admin_page),methods=['GET'])
    def create():
        d=request.get_json(force=True) or {}; post_id=rf.create_draft(d,actor(),metrics=metrics(d))
        if d.get('publish_now'):
            if (d.get('take_origin') or 'manual')!='manual': return jsonify({'ok':False,'error':'provider/AI drafts require separate admin review before publishing','post_id':post_id}),409
            rf.publish_post(post_id,actor())
        return jsonify({'ok':True,'post_id':post_id}),201
    bp.add_url_rule('/api/admin/live-research/posts','admin_create',require_admin(create),methods=['POST'])
    def update(post_id):
        d=request.get_json(force=True) or {}; svc.update_post(post_id,d,actor(),metrics=metrics(d)); return jsonify({'ok':True})
    bp.add_url_rule('/api/admin/live-research/posts/<post_id>','admin_update',require_admin(update),methods=['PATCH'])
    def publish(post_id): rf.publish_post(post_id,actor()); return jsonify({'ok':True})
    bp.add_url_rule('/api/admin/live-research/posts/<post_id>/publish','admin_publish',require_admin(publish),methods=['POST'])
    def delete(post_id): svc.delete_post(post_id,actor()); return jsonify({'ok':True})
    bp.add_url_rule('/api/admin/live-research/posts/<post_id>','admin_delete',require_admin(delete),methods=['DELETE'])
    return bp
