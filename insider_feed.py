"""Market-wide insider trades from SEC Form 4 — every ticker, stored, scored.

Sources (official, free):
  * SEC "current events" Atom feed of Form 4 filings (minutes old)
  * EDGAR daily form indexes (for a 30-day backfill, a slice per run)

Each new filing is fetched once (its complete submission .txt), the ownership
XML parsed with smart_money._transaction_rows, and only open-market trades
kept: code P (purchase) and code S (sale), non-derivative. One stored row per
filing per side, with shares, average price, value and holdings change.

The cron calls sync() every 5 minutes. The page reads the table — no SEC call
on page load.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

ATOM_URL = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&company=&dateb="
            "&owner=include&start={start}&count=100&output=atom")
DAILY_INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{ymd}.idx"
BACKFILL_DAYS = 30
EXEC_RE = re.compile(r"\b(ceo|chief executive|cfo|chief financial|president|chair|coo|chief operating)\b", re.I)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Tables ───────────────────────────────────────────────────────────────────

def ensure_tables(conn) -> None:
    from database import ensure_schema
    ensure_schema(conn, 'insider_feed', ['insider_trades', 'idx_insider_trades_ticker', 'idx_insider_trades_side', 'insider_feed_seen'], _create_tables)


def _create_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS insider_trades (
            id TEXT PRIMARY KEY,
            accession TEXT NOT NULL,
            side TEXT NOT NULL,
            ticker TEXT NOT NULL,
            issuer TEXT,
            owner TEXT,
            owner_cik TEXT,
            role TEXT,
            trade_date TEXT,
            filed_at TEXT,
            shares DOUBLE PRECISION,
            avg_price DOUBLE PRECISION,
            value DOUBLE PRECISION,
            holdings_after DOUBLE PRECISION,
            holdings_change_pct DOUBLE PRECISION,
            planned_10b5_1 INTEGER NOT NULL DEFAULT 0,
            source_url TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_insider_trades_ticker ON insider_trades(ticker, trade_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_insider_trades_side ON insider_trades(side, trade_date)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS insider_feed_seen (
            accession TEXT PRIMARY KEY,
            seen_at TEXT NOT NULL
        )
    """)


# ── Discovering filings ──────────────────────────────────────────────────────

_ACC_RE = re.compile(r"accession-number=(\d{10}-\d{2}-\d{6})")
_CIK_IN_LINK = re.compile(r"/edgar/data/(\d+)/")


def parse_atom(text: str) -> list[dict]:
    """[(accession, cik)] from the SEC current-events Atom feed (deduplicated)."""
    out, seen = [], set()
    for entry in re.findall(r"<entry>(.*?)</entry>", text or "", re.S):
        acc = _ACC_RE.search(entry)
        link = re.search(r'<link[^>]*href="([^"]+)"', entry)
        if not acc or not link:
            continue
        cik = _CIK_IN_LINK.search(link.group(1))
        a = acc.group(1)
        if a in seen or not cik:
            continue
        seen.add(a)
        updated = re.search(r"<updated>([^<]+)</updated>", entry)
        out.append({"accession": a, "cik": cik.group(1), "filed_at": (updated.group(1)[:10] if updated else "")})
    return out


def parse_daily_index(text: str) -> list[dict]:
    """Form 4 / 4/A rows from an EDGAR daily form.idx (deduplicated by accession)."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        if not (line.startswith("4 ") or line.startswith("4/A ")):
            continue
        m = re.search(r"edgar/data/(\d+)/(\d{10}-\d{2}-\d{6})\.txt\s*$", line)
        d = re.search(r"\s(\d{8})\s+edgar/", line)
        if not m or m.group(2) in seen:
            continue
        seen.add(m.group(2))
        filed = f"{d.group(1)[:4]}-{d.group(1)[4:6]}-{d.group(1)[6:]}" if d else ""
        out.append({"accession": m.group(2), "cik": m.group(1), "filed_at": filed})
    return out


def submission_url(cik: str, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{accession}.txt"


def index_url(cik: str, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{accession}-index.html"


def extract_ownership_xml(text: str) -> bytes | None:
    m = re.search(r"<XML>\s*(.*?)\s*</XML>", text or "", re.S | re.I)
    if not m or "ownershipDocument" not in m.group(1):
        return None
    return m.group(1).encode("utf-8")


# ── Turning one filing into stored rows ─────────────────────────────────────

def trades_from_filing(xml: bytes, accession: str, filed_at: str, url: str) -> list[dict]:
    """One row per side (P / S) for the filing's open-market, non-derivative trades."""
    from xml.etree import ElementTree
    import smart_money
    root = ElementTree.fromstring(xml)
    ticker = (smart_money._text(root, "issuerTradingSymbol") or "").upper().strip()
    issuer = smart_money._text(root, "issuerName")
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,7}", ticker):
        return []
    rows = smart_money._transaction_rows(xml, ticker, url, filed_at, accession=accession)
    by_side: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("derivative") or r.get("code") not in ("P", "S"):
            continue
        by_side[r["code"]].append(r)
    out = []
    for side, group in by_side.items():
        shares = sum(r["shares"] or 0 for r in group)
        priced = [r for r in group if r.get("price") is not None and r.get("shares")]
        value = sum(r["shares"] * r["price"] for r in priced) if priced else None
        avg = (value / sum(r["shares"] for r in priced)) if priced else None
        after = next((r.get("ownership_after") for r in reversed(group) if r.get("ownership_after") is not None), None)
        change = None
        if after is not None and shares:
            before = after - shares if side == "P" else after + shares
            if before > 0:
                change = (shares if side == "P" else -shares) / before * 100
            elif side == "P":
                change = None  # new position: no prior holdings to compare
        first = group[0]
        out.append({
            "id": f"{accession}:{side}", "accession": accession, "side": side, "ticker": ticker, "issuer": issuer,
            "owner": first.get("owner"), "owner_cik": first.get("owner_cik"), "role": first.get("role"),
            "trade_date": min(str(r.get("trade_date") or filed_at)[:10] for r in group), "filed_at": str(filed_at)[:10],
            "shares": shares, "avg_price": round(avg, 4) if avg else None, "value": round(value, 2) if value else None,
            "holdings_after": after, "holdings_change_pct": round(change, 2) if change is not None else None,
            "planned_10b5_1": 1 if any(r.get("transaction_10b5_1") for r in group) else 0,
            "source_url": url,
        })
    return out


def _store(conn, rows: list[dict]) -> int:
    # ON CONFLICT DO NOTHING, not try/except: on PostgreSQL a failed INSERT
    # aborts the whole transaction and every later statement would fail too.
    n = 0
    now = _now().isoformat()
    for r in rows:
        try:
            conn.execute(
                "INSERT INTO insider_trades (id,accession,side,ticker,issuer,owner,owner_cik,role,trade_date,filed_at,"
                "shares,avg_price,value,holdings_after,holdings_change_pct,planned_10b5_1,source_url,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (id) DO NOTHING",
                (r["id"], r["accession"], r["side"], r["ticker"], r["issuer"], r["owner"], r["owner_cik"], r["role"],
                 r["trade_date"], r["filed_at"], r["shares"], r["avg_price"], r["value"], r["holdings_after"],
                 r["holdings_change_pct"], r["planned_10b5_1"], r["source_url"], now))
            n += 1
        except Exception as exc:
            logger.debug("insider store %s: %s", r.get("id"), exc)
    return n


def _seen(conn, accessions: list[str]) -> set[str]:
    out: set[str] = set()
    accessions = list(dict.fromkeys(accessions))
    for i in range(0, len(accessions), 400):   # SQLite caps bound variables
        chunk = accessions[i:i + 400]
        marks = ",".join("?" for _ in chunk)
        rows = conn.execute(f"SELECT accession FROM insider_feed_seen WHERE accession IN ({marks})", tuple(chunk)).fetchall()
        out.update(r["accession"] for r in rows)
    return out


def _mark(conn, accession: str) -> None:
    try:
        conn.execute("INSERT INTO insider_feed_seen (accession, seen_at) VALUES (?, ?) ON CONFLICT (accession) DO NOTHING",
                     (accession, _now().isoformat()))
    except Exception:
        pass


def process(conn, filings: list[dict], *, budget: int, get, deadline: float) -> dict:
    """Fetch and store unseen filings until the budget or deadline runs out."""
    stats = {"checked": 0, "stored": 0, "errors": 0}
    unseen_set = set(f["accession"] for f in filings) - _seen(conn, [f["accession"] for f in filings])
    for f in filings:
        if stats["checked"] >= budget or time.monotonic() > deadline:
            break
        if f["accession"] not in unseen_set:
            continue
        stats["checked"] += 1
        try:
            resp = get(submission_url(f["cik"], f["accession"]))
            if resp.status_code == 404:
                _mark(conn, f["accession"])
                continue
            resp.raise_for_status()
            xml = extract_ownership_xml(resp.text)
            if xml:
                stats["stored"] += _store(conn, trades_from_filing(xml, f["accession"], f["filed_at"],
                                                                   index_url(f["cik"], f["accession"])))
            _mark(conn, f["accession"])
        except Exception as exc:
            stats["errors"] += 1
            logger.debug("insider feed %s: %s", f["accession"], exc)
        # Commit each filing: the next one is a network fetch, and an open
        # transaction must not sit on its row locks across it.
        conn.commit()
    conn.commit()
    return stats


def sync(conn=None, *, budget: int = 350, seconds: float = 75.0, get=None, today: date | None = None) -> dict:
    """One cron pass: newest filings first (live feed), then backfill older days."""
    from database import get_db, get_setting, set_setting
    import smart_money
    owns = conn is None
    conn = conn or get_db()
    get = get or (lambda url: smart_money._sec_get(url, timeout=15))
    deadline = time.monotonic() + seconds
    today = today or _now().date()
    out = {"live": None, "backfill": None, "errors": []}
    try:
        ensure_tables(conn)
        # 1) Live feed: up to 3 pages of the current-events feed.
        live = []
        for start in (0, 100, 200):
            try:
                resp = get(ATOM_URL.format(start=start))
                resp.raise_for_status()
                page = parse_atom(resp.text)
            except Exception as exc:
                out["errors"].append(f"atom:{type(exc).__name__}")
                break
            live.extend(page)
            if not page or len(_seen(conn, [p["accession"] for p in page])) == len(page):
                break
        out["live"] = process(conn, live, budget=budget, get=get, deadline=deadline)
        # 2) Backfill: walk back day by day with what is left of the budget.
        left = budget - out["live"]["checked"]
        state = {}
        try:
            state = json.loads(get_setting("insider_feed_backfill") or "{}")
        except Exception:
            state = {}
        done = set(state.get("done") or [])
        bstats = {"checked": 0, "stored": 0, "errors": 0, "days_done": len(done)}
        for back in range(1, BACKFILL_DAYS + 1):
            if left <= 0 or time.monotonic() > deadline:
                break
            d = today - timedelta(days=back)
            key = d.isoformat()
            if d.weekday() >= 5 or key in done:
                continue
            try:
                resp = get(DAILY_INDEX_URL.format(y=d.year, q=(d.month - 1) // 3 + 1, ymd=d.strftime("%Y%m%d")))
                if resp.status_code == 404:
                    done.add(key)
                    continue
                resp.raise_for_status()
                filings = parse_daily_index(resp.text)
            except Exception as exc:
                out["errors"].append(f"index:{key}:{type(exc).__name__}")
                break
            s = process(conn, filings, budget=left, get=get, deadline=deadline)
            for k in ("checked", "stored", "errors"):
                bstats[k] += s[k]
            left -= s["checked"]
            if len(_seen(conn, [f["accession"] for f in filings])) >= len(filings):
                done.add(key)
        cutoff = (today - timedelta(days=BACKFILL_DAYS + 2)).isoformat()
        done = {d for d in done if d >= cutoff}
        bstats["days_done"] = len(done)
        set_setting("insider_feed_backfill", json.dumps({"done": sorted(done)}))
        out["backfill"] = bstats
        # Keep the seen-table small.
        conn.execute("DELETE FROM insider_feed_seen WHERE seen_at < ?", ((_now() - timedelta(days=BACKFILL_DAYS + 5)).isoformat(),))
        conn.commit()
        return out
    finally:
        if owns:
            conn.close()


# ── Reading, scoring ─────────────────────────────────────────────────────────

def role_points(role: str) -> tuple[int, str]:
    r = role or ""
    if EXEC_RE.search(r):
        return 25, "C-suite"
    if "Officer" in r:
        return 15, "Officer"
    if "Director" in r:
        return 10, "Director"
    if "10%" in r:
        return 5, "10% owner"
    return 3, "Insider"


def score_buy(buy: dict, cluster_size: int, prior_buys_by_owner: int, dip_pct: float | None = None) -> dict:
    v = buy.get("value") or 0
    pts, reasons = 0, []
    size = 35 if v >= 1_000_000 else 28 if v >= 500_000 else 22 if v >= 250_000 else 15 if v >= 100_000 else 8
    pts += size
    reasons.append(f"${v:,.0f} of their own money")
    rp, rl = role_points(buy.get("role"))
    pts += rp
    reasons.append(f"{rl} buying")
    if cluster_size >= 3:
        pts += 20
        reasons.append(f"Cluster: {cluster_size} insiders bought within 14 days")
    elif cluster_size == 2:
        pts += 10
        reasons.append("Two insiders bought within 14 days")
    ch = buy.get("holdings_change_pct")
    if ch is not None and ch >= 20:
        pts += 10
        reasons.append(f"Holdings up {ch:.0f}%")
    elif ch is not None and ch >= 5:
        pts += 5
        reasons.append(f"Holdings up {ch:.0f}%")
    if prior_buys_by_owner == 0:
        pts += 5
        reasons.append("First open-market buy in our records")
    if dip_pct is not None and dip_pct <= -10:
        pts += 10
        reasons.append(f"Bought after a {dip_pct:.0f}% drop")
    if buy.get("planned_10b5_1"):
        pts -= 15
        reasons.append("Pre-planned (10b5-1) — less informative")
    pts = max(0, min(100, pts))
    label = "Strong" if pts >= 70 else "Notable" if pts >= 50 else "Routine"
    return {"score": pts, "label": label, "reasons": reasons}


def top_buys(conn=None, *, days: int = 7, min_value: float = 100_000, cluster_only: bool = False,
             tickers: list[str] | None = None, limit: int = 40, today: date | None = None,
             quotes=None) -> list[dict]:
    """Scored open-market purchases, strongest first."""
    from database import get_db
    owns = conn is None
    conn = conn or get_db()
    today = today or _now().date()
    since = (today - timedelta(days=days)).isoformat()
    try:
        ensure_tables(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM insider_trades WHERE side='P' AND trade_date >= ? ORDER BY trade_date DESC LIMIT 3000",
            ((today - timedelta(days=max(days, 14))).isoformat(),)).fetchall()]
        prior = defaultdict(int)
        for r in conn.execute("SELECT owner_cik, COUNT(*) AS n FROM insider_trades WHERE side='P' AND trade_date < ? GROUP BY owner_cik",
                              (since,)).fetchall():
            prior[r["owner_cik"]] = int(r["n"])
    finally:
        if owns:
            conn.close()
    want = {t.upper() for t in tickers} if tickers else None
    by_ticker = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append(r)
    out = []
    for r in rows:
        if r["trade_date"] < since or (r.get("value") or 0) < min_value:
            continue
        if want and r["ticker"] not in want:
            continue
        try:
            d0 = datetime.fromisoformat(r["trade_date"][:10]).date()
        except ValueError:
            continue
        cluster = {x["owner_cik"] or x["owner"] for x in by_ticker[r["ticker"]]
                   if abs((datetime.fromisoformat(x["trade_date"][:10]).date() - d0).days) <= 14}
        if cluster_only and len(cluster) < 3:
            continue
        s = score_buy(r, len(cluster), prior.get(r["owner_cik"], 0))
        out.append({**r, **s, "cluster_size": len(cluster)})
    # Price since the buy, when a live quote is handy.
    if quotes is None:
        try:
            from market_engine import live_quotes
            quotes = live_quotes(list({o["ticker"] for o in out})[:60]) or {}
        except Exception:
            quotes = {}
    for o in out:
        px = (quotes.get(o["ticker"]) or {}).get("price")
        o["price_now"] = px
        o["since_pct"] = round((px / o["avg_price"] - 1) * 100, 2) if px and o.get("avg_price") else None
    out.sort(key=lambda o: (-o["score"], -(o.get("value") or 0)))
    return out[:limit]


def watchlist_trades(tickers: list[str], *, days: int = 30, conn=None, today: date | None = None) -> list[dict]:
    """Stored open-market buys and sells for the user's tickers (no cap)."""
    from database import get_db
    tickers = [t.upper() for t in dict.fromkeys(tickers or []) if t]
    if not tickers:
        return []
    owns = conn is None
    conn = conn or get_db()
    today = today or _now().date()
    try:
        ensure_tables(conn)
        marks = ",".join("?" for _ in tickers)
        rows = conn.execute(
            f"SELECT * FROM insider_trades WHERE ticker IN ({marks}) AND trade_date >= ? ORDER BY trade_date DESC, value DESC LIMIT 400",
            tuple(tickers) + ((today - timedelta(days=days)).isoformat(),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owns:
            conn.close()


def owner_track_record(owner_cik: str, conn=None, quotes=None) -> dict:
    """This insider's stored buys and how they've done since (live price)."""
    from database import get_db
    owns = conn is None
    conn = conn or get_db()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT ticker, trade_date, avg_price, value FROM insider_trades WHERE side='P' AND owner_cik=? ORDER BY trade_date DESC LIMIT 20",
            (owner_cik,)).fetchall()]
    finally:
        if owns:
            conn.close()
    if quotes is None:
        try:
            from market_engine import live_quotes
            quotes = live_quotes(list({r["ticker"] for r in rows})) or {}
        except Exception:
            quotes = {}
    rets = []
    for r in rows:
        px = (quotes.get(r["ticker"]) or {}).get("price")
        if px and r.get("avg_price"):
            r["since_pct"] = round((px / r["avg_price"] - 1) * 100, 2)
            rets.append(r["since_pct"])
    return {"buys": rows, "count": len(rows), "avg_since_pct": round(sum(rets) / len(rets), 2) if rets else None,
            "winners": sum(1 for x in rets if x > 0)}


def feed_status(conn=None) -> dict:
    from database import get_db, get_setting
    owns = conn is None
    conn = conn or get_db()
    try:
        ensure_tables(conn)
        row = conn.execute("SELECT COUNT(*) AS n, MAX(created_at) AS last FROM insider_trades").fetchone()
        try:
            done = len(json.loads(get_setting("insider_feed_backfill") or "{}").get("done") or [])
        except Exception:
            done = 0
        return {"rows": int(row["n"] or 0), "last_stored": row["last"] or "", "backfill_days_done": done}
    finally:
        if owns:
            conn.close()


# ── Alerts for new buys ─────────────────────────────────────────────────────

def alert_new_buys(conn, *, since_iso: str, watch_by_user: dict[int, set[str]], owner_id: int,
                   telegram=None, push=None, record=None, min_value: float = 100_000) -> list[dict]:
    """Alert buys stored since `since_iso`: on a user's watchlist, or a strong / cluster buy anywhere."""
    from database import get_setting, set_setting
    try:
        sent = set(json.loads(get_setting("insider_alerts_sent") or "[]"))
    except Exception:
        sent = set()
    new_ids = {r["id"] for r in conn.execute(
        "SELECT id FROM insider_trades WHERE side='P' AND created_at >= ? AND value >= ?", (since_iso, min_value)).fetchall()}
    if not new_ids:
        return []
    recent = (_now().date() - timedelta(days=3)).isoformat()
    scored = [b for b in top_buys(conn, days=30, min_value=min_value, limit=500, quotes={})
              if b["id"] in new_ids and b["id"] not in sent and (b.get("filed_at") or "") >= recent]
    fired = []
    for b in scored:
        if len(fired) >= 8:          # never flood: the rest wait for the next run
            break
        users = [u for u, tickers in watch_by_user.items() if b["ticker"] in tickers]
        market_wide = b["score"] >= 70 or b["cluster_size"] >= 3
        if not users and not market_wide:
            continue
        tag = "🟢 Insider BUY" + (" · CLUSTER" if b["cluster_size"] >= 3 else "")
        text = (f"{tag} — {b['ticker']}: {b['owner']} ({b['role']}) bought ${b['value']:,.0f}"
                f"{' @ $%.2f' % b['avg_price'] if b.get('avg_price') else ''} on {b['trade_date']}. "
                f"Score {b['score']} ({b['label']}).")
        payload = {"title": f"{tag}: {b['ticker']}", "body": text[:230], "url": "/smart-money#top-buys", "tag": f"insider-{b['id']}"}
        try:
            if record:
                record(b, text)
            else:
                from database import add_scanner_alert
                add_scanner_alert(b["ticker"], "INSIDER BUY", text, "high" if b["score"] >= 70 else "medium")
        except Exception as exc:
            logger.debug("insider alert record: %s", exc)
        for u in set(users) | ({owner_id} if market_wide else set()):
            try:
                if push:
                    push(u, payload)
                else:
                    import browser_push
                    browser_push.send_to_user(u, payload, conn=conn)
            except Exception as exc:
                logger.debug("insider push: %s", exc)
        if owner_id in users or market_wide:
            try:
                if telegram:
                    telegram(text)
                else:
                    import uptime_watchdog
                    uptime_watchdog.send_telegram(text + "\nhttps://rockkstaar-trade-assistant.onrender.com/smart-money")
            except Exception as exc:
                logger.debug("insider telegram: %s", exc)
        sent.add(b["id"])
        fired.append({"id": b["id"], "ticker": b["ticker"], "users": users, "market_wide": market_wide})
    set_setting("insider_alerts_sent", json.dumps(sorted(sent)[-800:]))
    return fired
