"""Wealth Architect storage. Every query is scoped to one user."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from database import get_db

PORTFOLIO_KINDS = ("roth_ira", "traditional_ira", "taxable", "other")
KIND_LABELS = {"roth_ira": "Roth IRA", "traditional_ira": "Traditional IRA",
               "taxable": "Taxable brokerage", "other": "Other"}
TX_FIELDS = ("date", "type", "ticker", "shares", "price", "amount", "fees", "ratio", "note")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


# ── Portfolios ───────────────────────────────────────────────────────────

def list_portfolios(user_id: int) -> list[dict]:
    conn = get_db()
    try:
        return _rows(conn.execute(
            "SELECT * FROM wa_portfolios WHERE user_id = ? ORDER BY sort_order, id", (user_id,)))
    finally:
        conn.close()


def get_portfolio(user_id: int, pid: int) -> dict | None:
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM wa_portfolios WHERE user_id = ? AND id = ?", (user_id, pid)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def create_portfolio(user_id: int, name: str, kind: str = "taxable",
                     target_cash_pct=None, max_position_pct=None) -> int:
    kind = kind if kind in PORTFOLIO_KINDS else "taxable"
    conn = get_db()
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM wa_portfolios WHERE user_id = ?", (user_id,)).fetchone()["n"]
        cur = conn.execute(
            "INSERT INTO wa_portfolios (user_id, name, kind, target_cash_pct, max_position_pct, sort_order, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, name.strip()[:60], kind, target_cash_pct, max_position_pct, int(n), _now()),
            returning_id=True)
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def update_portfolio(user_id: int, pid: int, **fields) -> bool:
    allowed = {k: v for k, v in fields.items() if k in ("name", "kind", "target_cash_pct", "max_position_pct", "broker_account")}
    if "kind" in allowed and allowed["kind"] not in PORTFOLIO_KINDS:
        allowed.pop("kind")
    if "name" in allowed:
        allowed["name"] = str(allowed["name"]).strip()[:60]
    if not allowed:
        return False
    sets = ", ".join(f"{k} = ?" for k in allowed)
    conn = get_db()
    try:
        cur = conn.execute(f"UPDATE wa_portfolios SET {sets} WHERE user_id = ? AND id = ?",
                           (*allowed.values(), user_id, pid))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_portfolio(user_id: int, pid: int) -> bool:
    conn = get_db()
    try:
        conn.execute("DELETE FROM wa_transactions WHERE user_id = ? AND portfolio_id = ?", (user_id, pid))
        cur = conn.execute("DELETE FROM wa_portfolios WHERE user_id = ? AND id = ?", (user_id, pid))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── Transactions ─────────────────────────────────────────────────────────

def list_transactions(user_id: int, pid: int | None = None) -> list[dict]:
    conn = get_db()
    try:
        if pid is None:
            return _rows(conn.execute(
                "SELECT * FROM wa_transactions WHERE user_id = ? ORDER BY date, id", (user_id,)))
        return _rows(conn.execute(
            "SELECT * FROM wa_transactions WHERE user_id = ? AND portfolio_id = ? ORDER BY date, id",
            (user_id, pid)))
    finally:
        conn.close()


def add_transactions(user_id: int, pid: int, txs: list[dict], source: str = "manual") -> dict:
    """Insert transactions; ones whose fingerprint is already in this
    portfolio are skipped (re-importing an overlapping file is safe)."""
    conn = get_db()
    added = dupes = 0
    try:
        seen = {r["fingerprint"] for r in conn.execute(
            "SELECT fingerprint FROM wa_transactions WHERE user_id = ? AND portfolio_id = ? AND fingerprint IS NOT NULL",
            (user_id, pid)).fetchall()}
        for tx in txs:
            fp = tx.get("fingerprint")
            if fp and fp in seen:
                dupes += 1
                continue
            conn.execute(
                "INSERT INTO wa_transactions (user_id, portfolio_id, date, type, ticker, shares, price, amount, "
                "fees, ratio, note, source, fingerprint, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, pid, tx["date"], str(tx["type"]).upper(),
                 (str(tx.get("ticker") or "").upper() or None),
                 tx.get("shares"), tx.get("price"), tx.get("amount"), tx.get("fees") or 0.0,
                 tx.get("ratio"), (tx.get("note") or "")[:200], tx.get("source") or source, fp, _now()))
            if fp:
                seen.add(fp)
            added += 1
        conn.commit()
    finally:
        conn.close()
    return {"added": added, "duplicates": dupes}


def update_transaction(user_id: int, tx_id: int, tx: dict) -> bool:
    vals = {k: tx.get(k) for k in TX_FIELDS}
    vals["type"] = str(vals["type"] or "").upper()
    vals["ticker"] = str(vals.get("ticker") or "").upper() or None
    vals["fees"] = vals.get("fees") or 0.0
    sets = ", ".join(f"{k} = ?" for k in vals)
    conn = get_db()
    try:
        cur = conn.execute(f"UPDATE wa_transactions SET {sets}, fingerprint = NULL WHERE user_id = ? AND id = ?",
                           (*vals.values(), user_id, tx_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_transaction(user_id: int, tx_id: int) -> bool:
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM wa_transactions WHERE user_id = ? AND id = ?", (user_id, tx_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── Reference data the dashboard reads ───────────────────────────────────

def sectors_for(tickers) -> dict:
    """{ticker: (sector, industry)} from the fundamentals cache, whatever its
    age: a company's sector does not change day to day."""
    tickers = sorted({str(t).upper() for t in tickers or [] if t})
    if not tickers:
        return {}
    out = {}
    conn = get_db()
    try:
        marks = ",".join("?" * len(tickers))
        for r in conn.execute(f"SELECT ticker, data_json FROM fundamentals_cache WHERE ticker IN ({marks})",
                              tuple(tickers)).fetchall():
            try:
                d = json.loads(r["data_json"])
            except Exception:
                continue
            if d.get("sector"):
                out[r["ticker"]] = (d.get("sector"), d.get("industry"))
    except Exception:
        return out
    finally:
        conn.close()
    return out


def stored_prices(tickers) -> dict:
    """{ticker: (price, last_updated)} from stock_data."""
    tickers = sorted({str(t).upper() for t in tickers or [] if t})
    if not tickers:
        return {}
    conn = get_db()
    try:
        marks = ",".join("?" * len(tickers))
        return {r["ticker"]: (r["current_price"], r["last_updated"]) for r in conn.execute(
            f"SELECT ticker, current_price, last_updated FROM stock_data WHERE ticker IN ({marks})",
            tuple(tickers)).fetchall() if r["current_price"]}
    except Exception:
        return {}
    finally:
        conn.close()
