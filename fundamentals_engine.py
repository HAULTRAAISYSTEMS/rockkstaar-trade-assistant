"""
fundamentals_engine.py — Fundamental quality scorecard for Rockkstaar Trade Assistant.

Fetches balance sheet, income statement, and cash flow data via yfinance,
scores each metric on a 40-point scale (4 sections × 10 pts), detects red flags,
and returns structured data ready for the template.

Caches results in SQLite/PostgreSQL for 24 hours to avoid hammering Yahoo Finance.
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# ─── Education blurbs ────────────────────────────────────────────────────────
# Each entry: {"def": plain-English definition, "why": why it matters, "formula": formula string}

EDUCATION: dict[str, dict[str, str]] = {
    "current_ratio": {
        "def": "Measures whether a company can pay its short-term bills using its short-term assets.",
        "why": "Above 1.5 is healthy — the company has a comfortable cushion. Below 1.0 means current liabilities exceed liquid assets, which is a stress signal.",
        "formula": "Current Assets ÷ Current Liabilities",
    },
    "debt_to_equity": {
        "def": "Shows how much of the company is financed by debt vs. shareholder equity.",
        "why": "Below 1.0 means the company relies more on equity than debt. High D/E amplifies both gains and losses — fine in stable businesses, risky in cyclical ones.",
        "formula": "Total Debt ÷ Total Shareholders' Equity",
    },
    "cash_covers_debt": {
        "def": "Checks if the company's cash on hand could pay off its entire debt load in under a year.",
        "why": "A company with enough cash to wipe out its debt overnight is nearly impossible to bankrupt in the short term. This is a core safety signal.",
        "formula": "Cash & Equivalents ≥ Total Debt",
    },
    "retained_earnings_growth": {
        "def": "Retained earnings are the cumulative profits the company has kept (not paid out as dividends) over its life.",
        "why": "Growing retained earnings means the business is compounding wealth over time. Shrinking retained earnings often signal persistent losses or excessive buybacks funded by debt.",
        "formula": "Retained Earnings (Year N) > Retained Earnings (Year N−1)",
    },
    "goodwill_ratio": {
        "def": "Goodwill is the premium paid above fair value in acquisitions. Intangibles are non-physical assets (patents, brand value).",
        "why": "When goodwill + intangibles exceed 30% of total assets, a write-down (impairment) can vaporize earnings overnight. High goodwill is a risk hidden on the balance sheet.",
        "formula": "(Goodwill + Intangible Assets) ÷ Total Assets",
    },
    "revenue_growth": {
        "def": "Whether revenue (the top line) is growing year over year for 3+ consecutive years.",
        "why": "Consistent revenue growth shows the business has real demand. A single good year can be luck; three consecutive years is a trend.",
        "formula": "Revenue(Year N) > Revenue(Year N−1) for 3+ years",
    },
    "gross_margin": {
        "def": "The percentage of revenue left after subtracting the direct cost of making the product or delivering the service.",
        "why": "Gross margin reveals pricing power and production efficiency. A stable or rising margin means the company isn't sacrificing profit to grow.",
        "formula": "(Revenue − Cost of Goods Sold) ÷ Revenue × 100",
    },
    "operating_margin": {
        "def": "Profit as a percentage of revenue after paying all operating expenses (salaries, rent, R&D) but before interest and taxes.",
        "why": "Operating margin shows how well management runs the core business. A rising margin is a signal of improving efficiency or pricing power.",
        "formula": "Operating Income ÷ Revenue × 100",
    },
    "net_margin": {
        "def": "The percentage of each revenue dollar that flows through to the bottom line as profit.",
        "why": "This is the final measure of profitability after everything — taxes, interest, one-time charges. A positive and rising net margin is the gold standard.",
        "formula": "Net Income ÷ Revenue × 100",
    },
    "eps_growth": {
        "def": "Earnings Per Share (EPS) — the share of profit allocated to each outstanding share.",
        "why": "EPS growth is what drives share price appreciation over time. A company that grows EPS consistently is compounding investor wealth.",
        "formula": "Net Income ÷ Diluted Shares Outstanding",
    },
    "fcf_positive": {
        "def": "Free Cash Flow (FCF) is the cash a company generates after paying for capital expenditures needed to maintain or grow the business.",
        "why": "FCF is harder to manipulate than net income. A company that consistently generates positive FCF can fund dividends, buybacks, and acquisitions without borrowing.",
        "formula": "Operating Cash Flow − Capital Expenditures",
    },
    "fcf_vs_net_income": {
        "def": "Compares Free Cash Flow to Net Income to check whether reported profits are backed by real cash.",
        "why": "When FCF ≥ Net Income, earnings are of high quality — cash is actually arriving. When FCF < Net Income, profits may be coming from accounting adjustments rather than cash payments.",
        "formula": "Free Cash Flow ÷ Net Income ≥ 1.0",
    },
    "ocf_trend": {
        "def": "Whether Operating Cash Flow (the cash generated by the core business) is growing over 3–5 years.",
        "why": "A growing OCF trend tells you the business engine is getting stronger over time, not just the accounting line items.",
        "formula": "OCF(Year N) > OCF(Year N−1) for 3+ years",
    },
    "capex_ratio": {
        "def": "Capital Expenditures (CapEx) as a percentage of revenue — shows how much the company must reinvest just to keep running.",
        "why": "Low CapEx businesses (software, consumer brands) generate cash cheaply. High CapEx businesses (airlines, utilities) need to constantly reinvest. Above 10% of revenue is a flag worth monitoring.",
        "formula": "CapEx ÷ Revenue × 100",
    },
    "debt_financing": {
        "def": "Whether the company is relying on new debt issuance (borrowing) to fund its operations and investments.",
        "why": "A business that can't fund itself without constantly borrowing is fragile — any credit tightening can threaten its survival. Look for positive financing cash flow dominated by debt issuance as a warning sign.",
        "formula": "Financing Cash Flow — check for large positive debt issuance",
    },
    "roe": {
        "def": "Return on Equity (ROE) measures how much profit a company generates for every dollar of shareholder equity.",
        "why": "ROE > 15% over multiple years is the hallmark of a great business. Warren Buffett's core filter is consistent high ROE without excessive debt.",
        "formula": "Net Income ÷ Average Shareholders' Equity × 100",
    },
    "roic": {
        "def": "Return on Invested Capital (ROIC) measures how efficiently a company uses all the capital invested in it (both equity and debt).",
        "why": "ROIC > 10% means the company earns more than its cost of capital — it's creating value. ROIC < cost of capital means it's destroying value even if it shows profit.",
        "formula": "NOPAT ÷ Invested Capital × 100  (NOPAT = Operating Income × (1 − Tax Rate))",
    },
    "moat": {
        "def": "An economic moat is a durable competitive advantage that protects a company's profits from competition.",
        "why": "Companies with moats (brand loyalty, network effects, switching costs, cost advantages, patents) sustain high returns on capital for decades. This requires reading 10-Ks and understanding the business — it cannot be auto-scored.",
        "formula": "Manual review: brand strength, market share stability, pricing power, switching costs",
    },
    "insider_ownership": {
        "def": "The percentage of shares owned by company insiders (executives and directors).",
        "why": "High insider ownership aligns management's interests with shareholders. Insider buying (purchasing in the open market) is a strong signal that management believes the stock is undervalued.",
        "formula": "Insider shares ÷ Total shares outstanding × 100",
    },
}

# ─── Red flag definitions ─────────────────────────────────────────────────────

RED_FLAG_DEFS = {
    "income_positive_fcf_negative": "Net income is positive but Free Cash Flow is negative — earnings may not be backed by real cash.",
    "debt_growing_faster_than_revenue": "Total debt is growing faster than revenue year-over-year — leverage is increasing relative to the business.",
    "goodwill_impairment": "Goodwill impairment detected in the most recent year — a prior acquisition has declined in value.",
    "current_ratio_below_1": "Current ratio is below 1.0 — current liabilities exceed liquid assets, a near-term liquidity risk.",
}


# ─── Data fetching ────────────────────────────────────────────────────────────

def _safe_val(series, year_offset: int = 0) -> float | None:
    """Safely extract a value from a pandas Series (indexed by date columns)."""
    try:
        if series is None or len(series) == 0:
            return None
        vals = series.values
        if year_offset < len(vals):
            v = vals[year_offset]
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return None
            return float(v)
        return None
    except Exception:
        return None


def _pct_change(new_val: float | None, old_val: float | None) -> float | None:
    if new_val is None or old_val is None or old_val == 0:
        return None
    return (new_val - old_val) / abs(old_val)


def fetch_fundamentals_raw(ticker: str) -> dict:
    """
    Fetch raw financial data from yfinance and return a structured dict.
    Returns a dict with keys: income_stmt, balance_sheet, cash_flow, info, missing_fields.
    """
    missing: list[str] = []
    result: dict[str, Any] = {
        "ticker": ticker.upper(),
        "missing_fields": [],
        # Income statement (up to 5 years, most-recent first)
        "revenue":          [],
        "gross_profit":     [],
        "operating_income": [],
        "net_income":       [],
        "diluted_eps":      [],
        # Balance sheet
        "total_assets":         [],
        "total_liabilities":    [],
        "total_equity":         [],
        "current_assets":       [],
        "current_liabilities":  [],
        "cash":                 [],
        "total_debt":           [],
        "goodwill":             [],
        "intangible_assets":    [],
        "retained_earnings":    [],
        # Cash flow
        "operating_cash_flow":  [],
        "capex":                [],
        "free_cash_flow":       [],
        "financing_cash_flow":  [],
        # Info fields
        "roe":              None,
        "roic":             None,
        "insider_pct":      None,
        "company_name":     None,
        "sector":           None,
        "industry":         None,
    }

    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        result["error"] = "yfinance is not installed on this server."
        return result

    try:
        t = yf.Ticker(ticker)

        # ── Info ──────────────────────────────────────────────────────────────
        try:
            info = t.info or {}
            result["company_name"] = info.get("longName") or info.get("shortName")
            result["sector"]       = info.get("sector")
            result["industry"]     = info.get("industry")
            # ROE from info (yfinance pre-computes it)
            roe_raw = info.get("returnOnEquity")
            if roe_raw is not None:
                result["roe"] = float(roe_raw) * 100  # convert to %
            # ROIC — yfinance doesn't expose this directly; compute manually later
            # Insider ownership %
            insider_pct = info.get("heldPercentInsiders")
            if insider_pct is not None:
                result["insider_pct"] = float(insider_pct) * 100
        except Exception as e:
            logger.debug("fundamentals info fetch error %s: %s", ticker, e)
            missing.append("company_info")

        # ── Income statement ──────────────────────────────────────────────────
        try:
            inc = t.financials  # annual, columns = dates (most recent first)
            if inc is None or inc.empty:
                try:
                    inc = t.income_stmt  # new attribute name in yfinance 0.2.x
                except Exception:
                    inc = None
            if inc is not None and not inc.empty:
                def _row(label):
                    # Normalize: strip spaces/underscores for robust matching
                    label_norm = label.lower().replace(" ", "").replace("_", "")
                    for key in inc.index:
                        key_norm = str(key).lower().replace(" ", "").replace("_", "")
                        if label_norm == key_norm or label_norm in key_norm:
                            return inc.loc[key]
                    return None

                rev_row  = _row("Total Revenue") or _row("Revenue")
                gp_row   = _row("Gross Profit") or _row("GrossProfit")
                oi_row   = _row("Operating Income") or _row("OperatingIncome") or _row("EBIT")
                ni_row   = _row("Net Income") or _row("NetIncome") or _row("Net Income Common Stockholders")

                n_years = min(5, inc.shape[1])
                for i in range(n_years):
                    result["revenue"].append(_safe_val(rev_row, i))
                    result["gross_profit"].append(_safe_val(gp_row, i))
                    result["operating_income"].append(_safe_val(oi_row, i))
                    result["net_income"].append(_safe_val(ni_row, i))

                # EPS from earnings history
                try:
                    eh = t.earnings_history
                    if eh is not None and not eh.empty and "epsActual" in eh.columns:
                        eps_vals = eh.sort_index(ascending=False)["epsActual"].tolist()
                        # Aggregate quarterly to annual (sum of 4 quarters)
                        annual_eps = []
                        for qi in range(0, min(20, len(eps_vals)), 4):
                            chunk = [v for v in eps_vals[qi:qi+4] if v is not None]
                            if chunk:
                                annual_eps.append(sum(chunk))
                        result["diluted_eps"] = annual_eps[:5]
                except Exception:
                    # Fallback: grab from financials if possible
                    try:
                        eps_row = _row("Diluted EPS") or _row("Basic EPS")
                        if eps_row is not None:
                            result["diluted_eps"] = [_safe_val(eps_row, i) for i in range(n_years)]
                    except Exception:
                        missing.append("eps")
            else:
                missing.append("income_statement")
        except Exception as e:
            logger.debug("fundamentals income stmt error %s: %s", ticker, e)
            missing.append("income_statement")

        # ── Balance sheet ─────────────────────────────────────────────────────
        try:
            bs = t.balance_sheet  # annual, most recent first
            if bs is None or bs.empty:
                try:
                    bs = t.quarterly_balance_sheet
                except Exception:
                    bs = None
            if bs is not None and not bs.empty:
                def _brow(label):
                    label_norm = label.lower().replace(" ", "").replace("_", "")
                    for key in bs.index:
                        key_norm = str(key).lower().replace(" ", "").replace("_", "")
                        if label_norm == key_norm or label_norm in key_norm:
                            return bs.loc[key]
                    return None

                n_years = min(5, bs.shape[1])
                ta_row   = _brow("Total Assets") or _brow("TotalAssets")
                tl_row   = _brow("Total Liabilities Net Minority Interest") or _brow("Total Liabilities") or _brow("TotalLiabilities")
                eq_row   = _brow("Stockholders Equity") or _brow("Total Equity Gross Minority Interest") or _brow("Common Stock Equity") or _brow("StockholdersEquity")
                ca_row   = _brow("Current Assets") or _brow("CurrentAssets")
                cl_row   = _brow("Current Liabilities") or _brow("CurrentLiabilities")
                cash_row = _brow("Cash And Cash Equivalents") or _brow("Cash Cash Equivalents And Short Term Investments") or _brow("CashAndCashEquivalents")
                debt_row = _brow("Total Debt") or _brow("Long Term Debt And Capital Lease Obligation") or _brow("TotalDebt")
                gw_row   = _brow("Goodwill")
                ia_row   = _brow("Intangible Assets") or _brow("Other Intangible Assets") or _brow("OtherIntangibleAssets")
                re_row   = _brow("Retained Earnings") or _brow("RetainedEarnings")

                for i in range(n_years):
                    result["total_assets"].append(_safe_val(ta_row, i))
                    result["total_liabilities"].append(_safe_val(tl_row, i))
                    result["total_equity"].append(_safe_val(eq_row, i))
                    result["current_assets"].append(_safe_val(ca_row, i))
                    result["current_liabilities"].append(_safe_val(cl_row, i))
                    result["cash"].append(_safe_val(cash_row, i))
                    result["total_debt"].append(_safe_val(debt_row, i))
                    result["goodwill"].append(_safe_val(gw_row, i))
                    result["intangible_assets"].append(_safe_val(ia_row, i))
                    result["retained_earnings"].append(_safe_val(re_row, i))
            else:
                missing.append("balance_sheet")
        except Exception as e:
            logger.debug("fundamentals balance sheet error %s: %s", ticker, e)
            missing.append("balance_sheet")

        # ── Cash flow ─────────────────────────────────────────────────────────
        try:
            cf = t.cashflow  # annual, most recent first
            if cf is None or cf.empty:
                try:
                    cf = t.cash_flow  # alternate attribute name
                except Exception:
                    cf = None
            if cf is not None and not cf.empty:
                def _crow(label):
                    label_norm = label.lower().replace(" ", "").replace("_", "")
                    for key in cf.index:
                        key_norm = str(key).lower().replace(" ", "").replace("_", "")
                        if label_norm == key_norm or label_norm in key_norm:
                            return cf.loc[key]
                    return None

                n_years = min(5, cf.shape[1])
                ocf_row  = _crow("Operating Cash Flow") or _crow("Cash From Operations") or _crow("CashFlowFromContinuingOperatingActivities")
                cap_row  = _crow("Capital Expenditure") or _crow("Purchase Of Property Plant And Equipment") or _crow("CapitalExpenditure")
                fcf_row  = _crow("Free Cash Flow") or _crow("FreeCashFlow")
                fin_row  = _crow("Financing Activities") or _crow("Cash Flow From Financing") or _crow("CashFlowFromContinuingFinancingActivities")

                for i in range(n_years):
                    ocf = _safe_val(ocf_row, i)
                    cap = _safe_val(cap_row, i)
                    fcf_direct = _safe_val(fcf_row, i)
                    # CapEx in yfinance is typically negative; make it positive
                    if cap is not None:
                        cap = abs(cap)
                    # Compute FCF if not directly available
                    if fcf_direct is not None:
                        fcf = fcf_direct
                    elif ocf is not None and cap is not None:
                        fcf = ocf - cap
                    else:
                        fcf = None

                    result["operating_cash_flow"].append(ocf)
                    result["capex"].append(cap)
                    result["free_cash_flow"].append(fcf)
                    result["financing_cash_flow"].append(_safe_val(fin_row, i))
            else:
                missing.append("cash_flow")
        except Exception as e:
            logger.debug("fundamentals cash flow error %s: %s", ticker, e)
            missing.append("cash_flow")

        # ── ROIC (manual calc) ────────────────────────────────────────────────
        try:
            oi0  = result["operating_income"][0] if result["operating_income"] else None
            ni0  = result["net_income"][0] if result["net_income"] else None
            eq0  = result["total_equity"][0] if result["total_equity"] else None
            dt0  = result["total_debt"][0] if result["total_debt"] else None
            if oi0 and ni0 and eq0 is not None and dt0 is not None:
                # Estimate tax rate from net income vs operating income
                tax_rate = max(0, min(0.40, 1 - (ni0 / oi0))) if oi0 != 0 else 0.21
                nopat = oi0 * (1 - tax_rate)
                invested_capital = (eq0 or 0) + (dt0 or 0)
                if invested_capital > 0:
                    result["roic"] = (nopat / invested_capital) * 100
        except Exception:
            pass

    except Exception as e:
        logger.warning("fundamentals_engine fetch error for %s: %s", ticker, e)
        result["error"] = str(e)

    result["missing_fields"] = list(set(missing))
    return result


# ─── Scoring ──────────────────────────────────────────────────────────────────

def _score_check(condition: bool | None, points: int) -> tuple[int, int, bool | None]:
    """Returns (earned, available, passed) — if condition is None, skip (data missing)."""
    if condition is None:
        return 0, 0, None  # data missing — don't count against score
    return (points if condition else 0), points, bool(condition)


def score_fundamentals(raw: dict) -> dict:
    """
    Score a raw fundamentals dict and return a structured scorecard.
    Returns dict with sections, total_score, max_possible, verdict, red_flags, etc.
    """

    def v(lst, i=0):
        """Get value at index i from list, None if missing."""
        try:
            val = lst[i] if lst and i < len(lst) else None
            return val
        except (IndexError, TypeError):
            return None

    # ── Pre-compute helper values ─────────────────────────────────────────────
    cr0 = (v(raw["current_assets"]) / v(raw["current_liabilities"])
           if v(raw["current_assets"]) and v(raw["current_liabilities"]) and v(raw["current_liabilities"]) != 0
           else None)

    total_equity = v(raw["total_equity"])
    total_debt   = v(raw["total_debt"])
    de_ratio = (total_debt / total_equity
                if total_debt is not None and total_equity and total_equity != 0
                else None)

    cash0 = v(raw["cash"])
    cash_covers_debt = (cash0 >= total_debt if cash0 is not None and total_debt is not None else None)

    # Retained earnings growing?
    re_vals = [v(raw["retained_earnings"], i) for i in range(min(4, len(raw["retained_earnings"])))]
    re_growing = None
    if len([x for x in re_vals if x is not None]) >= 2:
        valid_re = [(i, x) for i, x in enumerate(re_vals) if x is not None]
        re_growing = all(valid_re[i][1] > valid_re[i+1][1] for i in range(len(valid_re)-1))

    # Goodwill ratio
    gw0 = v(raw["goodwill"]) or 0
    ia0 = v(raw["intangible_assets"]) or 0
    ta0 = v(raw["total_assets"])
    gw_ratio = ((gw0 + ia0) / ta0 if ta0 and ta0 != 0 else None)
    gw_ok = (gw_ratio < 0.30 if gw_ratio is not None else None)

    # Revenue growth 3+ consecutive years
    rev_vals = [v(raw["revenue"], i) for i in range(min(4, len(raw["revenue"])))]
    valid_rev = [(i, x) for i, x in enumerate(rev_vals) if x is not None]
    rev_growth = None
    if len(valid_rev) >= 3:
        rev_growth = all(valid_rev[i][1] > valid_rev[i+1][1] for i in range(min(2, len(valid_rev)-1)))

    # Gross margin trend
    def _margin(num_vals, den_vals):
        margins = []
        for i in range(min(4, max(len(num_vals), len(den_vals)))):
            num, den = v(num_vals, i), v(den_vals, i)
            if num is not None and den and den != 0:
                margins.append(num / den)
            else:
                margins.append(None)
        return margins

    gm_series = _margin(raw["gross_profit"], raw["revenue"])
    om_series = _margin(raw["operating_income"], raw["revenue"])
    nm_series = _margin(raw["net_income"], raw["revenue"])

    def _trending_up(series):
        valid = [(i, x) for i, x in enumerate(series) if x is not None]
        if len(valid) < 2:
            return None
        return valid[0][1] >= valid[-1][1]  # most-recent >= oldest (stable/rising)

    gm_ok = _trending_up(gm_series)
    om_ok = _trending_up(om_series)
    nm_positive = (v(raw["net_income"]) is not None and v(raw["net_income"], 0) is not None
                   and v(raw["net_income"], 0) > 0)
    nm_ok = (nm_positive and _trending_up(nm_series)) if nm_positive else (False if nm_positive is False else None)

    # EPS growing
    eps_vals = raw["diluted_eps"]
    eps_growing = None
    if len([x for x in eps_vals if x is not None]) >= 2:
        valid_eps = [(i, x) for i, x in enumerate(eps_vals) if x is not None]
        eps_growing = (valid_eps[0][1] > valid_eps[-1][1]) if valid_eps else None

    # FCF
    fcf0 = v(raw["free_cash_flow"])
    ni0  = v(raw["net_income"])
    ocf0 = v(raw["operating_cash_flow"])

    fcf_positive = (fcf0 > 0 if fcf0 is not None else None)
    fcf_ge_ni    = ((fcf0 >= ni0) if fcf0 is not None and ni0 is not None else None)

    ocf_vals = [v(raw["operating_cash_flow"], i) for i in range(min(4, len(raw["operating_cash_flow"])))]
    ocf_growing = _trending_up(ocf_vals) if len([x for x in ocf_vals if x is not None]) >= 2 else None

    # CapEx ratio
    capex0  = v(raw["capex"])
    rev0    = v(raw["revenue"])
    capex_ratio = (capex0 / rev0 if capex0 is not None and rev0 and rev0 != 0 else None)
    capex_ok    = (capex_ratio <= 0.10 if capex_ratio is not None else None)

    # Debt financing check — positive and large financing CF is a warning
    fin_cf0 = v(raw["financing_cash_flow"])
    not_debt_reliant = None
    if fin_cf0 is not None and ocf0 is not None and ocf0 != 0:
        # If financing CF is large positive relative to OCF, flag it
        not_debt_reliant = not (fin_cf0 > 0 and fin_cf0 > abs(ocf0) * 0.5)

    # Quality metrics
    roe  = raw.get("roe")
    roic = raw.get("roic")
    roe_ok  = (roe  > 15 if roe  is not None else None)
    roic_ok = (roic > 10 if roic is not None else None)

    insider_pct = raw.get("insider_pct")
    insider_ok  = (insider_pct is not None)  # 2pts if data exists at all

    # ── Score each section ────────────────────────────────────────────────────

    sections = []
    total_earned = 0
    total_possible = 0

    def add_section(name: str, metrics: list[dict]) -> None:
        nonlocal total_earned, total_possible
        sec_earned = 0
        sec_possible = 0
        rows = []
        for m in metrics:
            earned, avail, passed = _score_check(m["condition"], m["points"])
            sec_earned   += earned
            sec_possible += avail
            rows.append({
                "key":     m["key"],
                "label":   m["label"],
                "value":   m.get("display_value", ""),
                "points":  m["points"],
                "earned":  earned,
                "avail":   avail,
                "passed":  passed,       # True/False/None(missing)
                "edu":     EDUCATION.get(m["key"], {}),
            })
        total_earned   += sec_earned
        total_possible += sec_possible
        sections.append({
            "name":     name,
            "earned":   sec_earned,
            "possible": sec_possible,
            "rows":     rows,
        })

    def _fmt(val, suffix="", scale=1, decimals=2):
        if val is None:
            return "N/A"
        v_s = val * scale
        if abs(v_s) >= 1e9:
            return f"${v_s/1e9:.1f}B{suffix}"
        if abs(v_s) >= 1e6:
            return f"${v_s/1e6:.1f}M{suffix}"
        return f"${v_s:,.{decimals}f}{suffix}"

    def _pct_fmt(val):
        if val is None:
            return "N/A"
        return f"{val*100:.1f}%"

    # ── Section 1: Balance Sheet ──────────────────────────────────────────────
    add_section("Balance Sheet", [
        {
            "key": "current_ratio",
            "label": "Current Ratio > 1.5",
            "condition": (cr0 >= 1.5 if cr0 is not None else None),
            "points": 2,
            "display_value": f"{cr0:.2f}" if cr0 is not None else "N/A",
        },
        {
            "key": "debt_to_equity",
            "label": "Debt-to-Equity < 1.0",
            "condition": (de_ratio < 1.0 if de_ratio is not None else None),
            "points": 2,
            "display_value": f"{de_ratio:.2f}" if de_ratio is not None else "N/A",
        },
        {
            "key": "cash_covers_debt",
            "label": "Cash covers 1+ yr of total debt",
            "condition": cash_covers_debt,
            "points": 2,
            "display_value": (f"{_fmt(cash0)} vs {_fmt(total_debt)} debt"
                              if cash0 is not None and total_debt is not None else "N/A"),
        },
        {
            "key": "retained_earnings_growth",
            "label": "Retained earnings growing (3–5 yr)",
            "condition": re_growing,
            "points": 2,
            "display_value": _fmt(v(raw["retained_earnings"])),
        },
        {
            "key": "goodwill_ratio",
            "label": "Goodwill + Intangibles < 30% of assets",
            "condition": gw_ok,
            "points": 2,
            "display_value": f"{gw_ratio*100:.1f}%" if gw_ratio is not None else "N/A",
        },
    ])

    # ── Section 2: Income Statement ───────────────────────────────────────────
    add_section("Income Statement", [
        {
            "key": "revenue_growth",
            "label": "Revenue growing 3+ consecutive years",
            "condition": rev_growth,
            "points": 2,
            "display_value": _fmt(v(raw["revenue"])),
        },
        {
            "key": "gross_margin",
            "label": "Gross margin stable or rising",
            "condition": gm_ok,
            "points": 2,
            "display_value": _pct_fmt(gm_series[0]) if gm_series and gm_series[0] is not None else "N/A",
        },
        {
            "key": "operating_margin",
            "label": "Operating margin stable or rising",
            "condition": om_ok,
            "points": 2,
            "display_value": _pct_fmt(om_series[0]) if om_series and om_series[0] is not None else "N/A",
        },
        {
            "key": "net_margin",
            "label": "Net margin positive and trending up",
            "condition": nm_ok,
            "points": 2,
            "display_value": _pct_fmt(nm_series[0]) if nm_series and nm_series[0] is not None else "N/A",
        },
        {
            "key": "eps_growth",
            "label": "EPS growing",
            "condition": eps_growing,
            "points": 2,
            "display_value": (f"${eps_vals[0]:.2f}" if eps_vals and eps_vals[0] is not None else "N/A"),
        },
    ])

    # ── Section 3: Cash Flow ──────────────────────────────────────────────────
    add_section("Cash Flow", [
        {
            "key": "fcf_positive",
            "label": "Free Cash Flow positive",
            "condition": fcf_positive,
            "points": 2,
            "display_value": _fmt(fcf0),
        },
        {
            "key": "fcf_vs_net_income",
            "label": "FCF ≥ Net Income (earnings quality)",
            "condition": fcf_ge_ni,
            "points": 3,
            "display_value": (f"{_fmt(fcf0)} FCF vs {_fmt(ni0)} NI"
                              if fcf0 is not None and ni0 is not None else "N/A"),
        },
        {
            "key": "ocf_trend",
            "label": "Operating Cash Flow trending up (3–5 yr)",
            "condition": ocf_growing,
            "points": 2,
            "display_value": _fmt(ocf0),
        },
        {
            "key": "capex_ratio",
            "label": "CapEx ≤ 10% of revenue",
            "condition": capex_ok,
            "points": 2,
            "display_value": f"{capex_ratio*100:.1f}%" if capex_ratio is not None else "N/A",
        },
        {
            "key": "debt_financing",
            "label": "Not reliant on debt issuance for financing",
            "condition": not_debt_reliant,
            "points": 1,
            "display_value": _fmt(fin_cf0) if fin_cf0 is not None else "N/A",
        },
    ])

    # ── Section 4: Quality Metrics ────────────────────────────────────────────
    add_section("Quality Metrics", [
        {
            "key": "roe",
            "label": "ROE > 15%",
            "condition": roe_ok,
            "points": 3,
            "display_value": f"{roe:.1f}%" if roe is not None else "N/A",
        },
        {
            "key": "roic",
            "label": "ROIC > 10%",
            "condition": roic_ok,
            "points": 3,
            "display_value": f"{roic:.1f}%" if roic is not None else "N/A",
        },
        {
            "key": "moat",
            "label": "Economic Moat",
            "condition": None,  # always manual — no auto-score
            "points": 0,
            "display_value": "Needs manual review",
        },
        {
            "key": "insider_ownership",
            "label": "Insider ownership data available",
            "condition": (True if insider_ok else None),
            "points": 2,
            "display_value": f"{insider_pct:.1f}%" if insider_pct is not None else "N/A",
        },
    ])

    # ── Red flags ─────────────────────────────────────────────────────────────
    red_flags = []
    if ni0 is not None and ni0 > 0 and fcf0 is not None and fcf0 < 0:
        red_flags.append({
            "key": "income_positive_fcf_negative",
            "label": RED_FLAG_DEFS["income_positive_fcf_negative"],
        })
    # Debt growing faster than revenue
    if len(raw["total_debt"]) >= 2 and len(raw["revenue"]) >= 2:
        debt_chg = _pct_change(v(raw["total_debt"], 0), v(raw["total_debt"], 1))
        rev_chg  = _pct_change(v(raw["revenue"], 0),    v(raw["revenue"], 1))
        if debt_chg is not None and rev_chg is not None and debt_chg > rev_chg and debt_chg > 0.05:
            red_flags.append({
                "key": "debt_growing_faster_than_revenue",
                "label": RED_FLAG_DEFS["debt_growing_faster_than_revenue"],
            })
    # Current ratio below 1
    if cr0 is not None and cr0 < 1.0:
        red_flags.append({
            "key": "current_ratio_below_1",
            "label": RED_FLAG_DEFS["current_ratio_below_1"],
        })

    # ── Verdict ───────────────────────────────────────────────────────────────
    score_pct = (total_earned / total_possible * 40) if total_possible > 0 else 0

    if   score_pct >= 34: verdict = "Great Company"
    elif score_pct >= 26: verdict = "Good"
    elif score_pct >= 18: verdict = "Caution"
    else:                 verdict = "Avoid"

    verdict_class = {
        "Great Company": "verdict-great",
        "Good":          "verdict-good",
        "Caution":       "verdict-caution",
        "Avoid":         "verdict-avoid",
    }.get(verdict, "verdict-caution")

    # ── Historical table data (for sparkline display) ─────────────────────────
    def _history_table(raw_data: dict) -> list[dict]:
        """Build a year-by-year summary table (up to 5 years)."""
        n = max(
            len(raw_data.get("revenue", [])),
            len(raw_data.get("net_income", [])),
            len(raw_data.get("free_cash_flow", [])),
        )
        rows = []
        for i in range(min(n, 5)):
            rev  = v(raw_data["revenue"], i)
            ni   = v(raw_data["net_income"], i)
            fcf  = v(raw_data["free_cash_flow"], i)
            ocf  = v(raw_data["operating_cash_flow"], i)
            gm   = gm_series[i] if i < len(gm_series) else None
            om   = om_series[i] if i < len(om_series) else None
            rows.append({
                "year_offset": i,
                "label": f"Year -{i}" if i > 0 else "Latest",
                "revenue":  _fmt(rev),
                "net_income": _fmt(ni),
                "fcf": _fmt(fcf),
                "ocf": _fmt(ocf),
                "gross_margin": _pct_fmt(gm),
                "operating_margin": _pct_fmt(om),
            })
        return rows

    history = _history_table(raw)

    return {
        "ticker":        raw.get("ticker", ""),
        "company_name":  raw.get("company_name"),
        "sector":        raw.get("sector"),
        "industry":      raw.get("industry"),
        "sections":      sections,
        "total_earned":  total_earned,
        "total_possible": total_possible,
        "normalized_score": round(score_pct),
        "verdict":       verdict,
        "verdict_class": verdict_class,
        "red_flags":     red_flags,
        "missing_fields": raw.get("missing_fields", []),
        "error":         raw.get("error"),
        "history":       history,
        "roe":           raw.get("roe"),
        "roic":          raw.get("roic"),
        "insider_pct":   raw.get("insider_pct"),
    }


# ─── Public entry point ───────────────────────────────────────────────────────

def get_fundamentals(ticker: str, force_refresh: bool = False) -> dict:
    """
    Main entry point. Returns fully scored fundamentals dict for ticker.
    Uses 24-hr cache unless force_refresh=True.
    """
    ticker = ticker.upper().strip()

    # Try cache first
    if not force_refresh:
        try:
            from database import get_fundamentals_cache
            cached = get_fundamentals_cache(ticker)
            if cached:
                logger.debug("fundamentals cache hit for %s", ticker)
                return cached
        except Exception as e:
            logger.debug("fundamentals cache read error: %s", e)

    # Fetch fresh
    raw  = fetch_fundamentals_raw(ticker)
    result = score_fundamentals(raw)

    # Save to cache
    try:
        from database import save_fundamentals_cache
        save_fundamentals_cache(ticker, result)
    except Exception as e:
        logger.debug("fundamentals cache write error: %s", e)

    return result
