"""Chart geometry for the fundamentals scorecard.

Pure functions that turn the year-by-year history into SVG coordinates. There
is no rendering, no Flask and no fetching here, so the arithmetic that places a
bar can be tested exactly the way the arithmetic behind a metric is. The
template walks the returned dicts and emits ``<rect>``/``<polyline>`` elements.

Every chart reads oldest year on the left, newest on the right, which is the
reverse of the history table's newest-first ordering.
"""
from __future__ import annotations

WIDTH = 560
HEIGHT = 220
PAD_LEFT = 52
PAD_RIGHT = 10
PAD_TOP = 16
PAD_BOTTOM = 30

# Series identity is a CSS class, not a hex value. The page is themed from
# Elite's tokens and a chart that hardcodes its own palette drifts away from
# the rest of the app the first time a token changes.
REVENUE_SERIES = "s-revenue"
INCOME_SERIES = "s-income"
FCF_SERIES = "s-fcf"
GROSS_SERIES = "s-gross"
OPERATING_SERIES = "s-operating"
NET_SERIES = "s-net"
ROIC_SERIES = "s-roic"


def _plot_box():
    return (PAD_LEFT, PAD_TOP,
            WIDTH - PAD_LEFT - PAD_RIGHT,
            HEIGHT - PAD_TOP - PAD_BOTTOM)


def money(value, symbol: str = "$") -> str:
    """Compact currency label. Mirrors the formatting used in the score rows.

    A 20-F filer states its accounts in its own currency, so the axis has to
    be able to say NT$ or EUR rather than always a dollar sign.
    """
    if value is None:
        return "N/A"
    sign = "-" if value < 0 else ""
    n = abs(float(value))
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= cutoff:
            return f"{sign}{symbol}{n / cutoff:.2f}{suffix}".replace(".00", "")
    return f"{sign}{symbol}{n:,.0f}"


def percent(value) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def _nice_bounds(values: list[float], include_zero: bool = True):
    """A low/high pair with a little headroom, always spanning zero for bars."""
    vals = [v for v in values if v is not None]
    if not vals:
        return 0.0, 1.0
    lo, hi = min(vals), max(vals)
    if include_zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    if hi == lo:
        hi = lo + (abs(lo) or 1.0)
    span = hi - lo
    # An all-positive bar chart gets an axis that starts exactly at zero, so a
    # gridline lands on the baseline instead of just below it.
    pad_lo = 0.0 if (include_zero and lo == 0.0) else span * 0.02
    return lo - pad_lo, hi + span * 0.08


def _fiscal_labels(history: list[dict], ttm_flag: str | None = None) -> list[str]:
    """FY labels oldest-first, falling back to the row's own label.

    ``ttm_flag`` names a row key marking a point drawn from a trailing twelve
    month figure rather than the fiscal year the row is labelled with. The
    margin series is like this: its newest point comes from a metric feed
    because a fiscal year end can be eleven months stale, and drawing it under
    an FY label said the year closed at a number it did not close at.
    """
    labels = []
    for row in history:
        if ttm_flag and row.get(ttm_flag):
            labels.append("TTM")
            continue
        end = row.get("period_end")
        if end and len(str(end)) >= 4:
            labels.append(f"FY{str(end)[2:4]}")
        else:
            labels.append(str(row.get("label") or ""))
    return labels


def _grouped_bars(history: list[dict], series_defs: list[dict], fmt,
                  require: list[str] | None = None,
                  mark_declines: str | None = None) -> dict | None:
    """Grouped bar chart. ``series_defs`` is [{key, name, cls}, ...].

    A series with no values is dropped from the bars and the legend. Keys named
    in ``require`` must have data or the whole panel is dropped, so a chart
    titled "Free cash flow vs net income" never renders with the cash flow half
    silently missing.

    ``mark_declines`` names a series whose down years get their own class. A
    year the top line went backwards is the one bar on the chart worth pointing
    at, and a reader should not have to compare heights to find it.
    """
    rows = list(reversed(history or []))
    if len(rows) < 2:
        return None
    values = {spec["key"]: [row.get(spec["key"]) for row in rows]
              for spec in series_defs}
    for key in require or []:
        if not any(v is not None for v in values.get(key, [])):
            return None
    series_defs = [s for s in series_defs
                   if any(v is not None for v in values[s["key"]])]
    if not series_defs:
        return None
    flat = [v for spec in series_defs for v in values[spec["key"]] if v is not None]
    if len(flat) < 2:
        return None

    lo, hi = _nice_bounds(flat)
    x0, y0, w, h = _plot_box()
    group_w = w / len(rows)
    inner = group_w * 0.68
    bar_w = inner / len(series_defs)

    def y_of(value):
        return y0 + h - ((value - lo) / (hi - lo)) * h

    zero_y = y_of(0.0)
    bars = []
    for gi, row in enumerate(rows):
        left = x0 + gi * group_w + (group_w - inner) / 2
        for si, spec in enumerate(series_defs):
            value = values[spec["key"]][gi]
            if value is None:
                continue
            vy = y_of(value)
            declined = False
            if mark_declines and spec["key"] == mark_declines and gi > 0:
                prior = values[spec["key"]][gi - 1]
                declined = prior is not None and value < prior
            bars.append({
                "cls_extra": " is-decline" if declined else "",
                "x": round(left + si * bar_w, 2),
                "y": round(min(vy, zero_y), 2),
                "w": round(max(bar_w - 2, 1), 2),
                "h": round(abs(zero_y - vy), 2),
                "cls": spec["cls"],
                "title": f"{spec['name']} {_fiscal_labels(rows)[gi]}: {fmt(value)}",
            })
    return {
        "kind": "bars",
        "width": WIDTH, "height": HEIGHT,
        "bars": bars,
        "zero_y": round(zero_y, 2),
        "gridlines": _with_zero_line(_gridlines(lo, hi, y_of, fmt), zero_y, fmt),
        "x_labels": [
            {"x": round(x0 + i * group_w + group_w / 2, 2),
             "y": HEIGHT - PAD_BOTTOM + 16, "text": label}
            for i, label in enumerate(_fiscal_labels(rows))
        ],
        "legend": [{"name": s["name"], "cls": s["cls"]} for s in series_defs],
    }


def _with_zero_line(lines, zero_y, fmt):
    """Guarantee a baseline. Bars that cross zero need one to read correctly,
    and evenly spaced gridlines will not always land on it."""
    if any(line["zero"] for line in lines):
        return lines
    lines.append({"y": round(zero_y, 2), "x1": PAD_LEFT, "x2": WIDTH - PAD_RIGHT,
                  "text": fmt(0), "zero": True})
    return sorted(lines, key=lambda line: line["y"], reverse=True)


def _gridlines(lo, hi, y_of, fmt, count: int = 4):
    lines = []
    for i in range(count + 1):
        value = lo + (hi - lo) * i / count
        lines.append({
            "y": round(y_of(value), 2),
            "x1": PAD_LEFT, "x2": WIDTH - PAD_RIGHT,
            "text": fmt(value),
            "zero": abs(value) < (hi - lo) * 1e-9,
        })
    return lines


def _lines(history: list[dict], series_defs: list[dict],
           ttm_flag: str | None = None) -> dict | None:
    """Multi-line chart for percentage series."""
    rows = list(reversed(history or []))
    if len(rows) < 2:
        return None
    columns = {s["key"]: [row.get(s["key"]) for row in rows] for s in series_defs}
    flat = [v for col in columns.values() for v in col if v is not None]
    if len(flat) < 2:
        return None

    lo, hi = _nice_bounds(flat, include_zero=False)
    lo = min(lo, 0.0) if min(flat) < 0 else max(0.0, lo)
    x0, y0, w, h = _plot_box()
    step = w / max(len(rows) - 1, 1)

    def y_of(value):
        return y0 + h - ((value - lo) / (hi - lo)) * h

    labels = _fiscal_labels(rows, ttm_flag)
    lines, points = [], []
    for spec in series_defs:
        coords = []
        for i, value in enumerate(columns[spec["key"]]):
            if value is None:
                continue
            px, py = round(x0 + i * step, 2), round(y_of(value), 2)
            coords.append(f"{px},{py}")
            points.append({"cx": px, "cy": py, "cls": spec["cls"],
                           "title": f"{spec['name']} {labels[i]}: {percent(value)}"})
        if len(coords) >= 2:
            lines.append({"points": " ".join(coords), "cls": spec["cls"],
                          "name": spec["name"]})
    if not lines:
        return None
    return {
        "kind": "lines",
        "width": WIDTH, "height": HEIGHT,
        "lines": lines, "points": points,
        "gridlines": _gridlines(lo, hi, y_of, percent),
        "x_labels": [
            {"x": round(x0 + i * step, 2), "y": HEIGHT - PAD_BOTTOM + 16, "text": label}
            for i, label in enumerate(labels)
        ],
        "legend": [{"name": s["name"], "cls": s["cls"]}
                   for s in series_defs
                   if any(v is not None for v in columns[s["key"]])],
    }


def build_charts(history: list[dict] | None, currency: str = "$") -> list[dict]:
    """Charts for the fundamentals page, in display order.

    A chart that cannot be drawn from at least two years of data is dropped
    rather than rendered as an empty frame, so a thin filer shows fewer panels
    instead of misleading ones.
    """
    history = history or []
    _money = lambda value: money(value, currency)   # noqa: E731
    specs = [
        ("revenue_income", "Revenue and net income",
         "Top line against what actually reaches shareholders.",
         lambda: _grouped_bars(history, [
             {"key": "revenue_num", "name": "Revenue", "cls": REVENUE_SERIES},
             {"key": "net_income_num", "name": "Net income", "cls": INCOME_SERIES},
         ], _money, require=["revenue_num"], mark_declines="revenue_num")),
        ("margins", "Margin trend",
         "Widening margins mean pricing power; narrowing means competition.",
         lambda: _lines(history, [
             {"key": "gross_margin_num", "name": "Gross", "cls": GROSS_SERIES},
             {"key": "operating_margin_num", "name": "Operating", "cls": OPERATING_SERIES},
             {"key": "net_margin_num", "name": "Net", "cls": NET_SERIES},
         ], ttm_flag="margins_are_ttm")),
        ("cash_quality", "Free cash flow vs net income",
         "Cash above reported earnings is the sign of honest accounting.",
         lambda: _grouped_bars(history, [
             {"key": "net_income_num", "name": "Net income", "cls": INCOME_SERIES},
             {"key": "fcf_num", "name": "Free cash flow", "cls": FCF_SERIES},
         ], _money, require=["fcf_num", "net_income_num"])),
        ("roic", "Return on invested capital",
         "Competition drags returns toward the cost of capital. Staying above it is what a moat looks like in the numbers.",
         lambda: _lines(history, [
             {"key": "roic_num", "name": "ROIC", "cls": ROIC_SERIES},
         ])),
    ]
    charts = []
    for key, title, caption, build in specs:
        try:
            chart = build()
        except Exception:
            chart = None
        if chart:
            chart.update(key=key, title=title, caption=caption)
            charts.append(chart)
    return charts
