"""Append-only user-scoped investment decision journal."""
import json
from datetime import datetime, timezone
from database import get_db


def list_decisions(user_id):
    db = get_db()
    try:
        rows = db.execute("SELECT id, ticker, decision, amount, thesis, risk, invalidation, "
                          "review_date, evidence_json, created_at FROM wa_capital_decisions "
                          "WHERE user_id = ? ORDER BY id DESC LIMIT 100", (user_id,)).fetchall()
        return [{**{k: r[k] for k in ("id", "ticker", "decision", "amount", "thesis", "risk",
                 "invalidation", "review_date", "created_at")}, "evidence": json.loads(r["evidence_json"])}
                for r in rows]
    finally:
        db.close()


def add_decision(user_id, data):
    ticker = str(data.get("ticker") or "").strip().upper()
    decision = str(data.get("decision") or "").upper()
    amount = data.get("amount")
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise ValueError("Valid amount required")
    import math
    if not math.isfinite(amount) or amount <= 0 or amount > 1000000000:
        raise ValueError("Valid positive amount required")
    if decision not in ("BUY", "WAIT", "REJECT"):
        raise ValueError("Decision must be BUY, WAIT, or REJECT")
    if not ticker or len(ticker) > 10:
        raise ValueError("Valid ticker required")
    thesis = str(data.get("thesis") or "").strip()[:2000]
    risk = str(data.get("risk") or "").strip()[:2000]
    invalidation = str(data.get("invalidation") or "").strip()[:2000]
    if not thesis or not risk or not invalidation:
        raise ValueError("Thesis, risk, and invalidation are required")
    review = str(data.get("review_date") or "").strip()[:10] or None
    if review:
        from datetime import date
        try:
            date.fromisoformat(review)
        except ValueError:
            raise ValueError("Invalid review date")
    evidence = data.get("evidence") or {}
    if not isinstance(evidence, dict) or len(json.dumps(evidence)) > 20000:
        raise ValueError("Evidence exceeds size limit")
    db = get_db()
    try:
        db.execute("INSERT INTO wa_capital_decisions (user_id,ticker,decision,amount,thesis,risk,"
                   "invalidation,review_date,evidence_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (user_id,ticker,decision,amount,thesis,risk,invalidation,review,json.dumps(evidence),
                    datetime.now(timezone.utc).isoformat()))
        db.commit()
    finally:
        db.close()
