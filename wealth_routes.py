"""Wealth Architect pages and APIs.

Every route reads the signed-in user's id and every storage call is scoped
to it; a portfolio id from the URL is only used after it is confirmed to
belong to that user.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from flask import Blueprint, Response, abort, jsonify, redirect, render_template, request, url_for

import wealth_calc
import wealth_broker_sync
import wealth_csv
import wealth_history
import wealth_ledger
import wealth_mcff
import wealth_nextdollar
import wealth_planner
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


def create_wealth_blueprint(*, current_user, price_lookup=None, bars_lookup=None, broker_summary=None,
                            fundamentals_lookup=None, zone_lookup=None, ai_draft=None):
    bp = Blueprint("wealth", __name__)
    @bp.get("/api/wealth/financial-intelligence/<ticker>")
    def financial_intelligence_api(ticker):
        """Canonical source-backed SEC view shared by research and capital pages."""
        if not current_user():
            abort(401)
        from wealth_financial_data import financial_snapshot
        result = financial_snapshot(ticker)
        if not result["ok"]:
            return jsonify(result), 404
        return jsonify(result)

    @bp.get("/api/wealth/financial-contract/<ticker>")
    def financial_contract_api(ticker):
        """Shared normalized SEC financial metric contract for all UI surfaces."""
        if not current_user():
            abort(401)
        from wealth_financial_data import financial_snapshot
        from wealth_financial_contract import normalize_financial_contract
        result = normalize_financial_contract(financial_snapshot(ticker))
        return jsonify(result), (200 if result["ok"] else 404)

    @bp.get("/api/wealth/advanced-financials/<ticker>")
    def advanced_financials_api(ticker):
        if not current_user():
            abort(401)
        from wealth_financial_data import financial_snapshot
        from wealth_advanced_financials import advanced_financial_snapshot
        result = advanced_financial_snapshot(financial_snapshot(ticker))
        return jsonify(result), (200 if result["ok"] else 404)

    @bp.get("/api/wealth/intelligence-3/<ticker>")
    def intelligence_3_api(ticker):
        """Shared read-only SEC observations for M.C.F.F., Discover and Verify."""
        if not current_user():
            abort(401)
        from wealth_financial_data import financial_snapshot
        from wealth_advanced_financials import advanced_financial_snapshot
        from wealth_intelligence_3 import assess
        contract = advanced_financial_snapshot(financial_snapshot(ticker))
        return jsonify({"ticker": contract.get("ticker"), "contract": contract,
                        "intelligence": assess(contract)}), (200 if contract.get("ok") else 404)

    @bp.get("/api/wealth/sec-evidence/<ticker>")
    def sec_evidence_api(ticker):
        if not current_user():
            abort(401)
        from wealth_sec_evidence import SEC_CIK, fetch_sec_evidence
        symbol = ticker.upper()
        if symbol not in SEC_CIK:
            return jsonify({"ok": False, "error": "No verified SEC issuer mapping"}), 404
        return jsonify({"ok": True, **fetch_sec_evidence(symbol)})

    @bp.get("/api/wealth/sec-dashboard/<ticker>")
    def sec_dashboard_api(ticker):
        result = financial_intelligence_api(ticker)
        if isinstance(result, tuple):
            return result
        return jsonify({"ok": True, **result.get_json()["dashboard"]})

    @bp.get("/api/wealth/sec-explanations/<ticker>")
    def sec_explanations_api(ticker):
        result = financial_intelligence_api(ticker)
        if isinstance(result, tuple):
            return result
        data = result.get_json()
        return jsonify({"ok": True, "ticker": data["ticker"], "cik": data["cik"],
                        "status": data["status"], "warnings": data["warnings"],
                        **data["analysis"]})

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

    def _default_fundamentals(ticker):
        from fundamentals_engine import get_fundamentals
        return get_fundamentals(ticker), wealth_store.fundamentals_fetched_at(ticker)

    bars_lookup = bars_lookup or _default_bars
    broker_summary = broker_summary or _default_broker
    fundamentals_lookup = fundamentals_lookup or _default_fundamentals
    zone_lookup = zone_lookup or (lambda ticker, price: None)
    closes_cache: dict = {}
    closes_lock = threading.Lock()

    def closes_for(tickers) -> dict:
        """{ticker: [(date, close)]}, split-adjusted daily closes, cached 6 h."""
        now = time.time()
        out, need = {}, []
        with closes_lock:
            for t in tickers:
                hit = closes_cache.get(t)
                if hit and now - hit[0] < 6 * 3600:
                    out[t] = hit[1]
                else:
                    need.append(t)

        def one(t):
            try:
                return t, wealth_service._closes(bars_lookup(t))
            except Exception as exc:
                logger.debug("history %s: %s", t, exc)
                return t, []
        if need:
            with ThreadPoolExecutor(max_workers=6) as pool:
                for t, rows in pool.map(one, need):
                    out[t] = rows
                    if rows:
                        with closes_lock:
                            closes_cache[t] = (now, rows)
        return out

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

    def broker_holdings_for(user_id, portfolio=None):
        """Read the latest broker snapshots; never fabricate ledger trades or tax lots."""
        import schwab_account_preferences as prefs
        allowed = prefs.get_preferences(user_id)
        selected = (str(portfolio.get("broker_account") or "") if portfolio else None)
        by_ticker = {}
        for row in wealth_store.list_broker_snapshots(user_id):
            key = row["account_key"]
            if not allowed.get(key, {}).get("include_wealth", True):
                continue
            if selected and key != selected:
                continue
            if portfolio and not selected:
                continue
            for h in row["snapshot"].get("holdings", []):
                ticker = str(h.get("ticker") or "")
                qty = _num(h.get("shares"))
                if not _TICKER.fullmatch(ticker) or qty is None or qty <= 0:
                    continue
                entry = by_ticker.setdefault(ticker, {"ticker": ticker, "shares": 0.0,
                    "market_value": 0.0, "basis": 0.0, "basis_known": True,
                    "source": "schwab_snapshot", "as_of": row["synced_at"]})
                entry["shares"] += qty
                mv = _num(h.get("market_value"))
                if mv is not None and entry["market_value"] is not None:
                    entry["market_value"] += mv
                else:
                    entry["market_value"] = None
                basis = _num(h.get("cost_basis_snapshot"))
                if basis is not None and basis > 0:
                    entry["basis"] += basis
                else:
                    entry["basis_known"] = False
        return list(by_ticker.values())

    def merge_holdings(ledger_rows, broker_rows):
        """One holdings list: ledger positions and Schwab snapshot positions.

        Ledger figures win when both exist (they carry lots and cost basis);
        the snapshot only adds what the ledger doesn't have yet. Each row says
        where it came from so nothing is counted twice or passed off as
        ledger-verified.
        """
        broker = {h["ticker"]: h for h in broker_rows or []}
        out = []
        for h in ledger_rows:
            b = broker.pop(h["ticker"], None)
            row = dict(h, source="both" if b else "ledger", broker_shares=b["shares"] if b else None)
            row["share_gap"] = bool(b and abs((b["shares"] or 0) - (h["shares"] or 0)) > 1e-6)
            out.append(row)
        for b in broker.values():
            avg = (b["basis"] / b["shares"]) if b.get("basis_known") and b.get("shares") else None
            mv = b.get("market_value")
            unreal = (mv - b["basis"]) if (mv is not None and b.get("basis_known") and b.get("basis")) else None
            out.append({"ticker": b["ticker"], "shares": b["shares"], "avg_cost": avg, "price": None,
                        "market_value": mv, "unrealized": unreal,
                        "unrealized_pct": (unreal / b["basis"] * 100) if unreal is not None else None,
                        "weight_pct": None, "sector": None, "stale": False,
                        "source": "schwab", "as_of": b.get("as_of"), "share_gap": False})
        out.sort(key=lambda r: -(r.get("market_value") or 0))
        return out

    RING_COLORS = ("#e5b94f", "#5fb3ff", "#4fd1a5", "#c98bff", "#ff8a5c", "#f06292", "#9ccc65", "#8d9db6")

    def sector_ring(sectors):
        """conic-gradient stops for the allocation ring (top 7 + Other)."""
        rows = [r for r in sectors if (r.get("pct") or 0) > 0]
        top, rest = rows[:7], rows[7:]
        if rest:
            top = top + [{"sector": "Other", "pct": sum(r["pct"] for r in rest)}]
        stops, at, legend = [], 0.0, []
        for i, r in enumerate(top):
            color = RING_COLORS[i % len(RING_COLORS)]
            end = min(100.0, at + r["pct"])
            stops.append(f"{color} {at:.2f}% {end:.2f}%")
            legend.append({"sector": r["sector"], "pct": r["pct"], "color": color})
            at = end
        if at < 99.9 and stops:
            stops.append(f"rgba(255,255,255,.06) {at:.2f}% 100%")
        return {"gradient": ", ".join(stops), "legend": legend} if stops else None

    # Broker snapshots are deliberately separate from the transaction ledger.
    # GET only: no orders, deposits, or synthetic cost-basis transactions.
    @bp.get("/api/wealth/brokerage/discovery")
    def brokerage_discovery():
        user_id = uid()
        try:
            snapshot = broker_summary(user_id)
        except Exception:
            logger.exception("wealth brokerage discovery failed")
            return jsonify({"ok": False, "error": "Brokerage data is temporarily unavailable."}), 503
        # Account inclusion is controlled on the Schwab Account page.
        # Keep aggregate broker summary intact; filter only Wealth account rows.
        if snapshot and snapshot.get("connected"):
            import schwab_account_preferences as _prefs
            snapshot = dict(snapshot)
            snapshot["accounts"] = _prefs.select_accounts(
                snapshot.get("accounts") or [], _prefs.get_preferences(user_id), for_wealth=True)
        # Only successful fetches may update stored broker snapshots.
        # Never delete existing snapshots when a broker is unavailable.
        if snapshot and snapshot.get("connected"):
            for account in snapshot.get("accounts") or []:
                if not account.get("equity_positions"):
                    continue
                key = str(account.get("account_hash") or account.get("account_number") or "")
                if not key:
                    continue
                try:
                    wealth_store.link_broker_portfolio(user_id, key)
                except Exception:
                    logger.exception("Failed to provision brokerage portfolio")
        result = wealth_broker_sync.discover(
            snapshot, wealth_store.list_portfolios(user_id),
            wealth_store.list_transactions(user_id))
        for account in result.get("accounts", []):
            key = account["account_id"]
            if not account.get("portfolio_id"):
                continue
            try:
                wealth_store.save_broker_snapshot(user_id, key, account)
                txs = wealth_store.list_transactions(user_id, account["portfolio_id"])
                ledger = wealth_ledger.build(txs)
                account["discrepancies"] = wealth_broker_sync.reconcile(
                    account, {ticker: sum(lot.shares for lot in lots) for ticker, lots in ledger.positions.items()})
            except Exception:
                logger.exception("Brokerage snapshot/reconciliation failure")
                account["sync_error"] = "Unable to persist or reconcile this account"
            # Never expose full account identifiers in browser responses.
            account["account_id"] = "••••" + key[-4:]
        return jsonify({"ok": result["connected"], **result})

    # ── Pages ────────────────────────────────────────────────────────────
    @bp.get("/wealth")
    def dashboard():
        user_id = uid()
        portfolios = wealth_store.list_portfolios(user_id)
        current = owned(user_id, request.args.get("p"))
        s = summary_for(user_id, current) if portfolios else None
        broker = broker_holdings_for(user_id, current)
        rows = merge_holdings((s or {}).get("holdings") or [], broker)
        return render_template("wealth_dashboard.html", portfolios=wealth_store.visible_portfolio_tabs(user_id, portfolios), current=current,
                               s=s, broker_holdings=broker, rows=rows,
                               broker_total=sum(h["market_value"] or 0 for h in broker) if broker else None,
                               ring=sector_ring((s or {}).get("sectors") or []),
                               kinds=wealth_store.KIND_LABELS, quick_start=QUICK_START)

    @bp.get("/wealth/ledger")
    def ledger_page():
        user_id = uid()
        portfolios = wealth_store.list_portfolios(user_id)
        current = owned(user_id, request.args.get("p")) or (portfolios[0] if portfolios else None)
        txs = wealth_store.list_transactions(user_id, current["id"]) if current else []
        lg = wealth_ledger.build(txs) if current else None
        return render_template("wealth_ledger.html", portfolios=wealth_store.visible_portfolio_tabs(user_id, portfolios), current=current,
                               txs=list(reversed(txs)), ledger=lg, types=wealth_ledger.TYPES,
                               kinds=wealth_store.KIND_LABELS, quick_start=QUICK_START)

    @bp.get("/wealth/scoreboard")
    def scoreboard_page():
        user_id = uid()
        portfolios = wealth_store.list_portfolios(user_id)
        current = owned(user_id, request.args.get("p"))
        return render_template("wealth_scoreboard.html", portfolios=wealth_store.visible_portfolio_tabs(user_id, portfolios), current=current,
                               kinds=wealth_store.KIND_LABELS, quick_start=QUICK_START)

    @bp.get("/api/wealth/scoreboard")
    def scoreboard_api():
        user_id = uid()
        current = owned(user_id, request.args.get("p"))
        ledgers, _txs = ledgers_for(user_id, current)
        if not ledgers:
            return jsonify({"ok": False, "reason": "No transactions yet."})
        lgs = list(ledgers.values())
        tickers = sorted({t for lg in lgs for _, _, held in lg.snapshots for t in held})
        closes = closes_for(tickers + ["SPY"])
        spy = closes.pop("SPY", [])
        if not spy:
            return jsonify({"ok": False, "reason": "S&P 500 history is unavailable right now, so there is no trading calendar to value on."})
        live = {t: q["price"] for t, q in (price_lookup(tickers) or {}).items() if q.get("live")}
        today = date.today()
        held_now = {t for lg in lgs for t in lg.positions}
        series = wealth_history.build_series(lgs, closes, [d for d, _ in spy],
                                             today_prices=live if held_now <= set(live) else None, today=today)
        if not series["ok"]:
            return jsonify(series)
        out = wealth_history.scoreboard(series, spy)
        div_years = {}
        for t in _txs:
            if t["type"] in ("DIVIDEND", "REINVEST"):
                y = str(t["date"])[:4]
                v = t["amount"] or ((t["shares"] or 0) * (t["price"] or 0))
                div_years[y] = div_years.get(y, 0.0) + (v or 0)
        out["dividends_by_year"] = div_years
        out["reinvested"] = sum(1 for t in _txs if t["type"] == "REINVEST")
        return jsonify({"ok": True, **out})

    @bp.get("/wealth/lab")
    def lab_page():
        user_id = uid()
        portfolios = wealth_store.list_portfolios(user_id)
        current = owned(user_id, request.args.get("p"))
        s = summary_for(user_id, current) if portfolios else None
        holdings = [{"ticker": h["ticker"], "shares": h["shares"], "avg_cost": h["avg_cost"],
                     "price": h["price"], "price_source": h.get("price_source"), "price_as_of": h.get("price_as_of")}
                    for h in (s or {}).get("holdings", [])]
        for bh in broker_holdings_for(user_id, current):
            holdings = [h for h in holdings if h["ticker"] != bh["ticker"]]
            basis = bh["basis"] / bh["shares"] if bh["basis_known"] else None
            price = bh["market_value"] / bh["shares"] if bh["market_value"] is not None else None
            holdings.append({"ticker": bh["ticker"], "shares": bh["shares"], "avg_cost": basis,
                             "price": price, "price_source": "Schwab snapshot", "price_as_of": bh["as_of"],
                             "source": "schwab_snapshot"})
        return render_template("wealth_lab.html", portfolios=wealth_store.visible_portfolio_tabs(user_id, portfolios), current=current, holdings=holdings,
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
        ty = str(d.get("tax_year") or "").strip()
        tx["tax_year"] = int(ty) if ty.isdigit() and 1990 <= int(ty) <= 2100 else None
        errs = wealth_ledger.validate(tx)
        if tx["tax_year"] and tx["type"] == "DEPOSIT":
            try:
                dy = date.fromisoformat(tx["date"]).year
                if tx["tax_year"] not in (dy, dy - 1) or (tx["tax_year"] == dy - 1 and tx["date"][5:] > "04-15"):
                    errs.append("An IRA deposit can count toward this year, or the prior year only until April 15.")
            except ValueError:
                pass
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

    # ── Command Center: your money today ─────────────────────────────────
    @bp.get("/api/wealth/today")
    def today_api():
        """All portfolios combined, for the Command Center strip.

        Value, today's move in dollars and %, cash, and the best and worst
        holding today. During the regular session the move is the live price
        against the prior close; otherwise it is the last session's official
        close against the one before (labelled with that day), so an
        after-hours print never passes for the day's move. Falls back to the
        broker snapshot only when there are no Wealth portfolios, so a Schwab
        account imported into Wealth is never counted twice.
        """
        user_id = uid()
        s = summary_for(user_id, None)
        source = "wealth"
        holdings = [{"ticker": h["ticker"], "shares": h["shares"], "price": h.get("price"),
                     "market_value": h.get("market_value")} for h in s.get("holdings") or []]
        cash = float(s.get("cash") or 0.0)
        value = s.get("total_value")
        if not s.get("transaction_count"):
            snap = broker_holdings_for(user_id)
            if not snap:
                return jsonify({"ok": True, "empty": True})
            source = "schwab"
            holdings = [{"ticker": h["ticker"], "shares": h["shares"], "price": None,
                         "market_value": h.get("market_value")} for h in snap]
            cash = 0.0
            value = sum(h["market_value"] or 0 for h in holdings)
        tickers = [h["ticker"] for h in holdings if h["shares"]]
        try:
            import market_engine as _me
            session = _me.pulse_session()
        except Exception:
            session = "closed"
        closes = closes_for(tickers) if tickers else {}
        live = price_lookup(tickers) if (tickers and session == "regular") else {}
        today = date.today()
        moves, day_label = [], None
        for h in holdings:
            rows = closes.get(h["ticker"]) or []
            if len(rows) < 2 or not h["shares"]:
                continue
            if session == "regular" and (live.get(h["ticker"]) or {}).get("price"):
                prev = next((c for d, c in reversed(rows) if d < today), None)
                now_px = float(live[h["ticker"]]["price"])
            else:
                prev, now_px = rows[-2][1], rows[-1][1]
                day_label = rows[-1][0].strftime("%a")
            if not prev:
                continue
            pct = (now_px / prev - 1) * 100
            moves.append({"ticker": h["ticker"], "pct": round(pct, 2),
                          "dollars": round((now_px - prev) * h["shares"], 2)})
        day_dollars = round(sum(m["dollars"] for m in moves), 2)
        base = (value or 0) - day_dollars
        ranked = sorted(moves, key=lambda m: m["pct"])
        # Per-holding value + today's move, for the globe's My Portfolio view.
        by_move = {m["ticker"]: m for m in moves}
        sized = []
        for h in holdings:
            if not h["shares"]:
                continue
            mv = h.get("market_value")
            if mv is None and h.get("price") is not None:
                mv = float(h["price"]) * float(h["shares"])
            if not mv or mv <= 0:
                continue
            m = by_move.get(h["ticker"]) or {}
            sized.append({"ticker": h["ticker"], "value": round(float(mv), 2),
                          "pct": m.get("pct"), "dollars": m.get("dollars"),
                          "price": h.get("price")})
        try:
            import globe_sectors as _gs
            smap = _gs.sector_map()
            _gs.resolve_unknown_async([x["ticker"] for x in sized], known=smap)
        except Exception:
            smap = {}
        for x in sized:
            x["sector"] = smap.get(x["ticker"]) or None
        invested = sum(x["value"] for x in sized)
        for x in sized:
            x["weight"] = round(x["value"] / invested * 100, 2) if invested > 0 else None
        sized.sort(key=lambda x: -x["value"])
        return jsonify({"ok": True, "empty": False, "source": source, "value": value, "cash": cash,
                        "holdings": sized[:40],
                        "day_dollars": day_dollars,
                        "day_pct": round(day_dollars / base * 100, 2) if base > 0 else None,
                        "best": ranked[-1] if ranked else None, "worst": ranked[0] if ranked else None,
                        "positions": len(tickers), "priced": len(moves), "tickers": tickers[:40],
                        "session": session, "day_label": None if session == "regular" else day_label,
                        "unpriced": s.get("unpriced") or []})

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

    @bp.route("/api/wealth/lab/scenarios", methods=["GET", "POST"])
    def lab_scenarios_api():
        import wealth_lab_scenarios
        user_id = uid()
        if request.method == "GET":
            return jsonify({"ok": True, "scenarios": wealth_lab_scenarios.list_scenarios(user_id)})
        d = request.get_json(silent=True) or {}
        data = d.get("data") or {}
        ticker = str(data.get("ticker") or "").upper().strip()
        if not _TICKER.fullmatch(ticker):
            return jsonify({"ok": False, "error": "Invalid ticker"}), 400
        if not wealth_calc.simulate_purchase(
            existing_shares=data.get("existing_shares"), existing_avg_cost=data.get("existing_avg_cost"),
            purchase_price=data.get("purchase_price"), invest_amount=data.get("invest_amount"),
            future_price=data.get("future_price"), current_price=data.get("current_price"),
            fees=data.get("fees"), fractional=data.get("fractional", True)).get("ok"):
            return jsonify({"ok": False, "error": "Run a valid simulation before saving"}), 400
        try:
            wealth_lab_scenarios.save_scenario(user_id, d.get("name"), data)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "scenarios": wealth_lab_scenarios.list_scenarios(user_id)})

    @bp.delete("/api/wealth/lab/scenarios/<int:scenario_id>")
    def lab_scenario_delete(scenario_id):
        import wealth_lab_scenarios
        wealth_lab_scenarios.delete_scenario(uid(), scenario_id)
        return jsonify({"ok": True})

    @bp.get("/api/wealth/lab/compare")
    def lab_compare_api():
        user_id = uid()
        ticker = str(request.args.get("ticker") or "").upper().strip()
        if not _TICKER.fullmatch(ticker):
            return jsonify({"ok": False, "error": "Invalid ticker"}), 400
        data = mcff_for(user_id, ticker)
        return jsonify({"ok": True, "ticker": ticker, "price": data["price"],
                        "sector": data["sector"], "evaluation": data["evaluation"],
                        "value_zone": data["value_zone"], "price_as_of": data["price_as_of"]})

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

    # ── M.C.F.F. research ────────────────────────────────────────────────
    def mcff_for(user_id, ticker, research=None):
        research = research if research is not None else (wealth_store.get_research(user_id, ticker) or {})
        try:
            fund, fetched = fundamentals_lookup(ticker)
        except Exception as exc:
            logger.debug("mcff fundamentals %s: %s", ticker, exc)
            fund, fetched = None, None
        q = (price_lookup([ticker]) or {}).get(ticker) or {}
        price = q.get("price")
        try:
            zone = zone_lookup(ticker, price)
        except Exception as exc:
            logger.debug("mcff zone %s: %s", ticker, exc)
            zone = None
        fin = wealth_mcff.financials_gate(fund, fetched_at=fetched)
        ev = wealth_mcff.evaluate(research, fin, price=price, value_zone=zone)
        pe = ((fund or {}).get("valuation") or {}).get("pe")
        defaults = wealth_mcff.default_assumptions(fund, pe)
        assumptions = research.get("assumptions") or {}
        merged = {"years": assumptions.get("years") or defaults["years"],
                  "growth": {c: ((assumptions.get("growth") or {}).get(c) if (assumptions.get("growth") or {}).get(c) is not None
                                 else defaults["growth"][c]) for c in ("conservative", "base", "optimistic")},
                  "exit_pe": {c: ((assumptions.get("exit_pe") or {}).get(c) if (assumptions.get("exit_pe") or {}).get(c) is not None
                                  else defaults["exit_pe"][c]) for c in ("conservative", "base", "optimistic")}}
        return {"ticker": ticker, "company": (fund or {}).get("company_name"), "sector": (fund or {}).get("sector"),
                "price": price, "price_source": q.get("source"), "price_as_of": q.get("as_of"), "pe": pe,
                "value_zone": zone, "evaluation": ev, "research": research,
                "assumptions": merged, "defaults": defaults,
                "scenarios": wealth_mcff.scenarios(price, pe, merged),
                "prompts": wealth_mcff.GATE_PROMPTS, "labels": wealth_mcff.GATE_LABELS}

    def _tk(ticker):
        t = (ticker or "").upper().strip()
        if not _TICKER.match(t):
            abort(404)
        return t

    @bp.get("/wealth/research")
    def research_list_page():
        user_id = uid()
        portfolios = wealth_store.list_portfolios(user_id)
        researched = wealth_store.list_research(user_id)
        held = sorted({t for lg in ledgers_for(user_id, None)[0].values() for t in lg.positions})
        broker_tickers = {h["ticker"] for h in broker_holdings_for(user_id)}
        return render_template("wealth_research_list.html", portfolios=wealth_store.visible_portfolio_tabs(user_id, portfolios), current=None,
                               researched=researched, held=sorted(set(held) | broker_tickers), kinds=wealth_store.KIND_LABELS,
                               quick_start=QUICK_START, labels=wealth_mcff.GATE_LABELS)

    @bp.get("/wealth/research/<ticker>")
    def research_page(ticker):
        """M.C.F.F. now lives in the Fundamentals Analyzer, beside the numbers it scores."""
        return redirect(url_for("fundamentals_page", ticker=_tk(ticker), tab="mcff"), code=302)

    @bp.get("/api/wealth/position/<ticker>")
    def position_api(ticker):
        """'Your position' strip in the Fundamentals Analyzer: ledger first, Schwab snapshot as fallback."""
        import wealth_capital_journal
        user_id = uid()
        t = _tk(ticker)
        s = summary_for(user_id, None)
        h = next((x for x in s.get("holdings") or [] if x["ticker"] == t), None)
        out = {"ok": True, "ticker": t, "held": False}
        if h:
            out.update(held=True, source="ledger", shares=h["shares"], avg_cost=h.get("avg_cost"),
                       market_value=h.get("market_value"), weight_pct=h.get("weight_pct"),
                       unrealized=h.get("unrealized"), unrealized_pct=h.get("unrealized_pct"))
        else:
            b = next((x for x in broker_holdings_for(user_id) if x["ticker"] == t), None)
            if b:
                total = sum(x["market_value"] or 0 for x in broker_holdings_for(user_id)) or None
                avg = (b["basis"] / b["shares"]) if b.get("basis_known") and b.get("shares") else None
                unreal = (b["market_value"] - b["basis"]) if (b.get("market_value") is not None and b.get("basis_known") and b.get("basis")) else None
                out.update(held=True, source="schwab", shares=b["shares"], avg_cost=avg, market_value=b.get("market_value"),
                           weight_pct=(b["market_value"] / total * 100) if (total and b.get("market_value") is not None) else None,
                           unrealized=unreal, unrealized_pct=(unreal / b["basis"] * 100) if unreal is not None else None)
        try:
            last = next((d for d in wealth_capital_journal.list_decisions(user_id) if d["ticker"] == t), None)
        except Exception:
            last = None
        if last:
            out["last_decision"] = {k: last.get(k) for k in ("decision", "amount", "created_at", "review_date", "thesis")}
        return jsonify(out)

    @bp.get("/api/wealth/mcff/<ticker>")
    def mcff_api(ticker):
        user_id = uid()
        return jsonify({"ok": True, "ai_available": bool(ai_draft or _ai_configured()), **mcff_for(user_id, _tk(ticker))})

    @bp.post("/api/wealth/mcff/<ticker>")
    def mcff_save(ticker):
        user_id = uid()
        t = _tk(ticker)
        data = wealth_mcff.clean_research(request.get_json(silent=True) or {})
        prev = wealth_store.get_research(user_id, t) or {}
        for g in ("moat", "ceo", "future"):
            # Keep the record that a write-up started as an AI draft.
            if (request.get_json(silent=True) or {}).get(g, {}).get("drafted_by_ai") or (prev.get(g) or {}).get("drafted_by_ai"):
                data[g]["drafted_by_ai"] = True
        wealth_store.save_research(user_id, t, data)
        return jsonify({"ok": True, **mcff_for(user_id, t, data)})

    @bp.delete("/api/wealth/mcff/<ticker>")
    def mcff_delete(ticker):
        wealth_store.delete_research(uid(), _tk(ticker))
        return jsonify({"ok": True})

    @bp.get("/api/wealth/mcff-status")
    def mcff_statuses():
        """Status for each researched ticker (and holdings), for lists and alerts."""
        user_id = uid()
        researched = wealth_store.list_research(user_id)
        asked = {t for t in request.args.get("tickers", "").upper().split(",") if _TICKER.match(t)}
        base = set() if request.args.get("only") else set(researched)
        want = sorted(base | asked)[:40]
        def one(t):
            try:
                m = mcff_for(user_id, t, researched.get(t) or {})
                ev = m["evaluation"]
                return {"ticker": t, "status": ev["status"], "reasons": ev["reasons"], "price": m["price"],
                        "gates": {g: ev["gates"][g]["passed"] for g in wealth_mcff.GATES},
                        "zone_text": ev["valuation"]["zone_text"], "researched": t in researched}
            except Exception as exc:
                logger.debug("mcff status %s: %s", t, exc)
                return {"ticker": t, "status": None, "researched": t in researched}
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows = list(pool.map(one, want))
        return jsonify({"ok": True, "rows": rows})

    def _ai_configured():
        import os
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    @bp.post("/api/wealth/mcff/<ticker>/draft")
    def mcff_draft(ticker):
        """An AI-written starting point for one judged gate. Labeled as
        interpretation; it only counts once the investor saves it."""
        user_id = uid()
        t = _tk(ticker)
        gate = (request.get_json(silent=True) or {}).get("gate")
        if gate not in ("moat", "ceo", "future"):
            return jsonify({"ok": False, "error": "Choose Moat, CEO or Future."}), 400
        m = mcff_for(user_id, t)
        drafter = ai_draft or _anthropic_draft
        try:
            draft, why = drafter(t, gate, m)
        except Exception as exc:
            logger.warning("mcff draft %s %s: %s", t, gate, exc)
            draft, why = None, "The AI draft failed."
        if not draft:
            return jsonify({"ok": False, "error": why or "No draft."}), 503
        return jsonify({"ok": True, "gate": gate, "draft": draft})

    # ── Next Dollar ──────────────────────────────────────────────────────
    @bp.get("/wealth/next-dollar")
    def next_dollar_page():
        user_id = uid()
        portfolios = wealth_store.list_portfolios(user_id)
        current = owned(user_id, request.args.get("p")) or (portfolios[0] if portfolios else None)
        researched = sorted(wealth_store.list_research(user_id))
        held = sorted({t for lg in (ledgers_for(user_id, current)[0].values() if current else []) for t in lg.positions})
        broker_tickers = {h["ticker"] for h in broker_holdings_for(user_id, current)}
        return render_template("wealth_next_dollar.html", portfolios=wealth_store.visible_portfolio_tabs(user_id, portfolios), current=current,
                               candidates=sorted(set(researched) | set(held) | broker_tickers), researched=researched,
                               kinds=wealth_store.KIND_LABELS, quick_start=QUICK_START)

    @bp.post("/api/wealth/next-dollar")
    def next_dollar_api():
        user_id = uid()
        d = request.get_json(silent=True) or {}
        p = owned(user_id, d.get("portfolio_id"))
        amount = _num(d.get("amount"))
        if not amount or amount <= 0:
            return jsonify({"ok": False, "error": "Enter an amount to invest."}), 400
        tickers = []
        for t in (d.get("tickers") or [])[:12]:
            t = str(t).upper().strip()
            if _TICKER.match(t) and t not in tickers:
                tickers.append(t)
        if not tickers:
            return jsonify({"ok": False, "error": "Pick at least one candidate."}), 400
        s = summary_for(user_id, p)
        holdings = {h["ticker"]: h["market_value"] or 0.0 for h in s["holdings"]}
        sectors = {r["sector"]: r["value"] for r in s["sectors"] if r["sector"] != "Cash"}
        researched = wealth_store.list_research(user_id)

        def one(t):
            m = mcff_for(user_id, t, researched.get(t) or {})
            return {"ticker": t, "price": m["price"], "sector": m["sector"] or (wealth_store.sectors_for([t]).get(t) or (None,))[0],
                    "evaluation": m["evaluation"], "scenarios": m["scenarios"], "company": m["company"]}
        with ThreadPoolExecutor(max_workers=6) as pool:
            cands = list(pool.map(one, tickers))
        out = wealth_nextdollar.rank(amount, cands, total_value=s["total_value"], holdings=holdings, sectors=sectors,
                                     max_position_pct=(p or {}).get("max_position_pct"), hurdle_pct=_num(d.get("hurdle_pct")))
        out["cash_available"] = s["cash"]
        out["over_cash"] = amount > (s["cash"] or 0) + 0.005
        out["scenarios"] = {c["ticker"]: c["scenarios"] for c in cands}
        return jsonify({"ok": True, **out})

    # ── Capital Deployment: read-only balanced investment decisions ───────
    @bp.get("/wealth/capital")
    def capital_page():
        """Merged into Next Dollar's Split tab; old links and bookmarks still land."""
        args = {k: v for k, v in request.args.items() if k == "p"}
        return redirect(url_for("wealth.next_dollar_page", **args) + "#split", code=302)

    @bp.post("/api/wealth/capital/rank")
    def capital_rank_api():
        import wealth_capital
        user_id = uid()
        d = request.get_json(silent=True) or {}
        p = owned(user_id, d.get("portfolio_id"))
        amount = _num(d.get("amount"))
        if amount is None or amount <= 0 or amount > 1000000000:
            return jsonify({"ok": False, "error": "Enter a valid amount"}), 400
        tickers = []
        for raw in (d.get("tickers") or [])[:12]:
            t = str(raw).upper().strip()
            if _TICKER.fullmatch(t) and t not in tickers:
                tickers.append(t)
        if not tickers:
            return jsonify({"ok": False, "error": "Choose at least one ticker"}), 400
        summary = summary_for(user_id, p)
        brokers = broker_holdings_for(user_id, p)
        state = wealth_capital.portfolio_state(summary, brokers)
        sectors_lookup = wealth_store.sectors_for(list(state["holdings"]) + tickers)
        sectors = {}
        for ticker, value in state["holdings"].items():
            sector = (sectors_lookup.get(ticker) or ("Unclassified",))[0] or "Unclassified"
            sectors[sector] = sectors.get(sector, 0) + value
        state["sectors"] = sectors
        state["unknown_sectors"] = [t for t in state["holdings"] if t not in sectors_lookup]
        research = wealth_store.list_research(user_id)
        def one(t):
            m = mcff_for(user_id, t, research.get(t) or {})
            sector = (sectors_lookup.get(t) or (None,))[0]
            zone = m.get("value_zone") or {}
            price_date = str(m.get("price_as_of") or "")[:10]
            from datetime import datetime, timezone
            try:
                age = (datetime.now(timezone.utc).date() - date.fromisoformat(price_date)).days
            except (ValueError, TypeError):
                age = None
            valuation_verified = bool(m["evaluation"].get("valuation", {}).get("zone_source") == "investor" and price_date and age is not None and 0 <= age <= 7)
            return {"ticker": t, "price": m["price"], "company": m["company"],
                    "sector": sector, "sector_verified": bool(sector and sector != "Unclassified" and not state["unknown_sectors"]),
                    "valuation_verified": valuation_verified, "valuation_as_of": price_date,
                    "evaluation": m["evaluation"]}
        with ThreadPoolExecutor(max_workers=6) as pool:
            candidates = list(pool.map(one, tickers))
        try:
            position_limit = _num(d.get("position_limit")) or 25
            sector_limit = _num(d.get("sector_limit")) or 40
            if not (1 <= position_limit <= 100 and 1 <= sector_limit <= 100):
                return jsonify({"ok": False, "error": "Limits must be 1–100%"}), 400
            result = wealth_capital.balanced_rank(
                amount, candidates, state, position_limit=position_limit,
                sector_limit=sector_limit, cash_is_existing=bool(d.get("existing_cash")),
                cash_reserve=_num(d.get("cash_reserve")) or 0)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        import wealth_portfolio_intelligence as intelligence
        classifications = {t: intelligence.classify(t, (sectors_lookup.get(t) or (None,))[0]) for t in state["holdings"]}
        result["exposure"] = intelligence.exposure(state, classifications)
        result["allocation_plans"] = intelligence.split_options(amount, result["rows"])
        result["data_quality"] = intelligence.quality_monitor(candidates, state)
        import wealth_etf_lookthrough as etf_lookthrough
        fund_snapshots = etf_lookthrough.load_snapshots(state["holdings"])
        result["etf_lookthrough"] = etf_lookthrough.lookthrough(state, fund_snapshots)
        import wealth_etf_ingestion as issuer_ingestion
        import wealth_etf_refresh as etf_refresh
        fetched, feed_errors, feed_statuses = etf_refresh.refresh(state["holdings"])
        fund_snapshots.update(fetched)
        result["etf_lookthrough"] = etf_lookthrough.lookthrough(state, fund_snapshots)
        result["etf_lookthrough"]["configured"] = bool(fund_snapshots)
        result["etf_lookthrough"]["feed_errors"] = feed_errors
        result["etf_lookthrough"]["feed_statuses"] = feed_statuses
        result["etf_lookthrough"]["refresh_policy"] = "On-demand, at most hourly per server process; not scheduled or persisted"
        result["etf_lookthrough"]["overlap"] = issuer_ingestion.company_overlap(state, fund_snapshots)
        import wealth_security_identity as security_identity
        result["security_identity"] = {}
        if "SPCX" in state["holdings"]:
            broker_spcx = next((h for h in brokers if str(h.get("ticker") or h.get("symbol") or "").upper() == "SPCX"), {})
            identity = security_identity.identity("SPCX", cusip=broker_spcx.get("cusip"),
                                                 description=broker_spcx.get("description"))
            result["security_identity"]["SPCX"] = identity
            from wealth_data_confidence import data_confidence
            result["security_data_confidence"] = {"SPCX": data_confidence(
                "SPCX", broker_cusip=broker_spcx.get("cusip"),
                broker_description=broker_spcx.get("description"))}
            result["etf_lookthrough"]["identity_warnings"] = ([] if identity["verified"] else [identity["reason"]])
        else:
            result["etf_lookthrough"]["identity_warnings"] = []
        result["allocation_evidence"] = {"lookthrough_in_ranking": False,
            "reason": "ETF look-through is informational only until broker identifiers and all issuer sector data are verified; unverified exposure must not relax concentration gates."}

        return jsonify({"ok": True, **result, "snapshot": {"total_value": state["total_value"],
                        "cash": state["cash"], "holdings": state["holdings"],
                        "unknown_market_values": state["unknown_market_values"]}})

    @bp.route("/api/wealth/capital/journal", methods=["GET", "POST"])
    def capital_journal_api():
        import wealth_capital_journal
        user_id = uid()
        if request.method == "GET":
            return jsonify({"ok": True, "decisions": wealth_capital_journal.list_decisions(user_id)})
        d = request.get_json(silent=True) or {}
        ticker = str(d.get("ticker") or "").upper().strip()
        if not _TICKER.fullmatch(ticker):
            return jsonify({"ok": False, "error": "Invalid ticker"}), 400
        try:
            wealth_capital_journal.add_decision(user_id, d)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True})

    # ── Seasonal Planner ─────────────────────────────────────────────────
    @bp.get("/wealth/planner")
    def planner_page():
        user_id = uid()
        portfolios = wealth_store.list_portfolios(user_id)
        try:
            year = int(request.args.get("year") or date.today().year)
        except ValueError:
            year = date.today().year
        year = max(2000, min(2100, year))
        return render_template("wealth_planner.html", portfolios=wealth_store.visible_portfolio_tabs(user_id, portfolios), current=None, year=year,
                               kinds=wealth_store.KIND_LABELS, quick_start=QUICK_START,
                               months=wealth_planner.MONTHS, seasons=wealth_planner.SEASONS,
                               ira_kinds=wealth_planner.IRA_KINDS)

    def _plan_view(user_id, year):
        portfolios = wealth_store.list_portfolios(user_id)
        plan = wealth_store.get_plan(user_id, year) or {"entries": [], "goals": {}, "targets": {}}
        actual = [t for t in wealth_store.list_transactions(user_id) if t["type"] == "DEPOSIT"]
        view = wealth_planner.schedule(plan, year, portfolios, actual)
        return {"plan": plan, "view": view}

    @bp.get("/api/wealth/plan/<int:year>")
    def plan_get(year):
        return jsonify({"ok": True, **_plan_view(uid(), year)})

    @bp.post("/api/wealth/plan/<int:year>")
    def plan_save(year):
        user_id = uid()
        ids = {p["id"] for p in wealth_store.list_portfolios(user_id)}
        plan = wealth_planner.clean_plan(request.get_json(silent=True) or {}, year, ids)
        wealth_store.save_plan(user_id, year, plan)
        return jsonify({"ok": True, **_plan_view(user_id, year)})

    return bp


def _anthropic_draft(ticker: str, gate: str, m: dict):
    """(draft dict, reason-if-none). Uses only facts the app holds; anything
    from general knowledge must be marked as such by the model."""
    import json as _json
    import os
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None, "No ANTHROPIC_API_KEY is set on this server, so AI drafts are off."
    try:
        import anthropic
        from quarter_brief import MODEL
    except ImportError:
        return None, "The anthropic package is not installed on this server."
    fin = m["evaluation"]["gates"]["financials"]
    facts = [f"{e['label']}: {e['value']} ({'pass' if e['passed'] else 'fail'})" for e in fin.get("evidence") or []][:20]
    prompt = f"""Company: {m.get('company') or ticker} ({ticker}); sector: {m.get('sector') or 'unknown'}.
Gate to draft: {wealth_mcff.GATE_LABELS[gate]} — {wealth_mcff.GATE_PROMPTS[gate]}

VERIFIED FACTS FROM THE APP (SEC filings via the Fundamentals Engine):
{chr(10).join(facts) or 'none available'}

Write JSON only, with keys:
  "score": a number 0-10 for this gate,
  "evidence": 3 to 5 short sentences. Mark anything that is not in the verified facts above with "(general knowledge, verify)". Do not invent figures, dates, quotes or sources.
  "risks": 2 or 3 short sentences.
  "confidence": "low", "medium" or "high" — low unless the verified facts carry it.
  "check": 2 or 3 specific things the investor should verify, each naming where (e.g. "the latest 10-K, Item 1A", "the proxy statement's insider ownership table")."""
    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(model=MODEL, max_tokens=900,
                                  system="You draft investment research notes for an individual investor. You are careful, plain-spoken, and you never fabricate data or sources.",
                                  messages=[{"role": "user", "content": prompt}])
    text = "".join(getattr(b, "text", "") for b in resp.content).strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None, "The AI answered in a form the app could not read."
    try:
        out = _json.loads(match.group(0))
    except ValueError:
        return None, "The AI answer would not parse."
    try:
        score = max(0.0, min(10.0, float(out.get("score"))))
    except (TypeError, ValueError):
        score = None
    check = out.get("check") or []
    return {"score": score, "evidence": str(out.get("evidence") or "")[:3000],
            "risks": str(out.get("risks") or "")[:1500],
            "confidence": out.get("confidence") if out.get("confidence") in wealth_mcff.CONFIDENCE else "low",
            "check": [str(c)[:300] for c in check][:4] if isinstance(check, list) else []}, None
