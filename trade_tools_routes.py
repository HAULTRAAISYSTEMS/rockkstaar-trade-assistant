"""Pre-Trade Gate and Position Guardian pages + APIs."""
from __future__ import annotations

import logging
import re

from flask import Blueprint, jsonify, render_template, request

import position_guardian as guardian
import trade_gate

logger = logging.getLogger(__name__)
_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def _num(v):
    try:
        v = float(v)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def create_trade_tools_blueprint(*, current_user):
    bp = Blueprint("trade_tools", __name__)

    def uid() -> int:
        return int((current_user() or {}).get("id") or 0)

    def account_size(user_id: int) -> tuple[float | None, str]:
        try:
            import schwab
            if user_id and schwab.is_connected(user_id):
                s = schwab.get_account_summary(user_id)
                if s.get("total_value"):
                    return float(s["total_value"]), "Schwab"
        except Exception as exc:
            logger.debug("gate account size: %s", exc)
        return None, ""

    # ── Pre-Trade Gate ──────────────────────────────────────────────────
    @bp.get("/trade-gate")
    def gate_page():
        return render_template("trade_gate.html",
                               ticker=(request.args.get("ticker") or "").upper()[:10],
                               entry=request.args.get("entry") or "",
                               stop=request.args.get("stop") or "",
                               target=request.args.get("target") or "")

    @bp.get("/api/gate/<ticker>")
    def gate_context(ticker):
        ticker = (ticker or "").upper().strip()
        if not _TICKER.match(ticker):
            return jsonify({"ok": False, "error": "Enter a valid ticker."}), 400
        ind = trade_gate.gather(ticker)
        size, src = account_size(uid())
        return jsonify({"ok": True, "indicators": ind, "account_size": size, "account_source": src})

    @bp.post("/api/gate/evaluate")
    def gate_evaluate():
        data = request.get_json(silent=True) or {}
        ticker = str(data.get("ticker") or "").upper().strip()
        if not _TICKER.match(ticker):
            return jsonify({"ok": False, "error": "Enter a valid ticker."}), 400
        plan = {k: _num(data.get(k)) for k in ("entry", "stop", "target", "account_size", "risk_pct", "max_position_pct")}
        plan["direction"] = data.get("direction") or "long"
        if not plan["account_size"]:
            plan["account_size"], _ = account_size(uid())
        ind = trade_gate.gather(ticker)
        result = trade_gate.evaluate(ind, plan)
        return jsonify({"ok": True, "ticker": ticker, "indicators": ind, **result})

    # ── Position Guardian ──────────────────────────────────────────────
    @bp.get("/guardian")
    def guardian_page():
        return render_template("guardian.html")

    @bp.get("/api/guardian")
    def guardian_data():
        user_id = uid()
        if not user_id:
            return jsonify({"ok": False, "error": "Sign in first."}), 401
        resp = jsonify(guardian.scan(user_id))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @bp.post("/api/guardian/stop")
    def guardian_stop():
        user_id = uid()
        if not user_id:
            return jsonify({"ok": False, "error": "Sign in first."}), 401
        data = request.get_json(silent=True) or {}
        symbol = str(data.get("symbol") or "").upper().strip()
        if not _TICKER.match(symbol):
            return jsonify({"ok": False, "error": "Bad symbol."}), 400
        stops = guardian.set_stop(user_id, symbol, _num(data.get("stop")), _num(data.get("target")))
        return jsonify({"ok": True, "stops": stops})

    return bp
