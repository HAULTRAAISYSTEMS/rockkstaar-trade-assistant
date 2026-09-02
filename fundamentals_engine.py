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
import os
from datetime import datetime
import requests as _req_module
from typing import Any

from fundamentals_charts import build_charts

logger = logging.getLogger(__name__)

# ─── SEC EDGAR data source (primary — completely free, no API key needed) ────
# EDGAR hosts XBRL financial data for all SEC-reporting companies (US stocks
# plus foreign ADRs that file 20-F).  No API key, no rate limits that matter
# for personal use, and no IP-based blocking.
# Rate limit: 10 req/sec — we stay well under that with caching.

_EDGAR_HEADERS = {
    "User-Agent": "HAULTRA-AI/1.0 contact@haultra.ai",
    "Accept-Encoding": "gzip, deflate",
    "Host": "data.sec.gov",
}
_EDGAR_TICKERS_URL   = "https://www.sec.gov/files/company_tickers.json"
_EDGAR_FACTS_URL     = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_EDGAR_COMPANY_URL   = "https://data.sec.gov/submissions/CIK{cik}.json"

# In-process cache for the giant tickers file (downloaded once per process)
_edgar_tickers_cache: dict = {}

# Hardcoded CIK overrides for tickers that may be missing or wrong in company_tickers.json
# (recent spinoffs, renames, foreign co-listings, etc.)
_CIK_OVERRIDES: dict[str, tuple[str, str]] = {
    # (ticker_upper) -> (cik_padded, company_name)
    "GEV":  ("0001996810", "GE Vernova Inc."),      # GE spinoff Apr 2024, may lag in tickers file
    "GEHC": ("0001835016", "GE HealthCare Technologies Inc."),
    "SOLV": ("0001974964", "Solventum Corp"),        # 3M spinoff Apr 2024
}


# A per-share series spanning a stock split mixes two scales. KLA's reported
# diluted EPS reads 21.92 -> 24.15 -> 2.03 -> 3.04 -> 3.66 across a 10-for-1
# split in June 2026: the first two are pre-split. Comparing newest against
# oldest then reports a collapse in earnings that never happened.
SPLIT_BREAK_RATIO = 4.0
# A split leaves net income untouched. If earnings moved with the per-share
# figure, the break is an operating event and must not be hidden.
SPLIT_NI_STABLE_BAND = (0.5, 2.0)


def _fact_rank(fact: dict) -> tuple:
    """Ordering for two facts covering the same period end.

    Longest period first: a 10-K carries both the twelve-month and the
    three-month figure for the same end date, and picking the quarter would
    divide one quarter's earnings into a full year's revenue.

    Then the filing DATE, not the accession number. Accession prefixes belong
    to whoever transmitted the filing, so a company that changes filing agent
    gets numbers that no longer sort chronologically — and "the newest report"
    is exactly what decides whether a series comes back on one side of a stock
    split or straddles it. The accession stays as a last tiebreak so the choice
    is deterministic when two facts were filed the same day.
    """
    return (_period_days(fact), str(fact.get("filed", "")), str(fact.get("accn", "")))


def detect_split_break(series, net_income=None) -> int | None:
    """Index (newest-first) where a per-share series crosses a split boundary.

    A 4x or larger year-over-year move in per-share terms is the candidate. It
    is only treated as a split when net income for the same pair stayed roughly
    flat: a split multiplies the share count and leaves earnings alone, while an
    earnings collapse moves both together. Getting this wrong in the permissive
    direction would silently drop a genuine collapse out of the series, so the
    default when earnings also moved is to leave the data alone.
    """
    values = [(i, x) for i, x in enumerate(series or [])
              if isinstance(x, (int, float)) and x not in (None, 0)]
    incomes = list(net_income or [])
    for (newer_index, newer), (older_index, older) in zip(values, values[1:]):
        if (newer > 0) != (older > 0):
            continue                       # a swing through zero is real
        ratio = abs(older) / abs(newer)
        if not (ratio >= SPLIT_BREAK_RATIO or ratio <= 1 / SPLIT_BREAK_RATIO):
            continue
        newer_ni = incomes[newer_index] if newer_index < len(incomes) else None
        older_ni = incomes[older_index] if older_index < len(incomes) else None
        if not (newer_ni and older_ni and newer_ni > 0 and older_ni > 0):
            # No earnings to corroborate with. The docstring's promise is to
            # leave the data alone when a split cannot be confirmed, and
            # trimming here did the opposite: an 87% earnings collapse looked
            # identical to a split and was quietly cut from the series, with
            # the page telling the reader it crossed one.
            continue
        ni_ratio = older_ni / newer_ni
        low, high = SPLIT_NI_STABLE_BAND
        if not (low <= ni_ratio <= high):
            continue                   # earnings moved too - not a split
        return older_index
    return None


def split_adjusted_series(series, net_income=None) -> tuple[list, bool]:
    """Trim a per-share series at a split boundary. Returns (series, trimmed).

    Pre-split values are dropped rather than rescaled: inferring the ratio from
    a year whose earnings also changed would be a guess, and a wrong scale
    factor is worse than a shorter series.
    """
    break_index = detect_split_break(series, net_income)
    if break_index is None:
        return list(series or []), False
    return list(series or [])[:break_index], True


def _period_days(fact: dict) -> int:
    """Length of an XBRL fact's period in days. Instantaneous facts return 0."""
    start, end = fact.get("start"), fact.get("end")
    if not start or not end:
        return 0
    try:
        return (datetime.strptime(end, "%Y-%m-%d").date()
                - datetime.strptime(start, "%Y-%m-%d").date()).days
    except (TypeError, ValueError):
        return 0


def _is_annual_period(fact: dict) -> bool:
    """True for balance-sheet instants and for full-year duration facts.

    Income-statement and cash-flow concepts are durations. A 10-K reports the
    fiscal year AND its fourth quarter for the same period end, so without a
    duration filter a quarterly GrossProfit could be paired with annual revenue.
    Revenue and net income happened to survive this; gross profit and operating
    income did not.
    """
    if "start" not in fact:
        return True                      # instant - balance sheet item
    days = _period_days(fact)
    return 300 <= days <= 400            # a fiscal year, 52/53-week years included


def _edgar_cik(ticker: str) -> tuple[str, str] | tuple[None, None]:
    """
    Return (cik_padded, company_name) for ticker, or (None, None) if not found.
    The tickers file is cached in memory after the first download.
    """
    t_upper = ticker.upper()
    # Check hardcoded overrides first (covers recent spinoffs / renames)
    if t_upper in _CIK_OVERRIDES:
        return _CIK_OVERRIDES[t_upper]

    global _edgar_tickers_cache
    if not _edgar_tickers_cache:
        try:
            resp = _req_module.get(
                _EDGAR_TICKERS_URL,
                timeout=15,
                headers={"User-Agent": "HAULTRA-AI/1.0 contact@haultra.ai"},
            )
            if resp.status_code == 200:
                _edgar_tickers_cache = resp.json()
        except Exception as exc:
            logger.warning("EDGAR tickers fetch error: %s", exc)
            return None, None

    for _, entry in _edgar_tickers_cache.items():
        if entry.get("ticker", "").upper() == t_upper:
            cik = str(entry["cik_str"]).zfill(10)
            return cik, entry.get("title")
    return None, None


# Extracting one company's facts means downloading and parsing the whole XBRL
# company-facts document — tens of megabytes for a large filer — and that parse
# is CPU-bound, so it holds the interpreter lock and stalls every other thread
# in the worker, health checks included. Force refresh is meant to bypass the
# finished-scorecard cache, not to re-download the filing on every click, and
# repeatedly checking a fix is exactly when that happens most.
#
# Keyed by the scorecard version as well as the ticker, so a deploy that
# changes how facts are read still gets a clean fetch — the cache can never be
# the reason a fix appears not to have shipped, which cost hours earlier today.
_EDGAR_EXTRACT_TTL = 900          # seconds
_EDGAR_EXTRACT_MAX = 32           # tickers held per worker
_EDGAR_EXTRACT_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}


def clear_edgar_extract_cache() -> None:
    """Drop the extraction cache. Tests call this between cases."""
    _EDGAR_EXTRACT_CACHE.clear()


def fetch_fundamentals_edgar(ticker: str) -> dict | None:
    """Cached wrapper. See _fetch_fundamentals_edgar for the extraction."""
    import copy
    import time as _time

    key = (ticker.upper().strip(), SCORECARD_VERSION)
    hit = _EDGAR_EXTRACT_CACHE.get(key)
    if hit and (_time.time() - hit[0]) < _EDGAR_EXTRACT_TTL:
        # A copy, because the TTM layer augments what it is handed and the
        # cached extraction must stay as EDGAR reported it.
        return copy.deepcopy(hit[1])

    result = _fetch_fundamentals_edgar(ticker)
    if result is not None:
        if len(_EDGAR_EXTRACT_CACHE) >= _EDGAR_EXTRACT_MAX:
            oldest = min(_EDGAR_EXTRACT_CACHE, key=lambda k: _EDGAR_EXTRACT_CACHE[k][0])
            _EDGAR_EXTRACT_CACHE.pop(oldest, None)
        _EDGAR_EXTRACT_CACHE[key] = (_time.time(), copy.deepcopy(result))
    return result


def _fetch_fundamentals_edgar(ticker: str) -> dict | None:
    """
    Fetch fundamental data from SEC EDGAR XBRL API.
    Free, no API key, works for US-listed companies and ADRs (20-F filers).
    Returns the same dict structure as fetch_fundamentals_raw(), or None if
    the ticker isn't found or has no XBRL data.
    """
    result: dict[str, Any] = {
        "ticker": ticker.upper(),
        "missing_fields": [],
        "company_name": None, "sector": None, "industry": None,
        "roe": None, "roic": None, "insider_pct": None,
        "revenue": [], "gross_profit": [], "operating_income": [],
        "net_income": [], "diluted_eps": [],
        "total_assets": [], "total_liabilities": [], "total_equity": [],
        "current_assets": [], "current_liabilities": [],
        "cash": [], "cash_and_st_investments": [], "short_term_debt": [], "total_debt": [], "goodwill": [],
        "intangible_assets": [], "retained_earnings": [],
        "operating_cash_flow": [], "capex": [], "free_cash_flow": [],
        "financing_cash_flow": [],
    }
    missing = result["missing_fields"]

    # ── 1. Resolve CIK ───────────────────────────────────────────────────────
    cik, company_name = _edgar_cik(ticker)
    if not cik:
        logger.warning("EDGAR: %s not found in SEC tickers", ticker)
        return None
    result["company_name"] = company_name

    # ── 2. Fetch XBRL company facts ──────────────────────────────────────────
    try:
        resp = _req_module.get(
            _EDGAR_FACTS_URL.format(cik=cik),
            timeout=12,
            headers=_EDGAR_HEADERS,
        )
        if resp.status_code != 200:
            logger.warning("EDGAR facts %d for %s (CIK %s)", resp.status_code, ticker, cik)
            return None
        facts = resp.json()
    except Exception as exc:
        logger.warning("EDGAR facts fetch error for %s: %s", ticker, exc)
        return None

    all_facts = facts.get("facts", {})
    us_gaap   = all_facts.get("us-gaap", {})
    ifrs_data = all_facts.get("ifrs-full", {})
    is_ifrs   = bool(ifrs_data) and not us_gaap  # true for 20-F IFRS filers like TSM

    if not us_gaap and not ifrs_data:
        logger.warning("EDGAR: no us-gaap or ifrs-full facts for %s", ticker)
        return None

    # ── 3a. How current is this filer's data? ────────────────────────────────
    # Filers retire concepts. KLA stopped tagging OperatingIncomeLoss and
    # CostsAndExpenses in 2015 but both still carry a decade of facts, so a
    # lookup found them, returned 2011-2015 values, and every one aligned to
    # None against a 2022-2026 timeline — the operating margin trail read N/A
    # with no error anywhere. Worse, a bare list lookup would have handed a
    # 2015 balance sheet back as if it were current. Probing a few concepts
    # every filer keeps current gives a reference point to measure staleness
    # against.
    def _concept_latest_end(name: str):
        for ns in ([us_gaap] if us_gaap else []) + ([ifrs_data] if ifrs_data else []):
            concept = ns.get(name)
            if not concept:
                continue
            ends = [f["end"] for unit in concept.get("units", {}).values()
                    for f in unit
                    if f.get("form") in ("10-K", "20-F", "40-F") and f.get("end")]
            if ends:
                return max(ends)
        return None

    _reference_end = max(
        (e for e in (_concept_latest_end(n) for n in (
            "Assets", "NetIncomeLoss", "StockholdersEquity", "ProfitLoss",
            "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
            "CashAndCashEquivalentsAtCarryingValue")) if e),
        default=None)

    def _is_stale(newest_end: str) -> bool:
        """True when a concept cannot reach the window this card reports.

        The test is not "did the filer stop using it" — a line renamed three
        years ago still holds four years of history worth showing. It is
        whether the concept's newest fact falls outside the five years the
        page displays at all, in which case it can only contribute nulls to an
        aligned series or, worse, hand a decade-old balance to a lookup that
        reads index zero as current.
        """
        if not _reference_end or not newest_end:
            return False
        try:
            gap = (datetime.strptime(_reference_end, "%Y-%m-%d").date()
                   - datetime.strptime(newest_end, "%Y-%m-%d").date()).days
        except (TypeError, ValueError):
            return False
        return gap > 2200          # five reported years plus slack

    # ── 3. Helper: extract up to n annual values, most recent first ──────────
    def _annual_dated(concepts, n: int = 5, merge: bool = False,
                      same_filing: bool = False):
        """Try each concept in order; return (period_end, value) newest-first.

        ``same_filing`` keeps only the facts carried by the single newest
        filing that reports this concept. One report's comparative columns are
        always stated on one basis, which is what a series spanning a stock
        split needs: a filer restates prior years post-split inside the new
        report but the older reports keep their pre-split figures, so pulling
        each year from whichever report first mentioned it splices two bases
        together and turns a 10-for-1 split into 770% dilution.

        ``merge`` keeps going past the first concept that has facts and lets
        later concepts fill period ends the earlier ones did not cover. Filers
        rename lines as the taxonomy moves - revenue went from ``Revenues`` to
        ``RevenueFromContractWithCustomerExcludingAssessedTax`` in 2019 - so
        stopping at the first match returned only whatever coverage that one
        era happened to have. Pass merge=True only for concepts that are
        genuine renames of the same line: basic and diluted EPS, or net income
        and comprehensive income, are different numbers and must not blend.
        """
        collected: dict[str, dict] = {}
        if isinstance(concepts, str):
            concepts = [concepts]
        namespaces = []
        if us_gaap:
            namespaces.append(us_gaap)
        if ifrs_data:
            namespaces.append(ifrs_data)
        for ns in namespaces:
            for name in concepts:
                concept = ns.get(name)
                if not concept:
                    continue
                _units = concept.get("units", {})
                # Share counts are denominated in "shares", not USD. Reading
                # only the dollar buckets made every share-count concept look
                # absent, so the dilution row could never resolve.
                usd_vals = (_units.get("USD")
                            or _units.get("USD/shares")
                            or _units.get("shares")
                            or [])
                annual = [
                    v for v in usd_vals
                    if v.get("form") in ("10-K", "20-F", "40-F")
                    and "end" in v and "val" in v
                    and _is_annual_period(v)
                ]
                if not annual:
                    continue
                by_end: dict[str, dict] = {}
                for v in annual:
                    end = v["end"]
                    incumbent = by_end.get(end)
                    if incumbent is None:
                        by_end[end] = v
                        continue
                    # Prefer the longer period first, then the later filing. A 10-K
                    # carries both 12-month and 3-month facts for the same concept
                    # and the same period end; picking by accession alone could
                    # select the quarter, which then divided into annual revenue
                    # produced single-digit margins.
                    if _fact_rank(v) > _fact_rank(incumbent):
                        by_end[end] = v
                if _is_stale(max(by_end)):
                    continue          # retired concept; try the next name
                if same_filing:
                    newest = max(by_end.values(), key=lambda f: str(f.get("filed", "")))
                    newest_accn = str(newest.get("accn", ""))
                    by_end = {end: f for end, f in by_end.items()
                              if str(f.get("accn", "")) == newest_accn}
                    if not by_end:
                        continue
                if not merge:
                    sorted_vals = sorted(by_end.values(),
                                         key=lambda x: x["end"], reverse=True)
                    return [(v["end"], v["val"]) for v in sorted_vals[:n]]
                # Earlier concepts win outright; later ones only fill gaps.
                for _end, _fact in by_end.items():
                    collected.setdefault(_end, _fact)
        if not collected:
            return []
        sorted_vals = sorted(collected.values(), key=lambda x: x["end"], reverse=True)
        return [(v["end"], v["val"]) for v in sorted_vals[:n]]

    def _annual(concepts, n: int = 5):
        return [val for _end, val in _annual_dated(concepts, n)]

    def _align(dated_series: dict, timeline: list) -> dict:
        """Line every series up on the same fiscal year ends.

        _annual returned bare values and the scoring engine paired them by
        index, assuming every concept covered the same years. Concepts differ:
        a filer can tag Revenues for five years and OperatingIncomeLoss for
        four, or start a concept later. Index pairing then divided one year's
        operating income into another year's revenue, which is how operating
        margin came out as 11.1% against a real 38%. Missing years become None
        rather than silently shifting the rest of the series.
        """
        aligned = {}
        for field, pairs in dated_series.items():
            by_end = {end: val for end, val in pairs}
            aligned[field] = [by_end.get(end) for end in timeline]
        return aligned

    # ── 4. Income statement ──────────────────────────────────────────────────
    # US-GAAP names first, then IFRS equivalents (for 20-F filers like TSM)
    rev_d = _annual_dated(["RevenueFromContractWithCustomerExcludingAssessedTax",
                   "RevenueFromContractWithCustomerIncludingAssessedTax",
                   "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet",
                   # IFRS equivalents
                   "Revenue", "RevenueFromContractWithCustomer",
                   "SalesRevenueFromContractsWithCustomers"], merge=True)
    gp_d  = _annual_dated(["GrossProfit",
                   "GrossProfitLoss"], merge=True)       # IFRS
    oi_d  = _annual_dated(["OperatingIncomeLoss",
                   "ProfitLossFromOperatingActivities",  # IFRS
                   "OperatingProfit"], merge=True)
    # Components used to rebuild subtotals a filer may never present on the
    # face of its income statement.
    cor_d = _annual_dated(["CostOfRevenue",
                   "CostOfGoodsAndServicesSold",
                   "CostOfGoodsSold", "CostOfSales", "CostOfServices",
                   "CostOfRevenueFromContractWithCustomerExcludingAssessedTax",
                   # IFRS
                   "CostOfSalesIfrs"], merge=True)
    tce_d = _annual_dated(["CostsAndExpenses",
                   "BenefitsLossesAndExpenses"], merge=True)
    opex_d = _annual_dated(["OperatingExpenses",
                   "OperatingCostsAndExpenses"], merge=True)
    # When no operating-expense subtotal is tagged either, add up the lines
    # below the gross profit line that the filer does still report. KLA tags
    # R&D and SG&A through the current quarter while its operating subtotal
    # went stale a decade ago.
    _opex_parts = [
        ["ResearchAndDevelopmentExpense",
         "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
        ["SellingGeneralAndAdministrativeExpense",
         "GeneralAndAdministrativeExpense"],
        ["RestructuringCharges", "RestructuringSettlementAndImpairmentProvisions"],
        ["AmortizationOfIntangibleAssets"],
        ["GoodwillAndIntangibleAssetImpairment", "GoodwillImpairmentLoss"],
        ["OtherOperatingIncomeExpenseNet"],
    ]
    _opex_maps = [dict(_annual_dated(names, merge=True)) for names in _opex_parts]
    ni_d  = _annual_dated(["NetIncomeLoss", "NetIncome",
                   "NetIncomeLossAvailableToCommonStockholdersBasic",
                   # IFRS equivalents
                   "ProfitLoss",
                   "ProfitLossAttributableToOwnersOfParent",
                   "ComprehensiveIncome"])
    eps_d = _annual_dated(["EarningsPerShareDiluted", "EarningsPerShareBasic",
                   "DilutedEarningsLossPerShare",        # IFRS
                   "BasicEarningsLossPerShare"])

    # Align the income statement on one set of fiscal year ends before the
    # scoring engine pairs them by index.
    _dated = {
        "revenue": rev_d, "gross_profit": gp_d, "operating_income": oi_d,
        "net_income": ni_d, "diluted_eps": eps_d,
    }
    _timeline = sorted(
        {end for pairs in _dated.values() for end, _val in pairs}, reverse=True)[:5]
    _aligned = _align(_dated, _timeline)

    # ── 4b. Rebuild the subtotals the filer never tagged ─────────────────────
    # KLA runs "Total revenues" straight into "Costs and expenses" with no
    # gross profit subtotal on the face of the statement, so EDGAR carries no
    # GrossProfit fact for any year. The margin trail was therefore blank for
    # every historical year while the TTM provider supplied the newest point
    # alone - the page showed 61.6% and then four N/As. Both subtotals are
    # identities over lines the filer does tag, so rebuild them instead of
    # reporting the history as unavailable. Only gaps are filled; a directly
    # tagged value is never overwritten.
    _rev_a = _aligned["revenue"]
    _gp_a  = _aligned["gross_profit"]
    _oi_a  = _aligned["operating_income"]
    _cor_a = _align({"x": cor_d}, _timeline)["x"]
    _tce_a = _align({"x": tce_d}, _timeline)["x"]
    _opx_a = _align({"x": opex_d}, _timeline)["x"]
    derived_lines: list[str] = []

    def _fill_gaps(target, compute, plausible) -> int:
        filled = 0
        for i in range(len(_timeline)):
            if target[i] is not None:
                continue
            candidate = compute(i)
            if candidate is None or not plausible(candidate, i):
                continue
            target[i] = candidate
            filled += 1
        return filled

    def _plausible_gp(value, i):
        rev = _rev_a[i]
        return rev is not None and rev > 0 and 0 < value <= rev

    def _plausible_oi(value, i):
        rev = _rev_a[i]
        if rev is None or rev <= 0 or value > rev:
            return False
        gp = _gp_a[i]
        # Operating income cannot exceed gross profit; 2% of slack absorbs
        # rounding and the odd credit booked below the gross line.
        return gp is None or value <= gp * 1.02

    def _diff(a, b):
        return lambda i: (a[i] - b[i]) if a[i] is not None and b[i] is not None else None

    if _fill_gaps(_gp_a, _diff(_rev_a, _cor_a), _plausible_gp):
        derived_lines.append("gross profit = revenue - cost of revenue")
    if _fill_gaps(_oi_a, _diff(_rev_a, _tce_a), _plausible_oi):
        derived_lines.append("operating income = revenue - total costs and expenses")
    if _fill_gaps(_oi_a, _diff(_gp_a, _opx_a), _plausible_oi):
        derived_lines.append("operating income = gross profit - operating expenses")

    def _summed_opex(i):
        """Gross profit less every operating expense line that is tagged.

        Requires the two that dominate an operating cost base - research and
        development, and selling, general and administrative. Without both,
        the sum understates expenses and would overstate operating income,
        which is the direction that flatters a company.
        """
        end = _timeline[i]
        parts = [m.get(end) for m in _opex_maps]
        if parts[0] is None or parts[1] is None:
            return None
        total = sum(x for x in parts if x is not None)
        gp = _gp_a[i]
        return (gp - total) if gp is not None else None

    if _fill_gaps(_oi_a, _summed_opex, _plausible_oi):
        derived_lines.append(
            "operating income = gross profit - R&D - SG&A - other operating costs")

    result["derived_lines"] = derived_lines
    result.update(
        revenue=_aligned["revenue"], gross_profit=_aligned["gross_profit"],
        operating_income=_aligned["operating_income"],
        net_income=_aligned["net_income"], diluted_eps=_aligned["diluted_eps"],
        fiscal_period_ends=_timeline,
    )
    rev, ni = _aligned["revenue"], _aligned["net_income"]
    if not rev and not ni:
        missing.append("income_statement")

    # ── 5. Balance sheet ─────────────────────────────────────────────────────
    ta  = _annual(["Assets"])                   # same in IFRS
    tl  = _annual(["Liabilities"])              # same in IFRS
    eq  = _annual(["StockholdersEquity",
                   "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                   # IFRS equivalents
                   "Equity", "EquityAttributableToOwnersOfParent"])
    ca  = _annual(["AssetsCurrent",
                   "CurrentAssets"])            # IFRS
    cl  = _annual(["LiabilitiesCurrent",
                   "CurrentLiabilities"])       # IFRS
    csh_pairs = _annual_dated(["CashAndCashEquivalentsAtCarryingValue",
                   "CashCashEquivalentsAndShortTermInvestments",
                   "CashAndCashEquivalents",
                   # IFRS equivalents
                   "CashAndCashEquivalentsIfrs",
                   "CashAndBankBalancesAtCentralBanks"])
    csh = [val for _end, val in csh_pairs]
    _cash_ends = [end for end, _val in csh_pairs]

    # Liquidity and near-term obligations. Neither of these was ever populated
    # on the EDGAR path, so the cash-coverage row compared bare cash against
    # TOTAL debt and reported "current portion unavailable" - it could not pass
    # for any company carrying termed-out long-term debt. Both series are keyed
    # to the cash series' period ends so index 0 compares the same balance
    # sheet date on both sides rather than whichever date each concept happened
    # to cover.
    _csi_map = dict(_annual_dated(["CashCashEquivalentsAndShortTermInvestments"]))
    _sti_map = dict(_annual_dated(["ShortTermInvestments",
                   "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
                   "MarketableSecuritiesCurrent",
                   "AvailableForSaleSecuritiesCurrent",
                   "OtherShortTermInvestments",
                   # IFRS
                   "CurrentInvestments"], merge=True))
    # DebtCurrent is every borrowing due inside a year and comes first because
    # a coverage test wants the whole near-term obligation, not just the
    # current slice of long-term notes.
    _std_map = dict(_annual_dated(["DebtCurrent",
                   "LongTermDebtCurrent",
                   "LongTermDebtAndCapitalLeaseObligationsCurrent",
                   "ShortTermBorrowings",
                   "OtherShortTermBorrowings",
                   "CommercialPaper",
                   "LinesOfCreditCurrent",
                   # IFRS
                   "ShorttermBorrowings",
                   "CurrentPortionOfLongtermBorrowings"]))
    liquid, std = [], []
    for _i, _end in enumerate(_cash_ends):
        _direct = _csi_map.get(_end)
        if _direct is not None:
            liquid.append(_direct)
        else:
            _c, _s = csh[_i], _sti_map.get(_end)
            liquid.append((_c + _s) if _c is not None and _s is not None else _c)
        std.append(_std_map.get(_end))
    if not any(x is not None for x in std):
        std = []
    td  = _annual(["LongTermDebt",
                                      "LongTermDebtAndCapitalLeaseObligation",   # correct XBRL concept (no trailing 's')
                                      "LongTermDebtAndCapitalLeaseObligations",  # alternate spelling as fallback
                                      "LongTermDebtNoncurrent",
                                      # IFRS equivalents
                                      "Borrowings", "BorrowingsAndBankOverdrafts",
                                      "LongtermBorrowings",
                                      # Current/short-term debt as final fallbacks
                                      "DebtCurrent",
                                      "ShorttermBorrowings"])
    gw  = _annual(["Goodwill"])                 # same in IFRS
    ia  = _annual(["IntangibleAssetsNetExcludingGoodwill",
                   "FiniteLivedIntangibleAssetsNet",
                   "IntangibleAssetsOtherThanGoodwill"])   # IFRS
    re  = _annual(["RetainedEarningsAccumulatedDeficit",
                   "RetainedEarnings"])         # IFRS

    result["balance_period_ends"] = _cash_ends
    result.update(total_assets=ta, total_liabilities=tl, total_equity=eq,
                  current_assets=ca, current_liabilities=cl, cash=csh,
                  cash_and_st_investments=liquid, short_term_debt=std,
                  total_debt=td, goodwill=gw, intangible_assets=ia,
                  retained_earnings=re)
    if not ta and not eq:
        missing.append("balance_sheet")

    # ── 6. Cash flow ─────────────────────────────────────────────────────────
    ocf = _annual(["NetCashProvidedByUsedInOperatingActivities",
                   "CashFlowsFromUsedInOperatingActivities"])   # IFRS
    # The IFRS element is PurchaseOf..., not AcquisitionOf.... Getting that one
    # name wrong cost a 20-F filer three rows: with no capex there is no free
    # cash flow, and with no free cash flow the FCF-positive test, the earnings
    # quality test and the capex-to-revenue test all read N/A while operating
    # cash flow sat right there on the same card.
    cap = _annual(["PaymentsToAcquirePropertyPlantAndEquipment",
                   "PaymentsToAcquireProductiveAssets",
                   "CapitalExpendituresIncurredButNotYetPaid",
                   # IFRS equivalents
                   "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
                   "PurchaseOfPropertyPlantAndEquipment",
                   "PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",
                   "AcquisitionOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"])
    fin = _annual(["NetCashProvidedByUsedInFinancingActivities",
                   "CashFlowsFromUsedInFinancingActivities"])   # IFRS

    cap_abs = [abs(c) if c is not None else None for c in cap]
    fcf = []
    for i in range(max(len(ocf), len(cap_abs))):
        o = ocf[i] if i < len(ocf) else None
        c = cap_abs[i] if i < len(cap_abs) else None
        fcf.append((o - c) if o is not None and c is not None else None)

    result.update(operating_cash_flow=ocf, capex=cap_abs,
                  free_cash_flow=fcf, financing_cash_flow=fin)
    if not ocf:
        missing.append("cash_flow")

    # ── 7. Derived ratios ────────────────────────────────────────────────────
    ni0_d = ni[0] if ni else None
    eq0_d = eq[0] if eq else None
    _oi_aligned = _aligned["operating_income"]
    oi0_d = _oi_aligned[0] if _oi_aligned else None
    td0_d = td[0] if td else 0

    if ni0_d is not None and eq0_d and eq0_d != 0:
        result["roe"] = (ni0_d / eq0_d) * 100

    # ROIC = NOPAT / Invested Capital  (NOPAT = OperatingIncome × (1 - tax_rate))
    if oi0_d and ni0_d and eq0_d is not None and oi0_d != 0:
        try:
            tax_rate = max(0.0, min(0.40, 1.0 - (ni0_d / oi0_d)))
            nopat = oi0_d * (1.0 - tax_rate)
            invested_capital = (eq0_d or 0) + (td0_d or 0)
            if invested_capital > 0:
                result["roic"] = (nopat / invested_capital) * 100
        except Exception:
            pass

    # ── 8. Share count ───────────────────────────────────────────────────────
    # Diluted weighted-average shares, oldest-last, aligned to the income
    # statement's fiscal year ends. A company can grow revenue and earnings
    # while issuing so much stock that per-share value falls, which no other
    # row on the card would catch.
    # ...Adjustment is the dilutive increment — single-digit millions — not a
    # share count. In the merged lookup it filled period ends the real concepts
    # did not cover, producing a series like [150M, 148M, 2.1M] that either
    # tripped the split detector or reported several thousand percent dilution.
    _share_concepts = ["WeightedAverageNumberOfDilutedSharesOutstanding",
                       "WeightedAverageNumberOfSharesOutstandingDiluted",
                       # IFRS
                       "WeightedAverageNumberOfOrdinarySharesOutstanding"]
    # One filing's comparative columns first, since they are guaranteed to sit
    # on one split basis. A 10-K carries about three years, so fall back to the
    # merged series when that leaves too little to measure a trend; the split
    # guard in the scoring layer catches a spliced basis either way.
    _shares_map = dict(_annual_dated(_share_concepts, same_filing=True))
    if len(_shares_map) < 3:
        _merged_shares = dict(_annual_dated(_share_concepts, merge=True))
        if len(_merged_shares) > len(_shares_map):
            _shares_map = _merged_shares
    if not _shares_map:
        _shares_map = dict(_annual_dated(["CommonStockSharesOutstanding",
                       "CommonStockSharesIssued",
                       "EntityCommonStockSharesOutstanding"], same_filing=True))
    result["diluted_shares"] = [_shares_map.get(end) for end in _timeline]

    # Return on invested capital for every year, not just the latest. One good
    # year is a cycle; a decade of them is the arithmetic signature of a moat.
    _td_map = dict(_annual_dated(["LongTermDebt",
                   "LongTermDebtAndCapitalLeaseObligation",
                   "LongTermDebtAndCapitalLeaseObligations",
                   "LongTermDebtNoncurrent",
                   "Borrowings", "BorrowingsAndBankOverdrafts",
                   "LongtermBorrowings", "DebtCurrent", "ShorttermBorrowings"]))
    _eq_map = dict(_annual_dated(["StockholdersEquity",
                   "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                   "Equity", "EquityAttributableToOwnersOfParent"]))
    roic_series = []
    for _i, _end in enumerate(_timeline):
        _oi = _aligned["operating_income"][_i]
        _ni = _aligned["net_income"][_i]
        _eq = _eq_map.get(_end)
        _capital = (_eq or 0) + (_td_map.get(_end) or 0)
        if not _oi or _ni is None or _eq is None or _capital <= 0:
            roic_series.append(None)
            continue
        _tax = max(0.0, min(0.40, 1.0 - (_ni / _oi)))
        roic_series.append((_oi * (1.0 - _tax) / _capital) * 100)
    result["roic_series"] = roic_series

    # Return None only if ALL major sections are missing
    if len(missing) >= 3:
        logger.warning("EDGAR returned insufficient data for %s", ticker)
        return None

    return result


# ─── Financial Modeling Prep (FMP) data source ───────────────────────────────
# FMP is used as the primary data source when FMP_API_KEY is set in the
# environment.  It avoids the Yahoo Finance IP-based rate-limiting that blocks
# yfinance on cloud-hosted servers like Render.
# Free tier: 250 requests/day — plenty for personal use with 24-hr caching.
# Register at https://financialmodelingprep.com to get a free API key, then
# add FMP_API_KEY to your Render environment variables.

_FMP_API_KEY  = os.environ.get("FMP_API_KEY", "")
# FMP updated their API in 2024: free-tier accounts use the /stable/ base URL
# with ?symbol= query params instead of ticker in the path.
_FMP_BASE_V3     = "https://financialmodelingprep.com/api/v3"
_FMP_BASE_STABLE = "https://financialmodelingprep.com/stable"


def _fmp_get(endpoint: str, params: dict | None = None, ticker: str = "") -> list | dict | None:
    """
    GET from FMP API. Tries the new /stable/ endpoint first (free-tier friendly),
    falls back to the legacy /api/v3/ path. Returns parsed JSON or None on error.
    """
    if not _FMP_API_KEY:
        return None

    base_params = {"apikey": _FMP_API_KEY}
    if ticker:
        base_params["symbol"] = ticker
    if params:
        base_params.update(params)

    # Try /stable/ first (free tier)
    for base in (_FMP_BASE_STABLE, _FMP_BASE_V3):
        try:
            if base == _FMP_BASE_V3 and ticker:
                url = f"{base}/{endpoint}/{ticker}"
                p = {"apikey": _FMP_API_KEY}
                if params:
                    p.update(params)
            else:
                url = f"{base}/{endpoint}"
                p = base_params
            resp = _req_module.get(url, params=p, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                # FMP returns {"Error Message": "..."} on bad key / no access
                if isinstance(data, dict) and "Error Message" in data:
                    logger.warning("FMP error (%s): %s", endpoint, data["Error Message"])
                    return None
                return data
            logger.warning("FMP API %d for %s (base: %s)", resp.status_code, endpoint, base)
        except Exception as exc:
            logger.warning("FMP request error (%s @ %s): %s", endpoint, base, exc)
    return None


def fetch_fundamentals_fmp(ticker: str) -> dict | None:
    """
    Fetch fundamental data from Financial Modeling Prep.
    Returns the same raw-result dict as fetch_fundamentals_raw(), or None if
    FMP_API_KEY is not configured or if all four API calls fail.
    """
    if not _FMP_API_KEY:
        return None

    result: dict[str, Any] = {
        "ticker": ticker.upper(),
        "missing_fields": [],
        "revenue": [], "gross_profit": [], "operating_income": [],
        "net_income": [], "diluted_eps": [],
        "total_assets": [], "total_liabilities": [], "total_equity": [],
        "current_assets": [], "current_liabilities": [],
        "cash": [], "cash_and_st_investments": [], "short_term_debt": [], "total_debt": [], "goodwill": [],
        "intangible_assets": [], "retained_earnings": [],
        "operating_cash_flow": [], "capex": [], "free_cash_flow": [],
        "financing_cash_flow": [],
        "roe": None, "roic": None, "insider_pct": None,
        "company_name": None, "sector": None, "industry": None,
    }
    missing = result["missing_fields"]

    try:
        # ── Company profile ──────────────────────────────────────────────────
        profile = _fmp_get("profile", ticker=ticker)
        if profile and isinstance(profile, list) and profile:
            p = profile[0]
            result["company_name"] = p.get("companyName")
            result["sector"]       = p.get("sector")
            result["industry"]     = p.get("industry")
        else:
            missing.append("company_info")

        # ── Income statement ─────────────────────────────────────────────────
        inc = _fmp_get("income-statement", {"limit": 5, "period": "annual"}, ticker=ticker)
        if inc and isinstance(inc, list):
            for row in inc:
                result["revenue"].append(row.get("revenue"))
                result["gross_profit"].append(row.get("grossProfit"))
                result["operating_income"].append(row.get("operatingIncome"))
                result["net_income"].append(row.get("netIncome"))
                result["diluted_eps"].append(row.get("epsdiluted") or row.get("eps"))
        else:
            missing.append("income_statement")

        # ── Balance sheet ────────────────────────────────────────────────────
        bs = _fmp_get("balance-sheet-statement", {"limit": 5, "period": "annual"}, ticker=ticker)
        if bs and isinstance(bs, list):
            for row in bs:
                result["total_assets"].append(row.get("totalAssets"))
                result["total_liabilities"].append(row.get("totalLiabilities"))
                result["total_equity"].append(row.get("totalStockholdersEquity"))
                result["current_assets"].append(row.get("totalCurrentAssets"))
                result["current_liabilities"].append(row.get("totalCurrentLiabilities"))
                result["cash"].append(row.get("cashAndCashEquivalents"))
                # Cash alone understates liquidity for companies that park money in
                # short-term investments (KLA: $1.6B cash vs $4.9B cash+STI).
                result["cash_and_st_investments"].append(
                    row.get("cashAndShortTermInvestments") or row.get("cashAndCashEquivalents"))
                # Obligations actually due inside 12 months. The rubric asks whether
                # cash covers a year of obligations, not the entire debt stack.
                result["short_term_debt"].append(row.get("shortTermDebt"))
                result["total_debt"].append(row.get("totalDebt"))
                result["goodwill"].append(row.get("goodwill"))
                result["intangible_assets"].append(row.get("intangibleAssets"))
                result["retained_earnings"].append(row.get("retainedEarnings"))
        else:
            missing.append("balance_sheet")

        # ── Cash flow ────────────────────────────────────────────────────────
        cf = _fmp_get("cash-flow-statement", {"limit": 5, "period": "annual"}, ticker=ticker)
        if cf and isinstance(cf, list):
            for row in cf:
                ocf = row.get("operatingCashFlow")
                cap = row.get("capitalExpenditure")
                fcf = row.get("freeCashFlow")
                fin = (row.get("netCashUsedProvidedByFinancingActivities")
                       or row.get("financingActivitiesCashFlow"))
                if cap is not None:
                    cap = abs(cap)
                result["operating_cash_flow"].append(ocf)
                result["capex"].append(cap)
                result["free_cash_flow"].append(fcf)
                result["financing_cash_flow"].append(fin)
        else:
            missing.append("cash_flow")

        # ── Key metrics for ROE / ROIC ───────────────────────────────────────
        km = _fmp_get("key-metrics-ttm", ticker=ticker)
        if km and isinstance(km, list) and km:
            k = km[0]
            roe  = k.get("roeTTM")
            roic = k.get("roicTTM")
            if roe  is not None:
                result["roe"]  = float(roe)  * 100
            if roic is not None:
                result["roic"] = float(roic) * 100

        # Return None only if ALL major sections are missing (FMP returned nothing useful)
        if len(missing) >= 4:
            logger.warning("FMP returned no data for %s — falling back to yfinance", ticker)
            return None

    except Exception as exc:
        logger.warning("FMP fetch error for %s: %s", ticker, exc)
        return None

    return result

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
        "def": "Checks whether cash and short-term investments cover every borrowing that comes due inside a year.",
        "why": "A company that can retire its near-term maturities out of liquid assets never has to refinance on someone else's terms. Comparing cash against the entire debt stack instead would fail every healthy company that has termed out its borrowing.",
        "formula": "Cash & Short-Term Investments ≥ Debt Due Within 12 Months",
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
        "def": "An economic moat is a durable competitive advantage that protects a company's profits from competition. This row scores the evidence a moat leaves in the numbers, not the moat itself.",
        "why": "Competition drives returns on capital down toward the cost of capital. A company that holds ROIC above 10% year after year while its gross margin does not erode is being protected by something — brand, switching costs, network effects, scale. What that something is still requires reading the 10-K; this row only tells you whether there is anything there to look for.",
        "formula": "ROIC above 10% in every reported year AND gross margin not down more than 3 points across the trail",
    },
    "share_dilution": {
        "def": "Whether the diluted share count is shrinking (buybacks) or growing (issuance).",
        "why": "You own a fraction of a company, not the company. Revenue and earnings can both rise while the share count rises faster, leaving you with less than you started with. A falling count means every remaining share owns more of the same business — it is the one line on the card that measures what happens to your slice rather than the pie.",
        "formula": "Newest diluted share count ÷ oldest reported count − 1",
    },
}

# ─── Red flag definitions ─────────────────────────────────────────────────────

RED_FLAG_DEFS = {
    "restated_financials": "The company has told investors its previously issued financial statements cannot be relied on (8-K item 4.02).",
    "bankruptcy_filing": "The company has filed for bankruptcy or entered receivership (8-K item 1.03).",
    "delisting_notice": "The company has received a delisting or listing-standard notice from its exchange (8-K item 3.01).",
    "auditor_changed": "The company changed its accounting firm (8-K item 4.01).",
    "late_filing": "The company told the SEC it could not file a periodic report on time.",
    "income_positive_fcf_negative": "Net income is positive but Free Cash Flow is negative — earnings may not be backed by real cash.",
    "debt_growing_faster_than_revenue": "Total debt is growing faster than revenue year-over-year — leverage is increasing relative to the business.",
    "goodwill_impairment": "Goodwill impairment detected in the most recent year — a prior acquisition has declined in value.",
    "current_ratio_below_1": "Current ratio is below 1.0 — current liabilities exceed liquid assets, a near-term liquidity risk.",
    "goodwill_over_30pct": "Goodwill + intangibles exceed 30% of total assets — a write-down could vaporize earnings.",
}

# Flags that drop the verdict one full band. current_ratio_below_1 is shown as a
# warning but deliberately excluded: liquidity is already scored in Section 1,
# and downgrading on it as well would count the same weakness twice.
DOWNGRADE_TRIGGERS = {
    "income_positive_fcf_negative",
    "debt_growing_faster_than_revenue",
    "goodwill_impairment",
    "goodwill_over_30pct",
    # Filed disclosures, not derived from the statements. A restatement says
    # the numbers every other row is built from cannot be relied on, so no
    # score computed from them earns a top verdict.
    "restated_financials",
    "bankruptcy_filing",
    "delisting_notice",
}

# Worst-first, so a downgrade can step exactly one band.
VERDICT_BANDS = ["Avoid", "Caution", "Good", "Great Company"]


def apply_downgrade(verdict: str, red_flags: list) -> tuple[str, list]:
    """Drop the verdict one band if any auto-downgrade trigger fired.

    The rubric has always said a trigger drops the verdict a full band, but the
    verdict was computed from the score alone and the flags were only ever
    displayed. A company could report a goodwill impairment and FCF below net
    income and still read "Great Company".
    """
    fired = [flag for flag in (red_flags or []) if flag.get("key") in DOWNGRADE_TRIGGERS]
    if not fired or verdict not in VERDICT_BANDS:
        return verdict, fired
    index = VERDICT_BANDS.index(verdict)
    return VERDICT_BANDS[max(0, index - 1)], fired


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
    Fetch raw financial data and return a structured dict.
    Tries Financial Modeling Prep (FMP) first when FMP_API_KEY is set —
    FMP is not subject to Yahoo Finance's cloud-IP rate-limiting.
    Falls back to yfinance if FMP is not configured or returns no data.
    """
    # ── EDGAR primary path (free, no key needed) ─────────────────────────────
    edgar_result = fetch_fundamentals_edgar(ticker)
    if edgar_result is not None:
        return edgar_result
    logger.warning("EDGAR returned nothing for %s, trying FMP/yfinance", ticker)

    # ── FMP secondary path (requires FMP_API_KEY env var) ────────────────────
    if _FMP_API_KEY:
        fmp_result = fetch_fundamentals_fmp(ticker)
        if fmp_result is not None:
            return fmp_result
        logger.warning("FMP returned nothing for %s, falling back to yfinance", ticker)

    # ── yfinance fallback ────────────────────────────────────────────────────
    # Quick connectivity check: if Yahoo Finance is unreachable (Render cloud IPs
    # are IP-blocked), return an error immediately instead of hanging for 30s.
    _yf_reachable = False
    try:
        import requests as _rq_check
        _probe = _rq_check.head(
            "https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
            timeout=4,
            headers={"User-Agent": "Mozilla/5.0"},
            allow_redirects=True,
        )
        _yf_reachable = _probe.status_code < 500
    except Exception:
        _yf_reachable = False

    if not _yf_reachable:
        logger.warning("yfinance unreachable for %s — Yahoo Finance is blocking this server's IP. "
                       "EDGAR had no XBRL data for this ticker.", ticker)
        return {
            "ticker": ticker.upper(),
            "missing_fields": ["income_statement", "balance_sheet", "cash_flow"],
            "company_name": None, "sector": None, "industry": None,
            "roe": None, "roic": None, "insider_pct": None,
            "revenue": [], "gross_profit": [], "operating_income": [], "net_income": [], "diluted_eps": [],
            "total_assets": [], "total_liabilities": [], "total_equity": [],
            "current_assets": [], "current_liabilities": [], "cash": [],
            "total_debt": [], "goodwill": [], "intangible_assets": [], "retained_earnings": [],
            "operating_cash_flow": [], "capex": [], "free_cash_flow": [], "financing_cash_flow": [],
            "error": (
                f"No SEC EDGAR data found for {ticker}. "
                "This ticker may not file with the SEC (foreign stock not listed as ADR), "
                "may be too new (recent IPO with no annual filing yet), or may use a "
                "different ticker symbol in EDGAR. Try the full exchange ticker if this is an ADR."
            ),
        }

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
        import requests as _requests
    except ImportError:
        result["error"] = "yfinance is not installed on this server."
        return result

    try:
        # Use a browser-like session to avoid Yahoo Finance rate-limiting on cloud hosts
        _session = _requests.Session()
        _session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        t = yf.Ticker(ticker, session=_session)
        _info_cache: dict = {}  # shared info dict — fetched once, reused everywhere

        # ── Info ──────────────────────────────────────────────────────────────
        try:
            _info_cache.update(t.info or {})
            info = _info_cache
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
            inc = None
            for _attr in ("income_stmt", "financials"):
                try:
                    _df = getattr(t, _attr)
                    if _df is not None and not _df.empty:
                        inc = _df
                        break
                except Exception:
                    continue
            if inc is not None and not inc.empty:
                def _norm(text):
                    return str(text).lower().replace(" ", "").replace("_", "")

                def _row(*labels):
                    """First row matching any label, exact match preferred.

                    Two bugs lived here. Callers chained alternatives with
                    `_row(a) or _row(b)`, but `or` on a pandas Series raises
                    ValueError - and the whole block sits under a bare `except`,
                    so any successful match silently killed the entire income
                    statement. And a bare substring test let a short label match
                    a different metric: "ebit" matches "ebitda".
                    """
                    keys = {_norm(k): k for k in inc.index}
                    for label in labels:                       # exact wins
                        key = keys.get(_norm(label))
                        if key is not None:
                            return inc.loc[key]
                    for label in labels:                       # then prefix
                        norm = _norm(label)
                        if len(norm) < 5:
                            continue    # too short to disambiguate safely
                        for key_norm, original in keys.items():
                            if key_norm.startswith(norm):
                                return inc.loc[original]
                    return None

                rev_row  = _row("Total Revenue", "Revenue")
                gp_row   = _row("Gross Profit", "GrossProfit")
                oi_row   = _row("Operating Income", "OperatingIncome", "EBIT")
                ni_row   = _row("Net Income", "NetIncome", "Net Income Common Stockholders")

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
            cf = None
            for _attr in ("cash_flow", "cashflow"):
                try:
                    _df = getattr(t, _attr)
                    if _df is not None and not _df.empty:
                        cf = _df
                        break
                except Exception:
                    continue
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

        # ── t.info fallback: populate financial lists from info if DataFrames empty ──
        # t.info reliably returns single-year financial data even when the
        # statement endpoints are rate-limited by Yahoo Finance on cloud hosts.
        try:
            info_fb = _info_cache  # reuse already-fetched info — do NOT call t.info again

            if not result["revenue"]:
                rev = info_fb.get("totalRevenue")
                if rev:
                    result["revenue"] = [float(rev)]
                    missing[:] = [m for m in missing if m != "income_statement"]

            if not result["gross_profit"]:
                gp = info_fb.get("grossProfits")
                if gp:
                    result["gross_profit"] = [float(gp)]

            if not result["operating_income"]:
                oi = info_fb.get("operatingIncomeRatio") and result["revenue"] and \
                     info_fb.get("operatingIncomeRatio") * result["revenue"][0]
                if not oi:
                    oi = info_fb.get("ebitda")  # close enough for scoring
                if oi:
                    result["operating_income"] = [float(oi)]

            if not result["net_income"]:
                ni = info_fb.get("netIncomeToCommon") or info_fb.get("netIncome")
                if ni:
                    result["net_income"] = [float(ni)]

            if not result["total_assets"]:
                ta = info_fb.get("totalAssets")
                if ta:
                    result["total_assets"] = [float(ta)]
                    missing[:] = [m for m in missing if m != "balance_sheet"]

            if not result["total_debt"]:
                td = info_fb.get("totalDebt")
                if td:
                    result["total_debt"] = [float(td)]

            if not result["total_equity"]:
                eq = info_fb.get("bookValue") and info_fb.get("sharesOutstanding") and \
                     info_fb.get("bookValue") * info_fb.get("sharesOutstanding")
                if not eq:
                    eq = info_fb.get("totalStockholderEquity")
                if eq:
                    result["total_equity"] = [float(eq)]

            if not result["current_assets"]:
                ca = info_fb.get("totalCurrentAssets")
                if ca:
                    result["current_assets"] = [float(ca)]

            if not result["current_liabilities"]:
                cl = info_fb.get("totalCurrentLiabilities")
                if cl:
                    result["current_liabilities"] = [float(cl)]

            if not result["cash"]:
                cash = info_fb.get("totalCash") or info_fb.get("cashAndShortTermInvestments")
                if cash:
                    result["cash"] = [float(cash)]

            if not result["free_cash_flow"]:
                fcf = info_fb.get("freeCashflow")
                if fcf:
                    result["free_cash_flow"] = [float(fcf)]
                    missing[:] = [m for m in missing if m != "cash_flow"]

            if not result["operating_cash_flow"]:
                ocf = info_fb.get("operatingCashflow")
                if ocf:
                    result["operating_cash_flow"] = [float(ocf)]

            if not result["diluted_eps"]:
                eps = info_fb.get("trailingEps") or info_fb.get("forwardEps")
                if eps:
                    result["diluted_eps"] = [float(eps)]

        except Exception as e:
            logger.debug("fundamentals info fallback error %s: %s", ticker, e)

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

    # Clean up missing list based on what the info fallback may have filled
    if result["revenue"]:
        missing[:] = [m for m in missing if m != "income_statement"]
    if result["total_assets"]:
        missing[:] = [m for m in missing if m != "balance_sheet"]
    if result["free_cash_flow"] or result["operating_cash_flow"]:
        missing[:] = [m for m in missing if m != "cash_flow"]

    result["missing_fields"] = list(set(missing))
    return result


# ─── Scoring ──────────────────────────────────────────────────────────────────

def _score_check(condition, points: int) -> tuple[int, int, object]:
    """Score one row. Returns (earned, available, passed).

    ``condition`` is True, False, None for missing — or a fraction between 0
    and 1 for partial credit. Some criteria are not honestly binary: a company
    two years into a three-year revenue streak has not met the test, but it is
    not in the same position as one whose revenue is falling, and scoring both
    zero throws away the distinction. Free cash flow at 0.78x of net income is
    the same shape of answer.

    Partial always earns at least one point and always less than full, so it
    can never be mistaken for either neighbour.
    """
    if condition is None:
        return 0, 0, None
    if condition is True or condition is False:
        return (points if condition else 0), points, bool(condition)
    try:
        fraction = float(condition)
    except (TypeError, ValueError):
        return 0, 0, None
    if fraction >= 1:
        return points, points, True
    if fraction <= 0:
        return 0, points, False
    earned = min(max(1, round(points * fraction)), max(points - 1, 0))
    return earned, points, "partial"


def score_fundamentals(raw: dict) -> dict:
    """
    Score a raw fundamentals dict and return a structured scorecard.

    When raw["_ttm_metrics"] is populated (by finnhub_ttm.build_fundamentals_data),
    TTM/quarterly values are preferred over annual snapshots:
      - Gross / operating / net margin trend: TTM value replaces position-0 in series
      - Current ratio, D/E ratio: quarterly values from Finnhub override balance-sheet calc
      - FCF: TTM FCF (from Finnhub metric or computed from quarterly) overrides annual FCF
      - ROE / ROIC: TTM values already injected into raw["roe"] / raw["roic"] by build_fundamentals_data

    Returns dict with sections, total_score, max_possible, verdict, red_flags,
    partial_score, scored_metrics, total_metrics, and per-metric metadata.
    """

    def v(lst, i=0):
        """Get value at index i from list, None if missing."""
        try:
            val = lst[i] if lst and i < len(lst) else None
            return val
        except (IndexError, TypeError):
            return None

    # ── Extract TTM overlay ───────────────────────────────────────────────────
    ttm = raw.get("_ttm_metrics", {}) or {}

    def _ttm_period_label(key: str) -> str:
        """Return the period label for a metric key given the TTM source."""
        if ttm.get(key) is not None:
            period = ttm.get("period_end") or "TTM"
            return f"TTM (ends {period})" if period else "TTM"
        return "Annual"

    def _ttm_source(key: str) -> str:
        """Return the source endpoint string for a given TTM metric key."""
        src_map = {
            "gross_margin_ttm":     "finnhub_metric",
            "operating_margin_ttm": "finnhub_metric",
            "net_margin_ttm":       "finnhub_metric",
            "roe_ttm":              "finnhub_metric",
            "roi_ttm":              "finnhub_metric",
            "current_ratio_q":      "finnhub_metric",
            "de_ratio_q":           "finnhub_metric",
            "fcf_ttm_usd":          ttm.get("sources", {}).get("fcf", "annual"),
        }
        return src_map.get(key, "annual")

    # ── Pre-compute helper values ─────────────────────────────────────────────
    cr0 = (v(raw.get("current_assets", [])) / v(raw.get("current_liabilities", []))
           if v(raw.get("current_assets", [])) and v(raw.get("current_liabilities", [])) and v(raw.get("current_liabilities", [])) != 0
           else None)
    cr0_source = "annual"
    cr0_period = "Annual"

    # Prefer Finnhub quarterly current ratio
    if ttm.get("current_ratio_q") is not None:
        cr0 = ttm["current_ratio_q"]
        cr0_source = "finnhub_metric"
        cr0_period = "Quarterly (latest)"

    total_equity = v(raw.get("total_equity", []))
    total_debt   = v(raw.get("total_debt", []))
    de_ratio = (total_debt / total_equity
                if total_debt is not None and total_equity and total_equity != 0
                else None)
    de_source = "annual"
    de_period = "Annual"

    # Prefer Finnhub quarterly D/E ratio
    if ttm.get("de_ratio_q") is not None:
        provider_de = ttm["de_ratio_q"]
        # Guard against a provider unit change. If the quarterly figure disagrees
        # with the balance-sheet computation by more than 10x, something is being
        # reported in different units - keep the number we derived ourselves.
        accepted = de_ratio is None or de_ratio <= 0 or 0.1 <= (provider_de / de_ratio) <= 10
        logger.info(
            "fundamentals D/E  ticker=%s  computed=%s (debt=%s equity=%s)  provider=%s  accepted=%s",
            raw.get("ticker"), de_ratio, total_debt, total_equity, provider_de, accepted,
        )
        # Only claim the provider when the provider's figure was actually used.
        # These sat outside the branch, so a value rejected as a unit mismatch —
        # the exact case this guard exists for — still displayed as reported by
        # Finnhub for the latest quarter, when it was computed here from the
        # annual balance sheet.
        if accepted:
            de_ratio = provider_de
            de_source = "finnhub_metric"
            de_period = "Quarterly (latest)"

    # Liquidity: cash plus short-term investments against obligations due inside a
    # year. The previous check compared bare cash against TOTAL debt, which failed
    # any company carrying termed-out long-term debt regardless of when it matures.
    # Both sides of this comparison are read at the newest period end that
    # actually carries both. Taking index 0 on each meant that a year where the
    # filer had not yet tagged its current maturities produced None on one side
    # only, and the row fell back to comparing cash against the entire debt
    # stack — which is how a company with zero current maturities kept failing
    # a test it passes trivially.
    _cash_list = raw.get("cash", []) or []
    _liquid_list = raw.get("cash_and_st_investments", []) or []
    _std_list = raw.get("short_term_debt", []) or []
    cash0 = v(_cash_list)
    liquid0 = v(_liquid_list)
    if liquid0 is None:
        liquid0 = cash0
    near_term_debt = None
    _cover_period = None
    _balance_ends = raw.get("balance_period_ends") or []
    for _i in range(max(len(_std_list), len(_liquid_list), len(_cash_list))):
        _debt_i = v(_std_list, _i)
        _liq_i = v(_liquid_list, _i)
        if _liq_i is None:
            _liq_i = v(_cash_list, _i)
        if _debt_i is not None and _liq_i is not None:
            near_term_debt, liquid0 = _debt_i, _liq_i
            cash0 = v(_cash_list, _i)
            if _i < len(_balance_ends):
                _cover_period = _balance_ends[_i]
            break
    liquid_label = ("cash and short-term investments"
                    if liquid0 is not None and cash0 is not None and liquid0 != cash0
                    else "cash and equivalents")
    cash_cover_basis = "debt due within a year"
    if near_term_debt is None:
        # No current-portion disclosure available; fall back to the old, stricter
        # comparison rather than silently passing everything.
        near_term_debt = total_debt
        cash_cover_basis = "total debt (current portion unavailable)"
    cash_covers_debt = (liquid0 >= near_term_debt
                        if liquid0 is not None and near_term_debt is not None else None)

    # Retained earnings growing?
    re_vals = [v(raw.get("retained_earnings", []), i) for i in range(min(4, len(raw.get("retained_earnings", []))))]
    re_growing = None
    if len([x for x in re_vals if x is not None]) >= 2:
        valid_re = [(i, x) for i, x in enumerate(re_vals) if x is not None]
        re_growing = all(valid_re[i][1] > valid_re[i+1][1] for i in range(len(valid_re)-1))

    # Goodwill ratio
    gw0 = v(raw.get("goodwill", [])) or 0
    ia0 = v(raw.get("intangible_assets", [])) or 0
    ta0 = v(raw.get("total_assets", []))
    gw_ratio = ((gw0 + ia0) / ta0 if ta0 and ta0 != 0 else None)
    gw_ok = (gw_ratio < 0.30 if gw_ratio is not None else None)

    # Revenue growth 3+ consecutive years
    rev_vals = [v(raw.get("revenue", []), i) for i in range(min(4, len(raw.get("revenue", []))))]
    valid_rev = [(i, x) for i, x in enumerate(rev_vals) if x is not None]
    # The label and the rubric both say "3+ consecutive years", but this checked
    # only two year-over-year increases. KLA passed on a two-year run while FY24
    # revenue had fallen 6.5%, which breaks the streak the label claims.
    rev_growth = None
    rev_streak = 0
    for i in range(len(valid_rev) - 1):
        if valid_rev[i][1] > valid_rev[i + 1][1]:
            rev_streak += 1
        else:
            break
    if len(valid_rev) >= 4:
        # Two years into a three-year streak is not a pass, and it is not the
        # same answer as revenue going backwards either.
        rev_growth = True if rev_streak >= 3 else (0.5 if rev_streak == 2 else False)
    elif len(valid_rev) >= 2:
        # Not enough history to prove three consecutive years. An unbroken run
        # across what there is earns partial credit; a decline still fails.
        # This used to award full marks for clearing a bar the data cannot
        # reach — two years of history and one increase read as a three-year
        # streak.
        rev_growth = 0.5 if rev_streak >= (len(valid_rev) - 1) else False
    elif len(valid_rev) == 1:
        # One number is not a trend in either direction.
        rev_growth = None

    # Gross margin trend
    def _margin(num_vals, den_vals):
        # Five years, matching the history table. This capped at four, so the
        # oldest row in the table always read N/A even when both inputs were
        # present for it.
        margins = []
        for i in range(min(5, max(len(num_vals), len(den_vals)))):
            num, den = v(num_vals, i), v(den_vals, i)
            if num is not None and den and den != 0:
                margins.append(num / den)
            else:
                margins.append(None)
        return margins

    gm_series = _margin(raw.get("gross_profit", []), raw.get("revenue", []))
    om_series = _margin(raw.get("operating_income", []), raw.get("revenue", []))
    nm_series = _margin(raw.get("net_income", []), raw.get("revenue", []))

    # ── TTM margin injection: replace position-0 with TTM scalars ─────────────
    # This is the core fix: annual FY snapshots at [0] may lag a TTM recovery.
    # Injecting the TTM margin as the "most-recent" point makes _trending_up()
    # correctly compare TTM vs the oldest annual snapshot.
    gm_source = "annual"
    om_source = "annual"
    nm_source = "annual"

    if ttm.get("gross_margin_ttm") is not None:
        gm_series = [ttm["gross_margin_ttm"]] + list(gm_series[1:])
        gm_source = "finnhub_metric"

    if ttm.get("operating_margin_ttm") is not None:
        om_series = [ttm["operating_margin_ttm"]] + list(om_series[1:])
        om_source = "finnhub_metric"

    if ttm.get("net_margin_ttm") is not None:
        nm_series = [ttm["net_margin_ttm"]] + list(nm_series[1:])
        nm_source = "finnhub_metric"

    def _trending_up(series):
        valid = [(i, x) for i, x in enumerate(series) if x is not None]
        if len(valid) < 2:
            return None
        return valid[0][1] >= valid[-1][1]  # most-recent >= oldest (stable/rising)

    def _margin_ok(series, abs_threshold=None):
        """Trend check with absolute fallback when only 1 data point is available."""
        valid = [(i, x) for i, x in enumerate(series) if x is not None]
        if len(valid) >= 2:
            return valid[0][1] >= valid[-1][1]  # most-recent >= oldest
        if len(valid) == 1 and abs_threshold is not None:
            return valid[0][1] >= abs_threshold  # single-year absolute check
        return None

    gm_ok = _margin_ok(gm_series, abs_threshold=0.30)
    om_ok = _margin_ok(om_series, abs_threshold=0.10)
    # Missing data must leave the row unscored, not fail it. Both branches here
    # returned a bool, so the `else None` was unreachable and a company with no
    # net income figure at all was marked failed — it entered the denominator
    # and pushed the verdict down while the row displayed "N/A".
    _nm_latest = nm_series[0] if nm_series else None
    if _nm_latest is None:
        _ni_latest = v(raw.get("net_income", []), 0)
        _nm_latest = None if _ni_latest is None else (1.0 if _ni_latest > 0 else -1.0)
    if _nm_latest is None:
        nm_ok = None
    elif _nm_latest <= 0:
        nm_ok = False
    else:
        nm_ok = _margin_ok(nm_series, abs_threshold=0.05)

    # EPS growing — use TTM EPS growth if available
    eps_vals, eps_split_trimmed = split_adjusted_series(
        raw.get("diluted_eps", []), raw.get("net_income", []))
    eps_growing = None
    eps_source = "annual"
    ttm_eps_growth = ttm.get("eps_growth_ttm_yoy")  # percentage, e.g. 15.4
    if ttm_eps_growth is not None:
        eps_growing = ttm_eps_growth > 0
        eps_source = "finnhub_metric"
    elif len([x for x in eps_vals if x is not None]) >= 2:
        valid_eps = [(i, x) for i, x in enumerate(eps_vals) if x is not None]
        eps_growing = (valid_eps[0][1] > valid_eps[-1][1]) if valid_eps else None

    # FCF — prefer TTM FCF over annual snapshot
    fcf0 = v(raw.get("free_cash_flow", []))
    fcf_source = "annual"
    if ttm.get("fcf_ttm_usd") is not None:
        fcf0 = ttm["fcf_ttm_usd"]
        fcf_source = ttm.get("sources", {}).get("fcf", "finnhub_metric")

    ni0  = v(raw.get("net_income", []))
    ocf0 = v(raw.get("operating_cash_flow", []))

    fcf_positive = (fcf0 > 0 if fcf0 is not None else None)
    # Earnings quality is a spectrum, not a gate. Cash at or above reported
    # profit is the pass. Cash at three-quarters of it is a company absorbing
    # working capital, which is worth flagging and worth distinguishing from
    # one whose earnings are barely backed by cash at all.
    def _earnings_quality(fcf, ni):
        if fcf is None or ni is None or ni <= 0:
            return None
        ratio = fcf / ni
        if ratio >= 1.0:
            return True
        # A fixed single point of the three, not a sliding scale. Scaling with
        # the ratio would hand 0.78x two of three marks, which reads as a near
        # pass; it is the one line on a strong card that actually fails, and
        # the score should say so while still separating it from a company
        # whose earnings are barely backed by cash at all.
        return (1 / 3) if ratio >= 0.75 else False

    fcf_ge_ni    = _earnings_quality(fcf0, ni0)

    ocf_vals = [v(raw.get("operating_cash_flow", []), i) for i in range(min(4, len(raw.get("operating_cash_flow", []))))]
    ocf_growing = _trending_up(ocf_vals) if len([x for x in ocf_vals if x is not None]) >= 2 else None

    # CapEx ratio
    capex0  = v(raw.get("capex", []))
    rev0    = v(raw.get("revenue", []))
    capex_ratio = (capex0 / rev0 if capex0 is not None and rev0 and rev0 != 0 else None)
    capex_ok    = (capex_ratio <= 0.10 if capex_ratio is not None else None)

    # Debt financing check — positive and large financing CF is a warning
    fin_cf0 = v(raw.get("financing_cash_flow", []))
    not_debt_reliant = None
    if fin_cf0 is not None and ocf0 is not None and ocf0 != 0:
        not_debt_reliant = not (fin_cf0 > 0 and fin_cf0 > abs(ocf0) * 0.5)

    # Quality metrics (ROE / ROIC already injected into raw by build_fundamentals_data)
    roe  = raw.get("roe")
    roic = raw.get("roic")
    roe_ok  = (roe  > 15 if roe  is not None else None)
    roic_ok = (roic > 10 if roic is not None else None)
    roe_source  = "finnhub_metric" if ttm.get("roe_ttm") is not None else "annual"
    roic_source = "finnhub_metric" if ttm.get("roi_ttm") is not None else "annual"

    # ── Moat evidence: durable returns on capital ────────────────────────────
    # This row used to be unscorable by design, which quietly removed its
    # points from the denominator. A moat cannot be read off a single number,
    # but the trace one leaves can: competition drags returns on capital down,
    # so a company that holds ROIC above its cost of capital for years while
    # its gross margin holds is being protected by something.
    roic_years = [x for x in (raw.get("roic_series") or []) if x is not None]
    gm_valid = [x for x in gm_series if x is not None]
    gm_erosion = (gm_valid[-1] - gm_valid[0]) if len(gm_valid) >= 2 else None
    moat_ok = None
    if len(roic_years) >= 3:
        moat_ok = (all(x > 10.0 for x in roic_years)
                   and (gm_erosion is None or gm_erosion <= 0.03))

    # ── Share count: is your slice growing or shrinking? ─────────────────────
    # Replaces a row that awarded two points for an insider-ownership field
    # merely being present, from a provider this host cannot reach.
    # A split multiplies the share count and leaves earnings alone, which is
    # the same signature the EPS series already guards against, in the opposite
    # direction. KLA's count read 152M -> 1,320M and the row reported 770%
    # dilution. If a break survives the same-filing rule, drop everything on
    # the far side of it rather than compare two bases.
    share_series, shares_split_trimmed = split_adjusted_series(
        raw.get("diluted_shares") or [], raw.get("net_income") or [])
    share_vals = [(i, x) for i, x in enumerate(share_series)
                  if x is not None and x > 0]
    share_change = None
    if len(share_vals) >= 2:
        newest, oldest = share_vals[0][1], share_vals[-1][1]
        share_change = (newest / oldest) - 1.0
    # Flat counts pass: not every good company buys back stock, but a company
    # issuing shares faster than 2% a year is charging you for its own growth.
    dilution_ok = (share_change <= 0.02) if share_change is not None else None

    # ── Score each section ────────────────────────────────────────────────────

    sections = []
    total_earned   = 0
    total_possible = 0
    total_metrics_count  = 0
    scored_metrics_count = 0

    def add_section(name: str, metrics: list[dict]) -> None:
        nonlocal total_earned, total_possible, total_metrics_count, scored_metrics_count
        sec_earned = 0
        sec_possible = 0
        sec_partial = 0
        rows = []
        for m in metrics:
            earned, avail, passed = _score_check(m["condition"], m["points"])
            sec_earned   += earned
            sec_possible += avail
            total_metrics_count += 1
            if passed is not None:
                scored_metrics_count += 1
            if passed == "partial":
                sec_partial += 1
            rows.append({
                "key":     m["key"],
                "label":   m["label"],
                "value":   m.get("display_value", ""),
                "points":  m["points"],
                "earned":  earned,
                "avail":   avail,
                "passed":  passed,       # True/False/None(missing)
                "edu":     EDUCATION.get(m["key"], {}),
                "working": WORKING.get(m["key"], ""),
                "metadata": m.get("metadata", {}),  # TTM provenance data
            })
        total_earned   += sec_earned
        total_possible += sec_possible
        sections.append({
            "name":     name,
            "earned":   sec_earned,
            "possible": sec_possible,
            "partials": sec_partial,
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

    # Helper: build standard metadata dict for a metric
    def _meta(key: str, source: str = "annual", period: str = "Annual",
              gated: bool = False) -> dict:
        div = raw.get("_ttm_validation", {}).get(key, {})
        return {
            "period_label":          period,
            "source_endpoint":       source,
            "computed_or_reported": "reported" if source.startswith("finnhub") else "computed",
            "gated":                 gated,
            "divergence_warning":    div.get("divergence_warning", False) if div else False,
            "divergence_pct":        div.get("divergence_pct") if div else None,
        }

    _ttm_period = (
        f"TTM (ends {ttm.get('period_end')})" if ttm.get("period_end")
        else "TTM"
    )

    # ── Section 1: Balance Sheet ──────────────────────────────────────────────

    def _usd(val, unit="M"):
        """Format a raw USD figure the way a filing states it."""
        if val is None:
            return "n/a"
        try:
            val = float(val)
        except (TypeError, ValueError):
            return "n/a"
        if abs(val) >= 1_000_000_000:
            return f"${val/1_000_000_000:,.2f}B"
        if abs(val) >= 1_000_000:
            return f"${val/1_000_000:,.0f}M"
        return f"${val:,.0f}"

    def _pct_series(series, limit=5):
        """Oldest-to-newest percentage trail, e.g. '61.0% -> 59.8% -> 61.3%'."""
        vals = [x for x in (series or [])[:limit] if x is not None]
        if len(vals) < 2:
            return ""
        return " -> ".join(f"{x*100:.1f}%" for x in reversed(vals))

    def _usd_series(series, limit=5):
        vals = [x for x in (series or [])[:limit] if x is not None]
        if len(vals) < 2:
            return ""
        return " -> ".join(_usd(x) for x in reversed(vals))

    def _ratio_or_blank(num, den, fmt="{:.2f}"):
        if num is None or not den:
            return ""
        return fmt.format(num / den)

    ca0 = v(raw.get("current_assets", []))
    cl0 = v(raw.get("current_liabilities", []))
    ta0 = v(raw.get("total_assets", []))
    gw0 = v(raw.get("goodwill", []))
    ia0 = v(raw.get("intangible_assets", []))
    rev0, rev1 = v(raw.get("revenue", []), 0), v(raw.get("revenue", []), 1)
    ocf0 = v(raw.get("operating_cash_flow", []))
    capex0 = v(raw.get("capex", []))

    WORKING = {
        "current_ratio": (
            f"{_usd(ca0)} current assets / {_usd(cl0)} current liabilities = "
            f"{_ratio_or_blank(ca0, cl0)}" if ca0 is not None and cl0 else ""),
        "debt_to_equity": (
            f"{_usd(total_debt)} total debt / {_usd(total_equity)} equity = "
            f"{_ratio_or_blank(total_debt, total_equity)}" if total_debt is not None and total_equity else ""),
        "cash_covers_debt": (
            f"{_usd(liquid0)} {liquid_label} against {_usd(near_term_debt)} "
            f"of {cash_cover_basis}"
            # Both sides are read at the newest period end carrying both, which
            # is not always the newest year on the card. Say which one.
            + (f", at {_cover_period}" if _cover_period else "")
            if liquid0 is not None else ""),
        "retained_earnings_growth": _usd_series(raw.get("retained_earnings", [])),
        "goodwill_ratio": (
            f"({_usd(gw0)} goodwill + {_usd(ia0)} intangibles) / {_usd(ta0)} total assets = "
            f"{gw_ratio*100:.1f}%" if gw_ratio is not None else ""),
        "revenue_growth": (
            (_usd_series(raw.get("revenue", [])) +
             (f"  ({rev_streak} consecutive year{'' if rev_streak == 1 else 's'} of growth)"
              if rev_streak else "  (most recent year declined)"))
            if _usd_series(raw.get("revenue", [])) else ""),
        "gross_margin": _pct_series(gm_series),
        "operating_margin": _pct_series(om_series),
        "net_margin": _pct_series(nm_series),
        "eps_growth": (
            (" -> ".join(f"${x:,.2f}" for x in reversed([e for e in eps_vals[:5] if e is not None]))
             + (" (earlier years dropped - the reported series crosses a stock split)"
                if eps_split_trimmed else ""))
            if len([e for e in eps_vals[:5] if e is not None]) >= 2 else ""),
        "fcf_positive": (
            f"{_usd(fcf0)} free cash flow on {_usd(rev0)} revenue = {fcf0/rev0*100:.1f}% FCF margin"
            if fcf0 is not None and rev0 else ""),
        "fcf_vs_net_income": (
            f"{_usd(fcf0)} FCF / {_usd(ni0)} net income = {fcf0/ni0:.2f}x"
            if fcf0 is not None and ni0 else ""),
        "ocf_trend": _usd_series(raw.get("operating_cash_flow", [])),
        "capex_ratio": (
            f"{_usd(abs(capex0))} capex / {_usd(rev0)} revenue = {abs(capex0)/rev0*100:.1f}%"
            if capex0 is not None and rev0 else ""),
        "debt_financing": _usd_series(raw.get("total_debt", [])),
        # ROE/ROIC come from the TTM provider when available, and a TTM figure
        # uses average equity and trailing income - so it will not equal the
        # year-end division shown beside it. Pairing the two was worse than
        # showing nothing: the arithmetic on screen did not produce the result.
        "roe": (
            f"TTM return on equity {raw.get('roe'):.1f}% (provider). Year-end basis: "
            f"{_usd(ni0)} net income / {_usd(total_equity)} equity = {ni0/total_equity*100:.1f}%"
            if raw.get("roe") is not None and ni0 is not None and total_equity
            else (f"TTM return on equity = {raw.get('roe'):.1f}% (provider)"
                  if raw.get("roe") is not None else
                  (f"{_usd(ni0)} net income / {_usd(total_equity)} equity = {ni0/total_equity*100:.1f}%"
                   if ni0 is not None and total_equity else ""))),
        "roic": (f"TTM return on invested capital = {raw.get('roic'):.1f}% (provider)"
                 if raw.get("roic") is not None else ""),
        "moat": (
            ("ROIC by year: " + " -> ".join(f"{x:.1f}%" for x in reversed(roic_years))
             + (".  Gross margin held flat across the trail"
                if gm_erosion is not None and abs(gm_erosion) < 0.001 else
                (f".  Gross margin {'fell' if gm_erosion > 0 else 'rose'} "
                 f"{abs(gm_erosion)*100:.1f} points across the trail"
                 if gm_erosion is not None else "")))
            if roic_years else ""),
        "share_dilution": (
            f"{share_vals[-1][1]/1e6:,.1f}M shares -> {share_vals[0][1]/1e6:,.1f}M = "
            f"{share_change*100:+.1f}% over {len(share_vals)} reported years"
            + (" (earlier years dropped - the reported series crosses a stock split)"
               if shares_split_trimmed else "")
            if share_change is not None else
            ("Share counts either side of a stock split are not comparable and "
             "only one year survives the break"
             if shares_split_trimmed else "")),
    }

    add_section("Balance Sheet", [
        {
            "key": "current_ratio",
            "label": "Current Ratio > 1.5",
            "condition": (cr0 >= 1.5 if cr0 is not None else None),
            "points": 2,
            "display_value": f"{cr0:.2f}" if cr0 is not None else "N/A",
            "metadata": _meta("current_ratio_q", cr0_source, cr0_period),
        },
        {
            "key": "debt_to_equity",
            "label": "Debt-to-Equity < 1.0",
            "condition": (de_ratio < 1.0 if de_ratio is not None else None),
            "points": 2,
            "display_value": f"{de_ratio:.2f}" if de_ratio is not None else "N/A",
            "metadata": _meta("de_ratio_q", de_source, de_period),
        },
        {
            "key": "cash_covers_debt",
            "label": "Cash covers 1+ yr of debt obligations",
            "condition": cash_covers_debt,
            "points": 2,
            "display_value": (f"{_fmt(liquid0)} vs {_fmt(near_term_debt)} due"
                              if liquid0 is not None and near_term_debt is not None else "N/A"),
            "metadata": _meta("cash_covers_debt"),
        },
        {
            "key": "retained_earnings_growth",
            "label": "Retained earnings growing (3–5 yr)",
            "condition": re_growing,
            "points": 2,
            "display_value": _fmt(v(raw.get("retained_earnings", []))),
            "metadata": _meta("retained_earnings_growth"),
        },
        {
            "key": "goodwill_ratio",
            "label": "Goodwill + Intangibles < 30% of assets",
            "condition": gw_ok,
            "points": 2,
            "display_value": f"{gw_ratio*100:.1f}%" if gw_ratio is not None else "N/A",
            "metadata": _meta("goodwill_ratio"),
        },
    ])

    # ── Section 2: Income Statement ───────────────────────────────────────────
    add_section("Income Statement", [
        {
            "key": "revenue_growth",
            "label": "Revenue growing 3+ consecutive years",
            "condition": rev_growth,
            "points": 2,
            "display_value": _fmt(v(raw.get("revenue", []))),
            "metadata": _meta("revenue_growth"),
        },
        {
            "key": "gross_margin",
            "label": "Gross margin stable or rising",
            "condition": gm_ok,
            "points": 2,
            "display_value": _pct_fmt(gm_series[0]) if gm_series and gm_series[0] is not None else "N/A",
            "metadata": _meta("gross_margin_ttm", gm_source,
                               _ttm_period if gm_source == "finnhub_metric" else "Annual"),
        },
        {
            "key": "operating_margin",
            "label": "Operating margin stable or rising",
            "condition": om_ok,
            "points": 2,
            "display_value": _pct_fmt(om_series[0]) if om_series and om_series[0] is not None else "N/A",
            "metadata": _meta("operating_margin_ttm", om_source,
                               _ttm_period if om_source == "finnhub_metric" else "Annual"),
        },
        {
            "key": "net_margin",
            "label": "Net margin positive and trending up",
            "condition": nm_ok,
            "points": 2,
            "display_value": _pct_fmt(nm_series[0]) if nm_series and nm_series[0] is not None else "N/A",
            "metadata": _meta("net_margin_ttm", nm_source,
                               _ttm_period if nm_source == "finnhub_metric" else "Annual"),
        },
        {
            "key": "eps_growth",
            "label": "EPS growing",
            "condition": eps_growing,
            "points": 2,
            "display_value": (
                f"{ttm.get('eps_growth_ttm_yoy'):+.1f}% YoY (TTM)" if eps_source == "finnhub_metric" and ttm.get("eps_growth_ttm_yoy") is not None
                else (f"${eps_vals[0]:.2f}" if eps_vals and eps_vals[0] is not None else "N/A")
            ),
            "metadata": _meta("eps_growth_ttm_yoy", eps_source,
                               _ttm_period if eps_source == "finnhub_metric" else "Annual"),
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
            "metadata": _meta("fcf", fcf_source,
                               _ttm_period if fcf_source != "annual" else "Annual",
                               gated=raw.get("_ttm_partial", {}).get("gated_fields") is not None
                                     and "fcf_ttm_usd" in (raw.get("_ttm_partial", {}).get("gated_fields") or [])),
        },
        {
            "key": "fcf_vs_net_income",
            "label": "FCF ≥ Net Income (earnings quality)",
            "condition": fcf_ge_ni,
            "points": 3,
            "display_value": (f"{_fmt(fcf0)} FCF vs {_fmt(ni0)} NI"
                              if fcf0 is not None and ni0 is not None else "N/A"),
            "metadata": _meta("fcf_vs_net_income", fcf_source,
                               _ttm_period if fcf_source != "annual" else "Annual"),
        },
        {
            "key": "ocf_trend",
            "label": "Operating Cash Flow trending up (3–5 yr)",
            "condition": ocf_growing,
            "points": 2,
            "display_value": _fmt(ocf0),
            "metadata": _meta("ocf_trend"),
        },
        {
            "key": "capex_ratio",
            "label": "CapEx ≤ 10% of revenue",
            "condition": capex_ok,
            "points": 2,
            "display_value": f"{capex_ratio*100:.1f}%" if capex_ratio is not None else "N/A",
            "metadata": _meta("capex_ratio"),
        },
        {
            "key": "debt_financing",
            "label": "Not reliant on debt issuance for financing",
            "condition": not_debt_reliant,
            "points": 1,
            "display_value": _fmt(fin_cf0) if fin_cf0 is not None else "N/A",
            "metadata": _meta("debt_financing"),
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
            "metadata": _meta("roe_ttm", roe_source,
                               _ttm_period if roe_source == "finnhub_metric" else "Annual"),
        },
        {
            "key": "roic",
            "label": "ROIC > 10%",
            "condition": roic_ok,
            "points": 3,
            "display_value": f"{roic:.1f}%" if roic is not None else "N/A",
            "metadata": _meta("roi_ttm", roic_source,
                               _ttm_period if roic_source == "finnhub_metric" else "Annual"),
        },
        {
            "key": "moat",
            "label": "Moat evidence: returns hold up",
            "condition": moat_ok,
            "points": 2,
            "display_value": (f"ROIC {min(roic_years):.0f}–{max(roic_years):.0f}% "
                              f"over {len(roic_years)} yrs"
                              if len(roic_years) >= 3 else "N/A"),
            "metadata": _meta("moat"),
        },
        {
            "key": "share_dilution",
            "label": "Share count not diluting",
            "condition": dilution_ok,
            "points": 2,
            "display_value": (f"{share_change*100:+.1f}%"
                              if share_change is not None else "N/A"),
            "metadata": _meta("share_dilution"),
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
    if len(raw.get("total_debt", [])) >= 2 and len(raw.get("revenue", [])) >= 2:
        debt_chg = _pct_change(v(raw.get("total_debt", []), 0), v(raw.get("total_debt", []), 1))
        rev_chg  = _pct_change(v(raw.get("revenue", []), 0),    v(raw.get("revenue", []), 1))
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
    # Goodwill + intangibles above 30% of assets. Scored in Section 1 and also a
    # downgrade trigger: the rubric treats the write-down risk as structural.
    if gw_ratio is not None and gw_ratio > 0.30:
        red_flags.append({
            "key": "goodwill_over_30pct",
            "label": RED_FLAG_DEFS["goodwill_over_30pct"],
        })
    # Goodwill impairment. This flag was defined and referenced by the narrative
    # builder but never appended anywhere, so it could not fire. A material
    # year-over-year fall in goodwill is the available proxy; divestitures and FX
    # can also cause it, so the threshold is deliberately conservative.
    gw_now, gw_prior = v(raw.get("goodwill", []), 0), v(raw.get("goodwill", []), 1)
    if gw_now is not None and gw_prior is not None and gw_prior > 0 and gw_now < gw_prior * 0.90:
        red_flags.append({
            "key": "goodwill_impairment",
            "label": RED_FLAG_DEFS["goodwill_impairment"],
        })

    # Filed disclosures become red flags alongside the derived ones. These are
    # not inferred from the statements — an 8-K item code is the company saying
    # the thing outright — and a restatement in particular says the numbers
    # every other row is built from cannot be relied on.
    _signal_flags = {
        "4.02": "restated_financials",
        "1.03": "bankruptcy_filing",
        "3.01": "delisting_notice",
        "4.01": "auditor_changed",
    }
    filing_signals = raw.get("_filing_signals") or {}
    _seen_flags = {f["key"] for f in red_flags}
    for signal in (filing_signals.get("signals") or []):
        key = _signal_flags.get(signal.get("item"))
        if key is None and signal.get("form", "").startswith("NT "):
            key = "late_filing"
        if not key or key in _seen_flags:
            continue
        _seen_flags.add(key)
        red_flags.append({
            "key": key,
            "label": f"{RED_FLAG_DEFS[key]} Filed {signal.get('date', '')}.",
        })

    # ── Verdict ───────────────────────────────────────────────────────────────
    score_pct = (total_earned / total_possible * 40) if total_possible > 0 else 0

    if   score_pct >= 34: verdict = "Great Company"
    elif score_pct >= 26: verdict = "Good"
    elif score_pct >= 18: verdict = "Caution"
    else:                 verdict = "Avoid"

    base_verdict = verdict
    verdict, fired_triggers = apply_downgrade(verdict, red_flags)
    downgraded = verdict != base_verdict

    # Show that the triggers ran, not only that one caught something. A card
    # that stays silent when everything is clean leaves the reader unable to
    # tell a company that passed every integrity check from one where the
    # check never happened — and those are very different reasons to see no
    # warning.
    _fired = {f["key"] for f in red_flags}
    downgrade_check = {
        "any_fired": bool(fired_triggers),
        "verdict_before": base_verdict,
        "verdict_after": verdict,
        "checks": [
            {"key": key,
             "label": RED_FLAG_DEFS.get(key, key),
             "fired": key in _fired}
            for key in sorted(DOWNGRADE_TRIGGERS)
        ],
    }

    verdict_class = {
        "Great Company": "verdict-great",
        "Good":          "verdict-good",
        "Caution":       "verdict-caution",
        "Avoid":         "verdict-avoid",
    }.get(verdict, "verdict-caution")

    # ── Verdict reason — 1-2 sentences explaining the score ──────────────────
    def _build_verdict_reason() -> str:
        # Rank sections by how much they underperformed (worst first)
        sec_scores = [(s["name"], s["earned"], s["possible"]) for s in sections if s["possible"] > 0]
        sec_pct    = sorted(sec_scores, key=lambda x: x[1]/x[2])
        worst_sec  = sec_pct[0][0]  if sec_pct else None
        best_sec   = sec_pct[-1][0] if sec_pct else None

        roe_str  = f"{raw.get('roe'):.0f}%" if raw.get("roe") is not None else None
        roic_str = f"{raw.get('roic'):.0f}%" if raw.get("roic") is not None else None
        flags    = [f["key"] for f in red_flags]

        # Build nicely-named section references
        _sec_names = {
            "Balance Sheet":    "the balance sheet",
            "Income Statement": "the income statement",
            "Cash Flow":        "cash flow",
            "Quality Metrics":  "profitability metrics",
        }
        worst_label = _sec_names.get(worst_sec, worst_sec) if worst_sec else "key metrics"
        best_label  = _sec_names.get(best_sec,  best_sec)  if best_sec  else "some areas"

        # Specific signal phrases
        roe_phrase  = f"ROE of {roe_str}" if roe_str else None
        roic_phrase = f"ROIC of {roic_str}" if roic_str else None

        flag_phrases = {
            "income_positive_fcf_negative":     "earnings aren't backed by real cash",
            "debt_growing_faster_than_revenue":  "debt is growing faster than revenue",
            "current_ratio_below_1":             "current liabilities exceed liquid assets",
            "goodwill_impairment":               "a prior acquisition has lost value",
        }
        flag_text = next((flag_phrases[k] for k in flags if k in flag_phrases), None)

        if verdict == "Great Company":
            parts = []
            if roe_str and raw.get("roe", 0) > 15:
                parts.append(f"{roe_str} ROE")
            if roic_str and raw.get("roic", 0) > 10:
                parts.append(f"{roic_str} ROIC")
            strength = " and ".join(parts) if parts else f"strong {best_label}"
            s1 = f"Scores across all four pillars are elite — {strength} signals a durable, high-quality business."
            s2 = "This is the type of company worth holding through the long run." if not flag_text else f"Watch: {flag_text}."
            return f"{s1} {s2}"

        elif verdict == "Good":
            s1 = f"Solid fundamentals overall — {best_label} is a clear strength."
            if flag_text:
                s2 = f"Red flag to monitor: {flag_text}, which keeps this out of the elite tier."
            else:
                s2 = f"The main drag is {worst_label}, which has room to improve before this reaches elite status."
            return f"{s1} {s2}"

        elif verdict == "Caution":
            if flag_text:
                s1 = f"Material concern: {flag_text}."
            else:
                s1 = f"Weakness in {worst_label} is the primary drag on this score."
            if roe_str and raw.get("roe", 0) > 0:
                s2 = f"Some positives exist (e.g. {roe_str} ROE), but deeper due diligence is needed before committing capital."
            elif best_label and best_label != worst_label:
                s2 = f"There are pockets of strength in {best_label}, but overall the risk/reward needs more study before entry."
            else:
                s2 = "Requires deeper due diligence — don't size up until the picture is clearer."
            return f"{s1} {s2}"

        else:  # Avoid
            if flag_text:
                s1 = f"Multiple red flags including {flag_text} make this a high-risk hold."
            else:
                s1 = f"Significant weakness in {worst_label} puts this well below minimum quality standards."
            if len(flags) >= 2:
                flag2 = next((flag_phrases[k] for k in flags[1:] if k in flag_phrases), None)
                s2 = f"Also: {flag2}." if flag2 else "The scorecard shows structural problems across multiple pillars."
            else:
                s2 = "Until the fundamentals improve materially, capital is better deployed elsewhere."
            return f"{s1} {s2}"

    verdict_reason = _build_verdict_reason()

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
            nm   = nm_series[i] if i < len(nm_series) else None
            _rc  = (raw_data.get("roic_series") or [None] * (i + 1))
            rc   = (_rc[i] / 100.0) if i < len(_rc) and _rc[i] is not None else None
            rows.append({
                "year_offset": i,
                "label": f"Year -{i}" if i > 0 else "Latest",
                "revenue":  _fmt(rev),
                "revenue_num": rev,   # raw values so the UI can chart and compute
                "net_income_num": ni,
                "fcf_num": fcf,
                "period_end": (raw_data.get("fiscal_period_ends") or [None] * (i + 1))[i]
                              if i < len(raw_data.get("fiscal_period_ends") or []) else None,
                "fcf_over_ni": (fcf / ni) if (fcf is not None and ni) else None,
                "net_income": _fmt(ni),
                "fcf": _fmt(fcf),
                "ocf": _fmt(ocf),
                "gross_margin": _pct_fmt(gm),
                "operating_margin": _pct_fmt(om),
                "net_margin": _pct_fmt(nm),
                # Raw ratios for the charts. The formatted strings above stay
                # for the table; geometry needs numbers.
                "gross_margin_num": gm,
                "operating_margin_num": om,
                "net_margin_num": nm,
                "roic_num": rc,
            })
        return rows

    history = _history_table(raw)
    charts = build_charts(history)

    # ── Partial score bookkeeping ─────────────────────────────────────────────
    ttm_partial = raw.get("_ttm_partial", {}) or {}
    partial_score = ttm_partial.get("partial_score", False)

    # Backfill scored_metrics / total_metrics into the partial dict
    ttm_partial["scored_metrics"] = scored_metrics_count
    ttm_partial["total_metrics"]  = total_metrics_count

    # Divergence warnings from validation layer
    ttm_validation = raw.get("_ttm_validation", {}) or {}
    divergence_warnings = {
        k: v for k, v in ttm_validation.items()
        if isinstance(v, dict) and v.get("divergence_warning")
    }

    return {
        "ticker":           raw.get("ticker", ""),
        "company_name":     raw.get("company_name"),
        "sector":           raw.get("sector"),
        "industry":         raw.get("industry"),
        "sections":         sections,
        "total_earned":     total_earned,
        "total_possible":   total_possible,
        "normalized_score": round(score_pct),
        "verdict":          verdict,
        "base_verdict":     base_verdict,
        "downgraded":       downgraded,
        "downgrade_triggers": fired_triggers,
        "verdict_class":    verdict_class,
        "verdict_reason":   verdict_reason,
        "red_flags":        red_flags,
        "missing_fields":   raw.get("missing_fields", []),
        "error":            raw.get("error"),
        "history":          history,
        "charts":           charts,
        "filing_signals":   filing_signals,
        "downgrade_check":  downgrade_check,
        "valuation":        raw.get("_valuation") or {"available": False, "rows": []},
        "valuation_history": raw.get("_valuation_history") or {"available": False, "rows": []},
        # A split makes every per-share figure on the page incomparable with
        # anything a reader has seen from an older source. Say so at the top
        # rather than in a parenthetical halfway down.
        "split_notice":     (
            "The reported per-share series crosses a stock split. Earlier "
            "years are dropped where the two sides are not comparable, and "
            "every per-share figure here is on the current basis — a price or "
            "EPS from before the split will not reconcile with this page."
            if (eps_split_trimmed or shares_split_trimmed) else ""),
        "derived_lines":    raw.get("derived_lines", []),
        "roe":              raw.get("roe"),
        "roic":             raw.get("roic"),
        "insider_pct":      raw.get("insider_pct"),
        # ── TTM pipeline fields ───────────────────────────────────────────────
        "partial_score":       partial_score,
        "scored_metrics":      scored_metrics_count,
        "total_metrics":       total_metrics_count,
        "gated_fields":        ttm_partial.get("gated_fields", []),
        "ttm_period_end":      (raw.get("_ttm_metrics") or {}).get("period_end"),
        "ttm_quarters_used":   (raw.get("_ttm_metrics") or {}).get("quarters_used", 0),
        "divergence_warnings": divergence_warnings,
    }


# ─── Public entry point ───────────────────────────────────────────────────────

# Bump when scoring logic, units, series construction or the shape of the
# result changes. A cached scorecard computed under a different version is
# discarded rather than served.
#
# The 24-hour cache stores the FINISHED scorecard, not the raw inputs. Every
# fix tonight - the debt-to-equity unit, the liquidity definition, the revenue
# streak, the ROE line, split handling - shipped and deployed correctly and then
# appeared not to work, because the page kept serving a scorecard computed by the
# previous code. Hours went into re-diagnosing bugs that were already fixed.
SCORECARD_VERSION = "2026-09-02.11"


def get_fundamentals(ticker: str, force_refresh: bool = False) -> dict:
    """
    Main entry point. Returns fully scored fundamentals dict for ticker.
    Uses 24-hr cache unless force_refresh=True.

    Pipeline:
      1. fetch_fundamentals_raw()      — historical annual arrays from EDGAR/FMP/yfinance
      2. finnhub_ttm.build_fundamentals_data() — augments raw with TTM scalars from Finnhub
      3. score_fundamentals(raw)       — scores using TTM-injected values
    """
    ticker = ticker.upper().strip()

    # ── Cache check ──────────────────────────────────────────────────────────
    if not force_refresh:
        try:
            from database import get_fundamentals_cache
            cached = get_fundamentals_cache(ticker)
            if cached and cached.get("scorecard_version") == SCORECARD_VERSION:
                logger.debug("fundamentals cache hit for %s", ticker)
                return cached
            if cached:
                logger.info(
                    "fundamentals cache discarded for %s: built by %s, current is %s",
                    ticker, cached.get("scorecard_version") or "an older build",
                    SCORECARD_VERSION)
        except Exception as e:
            logger.debug("fundamentals cache read error: %s", e)

    # ── Step 1: fetch historical annual data ──────────────────────────────────
    raw = fetch_fundamentals_raw(ticker)

    # ── Step 1b: what the company had to disclose ────────────────────────────
    # Never fatal: a page whose statements loaded fine should still render if
    # the filing index is unreachable.
    try:
        from filing_signals import fetch_filing_signals
        if isinstance(raw, dict):
            raw["_filing_signals"] = fetch_filing_signals(ticker)
    except Exception as exc:
        logger.debug("filing signals unavailable for %s: %s", ticker, exc)

    # ── Step 1c: what it costs ───────────────────────────────────────────────
    # The scorecard says whether a business is worth owning and nothing about
    # price. A reader who stops there buys a great company at any number.
    try:
        from finnhub_ttm import fetch_finnhub_metrics, fetch_finnhub_quote
        from valuation import build_valuation
        _metrics = fetch_finnhub_metrics(ticker) or {}
        if isinstance(raw, dict):
            raw["_valuation"] = build_valuation(
                _metrics.get("_raw_metric") or {}, fetch_finnhub_quote(ticker))
    except Exception as exc:
        logger.debug("valuation unavailable for %s: %s", ticker, exc)

    # ── Step 1d: what it cost in its own past ────────────────────────────────
    # Today's multiple against the multiple at its own recent year ends. Only
    # the years the newest filing states on one split basis, because the price
    # series is split-adjusted and filed per-share figures are not.
    try:
        from market_data import fetch_chart_bars
        from valuation_history import build_history
        if isinstance(raw, dict) and (raw.get("_valuation") or {}).get("available"):
            _bars, _ = fetch_chart_bars(ticker, "1d", "5y")
            _val = raw["_valuation"]
            _shares = [x for x in (raw.get("diluted_shares") or []) if x]
            raw["_valuation_history"] = build_history(
                period_ends=raw.get("fiscal_period_ends") or [],
                eps=raw.get("diluted_eps") or [],
                fcf=raw.get("free_cash_flow") or [],
                shares=raw.get("diluted_shares") or [],
                bars=_bars,
                today_price=_val.get("price"),
                today_pe=_val.get("pe"),
                today_pfcf=_val.get("pfcf"),
                comparable_years=max(len(_shares), 3),
            )
    except Exception as exc:
        logger.debug("valuation history unavailable for %s: %s", ticker, exc)

    # ── Step 2: augment with Finnhub TTM data ─────────────────────────────────
    try:
        from finnhub_ttm import build_fundamentals_data
        raw = build_fundamentals_data(ticker, raw=raw, force_refresh=force_refresh)
        logger.debug(
            "fundamentals  TTM augmentation complete  ticker=%s  "
            "gm_ttm=%s  fcf_ttm=%s",
            ticker,
            (raw.get("_ttm_metrics") or {}).get("gross_margin_ttm"),
            (raw.get("_ttm_metrics") or {}).get("fcf_ttm_usd"),
        )
    except Exception as exc:
        logger.warning(
            "fundamentals  TTM augmentation failed for %s  error=%s  "
            "(falling back to annual data)", ticker, exc,
        )
        # Ensure _ttm_metrics is present so score_fundamentals() doesn't error
        raw.setdefault("_ttm_metrics", {})
        raw.setdefault("_ttm_validation", {})
        raw.setdefault("_ttm_partial", {
            "partial_score": False, "scored_metrics": None,
            "total_metrics": None, "gated_fields": [],
        })

    # ── Step 2b: cross-check the annual series against Finnhub ───────────────
    # EDGAR stays the source of record. Finnhub fills years EDGAR has no fact
    # for, and any material disagreement is recorded rather than silently
    # resolved in favour of whichever source was consulted first.
    try:
        from finnhub_annual import cross_check
        from finnhub_ttm import fetch_finnhub_annual
        raw = cross_check(raw, fetch_finnhub_annual(ticker))
    except Exception as exc:
        logger.debug("fundamentals annual cross-check unavailable for %s: %s", ticker, exc)

    # ── Step 3: score ─────────────────────────────────────────────────────────
    result = score_fundamentals(raw)
    result["scorecard_version"] = SCORECARD_VERSION
    result["source_notes"] = raw.get("_source_notes", [])
    result["fiscal_period_ends"] = raw.get("fiscal_period_ends", [])

    # ── Save to cache ─────────────────────────────────────────────────────────
    try:
        from database import save_fundamentals_cache
        save_fundamentals_cache(ticker, result)
    except Exception as e:
        logger.debug("fundamentals cache write error: %s", e)

    return result
