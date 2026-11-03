"""peer_comparison.py — a ratio is only an argument next to someone else's.

Return on equity of -11.2% means nothing on its own. Against a peer group
running 46.7% it is the whole case. The fundamentals engine already computes
every ratio needed — ROE, ROIC, debt to equity, gross, operating and net
margin, revenue growth, the current ratio — for one company at a time. This
adds the column beside them.

WHY THE PEERS ARE NAMED, AND NOT AVERAGED INTO "THE INDUSTRY".

A single figure labelled "Industry: 46.7%" cannot be checked. It looks
identical whether it came from two hundred companies or from a stale cache,
and an industry average is extremely sensitive to who someone decided was in
the bucket. That sensitivity is not hypothetical: the standard complaint
about Intel's growth comparison is that it is slow *for its category*, and
the category is an editorial choice made by a data vendor. An unnamed
average would bake that choice in invisibly, and the reader would have no
way to notice.

So every comparison here carries its peer set. The median is computed from
companies the page can list, each with its own number, and when a comparison
looks wrong the reason is visible rather than buried in a label.

WHAT IT REFUSES TO DO. A peer whose ratio did not arrive is not counted as
zero and is not quietly dropped into an average — it is reported as a peer
that could not be read, and the comparison says how many of the group it is
actually based on. With fewer than MIN_PEERS usable numbers there is no
median at all, because three companies is not an industry and pretending
otherwise is how a number gets trusted that should not be.
"""

from __future__ import annotations

import logging
import statistics
from typing import Optional

logger = logging.getLogger(__name__)

# Below this, a median is noise. Four is already thin and is reported as
# thin; the panel shows the count so the reader can discount it themselves.
MIN_PEERS = 4
MAX_PEERS = 12

# The ratios worth comparing, in the order they read as an argument:
# can it earn, does it keep what it earns, can it survive, is it growing.
#
# `higher_is_better` drives the ranking. `spread` is how many points away
# from the median counts as genuinely different for THIS ratio — a 3-point
# ROE gap is noise, a 3-point net margin gap is not, and a single threshold
# across all of them would call everything an outlier.
METRICS = (
    {"key": "roe", "label": "Return on equity", "unit": "%",
     "higher_is_better": True, "spread": 8.0,
     "note": "Profit earned on shareholder money."},
    {"key": "roic", "label": "Return on invested capital", "unit": "%",
     "higher_is_better": True, "spread": 6.0,
     "note": "Profit on everything put to work, borrowed money included."},
    {"key": "net_margin", "label": "Net margin", "unit": "%",
     "higher_is_better": True, "spread": 5.0,
     "note": "What survives all the way to the bottom line."},
    {"key": "operating_margin", "label": "Operating margin", "unit": "%",
     "higher_is_better": True, "spread": 5.0,
     "note": "Profit from running the business, before financing and tax."},
    {"key": "gross_margin", "label": "Gross margin", "unit": "%",
     "higher_is_better": True, "spread": 6.0,
     "note": "What is left after the cost of the product itself."},
    {"key": "revenue_growth", "label": "Revenue growth", "unit": "%",
     "higher_is_better": True, "spread": 5.0,
     "note": "Year on year. The one that gets compared to the sector."},
    {"key": "debt_to_equity", "label": "Debt to equity", "unit": "x",
     "higher_is_better": False, "spread": 0.35,
     "note": "Borrowed money against owners' money. Lower is safer."},
    {"key": "current_ratio", "label": "Current ratio", "unit": "x",
     "higher_is_better": True, "spread": 0.4,
     "note": "Short-term assets against short-term bills."},
)
METRICS_BY_KEY = {m["key"]: m for m in METRICS}


def _num(value) -> Optional[float]:
    """A finite number, or None. Strings, NaN and bools are not numbers."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _percentile(values: list[float], target: float, higher_is_better: bool) -> int:
    """Where `target` sits in `values`, 0–100, 100 being best.

    Ties count as half, so a company level with everyone lands at 50 rather
    than at 0 or 100 depending on sort order.
    """
    if not values:
        return 50
    better = sum(1 for v in values if (v < target if higher_is_better else v > target))
    same = sum(1 for v in values if v == target)
    return int(round((better + same / 2) / len(values) * 100))


def compare(subject: dict, peers: list[dict]) -> dict:
    """Rank one company's ratios against a named peer group.

    `subject` and each peer are the flat dicts the fundamentals engine already
    produces: {"ticker": ..., "roe": ..., "debt_to_equity": ..., ...}.

    Returns a dict carrying, for every metric the subject has a number for,
    the subject's value, the peer median, the gap, a percentile, and — the
    part that makes it checkable — how many peers that median is built from
    and which ones could not be read.
    """
    subject = subject if isinstance(subject, dict) else {}
    peers = [p for p in (peers or []) if isinstance(p, dict) and p.get("ticker")]
    peers = peers[:MAX_PEERS]

    rows = []
    for metric in METRICS:
        key = metric["key"]
        mine = _num(subject.get(key))
        if mine is None:
            continue  # nothing to compare; the engine already shows the gap

        usable, unreadable = [], []
        for peer in peers:
            value = _num(peer.get(key))
            (usable.append(value) if value is not None
             else unreadable.append(str(peer["ticker"]).upper()))

        if len(usable) < MIN_PEERS:
            rows.append({
                "key": key, "label": metric["label"], "unit": metric["unit"],
                "note": metric["note"], "value": mine,
                "median": None, "gap": None, "percentile": None,
                "verdict": "no_comparison", "peers_used": len(usable),
                "peers_unreadable": unreadable,
                "why_not": (f"only {len(usable)} of {len(peers)} peers reported this"
                            if peers else "no peer group available"),
            })
            continue

        median = statistics.median(usable)
        gap = mine - median
        better = gap > 0 if metric["higher_is_better"] else gap < 0
        material = abs(gap) >= metric["spread"]
        rows.append({
            "key": key, "label": metric["label"], "unit": metric["unit"],
            "note": metric["note"], "value": mine,
            "median": round(median, 2), "gap": round(gap, 2),
            "percentile": _percentile(usable, mine, metric["higher_is_better"]),
            # Four states, not two. "In line" is a real finding and the most
            # common one; collapsing it into better/worse would make every
            # company look like an outlier on something.
            "verdict": ("ahead" if better and material else
                        "behind" if not better and material else
                        "in_line"),
            "peers_used": len(usable),
            "peers_unreadable": unreadable,
            "why_not": "",
        })

    compared = [r for r in rows if r["verdict"] != "no_comparison"]
    return {
        "rows": rows,
        "peers": [str(p["ticker"]).upper() for p in peers],
        "peer_count": len(peers),
        "compared_count": len(compared),
        "usable": bool(compared),
        "ahead": [r["label"] for r in compared if r["verdict"] == "ahead"],
        "behind": [r["label"] for r in compared if r["verdict"] == "behind"],
    }


def _fmt(value, unit: str) -> str:
    if value is None:
        return "—"
    return f"{value:,.1f}%" if unit == "%" else f"{value:,.2f}x"


def written_read(subject_name: str, comparison: dict) -> list[str]:
    """The comparison in sentences, built only from the numbers above.

    Deliberately generated rather than written by a model. Every clause here
    is traceable to a row in the table beside it, which means it cannot
    describe a company the data does not support — and it costs nothing per
    page view, so it is always there rather than behind a button.
    """
    if not comparison.get("usable"):
        peers = comparison.get("peer_count", 0)
        return ["There is no peer comparison for this company yet — "
                + ("no peer group was returned." if not peers else
                   f"{peers} peers were found but too few reported the same "
                   "ratios to form a median.")]

    name = subject_name or "This company"
    group = ", ".join(comparison["peers"][:8])
    lines = [f"Compared with {comparison['peer_count']} peers ({group})."]

    rows = {r["key"]: r for r in comparison["rows"]}

    def phrase(row) -> str:
        direction = "above" if row["gap"] > 0 else "below"
        return (f"{row['label'].lower()} of {_fmt(row['value'], row['unit'])} "
                f"against a peer median of {_fmt(row['median'], row['unit'])} "
                f"({_fmt(abs(row['gap']), row['unit'])} {direction})")

    behind = [r for r in comparison["rows"] if r["verdict"] == "behind"]
    ahead = [r for r in comparison["rows"] if r["verdict"] == "ahead"]
    behind.sort(key=lambda r: -abs(r["gap"] / (METRICS_BY_KEY[r["key"]]["spread"] or 1)))
    ahead.sort(key=lambda r: -abs(r["gap"] / (METRICS_BY_KEY[r["key"]]["spread"] or 1)))

    if behind:
        lines.append(f"{name} trails the group on " + phrase(behind[0]) + ".")
        if len(behind) > 1:
            lines.append("Also behind on "
                         + ", ".join(r["label"].lower() for r in behind[1:4]) + ".")
    if ahead:
        lines.append(("It leads on " if behind else f"{name} leads on ")
                     + phrase(ahead[0]) + ".")
        if len(ahead) > 1:
            lines.append("Also ahead on "
                         + ", ".join(r["label"].lower() for r in ahead[1:4]) + ".")
    if not behind and not ahead:
        lines.append(f"{name} sits within the normal range of its peers on "
                     f"all {comparison['compared_count']} ratios compared.")

    # The two balance-sheet facts a reader should see even when they are
    # unremarkable, because "unremarkable debt" is itself worth knowing.
    de = rows.get("debt_to_equity")
    if de and de["verdict"] == "in_line" and de["median"] is not None:
        lines.append(f"Debt to equity of {_fmt(de['value'], 'x')} is ordinary "
                     f"for this group (median {_fmt(de['median'], 'x')}).")

    thin = [r for r in comparison["rows"] if r["verdict"] == "no_comparison"]
    if thin:
        lines.append("No peer median for "
                     + ", ".join(r["label"].lower() for r in thin[:3])
                     + " — too few peers reported it.")
    return lines


# ---------------------------------------------------------------------------
# Fetching the peer group
#
# FMP returns both halves: a peer list per ticker, and TTM key metrics per
# company. One call for the list and one per peer, which is why this is
# cached hard — peer ratios move on quarterly filings, not on page views.
# ---------------------------------------------------------------------------

import threading
import time as _time

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_S = 12 * 3600          # ratios change on filings, not on refreshes


def _cached(key: str):
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry and _time.monotonic() - entry["at"] < _CACHE_TTL_S:
            return entry["value"]
    return None


def _store(key: str, value) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = {"at": _time.monotonic(), "value": value}


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _pct(value) -> Optional[float]:
    """FMP reports ratios as fractions; the rest of this app uses percent."""
    v = _num(value)
    return None if v is None else v * 100


def fetch_peer_tickers(ticker: str, limit: int = MAX_PEERS) -> list[str]:
    """Who FMP considers this company's peers, minus the company itself."""
    import fundamentals_engine as fe
    ticker = str(ticker or "").upper().strip()
    if not ticker:
        return []
    cache_key = f"peers:{ticker}"
    hit = _cached(cache_key)
    if hit is not None:
        return hit[:limit]

    rows = fe._fmp_get("stock-peers", ticker=ticker) or fe._fmp_get("stock_peers", ticker=ticker)
    out: list[str] = []
    for row in (rows if isinstance(rows, list) else [rows] if rows else []):
        if not isinstance(row, dict):
            continue
        # The two shapes FMP has used: a list of symbols on one row, or one
        # row per peer.
        bundle = row.get("peersList") or row.get("peers") or []
        if isinstance(bundle, str):
            bundle = [p.strip() for p in bundle.split(",")]
        for sym in (bundle or [row.get("symbol")]):
            sym = str(sym or "").upper().strip()
            if sym and sym != ticker and sym not in out:
                out.append(sym)
    _store(cache_key, out)
    return out[:limit]


def fetch_metrics(ticker: str) -> Optional[dict]:
    """The comparable ratios for one company, or None if FMP had nothing.

    None and a dict of Nones are different answers: the first means the
    company could not be read at all, the second that it was read and
    reported nothing. compare() treats both as unreadable, but the caller
    can log which happened.
    """
    import fundamentals_engine as fe
    ticker = str(ticker or "").upper().strip()
    if not ticker:
        return None
    cache_key = f"metrics:{ticker}"
    hit = _cached(cache_key)
    if hit is not None:
        return hit

    km = fe._fmp_get("key-metrics-ttm", ticker=ticker)
    ratios = fe._fmp_get("ratios-ttm", ticker=ticker)
    k = km[0] if isinstance(km, list) and km else (km if isinstance(km, dict) else {})
    r = ratios[0] if isinstance(ratios, list) and ratios else (ratios if isinstance(ratios, dict) else {})
    if not k and not r:
        _store(cache_key, None)
        return None

    out = {
        "ticker": ticker,
        "roe":              _pct(k.get("roeTTM") if k else None),
        "roic":             _pct(k.get("roicTTM") if k else None),
        "net_margin":       _pct(r.get("netProfitMarginTTM") if r else None),
        "operating_margin": _pct(r.get("operatingProfitMarginTTM") if r else None),
        "gross_margin":     _pct(r.get("grossProfitMarginTTM") if r else None),
        "debt_to_equity":   _num(r.get("debtEquityRatioTTM") if r else None),
        "current_ratio":    _num(r.get("currentRatioTTM") if r else None),
        # FMP does not carry TTM revenue growth on these endpoints. Left
        # absent rather than derived from a different period and compared
        # against figures that are TTM — a growth rate over the wrong window
        # is worse than no growth rate.
        "revenue_growth":   None,
    }
    _store(cache_key, out)
    return out


def build(ticker: str, subject: dict, *, max_peers: int = MAX_PEERS) -> dict:
    """The whole panel for one company: peers, table and written read.

    Never raises and never fabricates. With no key, no peers or too few
    readable ones, it returns a result whose `usable` is False and whose
    `why_not` says which of those it was — the panel then says it cannot
    compare, which is a different statement from "this company is average".
    """
    import fundamentals_engine as fe
    ticker = str(ticker or "").upper().strip()
    name = (subject or {}).get("company_name") or ticker

    if not fe._FMP_API_KEY:
        return {"usable": False, "rows": [], "peers": [], "peer_count": 0,
                "compared_count": 0, "ahead": [], "behind": [],
                "why_not": "FMP_API_KEY is not set, so no peer data can be fetched.",
                "read": ["Peer comparison is unavailable: this deployment has no "
                         "fundamentals data key configured."]}
    try:
        symbols = fetch_peer_tickers(ticker, limit=max_peers)
        peers = []
        for sym in symbols:
            metrics = fetch_metrics(sym)
            if metrics:
                peers.append(metrics)
        comparison = compare(subject or {}, peers)
        comparison["why_not"] = "" if comparison["usable"] else (
            "no peer group was returned for this ticker" if not symbols else
            "the peers returned too few comparable ratios")
        comparison["read"] = written_read(name, comparison)
        return comparison
    except Exception as exc:
        logger.warning("peer comparison failed for %s: %s", ticker, exc)
        return {"usable": False, "rows": [], "peers": [], "peer_count": 0,
                "compared_count": 0, "ahead": [], "behind": [],
                "why_not": f"peer data could not be fetched ({type(exc).__name__})",
                "read": ["Peer comparison could not be built from the "
                         "fundamentals provider just now."]}
