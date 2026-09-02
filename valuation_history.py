"""What the company cost in each of the years it reported.

A single P/E says nothing on its own. The same 47.9x is cheap for one business
and absurd for another, and the only cheap comparison available is the company
against itself. This puts today's multiple next to the multiple at each of its
own recent fiscal year ends, which is what turns "down 44% from the high" into
a statement with content: a stock that tripled and then halved can be far off
its peak and still the most expensive it has ever been.

The split trap is the whole difficulty. Price history from a market data feed
is split-adjusted; earnings per share as originally filed are not. Dividing one
by the other across a split produces a multiple wrong by exactly the split
ratio, in the direction that makes an expensive stock look cheap. So this uses
only the years the newest annual report restates on one basis — usually three —
and says so, rather than reaching further back and quietly mixing them.

Nothing here is a recommendation. "The highest of the five shown" is a fact
about a list of numbers.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _epoch(day: str) -> int | None:
    try:
        return int(datetime.strptime(str(day)[:10], "%Y-%m-%d")
                   .replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


def close_on_or_before(bars: dict | None, day: str, *, max_gap_days: int = 10):
    """Closing price on ``day``, or the last close before it.

    A fiscal year can end on a weekend or a holiday, so the exact date is often
    missing. A gap wider than ten days means the series does not really cover
    that period and no price is returned, rather than reaching back a month and
    calling it a year-end price.
    """
    target = _epoch(day)
    if not target or not bars:
        return None
    stamps = bars.get("timestamps") or []
    closes = bars.get("closes") or []
    best = None
    for i in range(min(len(stamps), len(closes))):
        if stamps[i] <= target and (best is None or stamps[i] > stamps[best]):
            best = i
    if best is None:
        return None
    if (target - stamps[best]) > max_gap_days * 86400:
        return None
    value = closes[best]
    return float(value) if value and value > 0 else None


def fmt_multiple(value) -> str:
    return "—" if not value else f"{value:.1f}x"


def fmt_price(value) -> str:
    return "—" if not value else f"${value:,.2f}"


def _ratio(price, per_share):
    if price is None or not per_share or per_share <= 0:
        return None
    return price / per_share


def build_history(*, period_ends, eps, fcf, shares, bars,
                  today_price=None, today_pe=None, today_pfcf=None,
                  comparable_years=None) -> dict:
    """A multiple-per-year table plus today's row.

    ``comparable_years`` caps how far back to go — the count of years the newest
    filing states on one split basis. Beyond it the price series and the filed
    per-share figures may sit on different bases.
    """
    period_ends = list(period_ends or [])
    limit = len(period_ends) if comparable_years is None else max(0, comparable_years)
    rows = []
    for i, end in enumerate(period_ends[:limit]):
        price = close_on_or_before(bars, end)
        if price is None:
            continue
        eps_i = eps[i] if i < len(eps or []) else None
        fcf_i = fcf[i] if i < len(fcf or []) else None
        sh_i = shares[i] if i < len(shares or []) else None
        fcf_ps = (fcf_i / sh_i) if (fcf_i is not None and sh_i) else None
        pe, pfcf = _ratio(price, eps_i), _ratio(price, fcf_ps)
        if pe is None and pfcf is None:
            continue
        rows.append({
            "label": f"FY{str(end)[2:4]}", "period_end": end,
            "price": price, "pe": pe, "pfcf": pfcf, "is_today": False,
            "price_txt": fmt_price(price), "pe_txt": fmt_multiple(pe),
            "pfcf_txt": fmt_multiple(pfcf),
        })

    today = None
    if today_pe or today_pfcf:
        today = {"label": "Today", "period_end": "", "price": today_price,
                 "pe": today_pe, "pfcf": today_pfcf, "is_today": True,
                 "price_txt": fmt_price(today_price),
                 "pe_txt": fmt_multiple(today_pe),
                 "pfcf_txt": fmt_multiple(today_pfcf)}

    note, rank = "", None
    history_pes = [r["pe"] for r in rows if r["pe"]]
    if today and today_pe and len(history_pes) >= 2:
        higher = sum(1 for x in history_pes if x > today_pe)
        rank = higher
        if higher == 0:
            note = (f"At {today_pe:.1f}x, today is the most expensive of the "
                    f"{len(history_pes) + 1} periods shown on trailing earnings.")
        elif higher == len(history_pes):
            note = (f"At {today_pe:.1f}x, today is the cheapest of the "
                    f"{len(history_pes) + 1} periods shown on trailing earnings.")
        else:
            note = (f"At {today_pe:.1f}x, today sits above {len(history_pes) - higher} "
                    f"and below {higher} of the {len(history_pes)} fiscal year ends shown.")

    return {
        "available": bool(rows) and today is not None,
        "rows": rows + ([today] if today else []),
        "note": note,
        "cheaper_years": rank,
        "comparable_years": limit,
    }

