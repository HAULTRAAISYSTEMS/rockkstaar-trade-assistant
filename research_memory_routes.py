"""Authenticated pages for the personal Research Memory workflow."""
from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

import research_memory as memory

# AI summaries cost a model call each; cap them per user (30 per 10 minutes).
_SUM_LIMIT, _SUM_WINDOW = 30, 600
_sum_calls: dict[int, deque] = defaultdict(deque)
_sum_lock = threading.Lock()


def _allow_summary(user_id: int) -> bool:
    now = time.time()
    with _sum_lock:
        calls = _sum_calls[user_id]
        while calls and now - calls[0] > _SUM_WINDOW:
            calls.popleft()
        if len(calls) >= _SUM_LIMIT:
            return False
        calls.append(now)
        return True


def create_research_memory_blueprint(*, current_user):
    bp = Blueprint("research_memory", __name__)

    def uid() -> int:
        user = current_user()
        return int((user or {}).get("id") or 0)

    @bp.get("/research-memory")
    def notebook():
        ticker = request.args.get("ticker") or None
        post = memory.published_post(request.args.get("post_id") or "")
        all_cards = memory.list_cards(uid())
        cards = [card for card in all_cards if not ticker or card["ticker"] == ticker.upper()]
        return render_template(
            "research_memory.html", cards=cards, summary=memory.summary(uid(), all_cards),
            selected_ticker=ticker or "", source_post=post, impacts=memory.IMPACTS,
        )

    @bp.post("/research-memory/cards")
    def create():
        try:
            card_id = memory.create_card(uid(), request.form)
            flash("Saved to Research Memory. It is ready for your first recall.", "success")
            return redirect(url_for("research_memory.notebook", saved=card_id))
        except memory.ResearchMemoryError as exc:
            flash(str(exc), "error")
            post_id = request.form.get("research_post_id") or None
            return redirect(url_for("research_memory.notebook", post_id=post_id) if post_id else url_for("research_memory.notebook"))

    # ── JSON API for saving news stories from anywhere in the app ──────────
    @bp.post("/research-memory/api/summarize")
    def api_summarize():
        user_id = uid()
        if not user_id:
            return jsonify({"ok": False, "error": "Sign in to use Research Memory."}), 401
        if not _allow_summary(user_id):
            return jsonify({"ok": False, "error": "Too many summaries in a short time. Try again in a few minutes."}), 429
        import news_summarizer
        data = request.get_json(silent=True) or {}
        item = {k: str(data.get(k) or "")[:4000] for k in
                ("ticker", "headline", "summary", "source", "url", "published_at", "reason")}
        result = news_summarizer.summarize(item, fresh=bool(data.get("fresh")))
        return jsonify(result), (200 if result.get("ok") else 400)

    @bp.post("/research-memory/api/cards")
    def api_create():
        user_id = uid()
        if not user_id:
            return jsonify({"ok": False, "error": "Sign in to use Research Memory."}), 401
        import news_summarizer
        data = request.get_json(silent=True) or {}
        notes = str(data.get("notes") or "").strip()
        block = news_summarizer.notes_block(str(data.get("summary") or ""), data.get("key_points") or [])
        if block:
            notes = block + ("\n\nMY NOTES\n" + notes if notes else "")
        payload = {
            "ticker": data.get("ticker"), "company_name": data.get("company_name"),
            "headline": data.get("headline"), "source_name": data.get("source"),
            "source_url": data.get("url"), "source_published_at": data.get("published_at"),
            "why_it_matters": data.get("why_it_matters"), "key_change": data.get("key_change"),
            "thesis_impact": data.get("thesis_impact"),
            "disconfirming_evidence": data.get("disconfirming_evidence"),
            "notes": notes, "review_question": data.get("review_question"),
        }
        try:
            existing = None
            try:
                existing = memory.find_existing(
                    user_id, memory._ticker(payload["ticker"]), payload["headline"] or "", payload["source_url"])
            except memory.ResearchMemoryError:
                pass
            card_id = existing or memory.create_card(user_id, payload)
        except memory.ResearchMemoryError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "id": card_id, "duplicate": bool(existing),
                        "url": url_for("research_memory.notebook", saved=card_id) + f"#card-{card_id}"})

    @bp.get("/research-memory/api/saved")
    def api_saved():
        user_id = uid()
        if not user_id:
            return jsonify({"ok": False, "urls": {}, "headlines": {}}), 401
        keys = memory.saved_story_keys(user_id)
        resp = jsonify({"ok": True, **keys})
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # ── Memory edge: scorecard, AI bull/bear case, what changed ─────────
    def _tk(ticker):
        t = (ticker or "").upper().strip()
        return t if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", t) else ""

    @bp.get("/api/memory/scorecard/<ticker>")
    def api_scorecard(ticker):
        user_id, t = uid(), _tk(ticker)
        if not user_id or not t:
            return jsonify({"ok": False}), 400
        import memory_edge
        return jsonify({"ok": True, **memory_edge.thesis_scorecard(user_id, t)})

    @bp.post("/api/memory/case/<ticker>")
    def api_case(ticker):
        user_id, t = uid(), _tk(ticker)
        if not user_id or not t:
            return jsonify({"ok": False, "error": "Bad request."}), 400
        if not _allow_summary(user_id):
            return jsonify({"ok": False, "error": "Too many AI requests. Try again in a few minutes."}), 429
        import memory_edge
        out = memory_edge.ai_case(user_id, t)
        return jsonify(out), (200 if out.get("ok") else 400)

    @bp.get("/api/memory/changed/<ticker>")
    def api_changed(ticker):
        user_id, t = uid(), _tk(ticker)
        if not user_id or not t:
            return jsonify({"ok": False}), 400
        import memory_edge
        resp = jsonify(memory_edge.whats_changed(user_id, t))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @bp.get("/research-memory/review")
    def review():
        card = memory.due_card(uid())
        cards = memory.list_cards(uid())
        return render_template("research_memory_review.html", card=card, summary=memory.summary(uid(), cards))

    @bp.post("/research-memory/cards/<card_id>/review")
    def rate(card_id):
        try:
            memory.rate_card(uid(), card_id, request.form.get("rating") or "")
            flash("Review recorded. The next card is ready.", "success")
        except memory.ResearchMemoryError as exc:
            flash(str(exc), "error")
        return redirect(url_for("research_memory.review"))

    @bp.post("/research-memory/cards/<card_id>/archive")
    def archive(card_id):
        try:
            memory.archive_card(uid(), card_id)
            flash("Research card archived.", "info")
        except memory.ResearchMemoryError as exc:
            flash(str(exc), "error")
        return redirect(url_for("research_memory.notebook"))

    return bp
