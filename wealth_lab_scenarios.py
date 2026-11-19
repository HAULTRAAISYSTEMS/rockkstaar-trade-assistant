"""User-scoped saved investment simulations. No brokerage or ledger writes."""
import json
from datetime import datetime, timezone
from database import get_db

FIELDS = ("ticker", "existing_shares", "existing_avg_cost", "purchase_price",
          "invest_amount", "future_price", "current_price", "fees", "fractional",
          "cash_available", "portfolio_value")


def list_scenarios(user_id):
    db = get_db()
    try:
        rows = db.execute("SELECT id, name, ticker, inputs_json, created_at FROM wa_lab_scenarios "
                          "WHERE user_id = ? ORDER BY id DESC LIMIT 20", (user_id,)).fetchall()
        return [{"id": r["id"], "name": r["name"], "ticker": r["ticker"],
                 "data": json.loads(r["inputs_json"]), "date": r["created_at"]} for r in rows]
    finally:
        db.close()


def save_scenario(user_id, name, data):
    name = str(name or "").strip()[:70]
    ticker = str((data or {}).get("ticker") or "").upper().strip()
    if not name or not ticker or len(ticker) > 10:
        raise ValueError("Scenario name and ticker are required")
    inputs = {k: data[k] for k in FIELDS if k in data}
    inputs["ticker"] = ticker
    if len(json.dumps(inputs)) > 5000:
        raise ValueError("Scenario exceeds size limit")
    db = get_db()
    try:
        db.execute("INSERT INTO wa_lab_scenarios (user_id, name, ticker, inputs_json, created_at) "
                   "VALUES (?, ?, ?, ?, ?)",
                   (user_id, name, ticker, json.dumps(inputs),
                    datetime.now(timezone.utc).isoformat()))
        db.execute("DELETE FROM wa_lab_scenarios WHERE user_id = ? AND id NOT IN "
                   "(SELECT id FROM wa_lab_scenarios WHERE user_id = ? ORDER BY id DESC LIMIT 20)",
                   (user_id, user_id))
        db.commit()
    finally:
        db.close()


def delete_scenario(user_id, scenario_id):
    db = get_db()
    try:
        db.execute("DELETE FROM wa_lab_scenarios WHERE user_id = ? AND id = ?",
                   (user_id, scenario_id))
        db.commit()
    finally:
        db.close()
