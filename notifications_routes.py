"""Notification inbox API: the bell on every page and the Today panel."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

import notify_inbox


def create_notifications_blueprint(*, current_user):
    bp = Blueprint("notifications_inbox", __name__)

    def uid() -> int:
        return int((current_user() or {}).get("id") or 0)

    @bp.get("/api/notifications")
    def list_items():
        if not uid():
            return jsonify({"error": "login required"}), 401
        kind = (request.args.get("kind") or "").strip().lower() or None
        before = (request.args.get("before") or "").strip() or None
        try:
            limit = int(request.args.get("limit") or 40)
        except ValueError:
            limit = 40
        data = notify_inbox.items(uid(), kind=kind, before=before, limit=limit)
        data["kinds"] = notify_inbox.kinds()
        return jsonify(data)

    @bp.get("/api/notifications/unread")
    def unread():
        if not uid():
            return jsonify({"unread": 0})
        return jsonify({"unread": notify_inbox.unread_count(uid())})

    @bp.post("/api/notifications/read")
    def mark_read():
        if not uid():
            return jsonify({"error": "login required"}), 401
        return jsonify({"ok": True, "read_at": notify_inbox.mark_read(uid())})

    return bp
