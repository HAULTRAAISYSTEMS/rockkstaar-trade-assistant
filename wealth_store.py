"""Wealth Architect storage. Every query is scoped to one user."""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timezone

from database import get_db

PORTFOLIO_KINDS = ("roth_ira", "traditional_ira", "taxable", "other")
KIND_LABELS = {"roth_ira": "Roth IRA", "traditional_ira": "Traditional IRA",
               "taxable": "Taxable brokerage", "other": "Other"}
TX_FIELDS = ("date", "type", "ticker", "shares", "price", "amount", "fees", "ratio", "note", "tax_year")


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



def visible_portfolio_tabs(user_id: int, portfolios: list[dict]) -> list[dict]:
    """Keep manually created portfolios; show broker-linked tabs only with holdings.

    Never delete portfolio rows or transaction history. Respect Schwab account
    inclusion preferences; current holdings are from saved read-only snapshots.
    """
    import schwab_account_preferences as prefs
    preferences = prefs.get_preferences(user_id)
    snapshots = {row["account_key"]: row["snapshot"]
                 for row in list_broker_snapshots(user_id)}
    import wealth_ledger
    transactions = list_transactions(user_id)
    by_portfolio = {}
    for tx in transactions:
        by_portfolio.setdefault(int(tx["portfolio_id"]), []).append(tx)
    ledger_holdings = set()
    for pid, rows in by_portfolio.items():
        ledger = wealth_ledger.build(rows)
        if any(any(lot.shares > 0 for lot in lots)
               for lots in ledger.positions.values()):
            ledger_holdings.add(pid)
    visible = []
    for portfolio in portfolios:
        key = str(portfolio.get("broker_account") or "")
        if not key:
            visible.append(portfolio)
            continue
        if not preferences.get(key, {}).get("include_wealth", True):
            continue
        snapshot = snapshots.get(key) or {}
        positions = snapshot.get("holdings") or []
        has_broker_stocks = any(
            isinstance(p, dict) and
            isinstance(p.get("shares"), (int, float)) and p["shares"] > 0 and p.get("ticker")
            for p in positions if isinstance(p, dict))
        if has_broker_stocks or portfolio["id"] in ledger_holdings:
            visible.append(portfolio)
    return visible


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


def recent_transactions(user_id: int, pid: int) -> list[dict]:
    conn = get_db()
    try:
        return _rows(conn.execute(
            "SELECT date, type, ticker, shares, price, amount, fees, ratio, source "
            "FROM wa_transactions WHERE user_id = ? AND portfolio_id = ? ORDER BY date DESC, id DESC LIMIT 20",
            (user_id, pid)))
    finally:
        conn.close()


def _import_signature(tx):
    return tuple(tx.get(key) or None for key in ("date", "type", "ticker", "shares", "price",
                                                 "amount", "fees", "ratio"))


def _broker_overlap_key(tx):
    """CSV rounds quote fields and omits cash amounts on trades."""
    def rounded(key, digits):
        value = tx.get(key)
        return round(value, digits) if value is not None else 0
    return (tx["type"], tx.get("ticker"), rounded("shares", 6), rounded("price", 6),
            rounded("amount", 2) if tx["type"] not in ("BUY", "SELL") else 0,
            rounded("fees", 2), rounded("ratio", 6))


def add_transactions(user_id: int, pid: int, txs: list[dict], source: str = "manual", *, broker_key=None) -> dict:
    """Insert transactions; ones whose fingerprint is already in this
    portfolio are skipped (re-importing an overlapping file is safe)."""
    conn = get_db()
    added = dupes = 0
    try:
        # Take the portfolio write lock before deduplication. Concurrent
        # imports serialize here on both SQLite and PostgreSQL.
        guard = " AND broker_account = ?" if broker_key is not None else ""
        params = (user_id, pid, broker_key) if broker_key is not None else (user_id, pid)
        owner = conn.execute("UPDATE wa_portfolios SET id = id WHERE user_id = ? AND id = ?" + guard, params)
        if owner.rowcount == 0:
            return {"added": 0, "duplicates": 0, **({"mapping_changed": True} if broker_key is not None else {})}
        existing = _rows(conn.execute("SELECT * FROM wa_transactions WHERE user_id = ? AND portfolio_id = ?",
                                      (user_id, pid)))
        seen = {row["fingerprint"] for row in existing}
        stored_counts = Counter(_import_signature(row) for row in existing
                                if row["source"] == "schwab_csv" and row["fingerprint"])
        prior_broker = {}
        prior_csv = {}
        for row in existing:
            target = prior_broker if row["source"] == "schwab_api" else prior_csv
            target.setdefault(_broker_overlap_key(row), Counter())[row["date"]] += 1
        incoming_counts = Counter()
        checked = set()
        for tx in txs:
            fp = tx.get("fingerprint")
            duplicates_signature = False
            if fp and fp not in checked and tx.get("source") == "schwab_csv":
                signature = _import_signature(tx)
                incoming_counts[signature] += 1
                duplicates_signature = incoming_counts[signature] <= stored_counts[signature]
            if fp:
                checked.add(fp)
            if fp and (fp in seen or duplicates_signature):
                seen.add(fp)
                dupes += 1
                continue
            if fp and (tx.get("source") == "schwab_api" or source == "csv"):
                candidates = prior_csv if tx.get("source") == "schwab_api" else prior_broker
                nearby = candidates.get(_broker_overlap_key(tx), {})
                day = date.fromisoformat(tx["date"])
                # A trade's recorded date can differ from its settlement date.
                window = 3 if tx["type"] in ("BUY", "SELL") else 0
                match = next((key for key in sorted(nearby, key=lambda d: abs((date.fromisoformat(d) - day).days))
                              if nearby[key] and abs((date.fromisoformat(key) - day).days) <= window), None)
                if match:
                    nearby[match] -= 1
                    seen.add(fp)
                    dupes += 1
                    continue
            conn.execute(
                "INSERT INTO wa_transactions (user_id, portfolio_id, date, type, ticker, shares, price, amount, "
                "fees, ratio, note, source, fingerprint, created_at, tax_year) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, pid, tx["date"], str(tx["type"]).upper(),
                 (str(tx.get("ticker") or "").upper() or None),
                 tx.get("shares"), tx.get("price"), tx.get("amount"), tx.get("fees") or 0.0,
                 tx.get("ratio"), (tx.get("note") or "")[:200], tx.get("source") or source, fp, _now(),
                 tx.get("tax_year")))
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
        cur = conn.execute(f"UPDATE wa_transactions SET {sets}, fingerprint = CASE WHEN source = 'schwab_api' THEN fingerprint ELSE NULL END WHERE user_id = ? AND id = ?",
                           (*vals.values(), user_id, tx_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_transfer_note(user_id, tx_id, prior_note, note):
    """Keep the import fingerprint and reject a stale edit."""
    conn = get_db()
    try:
        row = conn.execute(
            "UPDATE wa_transactions SET note = ? WHERE user_id = ? AND id = ? AND COALESCE(note, '') = ? "
            "AND type IN ('DEPOSIT', 'WITHDRAWAL', 'TRANSFER_IN', 'TRANSFER_OUT')",
            (note, user_id, tx_id, prior_note))
        conn.commit()
        return row.rowcount > 0
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


# ── M.C.F.F. research ────────────────────────────────────────────────────

def get_research(user_id: int, ticker: str) -> dict | None:
    conn = get_db()
    try:
        r = conn.execute("SELECT data_json, updated_at FROM wa_research WHERE user_id = ? AND ticker = ?",
                         (user_id, ticker.upper())).fetchone()
    finally:
        conn.close()
    if not r:
        return None
    try:
        d = json.loads(r["data_json"])
    except ValueError:
        return None
    d["updated_at"] = r["updated_at"]
    return d


def list_research(user_id: int) -> dict:
    conn = get_db()
    try:
        rows = conn.execute("SELECT ticker, data_json, updated_at FROM wa_research WHERE user_id = ? ORDER BY ticker",
                            (user_id,)).fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        try:
            out[r["ticker"]] = dict(json.loads(r["data_json"]), updated_at=r["updated_at"])
        except ValueError:
            continue
    return out


def save_research(user_id: int, ticker: str, data: dict) -> None:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO wa_research (user_id, ticker, data_json, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, ticker) DO UPDATE SET data_json = excluded.data_json, updated_at = excluded.updated_at",
            (user_id, ticker.upper(), json.dumps(data), _now()))
        conn.commit()
    finally:
        conn.close()


def delete_research(user_id: int, ticker: str) -> bool:
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM wa_research WHERE user_id = ? AND ticker = ?", (user_id, ticker.upper()))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── Contribution plan ────────────────────────────────────────────────────

def get_plan(user_id: int, year: int) -> dict | None:
    conn = get_db()
    try:
        r = conn.execute("SELECT data_json FROM wa_plans WHERE user_id = ? AND year = ?", (user_id, year)).fetchone()
    finally:
        conn.close()
    try:
        return json.loads(r["data_json"]) if r else None
    except ValueError:
        return None


def save_plan(user_id: int, year: int, data: dict) -> None:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO wa_plans (user_id, year, data_json, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, year) DO UPDATE SET data_json = excluded.data_json, updated_at = excluded.updated_at",
            (user_id, int(year), json.dumps(data), _now()))
        conn.commit()
    finally:
        conn.close()


def fundamentals_fetched_at(ticker: str) -> str | None:
    conn = get_db()
    try:
        r = conn.execute("SELECT fetched_at FROM fundamentals_cache WHERE ticker = ?", (ticker.upper(),)).fetchone()
        return r["fetched_at"] if r else None
    except Exception:
        return None
    finally:
        conn.close()


# Brokerage snapshots are never posted as trades or tax lots.
def save_broker_snapshot(user_id: int, account_key: str, snapshot: dict) -> None:
    if not account_key or not isinstance(snapshot, dict):
        raise ValueError("Valid account and snapshot required")
    conn = get_db()
    try:
        payload = json.dumps(snapshot, allow_nan=False, separators=(",", ":"))
        conn.execute(
            "INSERT INTO wa_broker_snapshots (user_id, broker, account_key, snapshot_json, synced_at) "
            "VALUES (?, 'schwab', ?, ?, ?) "
            "ON CONFLICT (user_id, broker, account_key) DO UPDATE SET "
            "snapshot_json = excluded.snapshot_json, synced_at = excluded.synced_at",
            (user_id, account_key, payload, _now()))
        conn.commit()
    finally:
        conn.close()


def list_broker_snapshots(user_id: int) -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT account_key, snapshot_json, synced_at FROM wa_broker_snapshots "
            "WHERE user_id = ? AND broker = 'schwab' ORDER BY account_key", (user_id,)).fetchall()
        return [{"account_key": r["account_key"], "snapshot": json.loads(r["snapshot_json"]),
                 "synced_at": r["synced_at"]} for r in rows]
    finally:
        conn.close()


def link_broker_portfolio(user_id: int, account_key: str, kind: str = "taxable") -> int:
    """Idempotently provision a distinct portfolio for a broker account."""
    if not account_key:
        raise ValueError("Account key required")
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM wa_portfolios WHERE user_id = ? AND broker_account = ? "
            "ORDER BY id LIMIT 1", (user_id, account_key)).fetchone()
        if row:
            return int(row["id"])
        count = conn.execute("SELECT COUNT(*) AS n FROM wa_portfolios WHERE user_id = ?",
                             (user_id,)).fetchone()["n"]
        label = "Schwab " + (KIND_LABELS.get(kind) or "Brokerage")
        cur = conn.execute(
            "INSERT INTO wa_portfolios (user_id, name, kind, broker_account, sort_order, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, label, kind if kind in PORTFOLIO_KINDS else "taxable",
             account_key, int(count), _now()), returning_id=True)
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def performance_settings(user_id: int) -> tuple[dict, dict]:
    """Read annual goals and account classifications in one user-scoped query."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM user_settings WHERE user_id = ? "
                            "AND (key LIKE 'wealth.performance.goal.%' OR key LIKE 'wealth.performance.account.%')",
                            (user_id,)).fetchall()
    finally:
        conn.close()
    goals, accounts = {}, {}
    for row in rows:
        prefix, _, suffix = row["key"].rpartition(".")
        table = goals if prefix == "wealth.performance.goal" else accounts
        try:
            table[int(suffix)] = json.loads(row["value"])
        except (ValueError, TypeError):
            continue
    return goals, accounts


def save_performance_setting(user_id: int, scope: str, identifier: int, values: dict) -> None:
    conn = get_db()
    try:
        conn.execute("INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?) "
                     "ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value",
                     (user_id, f"wealth.performance.{scope}.{identifier}", json.dumps(values, allow_nan=False)))
        conn.commit()
    finally:
        conn.close()
