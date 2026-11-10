"""Market-wide SEC Form 4 feed: discovery, parsing, storage, scoring, alerts; FMP congress."""
import os
import sqlite3
from datetime import date, datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-insider-feed")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import insider_feed as f
import smart_money


def form4(ticker="CEG", owner="Dominguez Joseph", cik="0001234567", officer="Chief Executive Officer",
          txs=(("P", 1000, 270.0, 51000),), plan=False):
    rows = "".join(f"""
      <nonDerivativeTransaction>
        <securityTitle><value>Common Stock</value></securityTitle>
        <transactionDate><value>2026-11-06</value></transactionDate>
        <transactionCoding><transactionFormType>4</transactionFormType><transactionCode>{code}</transactionCode></transactionCoding>
        <transactionAmounts>
          <transactionShares><value>{sh}</value></transactionShares>
          <transactionPricePerShare><value>{px}</value></transactionPricePerShare>
          <transactionAcquiredDisposedCode><value>{'A' if code == 'P' else 'D'}</value></transactionAcquiredDisposedCode>
        </transactionAmounts>
        <postTransactionAmounts><sharesOwnedFollowingTransaction><value>{after}</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
        <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
      </nonDerivativeTransaction>""" for code, sh, px, after in txs)
    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>0001868275</issuerCik><issuerName>Constellation Energy Corp</issuerName><issuerTradingSymbol>{ticker}</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>{cik}</rptOwnerCik><rptOwnerName>{owner}</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>{officer}</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <aff10b5One>{'1' if plan else '0'}</aff10b5One>
  <nonDerivativeTable>{rows}</nonDerivativeTable>
</ownershipDocument>"""


def submission(xml):
    return f"<SEC-DOCUMENT>header\n<DOCUMENT>\n<TYPE>4\n<TEXT>\n<XML>\n{xml}\n</XML>\n</TEXT>\n</DOCUMENT>\n</SEC-DOCUMENT>"


ATOM = """<feed><entry><title>4 - Constellation Energy Corp (0001868275) (Issuer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/1868275/000123456726000001/0001234567-26-000001-index.htm"/>
<updated>2026-11-09T16:05:12-05:00</updated><id>urn:tag:sec.gov,2008:accession-number=0001234567-26-000001</id></entry>
<entry><title>4 - Dominguez Joseph (0001234567) (Reporting)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/0001234567-26-000001-index.htm"/>
<updated>2026-11-09T16:05:12-05:00</updated><id>urn:tag:sec.gov,2008:accession-number=0001234567-26-000001</id></entry></feed>"""

DAILY = """Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
4           Constellation Energy Corp                                     1868275     20261106    edgar/data/1868275/0001234567-26-000002.txt
4           Dominguez Joseph                                              1234567     20261106    edgar/data/1234567/0001234567-26-000002.txt
4/A         Other Co                                                      777         20261106    edgar/data/777/0000000777-26-000009.txt
10-K        Something                                                     888         20261106    edgar/data/888/0000000888-26-000001.txt
"""


class Resp:
    def __init__(self, text="", status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


@pytest.fixture
def db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    f.ensure_tables(conn)
    store = {}
    monkeypatch.setattr("database.get_setting", lambda k: store.get(k))
    monkeypatch.setattr("database.set_setting", lambda k, v: store.__setitem__(k, v))
    yield conn
    conn.close()


# ── Discovery ────────────────────────────────────────────────────────────────

def test_parse_atom_dedupes_issuer_and_reporting_entries():
    rows = f.parse_atom(ATOM)
    assert rows == [{"accession": "0001234567-26-000001", "cik": "1868275", "filed_at": "2026-11-09"}]


def test_parse_daily_index_keeps_form4_once():
    rows = f.parse_daily_index(DAILY)
    assert [r["accession"] for r in rows] == ["0001234567-26-000002", "0000000777-26-000009"]
    assert rows[0]["filed_at"] == "2026-11-06" and rows[0]["cik"] == "1868275"


def test_urls_and_xml_extraction():
    assert f.submission_url("0001868275", "0001234567-26-000001").endswith("/1868275/000123456726000001/0001234567-26-000001.txt")
    assert f.extract_ownership_xml(submission(form4())).startswith(b'<?xml')
    assert f.extract_ownership_xml("<XML><other/></XML>") is None


# ── Parsing ──────────────────────────────────────────────────────────────────

def test_open_market_buy_and_sell_rows():
    xml = form4(txs=(("P", 1000, 270.0, 51000), ("P", 500, 272.0, 51500), ("S", 200, 280.0, 51300), ("F", 50, 0, 51250))).encode()
    rows = {r["side"]: r for r in f.trades_from_filing(xml, "acc-1", "2026-11-09", "https://sec/idx")}
    assert set(rows) == {"P", "S"}                                    # tax withholding (F) dropped
    p = rows["P"]
    assert p["ticker"] == "CEG" and p["shares"] == 1500 and p["value"] == 406000.0
    assert p["avg_price"] == pytest.approx(270.6667, rel=1e-4)
    assert p["role"].startswith("Officer") and "Chief Executive" in p["role"]
    assert p["holdings_change_pct"] == pytest.approx(1500 / 50000 * 100)
    assert p["trade_date"] == "2026-11-06" and p["id"] == "acc-1:P"


# ── Sync ─────────────────────────────────────────────────────────────────────

def test_sync_stores_live_and_backfill_once(db):
    calls = []

    def get(url):
        calls.append(url)
        if "getcurrent" in url:
            return Resp(ATOM if "start=0" in url else "<feed></feed>")
        if "daily-index" in url:
            return Resp(DAILY) if "20261106" in url else Resp("", 404)
        if url.endswith("0001234567-26-000001.txt"):
            return Resp(submission(form4(txs=(("P", 1000, 270.0, 51000),))))
        if url.endswith("0001234567-26-000002.txt"):
            return Resp(submission(form4(owner="Rivera Ana", cik="0009999999", officer="", txs=(("S", 4000, 275.0, 9000),))))
        if url.endswith("0000000777-26-000009.txt"):
            return Resp("no xml here")
        return Resp("", 404)

    out = f.sync(db, get=get, today=date(2026, 11, 9), seconds=30)
    assert out["live"]["stored"] == 1 and out["backfill"]["stored"] == 1
    sides = {r["side"] for r in db.execute("SELECT side FROM insider_trades").fetchall()}
    assert sides == {"P", "S"}
    n_calls = len(calls)
    again = f.sync(db, get=get, today=date(2026, 11, 9), seconds=30)
    assert again["live"]["checked"] == 0 and again["backfill"]["checked"] == 0
    assert not any(u.endswith(".txt") for u in calls[n_calls:])         # nothing refetched


def test_sync_respects_budget(db):
    many = "\n".join(f"4  Co{i}  {i}  20261106  edgar/data/{i}/00000000{i:02d}-26-0000{i:02d}.txt" for i in range(10, 40))
    fetched = []

    def get(url):
        if "getcurrent" in url:
            return Resp("<feed></feed>")
        if "daily-index" in url:
            return Resp(many) if "20261106" in url else Resp("", 404)
        fetched.append(url)
        return Resp("nothing")

    out = f.sync(db, get=get, today=date(2026, 11, 9), budget=12, seconds=30)
    assert len(fetched) == 12 and out["backfill"]["checked"] == 12


# ── Scoring and reading ─────────────────────────────────────────────────────

def seed(db, rows):
    now = datetime.now(timezone.utc).isoformat()
    for i, r in enumerate(rows):
        base = {"id": f"a{i}:P", "accession": f"a{i}", "side": "P", "ticker": "CEG", "issuer": "Constellation",
                "owner": f"Owner {i}", "owner_cik": f"{i}", "role": "Director", "trade_date": "2026-11-06",
                "filed_at": "2026-11-09", "shares": 1000, "avg_price": 250.0, "value": 250000.0,
                "holdings_after": 5000, "holdings_change_pct": 25.0, "planned_10b5_1": 0, "source_url": "https://sec"}
        base.update(r)
        db.execute("INSERT INTO insider_trades (id,accession,side,ticker,issuer,owner,owner_cik,role,trade_date,filed_at,shares,avg_price,value,holdings_after,holdings_change_pct,planned_10b5_1,source_url,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   tuple(base[k] for k in ("id", "accession", "side", "ticker", "issuer", "owner", "owner_cik", "role", "trade_date", "filed_at", "shares", "avg_price", "value", "holdings_after", "holdings_change_pct", "planned_10b5_1", "source_url")) + (now,))


def test_score_rewards_ceo_cluster_size_and_penalizes_plans():
    ceo = f.score_buy({"value": 1_200_000, "role": "Officer, Chief Executive Officer", "holdings_change_pct": 30}, 3, 0, dip_pct=-15)
    assert ceo["score"] == 100 and ceo["label"] == "Strong" and any("Cluster" in r for r in ceo["reasons"])
    small = f.score_buy({"value": 60_000, "role": "Director", "planned_10b5_1": 1}, 1, 4)
    assert small["label"] == "Routine" and small["score"] < 20


def test_top_buys_filters_clusters_and_since(db):
    seed(db, [{"value": 900000, "role": "Officer, CEO"}, {}, {}, {"ticker": "XYZ", "value": 50000},
              {"ticker": "OLD", "trade_date": "2026-09-01"}])
    buys = f.top_buys(db, days=7, min_value=100000, today=date(2026, 11, 9), quotes={"CEG": {"price": 275.0}})
    assert [b["ticker"] for b in buys] == ["CEG", "CEG", "CEG"]
    assert buys[0]["owner"] == "Owner 0" and buys[0]["cluster_size"] == 3 and buys[0]["since_pct"] == 10.0
    assert f.top_buys(db, days=7, min_value=0, cluster_only=True, today=date(2026, 11, 9), quotes={}) and \
        all(b["cluster_size"] >= 3 for b in f.top_buys(db, days=7, min_value=0, cluster_only=True, today=date(2026, 11, 9), quotes={}))
    assert [w["ticker"] for w in f.watchlist_trades(["xyz"], days=30, conn=db, today=date(2026, 11, 9))] == ["XYZ"]


def test_track_record(db):
    seed(db, [{"owner_cik": "77", "ticker": "AAA", "avg_price": 10.0}, {"owner_cik": "77", "ticker": "BBB", "avg_price": 20.0}])
    t = f.owner_track_record("77", conn=db, quotes={"AAA": {"price": 12.0}, "BBB": {"price": 18.0}})
    assert t["count"] == 2 and t["avg_since_pct"] == 5.0 and t["winners"] == 1


def test_alerts_watchlist_and_strong_buys_once(db, monkeypatch):
    monkeypatch.setattr(f, "_now", lambda: datetime(2026, 11, 9, 21, 0, tzinfo=timezone.utc))
    seed(db, [{"ticker": "CEG", "value": 300000},                                   # on user 2's watchlist
              {"ticker": "ZZZ", "value": 2_000_000, "role": "Officer, Chief Executive Officer", "owner_cik": "z1"},  # strong anywhere
              {"ticker": "QQQX", "value": 150000, "owner_cik": "q1"},                # neither
              {"ticker": "OLDX", "value": 5_000_000, "role": "CEO", "filed_at": "2026-10-01", "owner_cik": "o1"}])  # backfilled old
    pushed, tg = [], []
    kw = dict(since_iso="2000-01-01", watch_by_user={2: {"CEG"}}, owner_id=1,
              telegram=lambda t: tg.append(t), push=lambda u, p: pushed.append((u, p["title"])), record=lambda b, t: None)
    fired = f.alert_new_buys(db, **kw)
    assert {x["ticker"] for x in fired} == {"CEG", "ZZZ"}
    assert (2, "🟢 Insider BUY: CEG") in pushed and (1, "🟢 Insider BUY: ZZZ") in pushed
    assert len(tg) == 1 and "ZZZ" in tg[0]                          # owner gets Telegram for the strong buy only
    assert f.alert_new_buys(db, **kw) == []                          # never twice


# ── FMP congressional adapter ───────────────────────────────────────────────

FMP_ROW = {"symbol": "NVDA", "firstName": "Nancy", "lastName": "Example", "type": "Purchase", "amount": "$1,001 - $15,000",
           "transactionDate": "2026-10-20", "disclosureDate": "2026-11-05",
           "link": "https://efdsearch.senate.gov/search/view/ptr/abc/"}


def test_fmp_congress_parse_requires_official_link():
    rows = smart_money.parse_fmp_congress("SENATE", [FMP_ROW, dict(FMP_ROW, link="https://example.com/x")])
    assert len(rows) == 1 and rows[0]["member"] == "Nancy Example" and rows[0]["chamber"] == "SENATE"


def test_fmp_congress_plan_denied_says_so(monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "k")
    smart_money._cache.pop("congress:fmp", None)
    rows, status = smart_money.fetch_fmp_congress_trades(get=lambda u: Resp("", 402))
    assert rows == [] and "plan does not include" in status["message"]
    smart_money._cache.pop("congress:fmp", None)

    class J(Resp):
        def json(self):
            return [FMP_ROW]
    rows, status = smart_money.fetch_fmp_congress_trades(get=lambda u: J("[]"))
    assert len(rows) == 2 and status["available"]
    smart_money._cache.pop("congress:fmp", None)


# ── Page ─────────────────────────────────────────────────────────────────────

def test_page_renders_top_buys_and_watchlist(monkeypatch):
    import web_app
    monkeypatch.setattr(smart_money, "fetch_sec_form4", lambda *a, **k: ([], {"available": True, "message": "ok"}))
    monkeypatch.setattr(f, "top_buys", lambda **k: [{**{"id": "a:P", "ticker": "CEG", "issuer": "Constellation", "owner": "Joe CEO",
                                                         "owner_cik": "9", "role": "Officer, CEO", "trade_date": "2026-11-06",
                                                         "filed_at": "2026-11-09", "shares": 1000, "avg_price": 250.0, "value": 250000.0,
                                                         "holdings_change_pct": 25.0, "planned_10b5_1": 0, "source_url": "https://sec"},
                                                      "score": 81, "label": "Strong", "reasons": ["$250,000", "C-suite buying", "Cluster"],
                                                      "cluster_size": 3, "price_now": 260.0, "since_pct": 4.0}])
    monkeypatch.setattr(f, "watchlist_trades", lambda *a, **k: [{"ticker": "NVDA", "side": "S", "owner": "Huang", "role": "CEO",
                                                                 "value": 9e6, "avg_price": 180.0, "trade_date": "2026-11-05",
                                                                 "planned_10b5_1": 1, "source_url": "https://sec"}])
    monkeypatch.setattr(f, "feed_status", lambda: {"rows": 1234, "last_stored": "x", "backfill_days_done": 12})
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    html = c.get("/smart-money").get_data(as_text=True)
    assert "Top insider buys" in html and "Joe CEO" in html and "+4.0%" in html and "3 insiders" in html
    assert "Your watchlist — open-market activity" in html and "NVDA" in html
    assert "1,234 open-market trades stored" in html and "Whole market" in html
    assert "Open-market buys &amp; sales" in html or "Open-market buys & sales" in html
