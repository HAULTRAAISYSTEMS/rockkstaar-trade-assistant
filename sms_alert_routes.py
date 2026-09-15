"""Authenticated settings routes for verified SMS thesis alerts."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

import sms_alerts


def create_sms_alerts_blueprint(*, current_user):
    bp = Blueprint("sms_alerts", __name__, url_prefix="/sms-alerts")

    def uid() -> int:
        user = current_user()
        return int((user or {}).get("id") or 0)

    @bp.get("")
    def settings():
        return render_template("sms_alerts.html", preference=sms_alerts.get_preference(uid()))

    @bp.post("/start")
    def start():
        try:
            if request.form.get("consent") != "1":
                raise sms_alerts.SmsAlertError("Confirm that you want to receive Tradestaar thesis-alert texts.")
            sms_alerts.begin_verification(uid(), request.form.get("phone", ""))
            flash("Verification code sent. It expires in 10 minutes.", "success")
        except (sms_alerts.SmsAlertError, sms_alerts.SmsProviderError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("sms_alerts.settings"))

    @bp.post("/verify")
    def verify():
        try:
            sms_alerts.verify_code(uid(), request.form.get("code", ""))
            flash("Phone verified. Material thesis alerts are now enabled.", "success")
        except sms_alerts.SmsAlertError as exc:
            flash(str(exc), "error")
        return redirect(url_for("sms_alerts.settings"))

    @bp.post("/preferences")
    def preferences():
        try:
            sms_alerts.update_preference(
                uid(), enabled=request.form.get("enabled") == "1",
                quiet_start=request.form.get("quiet_start", 21),
                quiet_end=request.form.get("quiet_end", 7),
                timezone_name=request.form.get("timezone", "America/New_York"),
                daily_cap=request.form.get("daily_cap", 3),
            )
            flash("Text alert preferences saved.", "success")
        except (sms_alerts.SmsAlertError, ValueError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("sms_alerts.settings"))

    @bp.post("/test")
    def test_message():
        try:
            sms_alerts.send_test(uid())
            flash("Test text sent.", "success")
        except (sms_alerts.SmsAlertError, sms_alerts.SmsProviderError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("sms_alerts.settings"))

    @bp.post("/disable")
    def disable():
        sms_alerts.disable(uid())
        flash("Text alerts disabled.", "success")
        return redirect(url_for("sms_alerts.settings"))

    return bp
