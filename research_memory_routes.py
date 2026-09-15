"""Authenticated pages for the personal Research Memory workflow."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

import research_memory as memory


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
