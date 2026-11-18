"""Quarterly figures for companies the SEC has no 10-Q for, from FMP.

Foreign issuers — TSMC, ASML, SK hynix — file a 20-F once a year and 6-Ks in
between, with no tagged quarterly numbers. Financial Modeling Prep publishes
their quarterly statements, so when EDGAR has nothing the drill fills from
there instead, says so, and keeps the same shape of answer the SEC path gives.

Two things differ and both are handled here rather than in the page:

  Currency. TSMC reports in New Taiwan dollars. Margins, growth and every
  ratio are currency-free, so the figures are passed through as reported and
  labelled with the currency. Only the P/E needs a US-dollar EPS.

  Share basis. One TSM share in New York (an ADR) is five Taiwan shares. The
  P/E is taken from FMP's own figure for the US listing, and the trailing EPS
  backed out of it, so neither the exchange rate nor the ADR ratio has to be
  known here. PEG's growth rate comes from the company's own reported EPS,
  where both years share a currency and a share basis.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_CACHE: dict = {}
_LOCK = threading.Lock()
_TTL_S = 6 * 3600


# The reason the last FMP call for each endpoint came back empty, so the page
# can say "your FMP plan doesn't include quarterly statements" instead of a
# blank "nothing to read". Written by _get_detail, read by _build.
_LAST_REASON: dict = {}
_LAST_FAIL: dict = {}


def _get_detail(endpoint, params=None, ticker=""):
    """(data, reason) from FMP. reason is None on success.

    Mirrors fundamentals_engine._fmp_get — /stable/ first, then /api/v3/ —
    but keeps what went wrong: a plan that does not cover the request (402 or
    403), a bad key (401), FMP's own error message, or simply no rows.
    """
    import fundamentals_engine as fe
    key = fe._FMP_API_KEY
    if not key:
        return None, "FMP_API_KEY is not set on the server."
    reasons = []
    for base in (fe._FMP_BASE_STABLE, fe._FMP_BASE_V3):
        if base == fe._FMP_BASE_V3 and ticker:
            url, p = f"{base}/{endpoint}/{ticker}", {"apikey": key, **(params or {})}
        else:
            url, p = f"{base}/{endpoint}", {"apikey": key, **({"symbol": ticker} if ticker else {}), **(params or {})}
        try:
            resp = fe._req_module.get(url, params=p, timeout=15)
        except Exception as exc:
            reasons.append(f"network error ({type(exc).__name__})")
            continue
        if resp.status_code in (402, 403):
            reasons.append("your FMP plan does not include this data")
            continue
        if resp.status_code == 401:
            return None, "FMP rejected the API key"
        if resp.status_code != 200:
            reasons.append(f"FMP returned HTTP {resp.status_code}")
            continue
        try:
            data = resp.json()
        except ValueError:
            text = (resp.text or "")[:160]
            reasons.append("your FMP plan does not include this data"
                           if "premium" in text.lower() or "subscription" in text.lower()
                           else "FMP sent an unreadable reply")
            continue
        if isinstance(data, dict) and ("Error Message" in data or "error" in data):
            msg = str(data.get("Error Message") or data.get("error"))
            reasons.append("your FMP plan does not include this data"
                           if "premium" in msg.lower() or "subscription" in msg.lower()
                           or "exclusive" in msg.lower() else f"FMP: {msg[:120]}")
            continue
        if isinstance(data, list) and not data:
            reasons.append("FMP has no rows for this symbol")
            continue
        return data, None
    # Prefer the most informative reason
    for pref in ("plan", "key", "HTTP", "FMP:", "no rows", "network"):
        for r in reasons:
            if pref in r:
                return None, r
    return None, reasons[0] if reasons else "no data"


def _get(endpoint, params=None, ticker=""):
    data, reason = _get_detail(endpoint, params, ticker)
    _LAST_REASON[(endpoint, (params or {}).get("period"), ticker)] = reason
    return data


def last_failure(ticker: str) -> str | None:
    """Why the last walkthrough for this ticker found nothing, in plain words."""
    return _LAST_FAIL.get(ticker.upper())


# US tickers whose company statements FMP files under the home-market
# listing. The US symbol is still used for the P/E, which must be in dollars
# per US share.
HOME_SYMBOLS = {
    "SKHY": ["000660.KS"],
    "TSM": ["2330.TW"],
    "ASML": ["ASML.AS"],
    "SONY": ["6758.T"],
    "TM": ["7203.T"],
}


def _rows(data):
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _f(row, *keys):
    for k in keys:
        v = (row or {}).get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _year(row):
    return str(row.get("fiscalYear") or row.get("calendarYear") or (row.get("date") or "")[:4])


def _same_quarter_last_year(rows, cur):
    """The row a year before `cur`: same fiscal quarter label, previous year."""
    try:
        want = str(int(_year(cur)) - 1)
    except ValueError:
        return rows[4] if len(rows) > 4 else None
    for r in rows[1:]:
        if r.get("period") == cur.get("period") and _year(r) == want:
            return r
    return rows[4] if len(rows) > 4 else None


def _year_to_date(rows, cur):
    """This fiscal year's quarters up to and including `cur`, newest first."""
    out = []
    for r in rows:
        if _year(r) != _year(cur):
            break
        out.append(r)
    return out or [cur]


def _sum(rows, *keys, absolute=False):
    vals = [_f(r, *keys) for r in rows]
    if not vals or any(v is None for v in vals):
        return None
    total = sum(abs(v) if absolute else v for v in vals)
    return total


def _prior_year_end_row(rows, cur):
    """The balance sheet at the previous fiscal year end (last year's Q4)."""
    try:
        want = str(int(_year(cur)) - 1)
    except ValueError:
        return None
    for r in rows:
        if _year(r) == want and r.get("period") in ("Q4", "FY"):
            return r
    return None


def _pe_usd(ticker):
    """FMP's P/E for the US listing, and the price it was computed at."""
    q = _rows(_get("quote", ticker=ticker))
    price = _f(q[0], "price") if q else None
    pe = _f(q[0], "pe") if q else None
    if not pe:
        r = _rows(_get("ratios-ttm", ticker=ticker))
        if r:
            pe = _f(r[0], "priceToEarningsRatioTTM", "peRatioTTM", "priceEarningsRatioTTM")
    return price, pe


def walkthrough(ticker: str) -> dict | None:
    """The drill's walkthrough payload, from FMP. None if FMP has no quarters."""
    ticker = ticker.upper()
    with _LOCK:
        hit = _CACHE.get(ticker)
        if hit and time.time() - hit[0] < _TTL_S:
            return hit[1]
    try:
        out = _build(ticker)
    except Exception as exc:
        logger.warning("FMP quarter fallback failed for %s: %s", ticker, exc)
        out = None
    with _LOCK:
        # A miss is often a plan limit or a passing outage: remember it for
        # 15 minutes, not 6 hours.
        _CACHE[ticker] = (time.time() - (_TTL_S - 900 if out is None else 0), out)
    return out


def _build(ticker):
    symbols = [ticker] + HOME_SYMBOLS.get(ticker, [])
    inc, sym, period, reasons = [], ticker, "quarter", []
    for per, limit in (("quarter", 12), ("annual", 4)):
        for candidate in symbols:
            rows = _rows(_get("income-statement", {"period": per, "limit": limit}, ticker=candidate))
            if rows:
                inc, sym, period = rows, candidate, per
                break
            r = _LAST_REASON.get(("income-statement", per, candidate))
            if r:
                reasons.append((per, r))
        if inc:
            break
    if not inc:
        plan = [r for per, r in reasons if "plan" in r]
        if plan:
            _LAST_FAIL[ticker] = ("FMP has this company, but " + plan[0] +
                                  " (quarterly statements are a paid FMP feature).")
        elif reasons:
            _LAST_FAIL[ticker] = "FMP: " + reasons[-1][1] + "."
        else:
            _LAST_FAIL[ticker] = "FMP returned nothing for this ticker."
        return None
    _LAST_FAIL.pop(ticker, None)
    annual = period == "annual"
    bal = _rows(_get("balance-sheet-statement", {"period": period, "limit": 8}, ticker=sym))
    cf = _rows(_get("cash-flow-statement", {"period": period, "limit": 8}, ticker=sym))

    cur = inc[0]
    end = cur.get("date")
    ago = (inc[1] if len(inc) > 1 else {}) if annual else (_same_quarter_last_year(inc, cur) or {})
    eps_keys = ("epsDiluted", "epsdiluted", "eps")
    figures = {
        "rev": _f(cur, "revenue"), "revP": _f(ago, "revenue"),
        "cogs": _f(cur, "costOfRevenue"), "cogsP": _f(ago, "costOfRevenue"),
        "opex": _f(cur, "operatingExpenses"), "opexP": _f(ago, "operatingExpenses"),
        "opinc": _f(cur, "operatingIncome"), "opincP": _f(ago, "operatingIncome"),
        "ni": _f(cur, "netIncome"), "niP": _f(ago, "netIncome"),
        "sh": _f(cur, "weightedAverageShsOutDil"), "shP": _f(ago, "weightedAverageShsOutDil"),
        "eps": _f(cur, *eps_keys), "epsP": _f(ago, *eps_keys),
    }

    b_now = next((b for b in bal if b.get("date") == end), bal[0] if bal else None)
    if b_now:
        b_prior = ((bal[1] if len(bal) > 1 else {}) if annual
                   else _prior_year_end_row(bal, b_now)) or {}
        figures.update({
            "ca": _f(b_now, "totalCurrentAssets"), "caP": _f(b_prior, "totalCurrentAssets"),
            "cl": _f(b_now, "totalCurrentLiabilities"), "clP": _f(b_prior, "totalCurrentLiabilities"),
            "ar": _f(b_now, "accountsReceivables", "netReceivables"),
            "arP": _f(b_prior, "accountsReceivables", "netReceivables"),
        })

    # Cash flow over the fiscal year to date, matched with income over the
    # same quarters — the drill pairs them, so they must share a span.
    ytd_inc = [cur] if annual else _year_to_date(inc, cur)
    ends = {r.get("date") for r in ytd_inc}
    ytd_cf = [r for r in cf if r.get("date") in ends]
    if len(ytd_cf) == len(ytd_inc):
        figures.update({
            "cfo": _sum(ytd_cf, "operatingCashFlow", "netCashProvidedByOperatingActivities"),
            "capex": _sum(ytd_cf, "capitalExpenditure", absolute=True),
            "niy": _sum(ytd_inc, "netIncome"),
            "revYtd": _sum(ytd_inc, "revenue"),
        })
    figures = {k: v for k, v in figures.items() if v is not None}

    currency = (cur.get("reportedCurrency") or "USD").upper()
    ttm, ttm_prior = _ttm(ticker, inc, currency, annual=annual)

    balance_rows = []
    if b_now:
        b_prior = ((bal[1] if len(bal) > 1 else {}) if annual
                   else _prior_year_end_row(bal, b_now)) or {}
        for key, label, field in (("ltd", "Long-term debt", "longTermDebt"),
                                  ("equity", "Total stockholders' equity", "totalStockholdersEquity")):
            v = _f(b_now, field)
            if v is not None:
                balance_rows.append({"key": key, "label": label, "indent": 0,
                                     "now": v, "prior": _f(b_prior, field)})

    name = None
    prof = _rows(_get("profile", ticker=ticker)) or _rows(_get("profile", ticker=sym))
    if prof:
        name = prof[0].get("companyName")
    import quarter_facts
    return {
        "ticker": ticker,
        "company": name or ticker,
        "profile": quarter_facts.context_for(None),
        "period_end": end,
        "prior_end": ago.get("date"),
        "form": (f"fiscal {_year(cur)} annual report (via FMP — quarterly statements "
                 f"are not available on this FMP plan, so both columns are full years)")
                if annual else f"{cur.get('period') or 'quarterly'} {_year(cur)} report (via FMP)",
        "basis": "annual" if annual else "quarter",
        "fmp_symbol": sym,
        "filed_on": cur.get("filingDate") or cur.get("fillingDate"),
        "figures": figures,
        "statements": {"balance": {"title": "Balance sheet", "sub": currency,
                                   "rows": balance_rows}} if balance_rows else None,
        "filing_url": None,
        "split": None,
        "ttm_eps": ttm,
        "ttm_eps_prior": ttm_prior,
        "source": "fmp",
        "currency": currency,
    }


def _ttm(ticker, inc, currency, annual=False):
    """Trailing EPS per US-listed share, and last year's on the same basis."""
    eps_keys = ("epsDiluted", "epsdiluted", "eps")
    growth = None
    if annual:
        e = [_f(r, *eps_keys) for r in inc[:2]]
        if len(e) == 2 and None not in e and e[1] > 0:
            growth = e[0] / e[1] - 1
    else:
        last8 = [_f(r, *eps_keys) for r in inc[:8]]
        if len(last8) == 8 and all(v is not None for v in last8):
            now, before = sum(last8[:4]), sum(last8[4:])
            if before > 0:
                growth = now / before - 1

    price, pe = _pe_usd(ticker)
    if not price or not pe or pe <= 0:
        return None, None
    eps_usd = price / pe
    note = "" if currency == "USD" else f"; the company reports in {currency}"
    ttm = {"value": round(eps_usd, 4), "quarters": [],
           "method": f"trailing EPS per US-listed share in USD, from FMP's P/E{note}"}
    prior = None
    if growth is not None and growth > -1:
        prior = {"value": round(eps_usd / (1 + growth), 4), "quarters": [],
                 "method": "last year's trailing EPS on the same basis, from the "
                           "company's own reported EPS growth"}
    return ttm, prior
