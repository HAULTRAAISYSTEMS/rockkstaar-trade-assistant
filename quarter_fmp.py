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


def _get(endpoint, params=None, ticker=""):
    import fundamentals_engine as fe
    return fe._fmp_get(endpoint, params, ticker=ticker)


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
        _CACHE[ticker] = (time.time(), out)
    return out


def _build(ticker):
    inc = _rows(_get("income-statement", {"period": "quarter", "limit": 12}, ticker=ticker))
    if not inc:
        return None
    bal = _rows(_get("balance-sheet-statement", {"period": "quarter", "limit": 8}, ticker=ticker))
    cf = _rows(_get("cash-flow-statement", {"period": "quarter", "limit": 8}, ticker=ticker))

    cur = inc[0]
    end = cur.get("date")
    ago = _same_quarter_last_year(inc, cur) or {}
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
        b_prior = _prior_year_end_row(bal, b_now) or {}
        figures.update({
            "ca": _f(b_now, "totalCurrentAssets"), "caP": _f(b_prior, "totalCurrentAssets"),
            "cl": _f(b_now, "totalCurrentLiabilities"), "clP": _f(b_prior, "totalCurrentLiabilities"),
            "ar": _f(b_now, "accountsReceivables", "netReceivables"),
            "arP": _f(b_prior, "accountsReceivables", "netReceivables"),
        })

    # Cash flow over the fiscal year to date, matched with income over the
    # same quarters — the drill pairs them, so they must share a span.
    ytd_inc = _year_to_date(inc, cur)
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
    ttm, ttm_prior = _ttm(ticker, inc, currency)

    balance_rows = []
    if b_now:
        b_prior = _prior_year_end_row(bal, b_now) or {}
        for key, label, field in (("ltd", "Long-term debt", "longTermDebt"),
                                  ("equity", "Total stockholders' equity", "totalStockholdersEquity")):
            v = _f(b_now, field)
            if v is not None:
                balance_rows.append({"key": key, "label": label, "indent": 0,
                                     "now": v, "prior": _f(b_prior, field)})

    name = None
    prof = _rows(_get("profile", ticker=ticker))
    if prof:
        name = prof[0].get("companyName")
    import quarter_facts
    return {
        "ticker": ticker,
        "company": name or ticker,
        "profile": quarter_facts.context_for(None),
        "period_end": end,
        "prior_end": ago.get("date"),
        "form": f"{cur.get('period') or 'quarterly'} {_year(cur)} report (via FMP)",
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


def _ttm(ticker, inc, currency):
    """Trailing EPS per US-listed share, and last year's on the same basis."""
    eps_keys = ("epsDiluted", "epsdiluted", "eps")
    last8 = [_f(r, *eps_keys) for r in inc[:8]]
    growth = None
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
