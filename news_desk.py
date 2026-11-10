"""News desk: catalyst heat map, earnings reaction replay, explain-this-move.

heat_map(tickers)       one tile per ticker: 24h story count, impact-weighted
                        intensity and average tone, plus today's % move.
earnings_replay(ticker) how the stock reacted to its last 8 reports: gap,
                        day close, 5-day follow-through, whether the gap filled.
explain_move(ticker)    why it is moving: its move vs SPY and its sector ETF,
                        volume, and the freshest headlines → two lines (Claude
                        when configured, otherwise a rule-based read).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_IMPACT_W = {"CRITICAL": 3.0, "HIGH": 2.0, "MEDIUM": 1.2, "LOW": 0.6}
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="news-desk")
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()
_ai_slots = threading.BoundedSemaphore(2)


def _cget(key, ttl):
    with _lock:
        hit = _cache.get(key)
        return hit[1] if hit and time.time() - hit[0] < ttl else None


def _cset(key, value):
    with _lock:
        if len(_cache) > 800:
            _cache.clear()
        _cache[key] = (time.time(), value)


def _parse(ts):
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── Heat map ─────────────────────────────────────────────────────────────────

_inflight: dict = {}          # ticker -> Future, so a ticker is fetched once at a time
NEWS_TTL = 300


def _news_future(ticker: str, fetch):
    """One lookup per ticker at a time, results (even "no news") kept 5 min.

    The heat map re-polls every 8 s while any tile is pending, and tickers with
    no news were never cached, so each poll queued another full provider
    lookup for every ticker — an ever-growing backlog in the shared pool.
    """
    from concurrent.futures import Future
    hit = _cget(f"news:{ticker}", NEWS_TTL)
    if hit is not None:
        f = Future()
        f.set_result(hit)
        return f
    with _lock:
        fut = _inflight.get(ticker)
        if fut is not None and not fut.done():
            return fut

        def run():
            try:
                rows = fetch(ticker) or []
                _cset(f"news:{ticker}", rows)
                return rows
            finally:
                with _lock:
                    _inflight.pop(ticker, None)
        fut = _pool.submit(run)
        _inflight[ticker] = fut
        return fut


def tile(ticker: str, stories: list[dict], change_pct=None, now: datetime | None = None) -> dict:
    """Score one ticker's last-24h coverage."""
    from sentiment_engine import score_headline
    now = now or datetime.now(timezone.utc)
    recent = []
    for s in stories or []:
        ts = _parse(s.get("published_at"))
        if ts and now - ts <= timedelta(hours=24):
            recent.append(s)
    weights, tones = 0.0, []
    for s in recent:
        w = _IMPACT_W.get(str(s.get("impact") or "LOW").upper(), 0.6)
        weights += w
        tones.append(score_headline(s.get("headline"), s.get("summary"))["score"] * w)
    tone = round(sum(tones) / weights) if weights else 0
    intensity = min(100, round(weights * 12))
    label = "BULLISH" if tone >= 20 else "BEARISH" if tone <= -20 else "NEUTRAL"
    top = sorted(recent, key=lambda s: -_IMPACT_W.get(str(s.get("impact") or "LOW").upper(), 0.6))[:1]
    return {"ticker": ticker, "stories": len(recent), "intensity": intensity, "tone": tone, "label": label,
            "change_pct": change_pct, "top": (top[0].get("headline") if top else ""),
            "top_url": (top[0].get("url") if top else "") or ""}


def heat_map(tickers: list[str], *, budget: float = 10.0, fetch=None, quotes=None) -> dict:
    """Tiles for up to 30 tickers; anything not fetched within budget is marked pending."""
    tickers = [t.upper() for t in dict.fromkeys(tickers) if t][:30]
    if fetch is None:
        import intel_engine
        fetch = lambda t: intel_engine.fetch_ticker_news(t, limit=12)
    if quotes is None:
        try:
            from market_engine import live_quotes
            quotes = live_quotes(tickers) or {}
        except Exception:
            quotes = {}
    futures = {t: _news_future(t, fetch) for t in tickers}
    wait(list(futures.values()), timeout=budget)
    tiles, pending = [], []
    for t, fut in futures.items():
        chg = _f((quotes.get(t) or {}).get("change_pct"))
        if fut.done() and not fut.exception():
            tiles.append(tile(t, fut.result() or [], chg))
        else:
            pending.append(t)
            tiles.append({"ticker": t, "stories": None, "intensity": 0, "tone": 0, "label": "PENDING",
                          "change_pct": chg, "top": "", "top_url": ""})
    tiles.sort(key=lambda x: (-(x["intensity"] or 0), x["ticker"]))
    return {"ok": True, "tiles": tiles, "pending": pending, "as_of": datetime.now(timezone.utc).isoformat()}


# ── Earnings reaction replay ─────────────────────────────────────────────────

def _finnhub_history(ticker: str) -> list[dict]:
    try:
        from finnhub_ttm import _finnhub_get
        today = date.today()
        data = _finnhub_get("/calendar/earnings", {"symbol": ticker, "from": (today - timedelta(days=3 * 365)).isoformat(),
                                                    "to": today.isoformat()}) or {}
        rows = data.get("earningsCalendar", []) if isinstance(data, dict) else []
        return [{"date": str(r.get("date") or "")[:10], "hour": r.get("hour") or "",
                 "eps": r.get("epsActual"), "eps_est": r.get("epsEstimate")}
                for r in rows if str(r.get("symbol") or "").upper() == ticker]
    except Exception as exc:
        logger.debug("replay finnhub %s: %s", ticker, exc)
        return []


def _fmp_history(ticker: str) -> list[dict]:
    key = os.environ.get("FMP_API_KEY", "").strip()
    if not key:
        return []
    url = f"https://financialmodelingprep.com/stable/earnings?symbol={ticker}&limit=16&apikey={key}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Tradestaar/1.0"}), timeout=8) as r:
            rows = json.loads(r.read().decode()) or []
        out = []
        for row in rows if isinstance(rows, list) else []:
            out.append({"date": str(row.get("date") or "")[:10], "hour": "",
                        "eps": row.get("epsActual", row.get("eps")), "eps_est": row.get("epsEstimated")})
        return out
    except Exception as exc:
        logger.debug("replay fmp %s: %s", ticker, exc)
        return []


def _edgar_hour(accepted: str) -> str:
    """bmo / amc from an EDGAR acceptance time (UTC ISO), '' if it can't tell."""
    try:
        from zoneinfo import ZoneInfo
        ts = datetime.fromisoformat(str(accepted).replace("Z", "+00:00"))
        et = ts.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return ""
    minutes = et.hour * 60 + et.minute
    if minutes < 9 * 60 + 30:
        return "bmo"
    if minutes >= 16 * 60:
        return "amc"
    return ""


def edgar_report_rows(submissions: dict) -> list[dict]:
    """Earnings release dates from SEC 8-K filings carrying Item 2.02
    (Results of Operations). Free, official and years deep — the backstop
    for Finnhub's free plan, which only returns about a month of history."""
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    items = recent.get("items") or []
    accepted = recent.get("acceptanceDateTime") or []
    out = []
    for i in range(min(len(forms), len(dates))):
        if str(forms[i]).strip() not in ("8-K", "8-K/A"):
            continue
        codes = [c.strip() for c in str(items[i] if i < len(items) else "").split(",")]
        if "2.02" not in codes:
            continue
        out.append({"date": str(dates[i])[:10], "hour": _edgar_hour(accepted[i]) if i < len(accepted) else "",
                    "eps": None, "eps_est": None})
    return out


def _edgar_history(ticker: str) -> list[dict]:
    try:
        import fundamentals_engine as fe
        cik, _name = fe._edgar_cik(ticker)
        if not cik:
            return []
        resp = fe._req_module.get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                                  timeout=10, headers=fe._EDGAR_HEADERS)
        if resp.status_code != 200:
            return []
        return edgar_report_rows(resp.json())
    except Exception as exc:
        logger.debug("replay edgar %s: %s", ticker, exc)
        return []


def earnings_dates(ticker: str) -> list[dict]:
    """Past report dates (newest first), merged from Finnhub, FMP and SEC
    8-K Item 2.02 filings. A filing within a day of a date we already have is
    the same report and only fills in a missing time of day."""
    today = date.today().isoformat()
    merged: dict[str, dict] = {}
    for row in _edgar_history(ticker):
        d = row.get("date")
        if not d or d >= today:
            continue
        merged[d] = row
    # EDGAR rows carry no EPS; vendor rows replace an EDGAR row on the same
    # or adjacent day so the surprise column still fills in.
    def _near(d):
        try:
            day = date.fromisoformat(d)
        except ValueError:
            return None
        for k in list(merged):
            try:
                if abs((date.fromisoformat(k) - day).days) <= 1 and merged[k].get("eps") is None \
                        and merged[k].get("eps_est") is None and k != d:
                    return k
            except ValueError:
                continue
        return None
    for row in _finnhub_history(ticker) + _fmp_history(ticker):
        d = row.get("date")
        if d and d < today and d not in merged:
            k = _near(d)
            if k:
                edgar = merged.pop(k)
                row = {**row, "hour": row.get("hour") or edgar.get("hour") or ""}
        if not d or d >= today or (row.get("eps") is None and d in merged):
            continue
        prev = merged.get(d, {})
        merged[d] = {"date": d, "hour": row.get("hour") or prev.get("hour") or "",
                     "eps": row.get("eps") if row.get("eps") is not None else prev.get("eps"),
                     "eps_est": row.get("eps_est") if row.get("eps_est") is not None else prev.get("eps_est")}
    return sorted(merged.values(), key=lambda r: r["date"], reverse=True)


def _bar_day(bar) -> str:
    t = bar.get("time")
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
    return str(t)[:10]


def reaction(bars: list[dict], report_date: str, hour: str = "") -> dict | None:
    """One report's reaction from daily bars. hour: bmo / amc / '' (guess)."""
    days = [_bar_day(b) for b in bars]
    idx = next((i for i, d in enumerate(days) if d >= report_date), None)
    if idx is None:
        return None
    candidates = []
    if hour in ("bmo", "dmh"):
        candidates = [idx if days[idx] == report_date else None]
    elif hour == "amc":
        candidates = [idx + 1 if days[idx] == report_date else idx]
    else:  # unknown: whichever of report day / next day moved more
        candidates = [idx, idx + 1] if days[idx] == report_date else [idx]
    best = None
    for r in candidates:
        if r is None or r < 1 or r >= len(bars):
            continue
        prev_close = bars[r - 1]["close"]
        b = bars[r]
        gap = (b["open"] / prev_close - 1) * 100
        day = (b["close"] / prev_close - 1) * 100
        end = bars[min(r + 4, len(bars) - 1)]
        five = (end["close"] / prev_close - 1) * 100
        window = bars[r:r + 5]
        filled = (gap > 0 and min(x["low"] for x in window) <= prev_close) or \
                 (gap < 0 and max(x["high"] for x in window) >= prev_close)
        out = {"reaction_day": days[r], "gap_pct": round(gap, 2), "day_pct": round(day, 2),
               "five_day_pct": round(five, 2), "gap_filled": bool(filled),
               "held": (gap > 0 and day > 0) or (gap < 0 and day < 0)}
        if best is None or abs(out["day_pct"]) > abs(best["day_pct"]):
            best = out
    return best


def earnings_replay(ticker: str, *, dates=None, bars=None) -> dict:
    ticker = ticker.upper()
    cached = _cget(f"replay:{ticker}", 6 * 3600) if dates is None and bars is None else None
    if cached:
        return cached
    dates = earnings_dates(ticker) if dates is None else dates
    if bars is None:
        try:
            from market_data import fetch_chart_bars
            from terminal_intelligence import normalize_ohlcv_data
            data, _ = fetch_chart_bars(ticker, interval="1d", range_str="5y")
            bars = normalize_ohlcv_data(data)
        except Exception:
            bars = []
    rows = []
    for d in dates:
        if len(rows) >= 8:
            break
        r = reaction(bars, d["date"], d.get("hour") or "")
        if r:
            surprise = None
            if _f(d.get("eps")) is not None and _f(d.get("eps_est")):
                surprise = round((_f(d["eps"]) - _f(d["eps_est"])) / abs(_f(d["eps_est"])) * 100, 1)
            rows.append({"date": d["date"], "eps": d.get("eps"), "eps_est": d.get("eps_est"), "surprise_pct": surprise, **r})
    out = {"ok": bool(rows), "ticker": ticker, "reports": rows}
    if rows:
        n = len(rows)
        out["summary"] = {
            "reports": n,
            "avg_abs_move": round(sum(abs(r["day_pct"]) for r in rows) / n, 2),
            "avg_gap": round(sum(r["gap_pct"] for r in rows) / n, 2),
            "up_days": sum(1 for r in rows if r["day_pct"] > 0),
            "gap_fill_rate": round(100 * sum(1 for r in rows if r["gap_filled"]) / n),
            "hold_rate": round(100 * sum(1 for r in rows if r["held"]) / n),
            "avg_five_day": round(sum(r["five_day_pct"] for r in rows) / n, 2),
        }
    else:
        out["error"] = "No past earnings dates with price history were found."
    if dates is not None and bars is not None:
        return out
    if rows:
        _cset(f"replay:{ticker}", out)
    return out


# ── Explain this move ────────────────────────────────────────────────────────

def _move_facts(ticker: str) -> dict:
    from market_engine import get_sector_for_ticker, live_quotes
    etf, sector = get_sector_for_ticker(ticker)
    snap = {}
    try:
        from database import get_stock_data
        snap = get_stock_data(ticker) or {}
        etf = etf or snap.get("sector_etf") or ""
    except Exception:
        pass
    syms = [s for s in (ticker, "SPY", etf) if s]
    q = live_quotes(syms) or {}
    facts = {"ticker": ticker, "change_pct": _f((q.get(ticker) or {}).get("change_pct")) or _f(snap.get("gap_pct")),
             "price": _f((q.get(ticker) or {}).get("price")) or _f(snap.get("current_price")),
             "spy_pct": _f((q.get("SPY") or {}).get("change_pct")), "sector_etf": etf, "sector": sector,
             "sector_pct": _f((q.get(etf) or {}).get("change_pct")) if etf else None,
             "rel_volume": _f(snap.get("rel_volume"))}
    try:
        import intel_engine
        news = intel_engine.fetch_ticker_news(ticker, limit=6)
    except Exception:
        news = []
    now = datetime.now(timezone.utc)
    facts["news"] = [{"headline": n.get("headline"), "source": n.get("source"), "time": n.get("time"), "url": n.get("url") or ""}
                     for n in news if (_parse(n.get("published_at")) or now) >= now - timedelta(hours=36)][:4]
    return facts


def rule_explanation(f: dict) -> dict:
    chg = f.get("change_pct")
    if chg is None:
        return {"headline": f"No live move data for {f['ticker']} right now.", "detail": "", "driver": "unknown"}
    parts = [f"{f['ticker']} {chg:+.1f}%"]
    if f.get("rel_volume"):
        parts.append(f"on {f['rel_volume']:.1f}× volume")
    spy, sec = f.get("spy_pct"), f.get("sector_pct")
    ctx = []
    if spy is not None:
        ctx.append(f"SPY {spy:+.1f}%")
    if sec is not None and f.get("sector_etf"):
        ctx.append(f"{f['sector_etf']} {sec:+.1f}%")
    own = chg - (sec if sec is not None else (spy or 0))
    same_dir = sec is not None and sec * chg > 0 and abs(sec) >= 0.5 * abs(chg)
    if same_dir:
        driver = "sector"
        why = f"Mostly sector-wide — {f['sector'] or f['sector_etf']} is moving the same way."
    elif f.get("news"):
        driver = "news"
        why = f"Stock-specific ({own:+.1f}% vs its sector) — likely the news: “{f['news'][0]['headline']}”."
    elif spy is not None and abs(spy) >= 1 and spy * chg > 0:
        driver = "market"
        why = "Moving with the whole market."
    else:
        driver = "flow"
        why = "Stock-specific with no fresh headline found — likely flows, positioning or a ratings/options catalyst."
    return {"headline": " ".join(parts) + (f" ({', '.join(ctx)})" if ctx else "") + ".", "detail": why, "driver": driver}


def explain_move(ticker: str, *, facts=None, ask=None) -> dict:
    ticker = ticker.upper()
    if facts is None:
        cached = _cget(f"explain:{ticker}", 300)
        if cached:
            return cached
        facts = _move_facts(ticker)
    base = rule_explanation(facts)
    out = {"ok": True, "ticker": ticker, "facts": facts, **base, "ai": False}
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if (api_key or ask) and facts.get("change_pct") is not None and _ai_slots.acquire(blocking=False):
      try:
        prompt = (f"Facts about {ticker} today (use ONLY these):\n{json.dumps({k: v for k, v in facts.items()}, default=str)}\n\n"
                  "In exactly two short lines, explain why the stock is moving. Line 1: the move and the main driver "
                  "(company news, sector, whole market, or no clear catalyst). Line 2: what a trader should watch next. "
                  "If the facts don't show a cause, say so. No made-up facts. Plain text.")
        try:
            text = (ask or _ask)(prompt, api_key)
            lines = [l.strip(" -•") for l in str(text).strip().splitlines() if l.strip()][:2]
            if lines:
                out.update({"ai_lines": lines, "ai": True})
        except Exception as exc:
            logger.debug("explain %s ai failed: %s", ticker, exc)
      finally:
        _ai_slots.release()
    if facts is not None and ask is None:
        _cset(f"explain:{ticker}", out)
    return out


def _ask(prompt: str, api_key: str) -> str:
    import anthropic
    model = os.environ.get("ANTHROPIC_NEWS_MODEL", "claude-sonnet-4-6")
    client = anthropic.Anthropic(api_key=api_key, timeout=20, max_retries=0)
    resp = client.messages.create(model=model, max_tokens=200, temperature=0.2,
                                  messages=[{"role": "user", "content": prompt}])
    return "".join(getattr(b, "text", "") for b in resp.content)
