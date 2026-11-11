"""Market feeds for the notification inbox, run by the 5-minute cron.

Everything else in the inbox is recorded where an alert is already decided.
These feeds add what nothing alerted on before:

  news      fresh headlines on every ticker someone follows (per user), and
            high-impact market-wide headlines (everyone)
  earnings  watchlist reports today / tomorrow, and results as they land
            (beat / missed / in line)
  economy   high-impact US releases on the day, and the number once it prints

Each item has a stable key, so a story or a result is stored once however
many runs see it. Provider calls are throttled through settings so the
5-minute cron does not hit them every run.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import notify_inbox

logger = logging.getLogger(__name__)

NEWS_WINDOW = timedelta(hours=2)
MARKET_WINDOW = timedelta(hours=2)
MAX_TICKERS = 40
PER_TICKER = 2
MARKET_PER_RUN = 6
EVERY = {"news": 0, "earnings": 30 * 60, "economy": 10 * 60, "releases": 20 * 60}   # seconds between provider calls

_SEV = {"CRITICAL": "danger", "HIGH": "watch"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", " ".join(str(text or "").lower().split()))[:90]


def _parse(ts) -> datetime | None:
    if not ts:
        return None
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(float(ts), timezone.utc)
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _due(name: str, now: datetime, get, put) -> bool:
    gap = EVERY.get(name, 0)
    if not gap:
        return True
    try:
        last = float(get(f"inbox_feed_at:{name}") or 0)
    except (TypeError, ValueError):
        last = 0.0
    if now.timestamp() - last < gap:
        return False
    put(f"inbox_feed_at:{name}", str(now.timestamp()))
    return True


def watch_by_user(conn) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for r in conn.execute("SELECT w.user_id AS uid, ws.ticker AS t FROM watchlist_stocks ws "
                          "JOIN watchlists w ON w.id = ws.watchlist_id").fetchall():
        r = dict(r)
        if r.get("uid") is not None and r.get("t"):
            out.setdefault(int(r["uid"]), set()).add(str(r["t"]).upper())
    return out


def _followers(watch: dict[int, set[str]]) -> dict[str, list[int]]:
    by: dict[str, list[int]] = {}
    for uid, tickers in watch.items():
        for t in tickers:
            by.setdefault(t, []).append(uid)
    return by


# ── News ─────────────────────────────────────────────────────────────────────

def news(watch: dict[int, set[str]], *, now: datetime, ticker_news, market_news, add) -> int:
    followers = _followers(watch)
    tickers = sorted(followers, key=lambda t: (-len(followers[t]), t))[:MAX_TICKERS]
    n = 0
    for t in tickers:
        try:
            stories = ticker_news(t) or []
        except Exception as exc:
            logger.debug("inbox news %s: %s", t, exc)
            continue
        fresh = []
        for s in stories:
            ts = _parse(s.get("published_at"))
            if s.get("headline") and ts and now - ts <= NEWS_WINDOW:
                fresh.append((ts, s))
        fresh.sort(key=lambda p: p[0], reverse=True)
        for ts, s in fresh[:PER_TICKER]:
            impact = str(s.get("impact") or "").upper()
            body = " ".join(str(s.get("summary") or "").split())[:280]
            src = s.get("source") or ""
            for uid in followers[t]:
                if add("news", f"{t} · {s['headline']}", (body + (f" — {src}" if src else "")).strip(" —"),
                       url=s.get("url") or f"/stock/{t}", ticker=t, severity=_SEV.get(impact, "info"),
                       user_id=uid, key=f"news|{t}|{_norm(s['headline'])}", now=now):
                    n += 1
    followed = set(followers)
    try:
        rows = market_news() or []
    except Exception as exc:
        logger.debug("inbox market news: %s", exc)
        rows = []
    picked = 0
    for s in sorted(rows, key=lambda r: str(r.get("published_at") or ""), reverse=True):
        if picked >= MARKET_PER_RUN:
            break
        impact = str(s.get("impact") or "").upper()
        ts = _parse(s.get("published_at"))
        t = str(s.get("ticker") or "MARKET").upper()
        if impact not in _SEV or not ts or now - ts > MARKET_WINDOW or t in followed or not s.get("headline"):
            continue
        body = " ".join(str(s.get("summary") or "").split())[:280]
        src = s.get("source") or ""
        if add("news", s["headline"], (body + (f" — {src}" if src else "")).strip(" —"),
               url=s.get("url") or "", ticker="" if t == "MARKET" else t, severity=_SEV[impact],
               user_id=0, key=f"news|MARKET|{_norm(s['headline'])}", now=now):
            n += 1
        picked += 1
    return n


# ── Earnings ─────────────────────────────────────────────────────────────────

def _money(v) -> str:
    try:
        return f"${float(v):.2f}"
    except (TypeError, ValueError):
        return "—"


def earnings(watch: dict[int, set[str]], *, now: datetime, calendar: dict, add) -> int:
    followers = _followers(watch)
    rows = []
    for bucket in ("today", "tomorrow", "this_week", "coming_up"):
        rows.extend(r for r in (calendar or {}).get(bucket) or [] if isinstance(r, dict) and r.get("ticker"))
    n = 0
    seen = set()
    for r in rows:
        t = str(r["ticker"]).upper()
        day = str(r.get("date") or "")[:10]
        if (t, day) in seen:
            continue
        seen.add((t, day))
        try:
            away = int(r.get("days_away"))
        except (TypeError, ValueError):
            away = 99
        users = followers.get(t) or []
        actual, est = r.get("eps_actual"), r.get("eps_est")
        if actual is not None and away <= 0:
            verdict, sev = "reported", "info"
            try:
                if est is not None:
                    diff = float(actual) - float(est)
                    verdict, sev = (("beat", "info") if diff > 0 else ("missed", "watch") if diff < 0
                                    else ("in line", "info"))
            except (TypeError, ValueError):
                pass
            title = f"{t} {verdict}" + (" estimates" if verdict in ("beat", "missed") else "")
            body = f"EPS {_money(actual)} vs {_money(est)} expected."
            for uid in (users or [0]):
                if add("calendar", title, body, url=f"/stock/{t}", ticker=t, severity=sev, user_id=uid,
                       key=f"earn-result|{t}|{day}", now=now):
                    n += 1
        elif users and away in (0, 1) and actual is None:
            when = "today" if away == 0 else "tomorrow"
            timing = r.get("time_text") or {"BMO": "before the open", "AMC": "after the close"}.get(
                str(r.get("time_label") or "").upper(), "time not announced")
            body = f"Consensus EPS {_money(est)}." if est is not None else "No consensus estimate yet."
            for uid in users:
                if add("calendar", f"{t} reports {when} — {timing}", body, url=f"/stock/{t}", ticker=t,
                       user_id=uid, key=f"earn-upcoming|{t}|{day}|{when}", now=now):
                    n += 1
    return n


def releases(watch: dict[int, set[str]], *, now: datetime, latest, add) -> int:
    """Watchlist companies whose earnings press release (8-K Item 2.02) is out
    but whose 10-Q/10-K is not: the headline numbers, once per release."""
    import earnings_release
    followers = _followers(watch)
    cutoff = (now - timedelta(days=3)).date().isoformat()
    n = 0
    for t, users in sorted(followers.items()):
        try:
            rel = latest(t)
        except Exception:
            rel = None
        if not rel or str(rel.get("filed") or "") < cutoff:
            continue
        line = earnings_release.summary(rel)
        body = (line + ". " if line else "") + "From the company's press release; statements update when the 10-Q is filed."
        for uid in users:
            if add("calendar", f"{t} reported results", body, url=f"/research?ticker={t}#financials", ticker=t,
                   user_id=uid, key=f"release|{t}|{rel.get('accn') or rel.get('filed')}", now=now):
                n += 1
    return n


# ── Economy ──────────────────────────────────────────────────────────────────

def _num(v, unit=""):
    if v is None or v == "":
        return None
    try:
        f = float(v)
        s = f"{f:,.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        s = str(v)
    return f"{s}{unit or ''}"


def economy(*, now: datetime, events: list[dict], add) -> int:
    n = 0
    for e in events or []:
        if str(e.get("impact") or "").upper() != "HIGH":
            continue
        try:
            away = int(e.get("days_away"))
        except (TypeError, ValueError):
            continue
        if away != 0:
            continue
        name, day, unit = e.get("event") or "Economic release", str(e.get("date") or "")[:10], e.get("unit") or ""
        actual = _num(e.get("actual"), unit)
        if actual is not None:
            est, prev = _num(e.get("estimate"), unit), _num(e.get("previous"), unit)
            body = f"Actual {actual}" + (f" vs {est} expected" if est else "") + (f" (prior {prev})" if prev else "") + "."
            if add("calendar", f"{name}: {actual}", body, url="/calendar", severity="watch", user_id=0,
                   key=f"econ-actual|{_norm(name)}|{day}", now=now):
                n += 1
        else:
            when = e.get("time") or "time TBD"
            est = _num(e.get("estimate"), unit)
            body = (f"Expected {est}. " if est else "") + (e.get("reason") or "")
            if add("calendar", f"Today: {name} at {when}", body.strip(), url="/calendar", user_id=0,
                   key=f"econ-today|{_norm(name)}|{day}", now=now):
                n += 1
    return n


# ── Cron entry ───────────────────────────────────────────────────────────────

def run(conn=None, *, now: datetime | None = None, ticker_news=None, market_news=None,
        earnings_calendar=None, economic_calendar=None, add=None, get=None, put=None,
        release_lookup=None) -> dict:
    now = now or _now()
    add = add or notify_inbox.add
    if get is None or put is None:
        from database import get_setting, set_setting
        get, put = get or get_setting, put or set_setting
    owns = conn is None
    if owns:
        from database import get_db
        conn = get_db()
    out = {"news": 0, "earnings": 0, "releases": 0, "economy": 0, "errors": []}
    try:
        watch = watch_by_user(conn)
    except Exception as exc:
        out["errors"].append("watch:" + type(exc).__name__)
        watch = {}
    finally:
        if owns:
            conn.close()
    import intel_engine as ie
    try:
        if _due("news", now, get, put):
            out["news"] = news(watch, now=now,
                               ticker_news=ticker_news or (lambda t: ie.fetch_ticker_news(t, limit=8)),
                               market_news=market_news or (lambda: ie.fetch_general_market_news(limit=20)),
                               add=add)
    except Exception as exc:
        out["errors"].append("news:" + type(exc).__name__)
    try:
        if _due("earnings", now, get, put):
            tickers = sorted({t for ts in watch.values() for t in ts})
            cal = earnings_calendar() if earnings_calendar else ie.fetch_earnings_calendar(tickers or None)
            out["earnings"] = earnings(watch, now=now, calendar=cal or {}, add=add)
    except Exception as exc:
        out["errors"].append("earnings:" + type(exc).__name__)
    try:
        if watch and _due("releases", now, get, put):
            import earnings_release
            out["releases"] = releases(watch, now=now, latest=release_lookup or earnings_release.latest, add=add)
    except Exception as exc:
        out["errors"].append("releases:" + type(exc).__name__)
    try:
        if _due("economy", now, get, put):
            events = economic_calendar() if economic_calendar else ie.fetch_economic_calendar()
            out["economy"] = economy(now=now, events=events or [], add=add)
    except Exception as exc:
        out["errors"].append("economy:" + type(exc).__name__)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run(), default=str))
