"""Authenticated browser-push settings and subscription endpoints."""
from __future__ import annotations

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

import browser_push


def create_browser_push_blueprint(*, current_user):
    bp = Blueprint("browser_push", __name__)

    def uid() -> int:
        user = current_user()
        return int((user or {}).get("id") or 0)

    @bp.get("/notifications")
    def settings():
        return render_template(
            "browser_push.html", preference=browser_push.get_settings(uid()),
            application_server_key=browser_push.public_key(),
        )

    @bp.post("/api/browser-push/subscriptions")
    def subscribe():
        try:
            payload = request.get_json(silent=True) or {}
            saved = browser_push.save_subscription(
                uid(), payload.get("subscription") or {}, user_agent=request.headers.get("User-Agent", ""),
            )
            return jsonify({"ok": True, "subscription": saved})
        except browser_push.BrowserPushError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @bp.delete("/api/browser-push/subscriptions")
    def unsubscribe():
        payload = request.get_json(silent=True) or {}
        browser_push.remove_subscription(uid(), payload.get("endpoint") or "")
        return jsonify({"ok": True})

    @bp.post("/notifications/preferences")
    def preferences():
        try:
            browser_push.update_settings(
                uid(), enabled=request.form.get("enabled") == "1",
                quiet_start=request.form.get("quiet_start", 21), quiet_end=request.form.get("quiet_end", 7),
                timezone_name=request.form.get("timezone", "America/New_York"),
                daily_cap=request.form.get("daily_cap", 3),
            )
            flash("Browser notification preferences saved.", "success")
        except (browser_push.BrowserPushError, ValueError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("browser_push.settings"))

    @bp.post("/api/browser-push/test")
    def test_notification():
        try:
            result = browser_push.send_test(uid())
            if not result["sent"]:
                raise browser_push.BrowserPushError("No active device accepted the test notification.")
            return jsonify({"ok": True, "sent": result["sent"]})
        except browser_push.BrowserPushError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    return bp
