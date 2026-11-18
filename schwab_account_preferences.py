"""Per-user Schwab account visibility and Wealth inclusion preferences.

No brokerage account, OAuth token, portfolio or transaction is deleted.
"""
from database import get_db


def get_preferences(user_id):
    if not user_id:
        return {}
    db = get_db()
    try:
        rows = db.execute(
            "SELECT account_key, visible, include_wealth FROM schwab_account_preferences WHERE user_id = ?",
            (user_id,)).fetchall()
        return {r["account_key"]: {"visible": bool(r["visible"]),
                "include_wealth": bool(r["include_wealth"])} for r in rows}
    finally:
        db.close()


def update_preference(user_id, account_key, *, visible, include_wealth):
    if not user_id or not account_key or len(account_key) > 200:
        raise ValueError("Invalid account selection")
    db = get_db()
    try:
        db.execute(
            "INSERT INTO schwab_account_preferences (user_id, account_key, visible, include_wealth) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (user_id, account_key) DO UPDATE SET "
            "visible = excluded.visible, include_wealth = excluded.include_wealth",
            (user_id, account_key, int(bool(visible)), int(bool(include_wealth))))
        db.commit()
    finally:
        db.close()


def select_accounts(accounts, preferences, *, for_wealth=False):
    field = "include_wealth" if for_wealth else "visible"
    return [a for a in accounts if preferences.get(
        str(a.get("account_hash") or a.get("account_number") or ""), {}).get(field, True)]
