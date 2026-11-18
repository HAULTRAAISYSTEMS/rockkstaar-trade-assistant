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

from flask import Blueprint, Response, abort, jsonify, render_template, request

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
        # Never auto-create empty brokerage portfolios. Existing portfolio
        # records are retained even if Schwab stops returning the account.
        if snapshot and snapshot.get("connected"):
            for account in snapshot.get("accounts") or []:
                key = str(account.get("account_hash") or account.get("account_number") or "")
                if not key or not account.get("equity_positions"):
                    continue
                try:
                    raw_type = str(account.get("account_type") or "").upper()
                    kind = ("roth_ira" if "ROTH" in raw_type else
                            "traditional_ira" if "IRA" in raw_type else "taxable")
                    wealth_store.link_broker_portfolio(user_id, key, kind)
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
        # Surface holdings to research without fabricating a trade or M.C.F.F. score.
        result["research_candidates"] = sorted({
            h["ticker"] for account in result.get("accounts", [])
            for h in account.get("holdings", [])
        })
        return jsonify({"ok": result["connected"], **result})

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

    @bp.get("/wealth/scoreboard")
    def scoreboard_page():
        user_id = uid()
        portfolios = wealth_store.list_portfolios(user_id)
        current = owned(user_id, request.args.get("p"))
        return render_template("wealth_scoreboard.html", portfolios=portfolios, current=current,
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
        return render_template("wealth_research_list.html", portfolios=portfolios, current=None,
                               researched=researched, held=held, kinds=wealth_store.KIND_LABELS,
                               quick_start=QUICK_START, labels=wealth_mcff.GATE_LABELS)

    @bp.get("/wealth/research/<ticker>")
    def research_page(ticker):
        user_id = uid()
        t = _tk(ticker)
        return render_template("wealth_research.html", portfolios=wealth_store.list_portfolios(user_id),
                               current=None, ticker=t, kinds=wealth_store.KIND_LABELS, quick_start=QUICK_START,
                               ai_available=bool(ai_draft or _ai_configured()))

    @bp.get("/api/wealth/mcff/<ticker>")
    def mcff_api(ticker):
        user_id = uid()
        return jsonify({"ok": True, **mcff_for(user_id, _tk(ticker))})

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
        return render_template("wealth_next_dollar.html", portfolios=portfolios, current=current,
                               candidates=sorted(set(researched) | set(held)), researched=researched,
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
        return render_template("wealth_planner.html", portfolios=portfolios, current=None, year=year,
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
