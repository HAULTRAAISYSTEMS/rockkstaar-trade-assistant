"""
finnhub_ttm.py — Finnhub TTM/quarterly data pipeline for Rockkstaar Trade Assistant.

Provides trailing-twelve-month accurate fundamental data by wiring two Finnhub endpoints:
  1. /stock/metric?metric=all  — pre-computed TTM ratios and margins (fast, free tier)
  2. /stock/financials-reported?freq=quarterly — raw as-reported quarterly statements

Both endpoints are cached in SQLite with 24-hour TTL.

Unit conventions (Finnhub specific):
  - grossMarginTTM / operatingMarginTTM / netProfitMarginTTM: percentage (e.g. 65.2 = 65.2%)
    → divide by 100 to get the ratio format used internally in score_fundamentals()
  - roeTTM / roiTTM: percentage (e.g. 25.0 = 25%)
    → same format as raw["roe"] / raw["roic"] — no conversion needed
  - currentRatioQuarterly: ratio (e.g. 2.3) — no conversion
  - totalDebt/totalEquityQuarterly: ratio (e.g. 0.97) — no conversion.
    This was previously assumed to be a percentage and divided by 100, which
    reported KLA's 0.97 debt-to-equity as 0.01. Finnhub returns this in the same
    ratio form as currentRatioQuarterly, which was already documented correctly
    and was already producing the right number.
  - freeCashFlowTTM: millions USD (e.g. 12500.0 = $12.5B)
    → multiply by 1_000_000 to get raw USD
  - revenueGrowthTTMYoy / epsGrowthTTMYoy: percentage (e.g. 8.5 = 8.5%)
  - financials-reported values: raw USD as filed in the statement — no scaling needed
"""

from __future__ import annotations

import os
import json
import logging
import requests
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_FINNHUB_API_KEY: str = os.environ.get("FINNHUB_API_KEY", "")
_BASE_URL = "https://finnhub.io/api/v1"

# Maximum number of quarterly reports to fetch (covers ~2 years)
_MAX_QUARTERS = 8

# Divergence threshold: if Finnhub pre-computed and manually computed TTM differ
# by more than this fraction, set divergence_warning=True.
_DIVERGENCE_THRESHOLD = 0.05  # 5%

# ── Concept name lists for financials-reported parsing ─────────────────────────
# Listed in priority order — first match wins.
_OCF_CONCEPTS = [
    "NetCashProvidedByUsedInOperatingActivities",
    "CashFlowsFromUsedInOperatingActivities",
    "NetCashFromOperatingActivities",
    "OperatingActivities",
]
_CAPEX_CONCEPTS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "CapitalExpendituresIncurredButNotYetPaid",
    "PurchaseOfPropertyPlantAndEquipment",
    "AcquisitionOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    "CapitalExpenditures",
]
_REVENUE_CONCEPTS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
    "Revenue",
    "NetRevenues",
    "TotalRevenues",
]
_NET_INCOME_CONCEPTS = [
    "NetIncomeLoss",
    "NetIncome",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
    "ProfitLoss",
    "ProfitLossAttributableToOwnersOfParent",
]


# ── Internal helpers ───────────────────────────────────────────────────────────

def _safe_get(d: dict, *keys: str, default=None):
    """Safely traverse nested dict keys."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
        if cur is None:
            return default
    return cur


def _as_float(val) -> float | None:
    """Pass a numeric metric through unchanged. None-safe."""
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _pct_to_ratio(val: float | None) -> float | None:
    """Convert a percentage value (e.g. 65.2) to a ratio (0.652). None-safe."""
    return val / 100.0 if val is not None else None


def _millions_to_usd(val: float | None) -> float | None:
    """Convert millions to raw USD. None-safe."""
    return val * 1_000_000.0 if val is not None else None


def _find_concept(items: list, concepts: list[str]) -> float | None:
    """
    Search a list of {concept, value} dicts for the first matching concept name.
    Returns the value (float) or None.
    """
    if not items:
        return None
    concept_map = {item.get("concept", ""): item.get("value") for item in items}
    for name in concepts:
        if name in concept_map:
            v = concept_map[name]
            return float(v) if v is not None else None
    return None


# ── Finnhub API calls ──────────────────────────────────────────────────────────

def _finnhub_get(path: str, params: dict) -> dict | list | None:
    """
    Make a GET request to Finnhub. Returns parsed JSON or None on error.
    Does NOT call this function if FINNHUB_API_KEY is empty.
    """
    if not _FINNHUB_API_KEY:
        logger.debug("finnhub_ttm: FINNHUB_API_KEY not set — skipping %s", path)
        return None
    params = {**params, "token": _FINNHUB_API_KEY}
    url = f"{_BASE_URL}{path}"
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 403:
            logger.info("finnhub_ttm: 403 (premium-gated) for %s", path)
            return {"_gated": True, "_status": 403}
        if resp.status_code != 200:
            logger.warning("finnhub_ttm: HTTP %d for %s", resp.status_code, path)
            return None
        return resp.json()
    except Exception as exc:
        logger.warning("finnhub_ttm: request error %s: %s", path, exc)
        return None


# ── Endpoint 1: /stock/metric ──────────────────────────────────────────────────

def fetch_finnhub_metrics(ticker: str, force_refresh: bool = False) -> dict | None:
    """
    Fetch /stock/metric?symbol={ticker}&metric=all.

    Returns a dict of TTM/quarterly metric scalars extracted from the 'metric' sub-object,
    converted to internal units:
      {
        "gross_margin_ttm":     float | None,  # ratio (0-1)
        "operating_margin_ttm": float | None,  # ratio (0-1)
        "net_margin_ttm":       float | None,  # ratio (0-1)
        "roe_ttm":              float | None,  # percentage (e.g. 25.0)
        "roi_ttm":              float | None,  # percentage
        "current_ratio_q":      float | None,  # ratio
        "de_ratio_q":           float | None,  # ratio, as returned
        "rev_growth_ttm_yoy":   float | None,  # percentage
        "eps_growth_ttm_yoy":   float | None,  # percentage
        "fcf_ttm_usd":          float | None,  # raw USD
        "gated":                bool,           # True if 403 returned
        "fetched_at":           str,            # ISO timestamp
        "_raw_metric":          dict,           # original Finnhub metric sub-object
      }
    Returns None if the API key is missing or the request fails entirely.
    """
    ticker = ticker.upper().strip()

    # Cache lookup (skip when force_refresh)
    if not force_refresh:
        try:
            from database import get_finnhub_metrics_cache
            cached = get_finnhub_metrics_cache(ticker)
            if cached is not None:
                logger.debug("finnhub_ttm metrics cache HIT  ticker=%s", ticker)
                return cached
        except Exception as exc:
            logger.debug("finnhub_ttm metrics cache read error: %s", exc)

    data = _finnhub_get("/stock/metric", {"symbol": ticker, "metric": "all"})

    if data is None:
        return None

    # 403 / premium-gated
    if isinstance(data, dict) and data.get("_gated"):
        result: dict[str, Any] = {
            "gross_margin_ttm": None, "operating_margin_ttm": None,
            "net_margin_ttm": None, "roe_ttm": None, "roi_ttm": None,
            "current_ratio_q": None, "de_ratio_q": None,
            "rev_growth_ttm_yoy": None, "eps_growth_ttm_yoy": None,
            "fcf_ttm_usd": None, "gated": True,
            "fetched_at": datetime.now().isoformat(), "_raw_metric": {},
        }
        _try_save_metrics_cache(ticker, result)
        return result

    metric = data.get("metric", {}) if isinstance(data, dict) else {}

    # Extract and unit-convert all fields
    result = {
        # Margins: Finnhub returns as %, we store as ratio (0-1)
        "gross_margin_ttm":     _pct_to_ratio(metric.get("grossMarginTTM")),
        "operating_margin_ttm": _pct_to_ratio(metric.get("operatingMarginTTM")),
        "net_margin_ttm":       _pct_to_ratio(metric.get("netProfitMarginTTM")),
        # Returns: Finnhub returns as %, same format as internal raw["roe"] / raw["roic"]
        "roe_ttm":              metric.get("roeTTM"),
        "roi_ttm":              metric.get("roiTTM"),
        # Balance sheet ratios
        "current_ratio_q":      metric.get("currentRatioQuarterly"),
        # D/E: Finnhub returns as % (e.g. 45.0 = 0.45 ratio) — convert
        "de_ratio_q":           _as_float(metric.get("totalDebt/totalEquityQuarterly")),
        # Growth rates: percentage form
        "rev_growth_ttm_yoy":   metric.get("revenueGrowthTTMYoy"),
        "eps_growth_ttm_yoy":   metric.get("epsGrowthTTMYoy"),
        # FCF: Finnhub returns in millions — convert to raw USD
        "fcf_ttm_usd":          _millions_to_usd(metric.get("freeCashFlowTTM")),
        "gated":                False,
        "fetched_at":           datetime.now().isoformat(),
        "_raw_metric":          metric,
    }

    _try_save_metrics_cache(ticker, result)
    return result


def _try_save_metrics_cache(ticker: str, data: dict) -> None:
    try:
        from database import save_finnhub_metrics_cache
        save_finnhub_metrics_cache(ticker, data)
    except Exception as exc:
        logger.debug("finnhub_ttm metrics cache write error: %s", exc)


# ── Endpoint 2: /stock/financials-reported ─────────────────────────────────────

def fetch_finnhub_annual(ticker: str) -> list | None:
    """Fetch /stock/financials-reported with freq=annual.

    The quarterly fetch above powers the TTM overlay. This one supplies the
    historical annual statements used to cross-check EDGAR, which reports
    period dates directly instead of requiring XBRL duration inference.

    Returns None on failure, [] when the endpoint is premium-gated.
    """
    ticker = ticker.upper().strip()
    data = _finnhub_get("/stock/financials-reported", {"symbol": ticker, "freq": "annual"})
    if data is None:
        return None
    if isinstance(data, dict) and data.get("_gated"):
        logger.info("finnhub_ttm: annual financials premium-gated for %s", ticker)
        return []
    reports = data.get("data", []) if isinstance(data, dict) else []
    try:
        reports = sorted(reports, key=lambda r: str(r.get("endDate") or ""), reverse=True)
    except Exception:
        pass
    return reports[:6]


def fetch_finnhub_financials(ticker: str, force_refresh: bool = False) -> list | None:
    """
    Fetch /stock/financials-reported?symbol={ticker}&freq=quarterly.

    Returns the last _MAX_QUARTERS quarterly report objects from the 'data' array,
    sorted most-recent first. Each object has the shape:
      {
        "year":      int,
        "quarter":   int,
        "startDate": "YYYY-MM-DD",
        "endDate":   "YYYY-MM-DD",
        "form":      "10-Q" | "10-K",
        "report":    {
          "ic": [{concept, label, unit, value}, ...],  # income statement
          "cf": [{concept, label, unit, value}, ...],  # cash flow
          "bs": [{concept, label, unit, value}, ...],  # balance sheet
        }
      }

    Returns None if the request fails or the API key is missing.
    Returns [] (empty list) if the endpoint is premium-gated (403).
    """
    ticker = ticker.upper().strip()

    # Cache lookup
    if not force_refresh:
        try:
            from database import get_finnhub_financials_cache
            cached = get_finnhub_financials_cache(ticker)
            if cached is not None:
                logger.debug("finnhub_ttm financials cache HIT  ticker=%s", ticker)
                return cached
        except Exception as exc:
            logger.debug("finnhub_ttm financials cache read error: %s", exc)

    data = _finnhub_get(
        "/stock/financials-reported",
        {"symbol": ticker, "freq": "quarterly"},
    )

    if data is None:
        return None

    if isinstance(data, dict) and data.get("_gated"):
        logger.info("finnhub_ttm: financials-reported is premium-gated for %s", ticker)
        _try_save_financials_cache(ticker, [])
        return []

    reports = data.get("data", []) if isinstance(data, dict) else []

    # Sort by endDate descending, take up to _MAX_QUARTERS
    try:
        reports = sorted(
            reports,
            key=lambda r: r.get("endDate", ""),
            reverse=True,
        )[:_MAX_QUARTERS]
    except Exception:
        pass

    _try_save_financials_cache(ticker, reports)
    return reports


def _try_save_financials_cache(ticker: str, data: list) -> None:
    try:
        from database import save_finnhub_financials_cache
        save_finnhub_financials_cache(ticker, data)
    except Exception as exc:
        logger.debug("finnhub_ttm financials cache write error: %s", exc)


# ── TTM computation from quarterly reports ─────────────────────────────────────

def compute_ttm(quarterly_reports: list) -> dict:
    """
    Sum the trailing four quarters of income statement and cash flow items.

    Input:  list of quarterly report objects from /stock/financials-reported
            (most-recent first, as returned by fetch_finnhub_financials)

    Output: {
        "ttm_revenue":    float | None,   # raw USD
        "ttm_net_income": float | None,   # raw USD
        "ttm_ocf":        float | None,   # raw USD
        "ttm_capex":      float | None,   # raw USD (positive)
        "ttm_fcf":        float | None,   # raw USD (OCF − CapEx)
        "period_end":     str | None,     # endDate of most-recent quarter
        "quarters_used":  int,            # how many quarters contributed
        "computed":       bool,           # True if at least 2 quarters found
    }
    """
    result: dict[str, Any] = {
        "ttm_revenue": None, "ttm_net_income": None,
        "ttm_ocf": None, "ttm_capex": None, "ttm_fcf": None,
        "period_end": None, "quarters_used": 0, "computed": False,
    }

    if not quarterly_reports:
        return result

    trailing = quarterly_reports[:4]
    result["quarters_used"] = len(trailing)
    result["period_end"] = trailing[0].get("endDate") if trailing else None

    rev_sum = ni_sum = ocf_sum = cap_sum = 0.0
    rev_ok = ni_ok = ocf_ok = cap_ok = False

    for q in trailing:
        report = q.get("report", {})
        ic = report.get("ic", [])
        cf = report.get("cf", [])

        rev = _find_concept(ic, _REVENUE_CONCEPTS)
        if rev is not None:
            rev_sum += rev
            rev_ok = True

        ni = _find_concept(ic, _NET_INCOME_CONCEPTS)
        if ni is not None:
            ni_sum += ni
            ni_ok = True

        ocf = _find_concept(cf, _OCF_CONCEPTS)
        if ocf is not None:
            ocf_sum += ocf
            ocf_ok = True

        cap = _find_concept(cf, _CAPEX_CONCEPTS)
        if cap is not None:
            cap_sum += abs(cap)  # CapEx is often reported as negative in CF statement
            cap_ok = True

    if rev_ok:
        result["ttm_revenue"] = rev_sum
    if ni_ok:
        result["ttm_net_income"] = ni_sum
    if ocf_ok:
        result["ttm_ocf"] = ocf_sum
    if cap_ok:
        result["ttm_capex"] = cap_sum
    if ocf_ok and cap_ok:
        result["ttm_fcf"] = ocf_sum - cap_sum

    result["computed"] = rev_ok or ni_ok or ocf_ok
    return result


# ── Validation layer ───────────────────────────────────────────────────────────

def validate_ttm(metric_ttm: dict, computed_ttm: dict) -> dict:
    """
    Cross-check Finnhub pre-computed TTM values against manually computed TTM.

    Returns:
      {
        "fcf": {
          "metric_value":   float | None,  # from /stock/metric (raw USD)
          "computed_value": float | None,  # from compute_ttm()
          "divergence_pct": float | None,  # absolute relative difference
          "divergence_warning": bool,
        },
        ...
      }

    Only FCF can be meaningfully cross-checked here because:
    - Margins from /stock/metric are pre-computed ratios (no independent dollar source)
    - FCF from metric endpoint (freeCashFlowTTM × 1e6) vs OCF-CapEx from quarterly reports
    """
    checks: dict[str, Any] = {}

    # ── FCF cross-check ────────────────────────────────────────────────────────
    finnhub_fcf = metric_ttm.get("fcf_ttm_usd") if metric_ttm else None
    computed_fcf = computed_ttm.get("ttm_fcf") if computed_ttm else None

    if finnhub_fcf is not None and computed_fcf is not None and computed_fcf != 0:
        div = abs(finnhub_fcf - computed_fcf) / abs(computed_fcf)
        checks["fcf"] = {
            "metric_value":      finnhub_fcf,
            "computed_value":    computed_fcf,
            "divergence_pct":    round(div * 100, 1),
            "divergence_warning": div > _DIVERGENCE_THRESHOLD,
        }
    else:
        checks["fcf"] = {
            "metric_value":      finnhub_fcf,
            "computed_value":    computed_fcf,
            "divergence_pct":    None,
            "divergence_warning": False,
        }

    return checks


# ── Orchestrator ───────────────────────────────────────────────────────────────

def build_fundamentals_data(
    ticker: str,
    raw: dict | None = None,
    force_refresh: bool = False,
) -> dict:
    """
    Augment a fundamentals raw dict with Finnhub TTM data.

    If `raw` is None, fetches from the existing fundamentals pipeline via
    fundamentals_engine.fetch_fundamentals_raw(). When `raw` is supplied
    (already fetched), only the Finnhub TTM augmentation is added.

    Returns the same dict structure as fetch_fundamentals_raw() plus:
      raw["_ttm_metrics"]   — all TTM scalar values (internal units)
      raw["_ttm_validation"]— divergence flags between metric and computed TTM
      raw["_ttm_partial"]   — {partial_score, scored_metrics, total_metrics, gated_fields}

    The caller (score_fundamentals) checks raw["_ttm_metrics"] and injects TTM
    values into the scoring calculation automatically.
    """
    ticker = ticker.upper().strip()

    # ── Fetch base raw data if not provided ───────────────────────────────────
    if raw is None:
        from fundamentals_engine import fetch_fundamentals_raw
        raw = fetch_fundamentals_raw(ticker)

    if not _FINNHUB_API_KEY:
        logger.debug("finnhub_ttm: no API key — skipping TTM augmentation for %s", ticker)
        raw["_ttm_metrics"] = {}
        raw["_ttm_validation"] = {}
        raw["_ttm_partial"] = {"partial_score": False, "scored_metrics": None, "total_metrics": None, "gated_fields": []}
        return raw

    # ── Fetch Finnhub /stock/metric (primary TTM source) ─────────────────────
    metric_ttm: dict | None = None
    gated_metric = False
    try:
        metric_ttm = fetch_finnhub_metrics(ticker, force_refresh=force_refresh)
        if metric_ttm is not None and metric_ttm.get("gated"):
            gated_metric = True
            logger.info("finnhub_ttm: /stock/metric gated for %s", ticker)
            metric_ttm = None
    except Exception as exc:
        logger.warning("finnhub_ttm: fetch_finnhub_metrics failed for %s: %s", ticker, exc)

    # ── Fetch Finnhub /stock/financials-reported (secondary — quarterly data) ─
    financials: list | None = None
    gated_financials = False
    try:
        financials = fetch_finnhub_financials(ticker, force_refresh=force_refresh)
        if financials is not None and len(financials) == 0:
            # Empty list indicates 403/premium-gated or no data
            gated_financials = True
            logger.info("finnhub_ttm: financials-reported gated/empty for %s", ticker)
            financials = None
    except Exception as exc:
        logger.warning("finnhub_ttm: fetch_finnhub_financials failed for %s: %s", ticker, exc)

    # ── Compute TTM from quarterly data ───────────────────────────────────────
    computed_ttm: dict = {}
    if financials:
        try:
            computed_ttm = compute_ttm(financials)
        except Exception as exc:
            logger.warning("finnhub_ttm: compute_ttm failed for %s: %s", ticker, exc)

    # ── Validate TTM cross-check ──────────────────────────────────────────────
    validation = validate_ttm(metric_ttm, computed_ttm)

    # ── Build the unified _ttm_metrics dict ───────────────────────────────────
    # Prefer pre-computed Finnhub TTM scalars for margins / ratios.
    # Fall back to computed_ttm for FCF if Finnhub metric is not available.
    ttm_metrics: dict[str, Any] = {
        # Margins — from /stock/metric (ratio form, 0-1)
        "gross_margin_ttm":     metric_ttm.get("gross_margin_ttm")    if metric_ttm else None,
        "operating_margin_ttm": metric_ttm.get("operating_margin_ttm") if metric_ttm else None,
        "net_margin_ttm":       metric_ttm.get("net_margin_ttm")       if metric_ttm else None,
        # Returns — from /stock/metric (percentage form)
        "roe_ttm":              metric_ttm.get("roe_ttm")              if metric_ttm else None,
        "roi_ttm":              metric_ttm.get("roi_ttm")              if metric_ttm else None,
        # Liquidity — from /stock/metric (ratio / ratio-converted)
        "current_ratio_q":      metric_ttm.get("current_ratio_q")      if metric_ttm else None,
        "de_ratio_q":           metric_ttm.get("de_ratio_q")           if metric_ttm else None,
        # Growth — from /stock/metric (percentage form)
        "rev_growth_ttm_yoy":   metric_ttm.get("rev_growth_ttm_yoy")  if metric_ttm else None,
        "eps_growth_ttm_yoy":   metric_ttm.get("eps_growth_ttm_yoy")  if metric_ttm else None,
        # FCF — prefer /stock/metric, fall back to computed from quarterly
        "fcf_ttm_usd":          (
            metric_ttm.get("fcf_ttm_usd")
            if metric_ttm and metric_ttm.get("fcf_ttm_usd") is not None
            else computed_ttm.get("ttm_fcf")
        ),
        # Computed TTM scalars (for reference and validation display)
        "computed_revenue":     computed_ttm.get("ttm_revenue"),
        "computed_net_income":  computed_ttm.get("ttm_net_income"),
        "computed_ocf":         computed_ttm.get("ttm_ocf"),
        "computed_capex":       computed_ttm.get("ttm_capex"),
        "computed_fcf":         computed_ttm.get("ttm_fcf"),
        # Metadata
        "period_end":           computed_ttm.get("period_end"),
        "quarters_used":        computed_ttm.get("quarters_used", 0),
        "sources": {
            "margins":      "finnhub_metric" if metric_ttm else None,
            "returns":      "finnhub_metric" if metric_ttm else None,
            "liquidity":    "finnhub_metric" if metric_ttm else None,
            "fcf":          (
                "finnhub_metric"
                if metric_ttm and metric_ttm.get("fcf_ttm_usd") is not None
                else ("finnhub_financials" if computed_ttm.get("ttm_fcf") is not None else None)
            ),
        },
        "gated_metric":         gated_metric,
        "gated_financials":     gated_financials,
    }

    # ── Inject TTM values into raw for the scoring engine ────────────────────
    # ROE and ROIC: the scoring engine reads raw["roe"] / raw["roic"] directly
    if ttm_metrics.get("roe_ttm") is not None:
        raw["roe"] = ttm_metrics["roe_ttm"]
    if ttm_metrics.get("roi_ttm") is not None:
        raw["roic"] = ttm_metrics["roi_ttm"]

    # ── Partial score tracking ─────────────────────────────────────────────────
    # List which fields are gated (no data due to premium gate)
    gated_fields: list[str] = []
    if gated_metric:
        gated_fields.extend([
            "gross_margin_ttm", "operating_margin_ttm", "net_margin_ttm",
            "roe_ttm", "roi_ttm", "current_ratio_q", "de_ratio_q",
            "rev_growth_ttm_yoy", "eps_growth_ttm_yoy", "fcf_ttm_usd",
        ])
    if gated_financials:
        gated_fields.extend(["quarterly_trend_ocf", "quarterly_trend_revenue"])

    partial_score = bool(gated_fields)

    raw["_ttm_metrics"] = ttm_metrics
    raw["_ttm_validation"] = validation
    raw["_ttm_partial"] = {
        "partial_score":  partial_score,
        "scored_metrics": None,  # filled in by score_fundamentals()
        "total_metrics":  None,  # filled in by score_fundamentals()
        "gated_fields":   gated_fields,
    }

    logger.info(
        "finnhub_ttm  ticker=%s  gm_ttm=%.4f  om_ttm=%.4f  nm_ttm=%.4f  "
        "roe=%.1f  roic=%.1f  fcf_ttm=%s  quarters_used=%d  gated_metric=%s",
        ticker,
        (ttm_metrics.get("gross_margin_ttm") or 0),
        (ttm_metrics.get("operating_margin_ttm") or 0),
        (ttm_metrics.get("net_margin_ttm") or 0),
        (ttm_metrics.get("roe_ttm") or 0),
        (ttm_metrics.get("roi_ttm") or 0),
        ttm_metrics.get("fcf_ttm_usd"),
        (ttm_metrics.get("quarters_used") or 0),
        gated_metric,
    )

    return raw
