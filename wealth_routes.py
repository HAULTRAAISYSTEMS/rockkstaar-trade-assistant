"""Wealth Architect pages and APIs.

Every route reads the signed-in user's id and every storage call is scoped
to it; a portfolio id from the URL is only used after it is confirmed to
belong to that user.
"""
from __future__ import annotations

import hmac
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, abort, current_app, jsonify, redirect, render_template, request, url_for
from itsdangerous import BadData, URLSafeTimedSerializer

import wealth_calc
import wealth_broker_sync
import wealth_csv
import wealth_history
import wealth_ledger
import wealth_mcff
import wealth_nextdollar
import wealth_performance as performance
import wealth_planner
import wealth_service
import wealth_schwab_history
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
        bars, _ = fetch_chart_bars(ticker, "1d", "max")
        return bars

    def _performance_bars(ticker):
        from data_fetcher import _yahoo_sym
        from market_data import fetch_chart_bars
        bars, meta = fetch_chart_bars(ticker, "1d", "max")
        if (meta or {}).get("provider") != "Yahoo" or not bars or bars.get("data_granularity") != "1d":
            return None
        if bars.get("currency") != "USD" or bars.get("symbol") != _yahoo_sym(ticker):
            return None
        return bars

    def _default_broker(user_id):
        import schwab
        if not schwab.is_connected(user_id):
            return None
        return schwab.get_account_summary(user_id)

    def _default_fundamentals(ticker):
        from fundamentals_engine import get_fundamentals
        return get_fundamentals(ticker), wealth_store.fundamentals_fetched_at(ticker)

    performance_bars = bars_lookup or _performance_bars
    bars_lookup = bars_lookup or _default_bars
    broker_summary = broker_summary or _default_broker
    fundamentals_lookup = fundamentals_lookup or _default_fundamentals
    zone_lookup = zone_lookup or (lambda ticker, price: None)
    closes_cache: dict = {}
    closes_lock = threading.Lock()

    def history_for(tickers, *, verified=True):
        """Reuse market history across account/filter requests, including short negative cache."""
        now = time.monotonic()
        out, need = {}, []
        with closes_lock:
            for ticker in sorted(set(tickers)):
                hit = closes_cache.get((verified, ticker))
                if hit and now - hit[0] < (1800 if hit[1][0] else 60):
                    out[ticker] = hit[1]
                else:
                    need.append(ticker)

        def one(ticker):
            try:
                bars = (performance_bars if verified else bars_lookup)(ticker)
                closes = wealth_service._closes(bars)
                raw_splits = bars.get("splits") if isinstance(bars, dict) else None
                splits = None if raw_splits is None else []
                for event in raw_splits or []:
                    day = wealth_service._bar_date(event["date"])
                    ratio = float(event["numerator"]) / float(event["denominator"])
                    if day is None or not 0 < ratio < 10000:
                        return ticker, ([], None)
                    splits.append((ticker, day, ratio))
                return ticker, (closes, splits)
            except Exception as exc:
                logger.debug("history %s: %s", ticker, exc)
                return ticker, ([], None)
        if need:
            with ThreadPoolExecutor(max_workers=4) as pool:
                for ticker, data in pool.map(one, need):
                    out[ticker] = data
                    with closes_lock:
                        closes_cache[verified, ticker] = (now, data)
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

    def broker_cash_for(user_id):
        """Cash & cash invest across the Schwab accounts included in Wealth.

        From the Schwab account summary the Schwab page itself shows (read
        from its cache, never fetched here), so both pages count the same
        cash, including accounts that hold only cash. Falls back to the
        stored snapshots when that summary isn't cached.
        """
        import schwab_account_preferences as prefs
        allowed = prefs.get_preferences(user_id)
        live = _cached_schwab_summary(user_id)
        if live and live.get("connected") and live.get("accounts"):
            total = 0.0
            for a in prefs.select_accounts(live.get("accounts") or [], allowed, for_wealth=True):
                c = _num(a.get("cash_and_sweep"))
                if c is None:
                    tv = _num(a.get("total_value"))
                    if tv is not None:
                        c = max(0.0, tv - sum(_num(p.get("market_value")) or 0.0
                                              for p in (a.get("positions") or [])))
                if c is not None and c > 0:
                    total += c
            return round(total, 2)
        total = 0.0
        for row in wealth_store.list_broker_snapshots(user_id):
            if not allowed.get(row["account_key"], {}).get("include_wealth", True):
                continue
            c = _num((row.get("snapshot") or {}).get("cash"))
            if c is not None and c > 0:
                total += c
        return round(total, 2)

    def _cached_schwab_summary(user_id, max_age=1800):
        """The app's cached Schwab summary (up to 30 min old), or None."""
        import sys
        import time as _t
        legacy = sys.modules.get("app")
        cache = getattr(legacy, "_schwab_account_cache", None) if legacy else None
        entry = (cache or {}).get(user_id) or {}
        if entry.get("data") and _t.time() - (entry.get("ts") or 0) <= max_age:
            return entry["data"]
        # Not cached in this worker (each server process keeps its own, and a
        # restart empties it): load it through the app's own 60-second cache,
        # so the strip asks Schwab at most once a minute per process.
        fetch = getattr(legacy, "_get_schwab_data", None) if legacy else None
        if fetch is None:
            return None
        try:
            import schwab as _schwab
            if not (_schwab.token_status(user_id) or {}).get("connected"):
                return None
            data = fetch(user_id)
            return data if data and data.get("connected") else None
        except Exception as exc:
            logger.debug("schwab summary for cash: %s", exc)
            return None

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
        portfolios = wealth_store.list_portfolios(user_id)
        aliases = None
        if snapshot and snapshot.get("connected"):
            import sys
            legacy = sys.modules.get("app")
            cache = getattr(legacy, "_schwab_account_cache", None)
            accounts = snapshot.get("accounts") or []
            live_aliases = wealth_broker_sync.account_aliases(accounts)
            saved_snapshots = wealth_store.list_broker_snapshots(user_id)
            aliases = wealth_broker_sync.include_stored_aliases(live_aliases, saved_snapshots)
            unresolved = any(p.get("broker_account") and p["broker_account"] not in aliases for p in portfolios)
            if broker_summary is _default_broker and unresolved and any(
                    a.get("account_number") and a.get("account_hash") == a["account_number"] for a in accounts):
                # Older portfolios may have been stored with an opaque ID when
                # /accounts provided one. Ask Schwab for the exact mapping once
                # during sync so the account and its ledger can be reused.
                import schwab
                try:
                    hashes = schwab.fetch_account_hashes(user_id)
                    accounts = [dict(a, verified_account_hash=hashes.get(str(a.get("account_number"))))
                                for a in accounts]
                    live_aliases = wealth_broker_sync.account_aliases(accounts)
                    aliases = wealth_broker_sync.include_stored_aliases(live_aliases, saved_snapshots)
                except Exception:
                    logger.info("Schwab legacy account mapping unavailable")
            if any(key not in live_aliases for a in accounts for key in wealth_broker_sync.account_keys(a)):
                return jsonify(ok=False, error="Conflicting Schwab account identifiers."), 503
            if cache is not None and broker_summary is _default_broker:
                cache[user_id] = {"data": dict(snapshot, accounts=accounts), "ts": time.time()}
            import schwab_account_preferences as prefs
            preferences = prefs.get_preferences(user_id)
            accounts = [a for a in accounts
                        if wealth_broker_sync.is_in_wealth(wealth_broker_sync.preference_keys(a, aliases), preferences)]
            snapshot = dict(snapshot, accounts=accounts)
            mapped = {aliases.get(str(p.get("broker_account") or "")) for p in portfolios}
            for account in accounts:
                keys = wealth_broker_sync.account_keys(account)
                if not keys or keys[0] in mapped:
                    continue
                total = account.get("performance_liquidation_value", account.get("total_value"))
                if not (account.get("positions") or account.get("equity_positions")
                        or wealth_calc._f(total) is not None or wealth_broker_sync._cash(account) is not None):
                    continue
                try:
                    wealth_store.link_broker_portfolio(user_id, keys[0])
                    mapped.add(keys[0])
                except Exception:
                    logger.exception("Failed to provision brokerage portfolio")
            portfolios = wealth_store.list_portfolios(user_id)
        result = wealth_broker_sync.discover(
            snapshot, portfolios, wealth_store.list_transactions(user_id), aliases=aliases)
        portfolios_by_id = {p["id"]: p for p in portfolios}
        for account in result.get("accounts", []):
            key = account["account_id"]
            last_four = account["account_last_four"]
            account["account_id"] = f"•••• {last_four}" if last_four else None
            pid = account.get("portfolio_id")
            if pid is None:
                account["sync_error"] = "No Wealth portfolio could be linked for this account"
                continue
            try:
                # Preserve existing mappings and stored ledger history when the live identifier changes form.
                stored_key = portfolios_by_id[pid]["broker_account"]
                wealth_store.save_broker_snapshot(user_id, stored_key, {**account, "account_id": key})
                txs = wealth_store.list_transactions(user_id, pid)
                ledger = wealth_ledger.build(txs)
                account["discrepancies"] = wealth_broker_sync.reconcile(
                    account, {ticker: sum(lot.shares for lot in lots) for ticker, lots in ledger.positions.items()})
            except Exception:
                logger.exception("Brokerage snapshot/reconciliation failure")
                account["sync_error"] = "Unable to persist or reconcile this account"
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

    def performance_profiles(user_id, portfolios):
        goals, profiles = wealth_store.performance_settings(user_id)
        for p in portfolios:
            profiles.setdefault(p["id"], {})
        return goals, profiles

    def performance_broker(user_id, portfolios):
        import sys
        import schwab_account_preferences as prefs
        preferences = prefs.get_preferences(user_id)
        stored = {}
        aliases = {}
        saved_snapshots = wealth_store.list_broker_snapshots(user_id)
        for row in saved_snapshots:
            key = row["account_key"]
            snapshot = row["snapshot"]
            canonical = str(snapshot.get("account_id") or key)
            aliases[key] = canonical
            if wealth_broker_sync.is_in_wealth((canonical, key), preferences):
                if canonical not in stored or row["synced_at"] > stored[canonical]["as_of"]:
                    stored[canonical] = dict(snapshot, as_of=row["synced_at"], live=False)
        cached = _cached_schwab_summary(user_id, max_age=120)
        legacy = sys.modules.get("app")
        entry = getattr(legacy, "_schwab_account_cache", {}).get(user_id) or {}
        stamp = entry.get("ts")
        if not cached or not cached.get("connected") or not stamp or time.time() - stamp >= 120:
            return stored, aliases, preferences, False
        accounts = cached.get("accounts")
        if not isinstance(accounts, list) or any(not wealth_broker_sync.account_keys(a) for a in accounts):
            return stored, aliases, preferences, False
        live_aliases = wealth_broker_sync.account_aliases(accounts)
        if any(key not in live_aliases for a in accounts for key in wealth_broker_sync.account_keys(a)):
            return stored, aliases, preferences, False
        # The live roster is authoritative. A stored alias is useful only when
        # that exact canonical key also appears in the current response.
        aliases = wealth_broker_sync.include_stored_aliases(live_aliases, saved_snapshots)
        included = [a for a in accounts
                    if wealth_broker_sync.is_in_wealth(wealth_broker_sync.preference_keys(a, aliases), preferences)]
        discovered = wealth_broker_sync.discover(dict(cached, accounts=included), portfolios, [], aliases=aliases)
        as_of = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
        snapshots = {}
        for account in discovered["accounts"]:
            key = account["account_id"]
            previous = stored.get(key, {})
            last_four = account.get("account_last_four") or previous.get("account_last_four")
            snapshots[key] = dict(account, account_last_four=last_four, as_of=as_of, live=True)
        excluded = {wealth_broker_sync.account_keys(a)[0] for a in accounts if a not in included}
        preferences.update({key: {"include_wealth": False} for key in excluded})
        return snapshots, aliases, preferences, True

    def performance_payload(user_id, current, strategy, *, balance_only=False):
        today = datetime.now(ZoneInfo("America/New_York")).date()
        portfolios = wealth_store.list_portfolios(user_id)
        goals, profiles = performance_profiles(user_id, portfolios)
        broker, aliases, preferences, roster_confirmed = performance_broker(user_id, portfolios)
        by_account = {}
        for tx in wealth_store.list_transactions(user_id):
            by_account.setdefault(tx["portfolio_id"], []).append(tx)
        all_ledgers = {p["id"]: wealth_ledger.build([tx for tx in by_account.get(p["id"], [])
                       if str(tx["date"])[:10] <= today.isoformat()]) for p in portfolios}
        paired, transfer_issues = performance.transfers(all_ledgers)

        def broker_key(portfolio):
            key = str(portfolio.get("broker_account") or "")
            return aliases.get(key, key)

        issues, available, groups = [], [], {}
        selection_issue = None
        for p in portfolios:
            key = broker_key(p)
            if key and not wealth_broker_sync.is_in_wealth((key, p["broker_account"]), preferences):
                if current and current["id"] == p["id"]:
                    selection_issue = "This account is excluded from Wealth in Schwab Account settings."
                continue
            if key and roster_confirmed and key not in broker and p["id"] not in by_account:
                issues.append("Unused Wealth entries absent from the latest Schwab account list were omitted.")
                continue
            available.append(p)
            if key:
                groups.setdefault(key, []).append(p)

        selected = [p for p in available if p["id"] == current["id"]] if current else available
        if current and not selected:
            selection_issue = selection_issue or "This Wealth entry is absent from the latest Schwab account list."
            issues.append(selection_issue)
        buckets = {key: {"ledgers": {}, "broker": [], "manual": {}, "history_issues": [], "reconciliation": []}
                   for key in performance.STRATEGIES}
        covered = set()
        for p in selected:
            pid = p["id"]
            profile = profiles[pid]
            bucket = buckets[profile.get("strategy", "unclassified")]
            key = broker_key(p)
            label = wealth_broker_sync.portfolio_label(p, broker.get(key))
            if key and (key in covered or len(groups.get(key, [])) > 1):
                bucket["history_issues"].append("Multiple ledger accounts reference the same Schwab account. Review the mappings.")
                if key not in covered and key in broker:
                    bucket["broker"].append(broker[key])
                covered.add(key)
                continue
            ledger = all_ledgers[pid]
            if key:
                covered.add(key)
                snapshot = broker.get(key)
                if snapshot is None:
                    bucket["history_issues"].append(f"{label}: Schwab snapshot unavailable.")
                    bucket["broker"].append({})
                else:
                    bucket["broker"].append(snapshot)
                    quantities = {ticker: sum(lot.shares for lot in lots) for ticker, lots in ledger.positions.items()}
                    discrepancies = wealth_broker_sync.reconcile(snapshot, quantities)
                    cash = wealth_calc._f(snapshot.get("cash"))
                    for discrepancy in discrepancies:
                        bucket["history_issues"].append(
                            f"{label}: {discrepancy['ticker']} ledger shares differ from Schwab by "
                            f"{discrepancy['difference']:g}.")
                    cash_difference = round(cash - ledger.cash, 2) if cash is not None else None
                    cash_matches = cash is not None and abs(cash - ledger.cash) <= .02
                    if not cash_matches:
                        bucket["history_issues"].append(f"{label}: Schwab cash cannot be reconciled with the ledger.")
                    bucket["reconciliation"].append(dict(account=label, portfolio_id=pid,
                        has_transactions=bool(ledger.first_date), history_confirmed=bool(profile.get("history_confirmed")),
                        live=bool(snapshot.get("live")), unsupported_positions=snapshot.get("unsupported_positions"),
                        broker_cash=cash, cash_matches=cash_matches,
                        ledger_cash=round(ledger.cash, 2) if ledger.first_date else None,
                        cash_difference=cash_difference if ledger.first_date and not cash_matches else None,
                        positions=discrepancies))
                    if snapshot.get("unsupported_positions"):
                        bucket["history_issues"].append(f"{label}: the ledger cannot value one or more Schwab positions.")
                    if not profile.get("history_confirmed"):
                        bucket["history_issues"].append(f"{label}: confirm complete ledger coverage in Account settings.")
                    if not snapshot.get("live"):
                        bucket["history_issues"].append(f"{label}: refresh Schwab to verify current ledger balances.")
            elif ledger.first_date:
                bucket["manual"][pid] = ledger
            if ledger.first_date or ledger.issues:
                bucket["ledgers"][pid] = ledger
        if not current:
            for key, snapshot in broker.items():
                if key not in covered:
                    buckets["unclassified"]["broker"].append(snapshot)
                    label = wealth_broker_sync.portfolio_label({"name": "Schwab account"}, snapshot)
                    buckets["unclassified"]["history_issues"].append(
                        f"{label}: use Sync Schwab to add this account to the selector.")

        def combine(names):
            result = {"ledgers": {}, "broker": [], "manual": {}, "history_issues": [], "reconciliation": []}
            for name in names:
                bucket = buckets[name]
                result["ledgers"].update(bucket["ledgers"])
                result["broker"].extend(bucket["broker"])
                result["manual"].update(bucket["manual"])
                result["history_issues"].extend(bucket["history_issues"])
                result["reconciliation"].extend(bucket["reconciliation"])
            return result

        chosen = combine([strategy] if strategy != "all" else buckets)
        current_tickers = {ticker for ledger in chosen["manual"].values() for ticker in ledger.positions}
        quotes = (price_lookup(current_tickers) or {}) if current_tickers else {}
        quotes = {t: q for t, q in quotes.items() if (wealth_calc._f(q.get("price")) or 0) > 0}
        def balance(bucket):
            return wealth_service.performance_balance(bucket["manual"], bucket["broker"], quotes, today)

        sync = dict(live=sum(bool(b.get("live")) for b in chosen["broker"]),
                    stored=sum(bool(b.get("as_of")) and not b.get("live") for b in chosen["broker"]),
                    as_of=sorted({b["as_of"] for b in chosen["broker"] if b.get("as_of")}))
        if balance_only:
            return dict(ok=True, position=balance(chosen), sync=sync, reconciliation=chosen["reconciliation"],
                        strategies={name: {key: values[key] for key in ("value", "cash", "unrealized")}
                                    for name, values in ((name, balance(bucket)) for name, bucket in buckets.items())
                                    if strategy in ("all", name)})

        tickers = {ticker for ledger in chosen["ledgers"].values() for _, _, held in ledger.snapshots for ticker in held}
        tickers.update(event["ticker"] for ledger in chosen["ledgers"].values() for event in ledger.events
                       if event["transaction"]["type"] in ("TRANSFER_IN", "TRANSFER_OUT"))
        historical = history_for(tickers | {performance.BENCHMARK, "SPY"}) if chosen["ledgers"] else {}
        closes = {t: historical[t][0] for t in tickers}
        splits = {t: historical[t][1] for t in tickers}
        provider_splits = {t: rows for t, rows in splits.items() if rows is not None}
        spy = historical.get("SPY", ([], None))[0]
        benchmark = historical.get(performance.BENCHMARK, ([], None))[0]
        from exchange_schedule import session_status
        session = session_status()
        now = datetime.now(ZoneInfo("America/New_York"))
        closing_hour = 13 if session["early_close"] else 16
        same_day_closed = session["known"] and now.hour >= closing_hour
        calendar = sorted({day for day, _ in [*spy, *benchmark]
                           if day < today or (day == today and same_day_closed)})
        sessions = set(calendar)
        benchmark = [(day, value) for day, value in benchmark if day in sessions]

        def calculate(bucket):
            ledgers = bucket["ledgers"]
            current_balance = balance(bucket)
            warnings = [*bucket["history_issues"],
                        *(issue for ledger in ledgers.values() for issue in performance.ledger_issues(ledger))]
            warnings.extend(issue for pid in ledgers for issue in transfer_issues.get(pid, ()))
            flow_stocks = provider_splits if len(provider_splits) == len(tickers) else None
            flows, security_flows, flow_issues = performance.external_flows(ledgers, paired, closes, flow_stocks)
            warnings.extend(flow_issues)
            for ledger in ledgers.values():
                warnings.extend(performance.split_issues(ledger, provider_splits, today))
            activity = performance.ledger_activity(ledgers, paired, today)
            trusted = bool(ledgers) and not warnings
            position = {**current_balance, "as_of": today.isoformat()}
            if trusted:
                rollup = {field: sum(year[field] for year in activity.values()) for field in (
                    "realized", "dividends", "interest", "fees", "deposits", "withdrawals")}
            else:
                rollup = dict.fromkeys(("realized", "dividends", "interest", "fees", "deposits", "withdrawals"))
            position.update(rollup)
            recorded = activity.get(today.year)
            position["recorded_activity_ytd"] = ({key: recorded[key] for key in
                ("deposits", "withdrawals", "dividends", "interest", "fees")} if recorded else None)
            position["capital_flows_to_date"] = sum(value for _, value in flows) if trusted else None
            position["investment_profit"] = (position["value"] - position["capital_flows_to_date"]
                if trusted and position["value"] is not None else None)
            for field in ("dividends", "fees"):
                position[f"{field}_ytd"] = activity.get(today.year, {}).get(field, 0.) if trusted else None
            if not trusted or not calendar:
                history = {"ok": False, "reason": (warnings[0] if warnings else
                           selection_issue or ("Daily market history is unavailable." if ledgers else
                                               "Add or import transactions in the Ledger."))}
            else:
                series = wealth_history.build_series(list(ledgers.values()), closes, calendar, today=today,
                                                      flows=flows, provider_splits=flow_stocks)
                if series["ok"]:
                    history = {"ok": True, **performance.analyze(series, benchmark, ledgers, security_flows, goals)}
                    history["dividends_by_year"] = {point: row["dividends"] for point, row in activity.items()}
                    history["reinvested"] = sum(e["transaction"]["type"] == "REINVEST" for ledger in ledgers.values()
                                                for e in ledger.events)
                else:
                    history = series
            return position, history, sorted(set(warnings)), activity if trusted else {}

        position, history, warnings, activity = calculate(chosen)
        strategies = {}
        for name, bucket in buckets.items():
            if strategy != "all" and strategy != name:
                continue
            p, h, _, _ = calculate(bucket) if strategy == "all" else (position, history, warnings, activity)
            strategies[name] = {key: p[key] for key in ("value", "cash", "unrealized", "realized", "dividends_ytd")}
            strategies[name]["return_pct"] = (h.get("periods") or [{}] * 5)[4].get("portfolio")
        year = today.year
        year_rows = []
        for point in sorted({year, year + 1, *activity, *goals, *(int(y) for y in history.get("years", {}))}, reverse=True):
            goal = goals.get(point, performance.DEFAULT_GOAL)
            row = activity.get(point)
            net = row["deposits"] - row["withdrawals"] if row is not None else (
                0. if activity and min(activity) < point <= year else None)
            plan = goal.get("contribution_plan")
            projection = performance.contribution_projection(
                point, plan, goal["contribution"], net, today, all_accounts=not current and strategy == "all")
            year_rows.append(dict(year=point, **goal, projection=projection,
                                  net=net, planned=point > year, saved=point in goals,
                                  remaining=max(0., goal["contribution"] - net) if net is not None else None,
                                  actual_return=history.get("years", {}).get(point)))
        return dict(ok=True, position=position, history=history, warnings=sorted(set(issues + warnings)),
                    strategies=strategies, goals=year_rows, reconciliation=chosen["reconciliation"],
                    selected_goal=next(row for row in year_rows if row["year"] == year),
                    accounts=[dict(id=p["id"], name=p["name"],
                                   label=wealth_broker_sync.portfolio_label(p, broker.get(broker_key(p))),
                                   linked=bool(p.get("broker_account")),
                                   strategy=profiles[p["id"]].get("strategy", "unclassified"),
                                   history_confirmed=bool(profiles[p["id"]].get("history_confirmed")))
                              for p in available],
                    benchmark=dict(label=performance.BENCHMARK_LABEL, symbol=performance.BENCHMARK,
                                   source="Yahoo Finance", as_of=benchmark[-1][0].isoformat() if benchmark else None),
                    sync=sync, days_left=(date(year, 12, 31) - today).days)

    @bp.get("/api/wealth/scoreboard")
    def scoreboard_api():
        user_id = uid()
        current = owned(user_id, request.args.get("p"))
        return jsonify(performance_payload(user_id, current, "all")["history"])

    @bp.get("/wealth/performance")
    def performance_page():
        user_id = uid()
        current = owned(user_id, request.args.get("p"))
        return render_template("wealth_performance.html", current=current,
                               portfolios=wealth_store.list_portfolios(user_id), strategies=performance.STRATEGIES)

    @bp.get("/api/wealth/performance")
    def performance_api():
        user_id = uid()
        current = owned(user_id, request.args.get("p"))
        strategy = request.args.get("strategy", "all")
        if strategy != "all" and strategy not in performance.STRATEGIES:
            abort(400)
        return jsonify(performance_payload(user_id, current, strategy,
                                           balance_only=request.args.get("view") == "balance"))

    @bp.post("/api/wealth/performance/sync")
    def performance_sync():
        result = brokerage_discovery()
        if isinstance(result, tuple):
            return jsonify(ok=False), result[1]
        data = result.get_json()
        accounts = data.get("accounts", [])
        failed = sum(bool(account.get("sync_error")) for account in accounts)
        return jsonify(ok=bool(data.get("ok")) and not failed, synced=len(accounts) - failed)

    def history_portfolio(user_id, pid):
        portfolio = owned(user_id, pid)
        if not portfolio:
            abort(404)
        return portfolio

    def history_signer():
        return URLSafeTimedSerializer(current_app.secret_key, salt="wealth-schwab-history-v1")

    def history_mapping_tag(key):
        secret = current_app.secret_key
        if isinstance(secret, str):
            secret = secret.encode()
        return hmac.digest(secret, ("wealth-schwab-history:" + key).encode(), "sha256").hex()

    @bp.get("/api/wealth/performance/history/<int:pid>")
    def performance_recent_transactions(pid):
        user_id = uid()
        history_portfolio(user_id, pid)
        return jsonify(ok=True, transactions=wealth_store.recent_transactions(user_id, pid))

    @bp.post("/api/wealth/performance/history/<int:pid>/preview")
    def performance_history_preview(pid):
        import schwab
        import schwab_account_preferences as prefs

        user_id = uid()
        portfolio = history_portfolio(user_id, pid)
        broker_key = str(portfolio.get("broker_account") or "")
        if not broker_key:
            return jsonify(ok=False, error="Select a Schwab-linked account or upload a CSV."), 400
        values = request.get_json(silent=True)
        if not isinstance(values, dict):
            abort(400)
        today = datetime.now(ZoneInfo("America/New_York")).date()
        try:
            start = date.fromisoformat(values.get("start_date")) if values.get("start_date") else today - timedelta(days=364)
            end = date.fromisoformat(values.get("end_date")) if values.get("end_date") else today
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Enter valid start and end dates."), 400
        if end > today or not 0 <= (end - start).days <= 364:
            return jsonify(ok=False, error="Choose a date window up to 365 days, ending today or earlier."), 400
        try:
            accounts = schwab.fetch_account_hashes(user_id)
            matches = [(number, key) for number, key in accounts.items() if broker_key in (number, key)]
            if len(matches) != 1:
                return jsonify(ok=False, error="This account could not be matched. Sync Schwab or upload its CSV."), 409
            number, account_hash = matches[0]
            if not wealth_broker_sync.is_in_wealth((account_hash, number), prefs.get_preferences(user_id)):
                return jsonify(ok=False, error="This account is excluded from Wealth."), 409
            raw = schwab.fetch_transaction_history(account_hash, start, end, user_id=user_id)
        except Exception:
            logger.info("Read-only Schwab history request unavailable")
            return jsonify(ok=False, error="Schwab history is unavailable. Try again or upload the account's CSV."), 503
        if len(raw) > 1000:
            return jsonify(ok=False, error="This range has too many transactions to review at once. Choose a shorter range."), 400
        result = wealth_schwab_history.normalize(raw, start, end)
        token = history_signer().dumps({"user": user_id, "portfolio": pid,
                                      "mapping": history_mapping_tag(broker_key), "rows": result["transactions"]})
        return jsonify(ok=True, **result, token=token, start=start.isoformat(), end=end.isoformat())

    @bp.post("/api/wealth/performance/history/<int:pid>/commit")
    def performance_history_commit(pid):
        user_id = uid()
        portfolio = history_portfolio(user_id, pid)
        broker_key = str(portfolio.get("broker_account") or "")
        values = request.get_json(silent=True)
        if not isinstance(values, dict) or not isinstance(values.get("token"), str) or len(values["token"]) > 500_000:
            abort(400)
        try:
            preview = history_signer().loads(values["token"], max_age=900)
        except BadData:
            return jsonify(ok=False, error="Preview expired or invalid. Preview Schwab history again."), 400
        if preview["user"] != user_id or preview["portfolio"] != pid:
            abort(404)
        if not broker_key or not hmac.compare_digest(preview["mapping"], history_mapping_tag(broker_key)):
            return jsonify(ok=False, error="The portfolio's Schwab link changed. Preview history again."), 409
        indexes = values.get("indexes")
        rows = preview["rows"]
        if (not isinstance(indexes, list) or not 0 < len(indexes) <= len(rows)
                or any(type(i) is not int or not 0 <= i < len(rows) for i in indexes)):
            return jsonify(ok=False, error="Select transactions from the preview."), 400
        result = wealth_store.add_transactions(user_id, pid, [rows[i] for i in sorted(set(indexes))],
                                               source="schwab_api", broker_key=broker_key)
        if result.get("mapping_changed"):
            return jsonify(ok=False, error="The portfolio's Schwab link changed. Preview history again."), 409
        return jsonify(ok=True, **result)

    @bp.post("/api/wealth/performance/goals/<int:year>/preview")
    @bp.put("/api/wealth/performance/goals/<int:year>")
    def performance_goals(year):
        user_id = uid()
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400)
        rate, amount = wealth_calc._f(data.get("return_pct")), wealth_calc._f(data.get("contribution"))
        today = datetime.now(ZoneInfo("America/New_York")).date()
        if not 1900 <= year <= today.year + 1:
            return jsonify(error="Choose the current year, next year, or a historical goal year."), 400
        if amount is None or not 0 < amount <= 1e10:
            return jsonify(error="Enter a positive annual contribution goal."), 400
        try:
            plan = performance.clean_contribution_plan(year, data.get("contribution_plan"))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        if request.method == "POST":
            return jsonify(projection=performance.contribution_projection(year, plan, amount, None, today))
        if rate is None or not -100 < rate <= 10000:
            return jsonify(error="Enter a return target above −100% and no more than 10,000%."), 400
        if "contribution_plan" not in data:
            goals, _ = wealth_store.performance_settings(user_id)
            plan = goals.get(year, {}).get("contribution_plan")
        setting = {"return_pct": rate, "contribution": amount}
        if plan is not None:
            setting["contribution_plan"] = plan
        wealth_store.save_performance_setting(user_id, "goal", year, setting)
        return jsonify(ok=True)

    @bp.put("/api/wealth/performance/accounts/<int:pid>")
    def performance_account(pid):
        user_id = uid()
        owned(user_id, pid)
        data = request.get_json(silent=True)
        if (not isinstance(data, dict) or data.get("strategy") not in performance.STRATEGIES
                or not isinstance(data.get("history_confirmed"), bool)):
            abort(400)
        wealth_store.save_performance_setting(user_id, "account", pid, {
            "strategy": data["strategy"], "history_confirmed": data["history_confirmed"]})
        return jsonify(ok=True)

    @bp.after_request
    def prevent_performance_cache(response):
        if request.path.startswith(("/api/wealth/performance", "/api/wealth/scoreboard")):
            response.headers["Cache-Control"] = "private, no-store"
        return response

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
        if not isinstance(text, str) or len(text) > 2_000_000:
            return jsonify(ok=False, error="Upload CSV text up to 2 MB."), 400
        parsed = wealth_csv.parse(text)
        if len(parsed["transactions"]) > 5000:
            return jsonify(ok=False, error="Import up to 5,000 rows at a time."), 400
        if not parsed["format"]:
            return jsonify({"ok": False, "error": parsed["skipped"][0]["reason"]}), 400
        return jsonify({"ok": True, **parsed})

    @bp.post("/api/wealth/import/commit")
    def import_commit():
        user_id = uid()
        d = request.get_json(silent=True)
        if not isinstance(d, dict) or not isinstance(d.get("transactions"), list):
            return jsonify(ok=False, error="Choose transactions to import."), 400
        p = owned(user_id, d.get("portfolio_id"))
        if not p:
            return jsonify({"ok": False, "error": "Choose a portfolio."}), 400
        if len(d["transactions"]) > 5000:
            return jsonify({"ok": False, "error": "Import up to 5,000 rows at a time."}), 400
        rows, bad = [], []
        for i, raw in enumerate(d["transactions"]):
            if not isinstance(raw, dict):
                bad.append({"index": i, "error": "Invalid transaction."})
                continue
            tx, errs = _clean_tx(raw)
            if errs:
                bad.append({"index": i, "error": " ".join(errs)})
                continue
            fingerprint = raw.get("fingerprint")
            tx["fingerprint"] = fingerprint if isinstance(fingerprint, str) and re.fullmatch(
                r"[a-f0-9]{24}", fingerprint) else wealth_csv.fingerprint(tx)
            tx["source"] = "schwab_csv" if raw.get("source") == "schwab_csv" else "csv"
            rows.append(tx)
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
                     "market_value": h.get("market_value"),
                     "cost_basis": h.get("cost_basis") if h.get("cost_basis") else None}
                    for h in s.get("holdings") or []]
        cash = float(s.get("cash") or 0.0)
        value = s.get("total_value")
        if not s.get("transaction_count"):
            snap = broker_holdings_for(user_id)
            if not snap:
                return jsonify({"ok": True, "empty": True})
            source = "schwab"
            holdings = [{"ticker": h["ticker"], "shares": h["shares"], "price": None,
                         "market_value": h.get("market_value"),
                         "cost_basis": h.get("basis") if (h.get("basis_known") and h.get("basis")) else None}
                        for h in snap]
            # Cash & cash invest from the same snapshots, so the total matches
            # the broker's (it was left out, about $77 short of Schwab's).
            cash = broker_cash_for(user_id)
            value = sum(h["market_value"] or 0 for h in holdings) + cash
        tickers = [h["ticker"] for h in holdings if h["shares"]]
        try:
            import market_engine as _me
            session = _me.pulse_session()
        except Exception:
            session = "closed"
        closes = {t: data[0] for t, data in history_for(tickers, verified=False).items()}
        live = price_lookup(tickers) if (tickers and session == "regular") else {}
        today = date.today()
        moves, day_label = [], None
        for h in holdings:
            rows = closes.get(h["ticker"]) or []
            if len(rows) < 2 or not h["shares"]:
                continue
            # The close before today: the base for today's live move.
            prior = next((c for d, c in reversed(rows) if d < today), None)
            if session == "regular" and (live.get(h["ticker"]) or {}).get("price"):
                prev = prior
                now_px = float(live[h["ticker"]]["price"])
            else:
                prev, now_px = rows[-2][1], rows[-1][1]
                day_label = rows[-1][0].strftime("%a")
            if not prev:
                continue
            pct = (now_px / prev - 1) * 100
            moves.append({"ticker": h["ticker"], "pct": round(pct, 2),
                          "dollars": round((now_px - prev) * h["shares"], 2),
                          # For the strip to keep ticking between polls:
                          # the price this move used, the close it is
                          # measured from, and the latest official close
                          # (the base for a pre / after-hours / 24H move).
                          "now": round(now_px, 4), "prev": round(float(prev), 4),
                          "last_close": round(float(rows[-1][1]), 4),
                          "prior_close": round(float(prior), 4) if prior else None,
                          "shares": float(h["shares"])})
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
            row = {"ticker": h["ticker"], "value": round(float(mv), 2),
                   "pct": m.get("pct"), "dollars": m.get("dollars"),
                   "price": h.get("price") if h.get("price") is not None else round(float(mv) / float(h["shares"]), 4),
                   "shares": round(float(h["shares"]), 6)}
            cb = h.get("cost_basis")
            if cb:
                row["cost_basis"] = round(float(cb), 2)
                row["avg_cost"] = round(float(cb) / float(h["shares"]), 4)
                row["unrealized"] = round(float(mv) - float(cb), 2)
                row["unrealized_pct"] = round((float(mv) / float(cb) - 1) * 100, 2)
            sized.append(row)
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
        # Pre-market / after-hours / overnight move per holding, from the same
        # live overlay as the tape (ext_* only when such a print exists), and
        # what it is worth in dollars on your shares.
        ext_dollars, ext_base, ext_n = 0.0, 0.0, 0
        try:
            import market_engine as _me2
            over = {i["symbol"]: i for i in _me2.overlay_live_tape(
                [{"symbol": x["ticker"], "price": x.get("price"), "change_pct": x.get("pct")} for x in sized])}
        except Exception:
            over = {}
        for x in sized:
            o = over.get(x["ticker"]) or {}
            if o.get("ext") and o.get("ext_pct") is not None and o.get("ext_price"):
                xp, xpct = float(o["ext_price"]), float(o["ext_pct"])
                close = xp / (1 + xpct / 100)
                d = (xp - close) * x["shares"]
                x.update(ext=o["ext"], ext_pct=round(xpct, 2), ext_price=round(xp, 2), ext_dollars=round(d, 2))
                ext_dollars += d; ext_base += close * x["shares"]; ext_n += 1
        # Only publish a total when every open position has a usable basis and value.
        import math
        complete = bool(tickers) and len(sized) == len(tickers) and all(
            x.get("cost_basis") is not None and math.isfinite(x["cost_basis"])
            and x["cost_basis"] > 0 and math.isfinite(x["value"]) for x in sized)
        basis = sum(x["cost_basis"] for x in sized) if complete else None
        unrealized = {"cost_basis": round(basis, 2), "market_value": round(invested, 2),
                      "dollars": round(invested - basis, 2),
                      "pct": round((invested / basis - 1) * 100, 2)} if basis else None
        return jsonify({"ok": True, "empty": False, "source": source, "value": value, "cash": cash,
                        "unrealized_total": unrealized,
                        "holdings": sized[:40],
                        "ext_dollars": round(ext_dollars, 2) if ext_n else None,
                        "ext_pct": round(ext_dollars / ext_base * 100, 2) if ext_n and ext_base > 0 else None,
                        "ext_count": ext_n,
                        "invested": round(invested, 2),
                        "day_dollars": day_dollars,
                        "day_pct": round(day_dollars / base * 100, 2) if base > 0 else None,
                        "best": ranked[-1] if ranked else None, "worst": ranked[0] if ranked else None,
                        "moves": moves[:60],
                        "positions": len(tickers), "priced": len(moves), "tickers": tickers[:40],
                        "session": session, "day_label": None if session == "regular" else day_label,
                        "unpriced": s.get("unpriced") or []})

    @bp.get("/api/wealth/today/live")
    def today_live_api():
        """Latest prices for your holdings, every few seconds — never blocks.

        The Command Center strip loads /api/wealth/today once a minute and
        ticks between those loads from this: the shared live-quote feed (the
        same 3-second prices as the tape), so value, today's move and the
        best and worst holding move with the market.
        """
        syms = []
        for t in (request.args.get("symbols") or "").split(","):
            t = t.strip().upper()
            if t and len(t) <= 12 and t.replace(".", "").replace("-", "").isalnum() and t not in syms:
                syms.append(t)
        syms = syms[:60]
        quotes, session = {}, "closed"
        try:
            import market_engine as _me
            session = _me.pulse_session()
            for sym, q in (_me.live_quotes(syms) or {}).items():
                if q and q.get("price"):
                    quotes[sym] = {"price": round(float(q["price"]), 4), "ext": q.get("ext"),
                                   "epoch": q.get("epoch")}
        except Exception as exc:
            logger.debug("wealth live quotes: %s", exc)
        resp = jsonify({"ok": True, "session": session, "quotes": quotes})
        resp.headers["Cache-Control"] = "no-store"
        return resp

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
