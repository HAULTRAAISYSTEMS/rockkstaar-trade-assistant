"""Quarterly figures from Yahoo Finance, the backup behind the FMP backup.

SK hynix files no 10-Q, and FMP's free plan does not cover its statements
(quarterly or annual). Yahoo Finance publishes them under the home listing
(000660.KS) at no cost, via the yfinance library the app already uses for
prices. The walkthrough payload has the same shape as the SEC and FMP paths.

Yahoo keeps about five quarters, so the year-ago column is the quarter whose
end date falls about a year earlier; when it is missing, that column stays
blank rather than being guessed. Figures stay in the reporting currency; the
P/E uses the US listing's own price and P/E so neither the exchange rate nor
the ADR ratio is needed.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import date, datetime

logger = logging.getLogger(__name__)

_CACHE: dict = {}
_LOCK = threading.Lock()
_TTL_S = 6 * 3600
_LAST_FAIL: dict = {}


def _ticker(symbol):
    import yfinance as yf
    return yf.Ticker(symbol)


def last_failure(ticker: str) -> str | None:
    return _LAST_FAIL.get(ticker.upper())


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _norm(s):
    return str(s).lower().replace(" ", "").replace("_", "")


def _row(df, *labels):
    """The first matching row of a Yahoo statement, exact label first."""
    if df is None or getattr(df, "empty", True):
        return None
    index = list(df.index)
    for label in labels:
        if label in index:
            return df.loc[label]
    normed = {_norm(k): k for k in index}
    for label in labels:
        k = normed.get(_norm(label))
        if k is not None:
            return df.loc[k]
    return None


def _val(df, col, *labels):
    r = _row(df, *labels)
    if r is None or col is None:
        return None
    try:
        return _num(r[col])
    except (KeyError, IndexError):
        return None


def _day(col):
    if isinstance(col, (datetime,)):
        return col.date()
    if isinstance(col, date):
        return col
    try:
        return datetime.strptime(str(col)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _cols(df):
    """Statement columns, newest first, as (column, date) pairs."""
    if df is None or getattr(df, "empty", True):
        return []
    pairs = [(c, _day(c)) for c in df.columns]
    pairs = [p for p in pairs if p[1]]
    pairs.sort(key=lambda p: p[1], reverse=True)
    return pairs


def _near(pairs, target, slack=20):
    best = None
    for c, d in pairs:
        gap = abs((d - target).days)
        if gap <= slack and (best is None or gap < best[0]):
            best = (gap, c, d)
    return (best[1], best[2]) if best else (None, None)


REV = ("Total Revenue", "Operating Revenue")
COGS = ("Cost Of Revenue", "Reconciled Cost Of Revenue")
OPEX = ("Operating Expense", "Total Operating Expenses")
OPINC = ("Operating Income", "Total Operating Income As Reported")
NI = ("Net Income", "Net Income Common Stockholders", "Net Income From Continuing Operation Net Minority Interest")
SH = ("Diluted Average Shares",)
EPS = ("Diluted EPS",)


def walkthrough(ticker: str) -> dict | None:
    ticker = ticker.upper()
    with _LOCK:
        hit = _CACHE.get(ticker)
        if hit and time.time() - hit[0] < _TTL_S:
            return hit[1]
    try:
        out = _build(ticker)
    except Exception as exc:
        logger.warning("Yahoo quarter fallback failed for %s: %s", ticker, exc)
        _LAST_FAIL[ticker] = "Yahoo Finance could not be reached."
        out = None
    with _LOCK:
        _CACHE[ticker] = (time.time() - (_TTL_S - 900 if out is None else 0), out)
    return out


def _build(ticker):
    from quarter_fmp import HOME_SYMBOLS
    symbols = [ticker] + HOME_SYMBOLS.get(ticker, [])
    t = inc = None
    sym = ticker
    for candidate in symbols:
        tk = _ticker(candidate)
        try:
            frame = tk.quarterly_income_stmt
        except Exception:
            frame = None
        if frame is not None and not frame.empty and _row(frame, *REV) is not None:
            t, inc, sym = tk, frame, candidate
            break
    if inc is None:
        _LAST_FAIL[ticker] = "Yahoo Finance has no quarterly statements for this ticker either."
        return None
    _LAST_FAIL.pop(ticker, None)

    qcols = _cols(inc)
    cur, cur_day = qcols[0]
    year_ago = date(cur_day.year - 1, cur_day.month, min(cur_day.day, 28))
    ago, ago_day = _near(qcols[1:], year_ago)

    f = {}
    for key, labels in (("rev", REV), ("cogs", COGS), ("opex", OPEX), ("opinc", OPINC),
                        ("ni", NI), ("sh", SH), ("eps", EPS)):
        f[key] = _val(inc, cur, *labels)
        f[key + "P"] = _val(inc, ago, *labels)

    # Balance sheet: now, and the previous fiscal year end.
    try:
        bal = t.quarterly_balance_sheet
    except Exception:
        bal = None
    bcols = _cols(bal)
    fy_month = _fiscal_year_end_month(t) or 12
    b_now, _ = _near(bcols, cur_day) if bcols else (None, None)
    b_prior = None
    for c, d in bcols:
        if d < cur_day and d.month == fy_month:
            b_prior = c
            break
    for key, labels in (("ca", ("Current Assets",)), ("cl", ("Current Liabilities",)),
                        ("ar", ("Accounts Receivable", "Receivables"))):
        f[key] = _val(bal, b_now, *labels)
        f[key + "P"] = _val(bal, b_prior, *labels)

    # Cash flow over the fiscal year to date, matched with income over the
    # same quarters.
    try:
        cf = t.quarterly_cashflow
    except Exception:
        cf = None
    ccols = dict((d, c) for c, d in _cols(cf))
    ytd = [(c, d) for c, d in qcols if d <= cur_day and _same_fiscal_year(d, cur_day, fy_month)]
    if ytd and all(d in ccols for _c, d in ytd):
        cfo = [_val(cf, ccols[d], "Operating Cash Flow") for _c, d in ytd]
        capex = [_val(cf, ccols[d], "Capital Expenditure") for _c, d in ytd]
        ni = [_val(inc, c, *NI) for c, _d in ytd]
        rev = [_val(inc, c, *REV) for c, _d in ytd]
        if None not in cfo:
            f["cfo"] = sum(cfo)
        if None not in capex:
            f["capex"] = sum(abs(x) for x in capex)
        if None not in ni:
            f["niy"] = sum(ni)
        if None not in rev:
            f["revYtd"] = sum(rev)
    figures = {k: v for k, v in f.items() if v is not None}

    currency = "USD"
    name = sym
    try:
        info = t.info or {}
        currency = (info.get("financialCurrency") or info.get("currency") or "USD").upper()
        name = info.get("longName") or info.get("shortName") or sym
    except Exception:
        pass

    ttm, ttm_prior = _ttm(ticker, t, inc, currency)

    balance_rows = []
    for key, label, labels in (("ltd", "Long-term debt", ("Long Term Debt",)),
                               ("equity", "Total stockholders' equity", ("Stockholders Equity", "Common Stock Equity"))):
        v = _val(bal, b_now, *labels)
        if v is not None:
            balance_rows.append({"key": key, "label": label, "indent": 0,
                                 "now": v, "prior": _val(bal, b_prior, *labels)})

    import quarter_facts
    return {
        "ticker": ticker,
        "company": name,
        "profile": quarter_facts.context_for(None),
        "period_end": cur_day.isoformat(),
        "prior_end": ago_day.isoformat() if ago_day else None,
        "form": "quarterly report (via Yahoo Finance)",
        "filed_on": None,
        "figures": figures,
        "statements": {"balance": {"title": "Balance sheet", "sub": currency,
                                   "rows": balance_rows}} if balance_rows else None,
        "filing_url": None,
        "split": None,
        "ttm_eps": ttm,
        "ttm_eps_prior": ttm_prior,
        "source": "yahoo",
        "source_symbol": sym,
        "basis": "quarter",
        "currency": currency,
    }


def _fiscal_year_end_month(t):
    try:
        cols = _cols(t.income_stmt)
        return cols[0][1].month if cols else None
    except Exception:
        return None


def _same_fiscal_year(d, cur, fy_month):
    def fy(x):
        return x.year if x.month <= fy_month else x.year + 1
    return fy(d) == fy(cur)


def _ttm(ticker, t, inc, currency):
    """Trailing EPS per US-listed share in USD, and last year's on that basis.

    The P/E comes from the US listing (Yahoo first, then FMP's quote). The
    growth for PEG comes from the company's own reported EPS: eight quarters
    when Yahoo has them, otherwise the last two fiscal years.
    """
    pe = price = None
    try:
        us = _ticker(ticker).info or {}
        pe = _num(us.get("trailingPE"))
        price = _num(us.get("currentPrice") or us.get("regularMarketPrice"))
    except Exception:
        pass
    if not (pe and price):
        try:
            from quarter_fmp import _pe_usd
            price, pe = _pe_usd(ticker)
        except Exception:
            price, pe = None, None
    if not price or not pe or pe <= 0:
        return None, None
    eps_usd = price / pe

    growth = None
    qcols = _cols(inc)
    q = [_val(inc, c, *EPS) for c, _d in qcols[:8]]
    if len(q) == 8 and None not in q and sum(q[4:]) > 0:
        growth = sum(q[:4]) / sum(q[4:]) - 1
    else:
        try:
            ann = t.income_stmt
            acols = _cols(ann)
            e = [_val(ann, c, *EPS) for c, _d in acols[:2]]
            if len(e) == 2 and None not in e and e[1] > 0:
                growth = e[0] / e[1] - 1
        except Exception:
            pass
    note = "" if currency == "USD" else f"; the company reports in {currency}"
    ttm = {"value": round(eps_usd, 4), "quarters": [],
           "method": f"trailing EPS per US-listed share in USD, from the US listing's P/E{note}"}
    prior = None
    if growth is not None and growth > -1:
        prior = {"value": round(eps_usd / (1 + growth), 4), "quarters": [],
                 "method": "last year's EPS on the same basis, from the company's own reported EPS growth"}
    return ttm, prior
