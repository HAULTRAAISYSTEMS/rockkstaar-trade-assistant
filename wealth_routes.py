"""Wealth Architect pages and APIs.

Every route reads the signed-in user's id and every storage call is scoped
to it; a portfolio id from the URL is only used after it is confirmed to
belong to that user.
"""
from __future__ import annotations

import logging
import re
from datetime import date

from flask import Blueprint, Response, abort, jsonify, render_template, request

import wealth_calc
import wealth_csv
import wealth_ledger
import wealth_service
import wealth_store

logger = logging.getLogger(__name__)
_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
QUICK_START = (("Roth IRA", "roth_ira"), ("Aggressive", "taxable"), ("Diversified", "taxable"))


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def create_wealth_blueprint(*, current_user, price_lookup=None, bars_lookup=None, broker_summary=None):
    bp = Blueprint("wealth", __name__)
    price_lookup = price_lookup or wealth_service.default_price_lookup

    def _default_bars(ticker):
        from market_data import fetch_chart_bars
        bars, _meta = fetch_chart_bars(ticker, "1d", "max")
        return bars

    def _default_broker(user_id):
        import schwab
        if not schwab.is_connected(user_id):
            return None
        return schwab.get_account_summary(user_id)

    bars_lookup = bars_lookup or _default_bars
    broker_summary = broker_summary or _default_broker

    def uid() -> int:
        u = int((current_user() or {}).get("id") or 0)
        if not u:
            abort(401)
        return u

    def owned(user_id, pid):
        if pid in (None, "", "all"):
            return None
        try:
            p = wealth_store.get_portfolio(user_id, int(pid))
        except (TypeError, ValueError):
            p = None
        if not p:
            abort(404)
        return p

    def ledgers_for(user_id, portfolio):
        txs = wealth_store.list_transactions(user_id, portfolio["id"] if portfolio else None)
        groups = {}
        for t in txs:
            groups.setdefault(t["portfolio_id"], []).append(t)
        return {pid: wealth_ledger.build(rows) for pid, rows in groups.items()}, txs

    def summary_for(user_id, portfolio):
        ledgers, txs = ledgers_for(user_id, portfolio)
        tickers = {t for lg in ledgers.values() for t in lg.positions}
        prices = price_lookup(tickers) if tickers else {}
        sectors = wealth_store.sectors_for(tickers)
        s = wealth_service.summarize(ledgers, prices, sectors,
                                     max_position_pct=(portfolio or {}).get("max_position_pct"))
        s["transaction_count"] = len(txs)
        return s

    # ── Pages ────────────────────────────────────────────────────────────
    @bp.get("/wealth")
    def dashboard():
        user_id = uid()
        portfolios = wealth_store.list_portfolios(user_id)
        current = owned(user_id, request.args.get("p"))
        s = summary_for(user_id, current) if portfolios else None
        return render_template("wealth_dashboard.html", portfolios=portfolios, current=current,
                               s=s, kinds=wealth_store.KIND_LABELS, quick_start=QUICK_START)

    @bp.get("/wealth/ledger")
    def ledger_page():
        user_id = uid()
        portfolios = wealth_store.list_portfolios(user_id)
        current = owned(user_id, request.args.get("p")) or (portfolios[0] if portfolios else None)
        txs = wealth_store.list_transactions(user_id, current["id"]) if current else []
        lg = wealth_ledger.build(txs) if current else None
        return render_template("wealth_ledger.html", portfolios=portfolios, current=current,
                               txs=list(reversed(txs)), ledger=lg, types=wealth_ledger.TYPES,
                               kinds=wealth_store.KIND_LABELS, quick_start=QUICK_START)

    @bp.get("/wealth/lab")
    def lab_page():
        user_id = uid()
        portfolios = wealth_store.list_portfolios(user_id)
        current = owned(user_id, request.args.get("p"))
        s = summary_for(user_id, current) if portfolios else None
        holdings = [{"ticker": h["ticker"], "shares": h["shares"], "avg_cost": h["avg_cost"],
                     "price": h["price"], "price_source": h.get("price_source"), "price_as_of": h.get("price_as_of")}
                    for h in (s or {}).get("holdings", [])]
        return render_template("wealth_lab.html", portfolios=portfolios, current=current, holdings=holdings,
                               total_value=(s or {}).get("total_value"), cash=(s or {}).get("cash"),
                               ticker=(request.args.get("ticker") or "").upper()[:10])

    # ── Portfolios ───────────────────────────────────────────────────────
    @bp.post("/api/wealth/portfolios")
    def portfolio_create():
        user_id = uid()
        d = request.get_json(silent=True) or {}
        name = str(d.get("name") or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "Give the portfolio a name."}), 400
        if len(wealth_store.list_portfolios(user_id)) >= 20:
            return jsonify({"ok": False, "error": "Up to 20 portfolios."}), 400
        pid = wealth_store.create_portfolio(user_id, name, d.get("kind") or "taxable",
                                            _num(d.get("target_cash_pct")), _num(d.get("max_position_pct")))
        return jsonify({"ok": True, "id": pid})

    @bp.post("/api/wealth/portfolios/<int:pid>")
    def portfolio_update(pid):
        user_id = uid()
        owned(user_id, pid)
        d = request.get_json(silent=True) or {}
        fields = {k: d[k] for k in ("name", "kind", "broker_account") if k in d}
        for k in ("target_cash_pct", "max_position_pct"):
            if k in d:
                fields[k] = _num(d[k])
        wealth_store.update_portfolio(user_id, pid, **fields)
        return jsonify({"ok": True})

    @bp.delete("/api/wealth/portfolios/<int:pid>")
    def portfolio_delete(pid):
        user_id = uid()
        owned(user_id, pid)
        wealth_store.delete_portfolio(user_id, pid)
        return jsonify({"ok": True})

    # ── Transactions ─────────────────────────────────────────────────────
    def _clean_tx(d):
        tx = {"date": str(d.get("date") or "")[:10], "type": str(d.get("type") or "").upper(),
              "ticker": str(d.get("ticker") or "").upper().strip() or None,
              "shares": _num(d.get("shares")), "price": _num(d.get("price")), "amount": _num(d.get("amount")),
              "fees": _num(d.get("fees")) or 0.0, "ratio": _num(d.get("ratio")),
              "note": str(d.get("note") or "")[:200]}
        errs = wealth_ledger.validate(tx)
        if tx["ticker"] and not _TICKER.match(tx["ticker"]):
            errs.append("Ticker looks wrong.")
        return tx, errs

    @bp.post("/api/wealth/transactions")
    def tx_add():
        user_id = uid()
        d = request.get_json(silent=True) or {}
        p = owned(user_id, d.get("portfolio_id"))
        if not p:
            return jsonify({"ok": False, "error": "Choose a portfolio."}), 400
        tx, errs = _clean_tx(d)
        if errs:
            return jsonify({"ok": False, "error": " ".join(errs)}), 400
        wealth_store.add_transactions(user_id, p["id"], [tx], source="manual")
        lg = wealth_ledger.build(wealth_store.list_transactions(user_id, p["id"]))
        return jsonify({"ok": True, "issues": lg.issues[-3:]})

    @bp.put("/api/wealth/transactions/<int:tx_id>")
    def tx_update(tx_id):
        user_id = uid()
        tx, errs = _clean_tx(request.get_json(silent=True) or {})
        if errs:
            return jsonify({"ok": False, "error": " ".join(errs)}), 400
        if not wealth_store.update_transaction(user_id, tx_id, tx):
            abort(404)
        return jsonify({"ok": True})

    @bp.delete("/api/wealth/transactions/<int:tx_id>")
    def tx_delete(tx_id):
        if not wealth_store.delete_transaction(uid(), tx_id):
            abort(404)
        return jsonify({"ok": True})

    # ── CSV import: preview, then commit ─────────────────────────────────
    @bp.post("/api/wealth/import/preview")
    def import_preview():
        uid()
        f = request.files.get("file")
        if f:
            raw = f.read(2_000_001)
            if len(raw) > 2_000_000:
                return jsonify({"ok": False, "error": "File is over 2 MB."}), 400
            text = raw.decode("utf-8-sig", errors="replace")
        else:
            text = (request.get_json(silent=True) or {}).get("text") or ""
        parsed = wealth_csv.parse(text)
        if not parsed["format"]:
            return jsonify({"ok": False, "error": parsed["skipped"][0]["reason"]}), 400
        return jsonify({"ok": True, **parsed})

    @bp.post("/api/wealth/import/commit")
    def import_commit():
        user_id = uid()
        d = request.get_json(silent=True) or {}
        p = owned(user_id, d.get("portfolio_id"))
        if not p:
            return jsonify({"ok": False, "error": "Choose a portfolio."}), 400
        rows, bad = [], []
        for i, raw in enumerate(d.get("transactions") or []):
            tx, errs = _clean_tx(raw)
            if errs:
                bad.append({"index": i, "error": " ".join(errs)})
                continue
            tx["fingerprint"] = raw.get("fingerprint") or wealth_csv.fingerprint(tx)
            tx["source"] = raw.get("source") or "csv"
            rows.append(tx)
        if len(rows) > 5000:
            return jsonify({"ok": False, "error": "Import up to 5,000 rows at a time."}), 400
        res = wealth_store.add_transactions(user_id, p["id"], rows, source="csv")
        return jsonify({"ok": True, **res, "rejected": bad})

    @bp.get("/api/wealth/template.csv")
    def template():
        uid()
        return Response(wealth_csv.template_csv(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=wealth-architect-template.csv"})

    # ── Data ─────────────────────────────────────────────────────────────
    @bp.get("/api/wealth/summary")
    def summary_api():
        user_id = uid()
        s = summary_for(user_id, owned(user_id, request.args.get("p")))
        s["flows"] = [(d.isoformat(), a) for d, a in s["flows"]]
        return jsonify({"ok": True, **s})

    @bp.get("/api/wealth/benchmark")
    def benchmark_api():
        """The same deposits and withdrawals, put into SPY on the same days."""
        user_id = uid()
        s = summary_for(user_id, owned(user_id, request.args.get("p")))
        if not s["flows"]:
            return jsonify({"ok": False, "reason": "Add deposits to compare with the S&P 500."})
        if s["unpriced"]:
            return jsonify({"ok": False, "reason": "Some holdings have no price, so the total is incomplete."})
        try:
            bars = bars_lookup("SPY")
        except Exception as exc:
            logger.debug("SPY bars: %s", exc)
            bars = None
        spy = price_lookup(["SPY"]).get("SPY") or {}
        shadow = wealth_service.spy_shadow(s["flows"], bars, date.today(), spy.get("price"))
        if not shadow:
            return jsonify({"ok": False, "reason": "S&P 500 history is unavailable right now."})
        return jsonify({"ok": True, "portfolio_value": s["total_value"], "portfolio_mwr_pct": s["money_weighted_pct"],
                        "spy": shadow, "spy_price_source": spy.get("source"), "spy_as_of": spy.get("as_of"),
                        "note": "Each deposit buys SPY at that day's close; withdrawals sell it. "
                                "Price return of SPY; its dividends are not added."})

    @bp.post("/api/wealth/lab")
    def lab_api():
        uid()
        d = request.get_json(silent=True) or {}
        r = wealth_calc.simulate_purchase(
            existing_shares=d.get("existing_shares"), existing_avg_cost=d.get("existing_avg_cost"),
            purchase_price=d.get("purchase_price"), invest_amount=d.get("invest_amount"),
            future_price=d.get("future_price"), current_price=d.get("current_price"),
            fees=d.get("fees"), fractional=bool(d.get("fractional", True)),
            cash_available=d.get("cash_available"), portfolio_value=d.get("portfolio_value"))
        return jsonify(r), (200 if r.get("ok") else 400)

    @bp.get("/api/wealth/reconcile")
    def reconcile_api():
        user_id = uid()
        p = owned(user_id, request.args.get("p"))
        if not p:
            return jsonify({"ok": False, "reason": "Choose a portfolio."}), 400
        try:
            summ = broker_summary(user_id)
        except Exception as exc:
            logger.debug("reconcile broker: %s", exc)
            summ = None
        if not summ or not summ.get("connected"):
            return jsonify({"ok": False, "reason": "Schwab is not connected. Connect it under Account → Brokers "
                                                   "to compare this ledger with your Schwab positions."})
        accounts = summ.get("accounts") or []
        choices = [{"id": a.get("account_number"), "label": f"{a.get('account_type') or 'Account'} …{str(a.get('account_number'))[-4:]}"}
                   for a in accounts]
        want = request.args.get("account") or p.get("broker_account")
        acct = next((a for a in accounts if str(a.get("account_number")) == str(want)), None)
        if not acct:
            return jsonify({"ok": True, "needs_account": True, "accounts": choices})
        if str(p.get("broker_account") or "") != str(acct.get("account_number")):
            wealth_store.update_portfolio(user_id, p["id"], broker_account=str(acct.get("account_number")))
        lg = wealth_ledger.build(wealth_store.list_transactions(user_id, p["id"]))
        rec = wealth_service.reconcile(lg.holdings(), lg.cash, acct)
        return jsonify({"ok": True, "accounts": choices, "account": acct.get("account_number"), **rec})

    return bp
