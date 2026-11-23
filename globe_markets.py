"""World exchanges for the Command Center globe: sessions, holidays, index pulse.

The globe used to light an exchange green whenever its local clock sat inside
regular hours. That ignored holidays (NYSE glowed "open" on Thanksgiving),
lunch breaks (Tokyo, Shanghai and Hong Kong stop mid-day), early closes and
B3's seasonal hours. This module is the one place that knows those rules, so
the beacons, their countdowns and the "Follow the Sun" hand-off arcs all
read the same answer.

Calendars are the exchanges' own published schedules, entered by hand with
the source noted. Each exchange carries ``verified_through``: past that date
its holidays are not loaded, and the state says so (``verified: False``)
instead of guessing. Review this file each year when the exchanges publish
their next calendar (NSE and SSE publish theirs in December).

Index pulse is one Yahoo chart request per index, refreshed in a background
thread at most once a minute. Nothing here blocks a page on the network: a
cold cache returns no numbers and the next poll has them. Yahoo index data is
unofficial and may be delayed, and the payload says so.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def _d(s: str) -> date:
    return date.fromisoformat(s)


# ── NYSE: dates come from exchange_schedule (the app's execution calendar) ──
# Names only label the beacon; which days are closed is exchange_schedule's call.
_NYSE_NAMES = {
    "2026-11-26": "Thanksgiving Day", "2026-12-25": "Christmas Day",
    "2027-01-01": "New Year's Day", "2027-01-18": "Martin Luther King Jr. Day",
    "2027-02-15": "Washington's Birthday", "2027-03-26": "Good Friday",
    "2027-05-31": "Memorial Day", "2027-06-18": "Juneteenth (observed)",
    "2027-07-05": "Independence Day (observed)", "2027-09-06": "Labor Day",
    "2027-11-25": "Thanksgiving Day", "2027-12-24": "Christmas Day (observed)",
}


def _nyse_calendar():
    import exchange_schedule as es
    holidays, early = {}, {}
    for year, days in es.HOLIDAYS.items():
        for md in days:
            key = f"{year}-{md}"
            holidays[key] = _NYSE_NAMES.get(key, "NYSE holiday")
    for year, days in es.EARLY_CLOSES.items():
        for md in days:
            early[f"{year}-{md}"] = (1300, "Early close")
    through = date(max(es.HOLIDAYS), 12, 31)
    return holidays, early, through


_NYSE_HOL, _NYSE_EARLY, _NYSE_THROUGH = _nyse_calendar()

# Each exchange: regular sessions in local HHMM (two sessions = lunch break),
# full-day closures, early closes (HHMM the day ends) and late opens.
EXCHANGES: list[dict] = [
    {
        "code": "NYSE", "name": "New York", "city": "New York",
        "lat": 40.71, "lon": -74.01, "tz": "America/New_York",
        "sessions": [(930, 1600)],
        "index": ("^GSPC", "S&P 500"),
        "holidays": _NYSE_HOL, "early": _NYSE_EARLY,
        "verified_through": _NYSE_THROUGH,
        "source": "NYSE Group holiday calendar",
    },
    {
        # TMX Group calendar (tsx.com); 2026 Christmas Eve 1:00 pm close per TMX practice.
        "code": "TSX", "name": "Toronto", "city": "Toronto",
        "lat": 43.65, "lon": -79.38, "tz": "America/Toronto",
        "sessions": [(930, 1600)],
        "index": ("^GSPTSE", "S&P/TSX Composite"),
        "holidays": {
            "2026-12-25": "Christmas Day", "2026-12-28": "Boxing Day (in lieu)",
            "2027-01-01": "New Year's Day", "2027-02-15": "Family Day",
            "2027-03-26": "Good Friday", "2027-05-24": "Victoria Day",
            "2027-07-01": "Canada Day", "2027-08-02": "Civic Holiday",
            "2027-09-06": "Labour Day", "2027-10-11": "Thanksgiving Day",
            "2027-12-27": "Christmas Day (in lieu)", "2027-12-28": "Boxing Day (in lieu)",
        },
        "early": {"2026-12-24": (1300, "Christmas Eve"), "2027-12-24": (1300, "Christmas Eve")},
        "verified_through": _d("2027-12-31"),
        "source": "TMX Group trading calendar",
    },
    {
        # B3 Ofício Circular for 2026 and the 2027 calendar published 17 Nov 2026.
        # Regular equity session 10:00–16:55 while New York is on daylight time,
        # 10:00–17:55 otherwise (B3 shifts with US DST; see _sessions_for).
        "code": "B3", "name": "São Paulo", "city": "São Paulo",
        "lat": -23.55, "lon": -46.63, "tz": "America/Sao_Paulo",
        "sessions": [(1000, 1755)],
        "index": ("^BVSP", "Ibovespa"),
        "holidays": {
            "2026-12-24": "Christmas Eve", "2026-12-25": "Christmas Day",
            "2026-12-31": "New Year's Eve",
            "2027-01-01": "Confraternização Universal", "2027-02-08": "Carnaval",
            "2027-02-09": "Carnaval", "2027-03-26": "Good Friday",
            "2027-04-21": "Tiradentes", "2027-05-27": "Corpus Christi",
            "2027-09-07": "Independence Day", "2027-10-12": "Nossa Senhora Aparecida",
            "2027-11-02": "Finados", "2027-11-15": "Proclamação da República",
            "2027-12-24": "Christmas Eve", "2027-12-31": "New Year's Eve",
        },
        "early": {},
        "late_open": {"2027-02-10": (1300, "Ash Wednesday")},
        "us_dst_close": 1655,
        "verified_through": _d("2027-12-31"),
        "source": "B3 Ofícios Circulares (2026, 2027 holiday calendars)",
    },
    {
        # LSE business-days page (England & Wales bank holidays).
        "code": "LSE", "name": "London", "city": "London",
        "lat": 51.51, "lon": -0.09, "tz": "Europe/London",
        "sessions": [(800, 1630)],
        "index": ("^FTSE", "FTSE 100"),
        "holidays": {
            "2026-12-25": "Christmas Day", "2026-12-28": "Boxing Day (substitute)",
            "2027-01-01": "New Year's Day", "2027-03-26": "Good Friday",
            "2027-03-29": "Easter Monday", "2027-05-03": "Early May bank holiday",
            "2027-05-31": "Spring bank holiday", "2027-08-30": "Summer bank holiday",
            "2027-12-27": "Christmas Day (substitute)", "2027-12-28": "Boxing Day (substitute)",
        },
        "early": {
            "2026-12-24": (1230, "Christmas Eve"), "2026-12-31": (1230, "New Year's Eve"),
            "2027-12-24": (1230, "Christmas Eve"), "2027-12-31": (1230, "New Year's Eve"),
        },
        "verified_through": _d("2027-12-31"),
        "source": "London Stock Exchange business days",
    },
    {
        # Deutsche Börse "handelsfreie Tage" for Xetra / Börse Frankfurt.
        "code": "XETRA", "name": "Frankfurt", "city": "Frankfurt",
        "lat": 50.11, "lon": 8.68, "tz": "Europe/Berlin",
        "sessions": [(900, 1730)],
        "index": ("^GDAXI", "DAX"),
        "holidays": {
            "2026-12-24": "Christmas Eve", "2026-12-25": "Christmas Day",
            "2026-12-31": "New Year's Eve",
            "2027-01-01": "New Year's Day", "2027-03-26": "Good Friday",
            "2027-03-29": "Easter Monday", "2027-12-24": "Christmas Eve",
            "2027-12-31": "New Year's Eve",
        },
        "early": {},
        "verified_through": _d("2027-12-31"),
        "source": "Deutsche Börse trading calendar",
    },
    {
        # NSE circular NSE/FAOP/71777 (2026). 2027 list not yet published.
        "code": "NSE", "name": "Mumbai", "city": "Mumbai",
        "lat": 19.06, "lon": 72.87, "tz": "Asia/Kolkata",
        "sessions": [(915, 1530)],
        "index": ("^NSEI", "Nifty 50"),
        "holidays": {
            "2026-11-24": "Guru Nanak Jayanti", "2026-12-25": "Christmas",
        },
        "early": {},
        "verified_through": _d("2026-12-31"),
        "source": "NSE circular 212/2025 (2026 trading holidays)",
    },
    {
        # SSE: no closures left in 2026; the 2027 schedule is published in December.
        "code": "SSE", "name": "Shanghai", "city": "Shanghai",
        "lat": 31.23, "lon": 121.47, "tz": "Asia/Shanghai",
        "sessions": [(930, 1130), (1300, 1500)],
        "index": ("000001.SS", "SSE Composite"),
        "holidays": {},
        "early": {},
        "verified_through": _d("2026-12-31"),
        "source": "SSE trading calendar 2026",
    },
    {
        # HKEX circulars CT/075/25 (2026) and CT/077/26 (2027). Half days end at noon.
        "code": "HKEX", "name": "Hong Kong", "city": "Hong Kong",
        "lat": 22.28, "lon": 114.16, "tz": "Asia/Hong_Kong",
        "sessions": [(930, 1200), (1300, 1600)],
        "index": ("^HSI", "Hang Seng"),
        "holidays": {
            "2026-12-25": "Christmas Day",
            "2027-01-01": "New Year's Day", "2027-02-08": "Lunar New Year",
            "2027-02-09": "Lunar New Year", "2027-03-26": "Good Friday",
            "2027-03-29": "Easter Monday", "2027-04-05": "Ching Ming Festival",
            "2027-05-13": "Buddha's Birthday", "2027-06-09": "Tuen Ng Festival",
            "2027-07-01": "HKSAR Establishment Day",
            "2027-09-16": "Day after Mid-Autumn Festival", "2027-10-01": "National Day",
            "2027-10-08": "Chung Yeung Festival",
            "2027-12-27": "First weekday after Christmas",
        },
        "early": {
            "2026-12-24": (1200, "Christmas Eve half day"),
            "2026-12-31": (1200, "New Year's Eve half day"),
            "2027-02-05": (1200, "Lunar New Year's Eve half day"),
            "2027-12-24": (1200, "Christmas Eve half day"),
            "2027-12-31": (1200, "New Year's Eve half day"),
        },
        "verified_through": _d("2027-12-31"),
        "source": "HKEX securities market holiday circulars",
    },
    {
        # JPX market holidays page (2026, 2027). Weekend entries are harmless.
        "code": "TSE", "name": "Tokyo", "city": "Tokyo",
        "lat": 35.68, "lon": 139.77, "tz": "Asia/Tokyo",
        "sessions": [(900, 1130), (1230, 1530)],
        "index": ("^N225", "Nikkei 225"),
        "holidays": {
            "2026-11-23": "Labor Thanksgiving Day", "2026-12-31": "Market holiday",
            "2027-01-01": "New Year's Day", "2027-01-11": "Coming of Age Day",
            "2027-02-11": "National Foundation Day", "2027-02-23": "Emperor's Birthday",
            "2027-03-22": "Vernal Equinox (observed)", "2027-04-29": "Showa Day",
            "2027-05-03": "Constitution Memorial Day", "2027-05-04": "Greenery Day",
            "2027-05-05": "Children's Day", "2027-07-19": "Marine Day",
            "2027-08-11": "Mountain Day", "2027-09-20": "Respect for the Aged Day",
            "2027-09-23": "Autumnal Equinox", "2027-10-11": "Sports Day",
            "2027-11-03": "Culture Day", "2027-11-23": "Labor Thanksgiving Day",
            "2027-12-31": "Market holiday",
        },
        "early": {},
        "verified_through": _d("2027-12-31"),
        "source": "JPX market holidays",
    },
    {
        # ASX Trade calendar. Early closes end normal trading at 14:10 Sydney.
        "code": "ASX", "name": "Sydney", "city": "Sydney",
        "lat": -33.87, "lon": 151.21, "tz": "Australia/Sydney",
        "sessions": [(1000, 1600)],
        "index": ("^AXJO", "S&P/ASX 200"),
        "holidays": {
            "2026-12-25": "Christmas Day", "2026-12-28": "Boxing Day (substitute)",
            "2027-01-01": "New Year's Day", "2027-01-26": "Australia Day",
            "2027-03-26": "Good Friday", "2027-03-29": "Easter Monday",
            "2027-06-14": "King's Birthday", "2027-12-27": "Christmas Day (substitute)",
            "2027-12-28": "Boxing Day (substitute)",
        },
        "early": {
            "2026-12-24": (1410, "Christmas Eve"), "2026-12-31": (1410, "New Year's Eve"),
            "2027-12-24": (1410, "Christmas Eve"), "2027-12-31": (1410, "New Year's Eve"),
        },
        "verified_through": _d("2027-12-31"),
        "source": "ASX Trade calendar",
    },
]

BY_CODE = {e["code"]: e for e in EXCHANGES}

# The trading day's relay, in the order the world wakes up: each arc carries
# the source market's move to the next one to open. The last arc closes the
# loop across the Pacific.
RELAY = [
    ("ASX", "TSE"), ("TSE", "SSE"), ("SSE", "HKEX"), ("HKEX", "NSE"),
    ("NSE", "XETRA"), ("XETRA", "LSE"), ("LSE", "NYSE"), ("NYSE", "TSX"),
    ("NYSE", "B3"), ("NYSE", "ASX"),
]


def _hm(day: date, hhmm: int, tz: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, hhmm // 100, hhmm % 100, tzinfo=tz)


def _us_dst(day: date) -> bool:
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=ZoneInfo("America/New_York"))
    return bool(noon.dst())


def _sessions_for(ex: dict, day: date) -> tuple[list[tuple[int, int]], str | None, str | None]:
    """(sessions, closed_reason, note) for one local calendar day.

    closed_reason is set (and sessions empty) on weekends and holidays. note
    describes a shortened day. Days past verified_through use the regular
    weekday schedule; the caller marks the state unverified.
    """
    if day.weekday() >= 5:
        return [], "Weekend", None
    key = day.isoformat()
    if day <= ex["verified_through"] and key in ex["holidays"]:
        return [], ex["holidays"][key], None
    sessions = list(ex["sessions"])
    note = None
    if ex.get("us_dst_close") and _us_dst(day):
        sessions = [(o, min(c, ex["us_dst_close"])) for o, c in sessions]
    if day <= ex["verified_through"]:
        late = (ex.get("late_open") or {}).get(key)
        if late:
            sessions = [(max(o, late[0]), c) for o, c in sessions if c > late[0]]
            note = f"{late[1]} · opens {late[0] // 100}:{late[0] % 100:02d}"
        early = ex["early"].get(key)
        if early:
            sessions = [(o, min(c, early[0])) for o, c in sessions if o < early[0]]
            note = f"{early[1]} · closes {early[0] // 100}:{early[0] % 100:02d}"
    return sessions, None, note


def session_state(ex: dict, now: datetime | None = None) -> dict:
    """Where one exchange stands right now, plus the next change.

    status: "open" | "lunch" | "closed" | "holiday" (a weekday the exchange
    is shut). opens_at / closes_at are UTC epoch seconds of the next open and
    the current session's close; last_close_at is when the most recent
    session ended (the hand-off arcs order markets by it).
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    tz = ZoneInfo(ex["tz"])
    local = now.astimezone(tz)
    today = local.date()
    sessions, reason, note = _sessions_for(ex, today)
    status, closes_at, opens_at, holiday = "closed", None, None, None
    for o, c in sessions:
        so, sc = _hm(today, o, tz), _hm(today, c, tz)
        if so <= local < sc:
            status, closes_at = "open", sc
            break
    if status != "open":
        if reason and reason != "Weekend":
            status, holiday = "holiday", reason
        elif sessions and _hm(today, sessions[0][0], tz) <= local < _hm(today, sessions[-1][1], tz):
            status = "lunch"
        # Next open: later today, else the next day with a session.
        for o, _c in sessions:
            if _hm(today, o, tz) > local:
                opens_at = _hm(today, o, tz)
                break
        d = today
        for _ in range(21):
            if opens_at:
                break
            d += timedelta(days=1)
            s2, _r, _n = _sessions_for(ex, d)
            if s2:
                opens_at = _hm(d, s2[0][0], tz)
    # Most recent end of a trading DAY (a lunch break is not a close).
    last_close = None
    if sessions and _hm(today, sessions[-1][1], tz) <= local:
        last_close = _hm(today, sessions[-1][1], tz)
    d = today
    for _ in range(21):
        if last_close:
            break
        d -= timedelta(days=1)
        s3, _r, _n = _sessions_for(ex, d)
        if s3:
            last_close = _hm(d, s3[-1][1], tz)
    verified = today <= ex["verified_through"]
    sym, idx_name = ex["index"]
    return {
        "code": ex["code"], "name": ex["name"], "city": ex["city"],
        "lat": ex["lat"], "lon": ex["lon"], "tz": ex["tz"],
        "status": status,
        "holiday": holiday,
        "note": note,
        "opens_at": int(opens_at.timestamp()) if opens_at else None,
        "closes_at": int(closes_at.timestamp()) if closes_at else None,
        "last_close_at": int(last_close.timestamp()) if last_close else None,
        "verified": verified,
        "verified_through": ex["verified_through"].isoformat(),
        "index": {"symbol": sym, "name": idx_name},
        "source": ex["source"],
    }


def exchange_states(now: datetime | None = None) -> list[dict]:
    return [session_state(ex, now) for ex in EXCHANGES]


# ── Index pulse (Yahoo chart API, background refresh) ─────────────────────
_PULSE_TTL_S = 60
_PULSE_MAX_AGE_S = 30 * 60
_PULSE: dict = {"at": 0.0, "data": {}, "refreshing": False, "last_error": ""}
_PULSE_LOCK = threading.Lock()


def _parse_chart(res: dict) -> dict | None:
    meta = res.get("meta") or {}
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not isinstance(price, (int, float)) or not isinstance(prev, (int, float)) or not prev:
        return None
    closes = []
    try:
        raw = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        closes = [round(float(c), 2) for c in raw if isinstance(c, (int, float))]
    except Exception:
        closes = []
    if len(closes) > 48:   # a sparkline needs shape, not every 5-minute bar
        step = len(closes) / 48
        closes = [closes[int(i * step)] for i in range(48)] + [closes[-1]]
    return {
        "price": round(float(price), 2),
        "prev_close": round(float(prev), 2),
        "change_pct": round((float(price) / float(prev) - 1) * 100, 2),
        "as_of": int(meta.get("regularMarketTime") or 0) or None,
        "spark": closes,
        "currency": meta.get("currency") or "",
    }


def _fetch_index(symbol: str) -> dict | None:
    import requests
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    for base in ("https://query1.finance.yahoo.com/v8/finance/chart",
                 "https://query2.finance.yahoo.com/v8/finance/chart"):
        try:
            r = requests.get(f"{base}/{symbol}", params={"range": "1d", "interval": "5m"},
                             headers=headers, timeout=6)
            if r.status_code != 200:
                continue
            res = ((r.json().get("chart") or {}).get("result") or [None])[0]
            if res:
                return _parse_chart(res)
        except Exception as exc:
            logger.debug("globe index %s via %s failed: %s", symbol, base, exc)
    return None


def _refresh_pulse() -> None:
    from concurrent.futures import ThreadPoolExecutor
    got: dict = {}
    pairs = [(e["code"], e["index"][0]) for e in EXCHANGES]
    with ThreadPoolExecutor(max_workers=5) as pool:
        for (code, _sym), q in zip(pairs, pool.map(lambda p: _fetch_index(p[1]), pairs)):
            if q:
                q["fetched_at"] = int(time.time())
                got[code] = q
    with _PULSE_LOCK:
        # Keep an older good quote rather than drop a market for one failed request.
        _PULSE["data"].update(got)


def index_pulse() -> dict:
    """{exchange code: index quote} — never blocks.

    Starts one background refresh when the cache is past its TTL (off under
    TRADESTAAR_NO_BACKGROUND, like the app's other feeds). Quotes older than
    _PULSE_MAX_AGE_S are dropped rather than shown as current.
    """
    now = time.time()
    background = os.environ.get("TRADESTAAR_NO_BACKGROUND", "").strip().lower() not in ("1", "true", "yes")
    with _PULSE_LOCK:
        start = background and now - _PULSE["at"] >= _PULSE_TTL_S and not _PULSE["refreshing"]
        if start:
            _PULSE["refreshing"] = True
            _PULSE["at"] = now
    if start:
        def _work():
            try:
                _refresh_pulse()
                _PULSE["last_error"] = ""
            except Exception as exc:
                _PULSE["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
                logger.warning("globe index pulse refresh failed: %s", exc)
            finally:
                _PULSE["refreshing"] = False
        threading.Thread(target=_work, daemon=True, name="globe_index_pulse").start()
    with _PULSE_LOCK:
        return {k: dict(v) for k, v in _PULSE["data"].items()
                if now - (v.get("fetched_at") or 0) <= _PULSE_MAX_AGE_S}


def payload(now: datetime | None = None) -> dict:
    """Everything the globe needs about world markets, in one object."""
    now = now or datetime.now(timezone.utc)
    return {
        "exchanges": exchange_states(now),
        "indices": index_pulse(),
        "relay": [list(p) for p in RELAY],
        "server_epoch": int(now.timestamp()),
        "index_source": "Yahoo Finance index quotes · unofficial, may be delayed",
    }


# ── Setup scores for globe pins ───────────────────────────────────────────
def score_map(tickers) -> dict:
    """{TICKER: {"s": 1–10 swing score, "g": grade, "setup": label}} from the
    scanner's stored analysis — one query, no network. Tickers never scanned
    are simply absent (the globe draws no ring rather than a zero)."""
    syms = sorted({str(t or "").upper().strip() for t in (tickers or []) if t})[:200]
    if not syms:
        return {}
    try:
        from database import get_db
        from scoring import compute_swing_grade
    except Exception:
        return {}
    out: dict = {}
    try:
        conn = get_db()
        marks = ",".join("?" for _ in syms)
        rows = conn.execute(
            f"SELECT ticker, swing_score, swing_setup_type FROM stock_data WHERE ticker IN ({marks})",
            tuple(syms)).fetchall()
        conn.close()
    except Exception as exc:
        logger.debug("globe score map unavailable: %s", exc)
        return {}
    for row in rows:
        r = dict(row)
        try:
            s = int(r.get("swing_score") or 0)
        except (TypeError, ValueError):
            s = 0
        if 1 <= s <= 10:
            out[str(r["ticker"]).upper()] = {"s": s, "g": compute_swing_grade(s),
                                             "setup": r.get("swing_setup_type") or ""}
    return out
