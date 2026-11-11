"""Headline results from a company's earnings press release, before the 10-Q.

A company announces its quarter in a press release, filed with the SEC as an
8-K under Item 2.02 with the release attached as Exhibit 99.1. The 10-Q, which
is where the app's statements come from (SEC XBRL company facts), follows a
day to a few weeks later. In between, the research page showed the previous
quarter with no sign that a newer one had been announced.

This reads the release itself: revenue, net income (loss) and diluted EPS for
the quarter, as printed. It is the company's own unaudited release, labelled
as such, and only shown while no 10-Q/10-K has been filed after it. Anything
it cannot read with confidence it leaves out rather than guessing.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, dict]] = {}
_LOCK = threading.Lock()
_TTL_HIT, _TTL_MISS = 1800, 600
_ARCHIVE_HEADERS = {"User-Agent": "HAULTRA-AI/1.0 contact@haultra.ai", "Accept-Encoding": "gzip, deflate"}
PERIODIC = {"10-Q", "10-Q/A", "10-K", "10-K/A"}


# ── Reading the release tables ───────────────────────────────────────────────

class _Tables(HTMLParser):
    """Rows of every table as lists of cell text, each tagged with the
    "(in millions)" / "(in thousands)" scale last seen above or inside it."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[list[str], float | None]] = []
        self.scale: float | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            cells = [c for c in self._row if c]
            if cells:
                self.rows.append((cells, self.scale))
            self._row = None

    def handle_data(self, data):
        low = data.lower()
        if "in millions" in low:
            self.scale = 1e6
        elif "in thousands" in low:
            self.scale = 1e3
        elif "in billions" in low:
            self.scale = 1e9
        if self._cell is not None:
            self._cell.append(data)


_NUM = re.compile(r"(\()?\s*\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(\))?")
_REVENUE = {"revenue", "revenues", "total revenue", "total revenues", "net revenue", "net revenues",
            "net sales", "total net sales", "total net revenue", "total net revenues", "sales"}


def _first_number(cells: list[str]):
    text = " ".join(cells[1:])
    m = _NUM.search(text)
    if not m:
        return None
    value = float(m.group(2).replace(",", ""))
    if m.group(1) and (m.group(3) or text[m.end():].lstrip().startswith(")")):
        value = -value
    elif m.group(1):
        value = -value
    return value


def _label(cells: list[str]) -> str:
    return re.sub(r"[:\s]+$", "", re.sub(r"\(\d\)|\s+", " ", cells[0]).strip().lower())


def parse_release(doc: str) -> dict:
    """{revenue, net_income, eps_diluted} in dollars (EPS per share), each
    present only when read with confidence."""
    p = _Tables()
    try:
        p.feed(doc or "")
    except Exception:
        return {}
    out: dict = {}
    for cells, scale in p.rows:
        if len(cells) < 2:
            continue
        label = _label(cells)
        if not label or any(w in label for w in ("adjusted", "non-gaap", "margin", "weighted", "growth", "%")):
            continue
        value = _first_number(cells)
        if value is None:
            continue
        if "revenue" not in out and label in _REVENUE and scale:
            out["revenue"] = value * scale
        elif ("net_income" not in out and scale
              and re.match(r"net \(?(income|loss)\)?", label)
              and not any(w in label for w in ("per share", "per diluted", "noncontrolling", "attributable to non"))):
            if label.startswith("net loss") and "income" not in label and value > 0:
                value = -value               # "Net loss  709" printed without parentheses
            out["net_income"] = value * scale
        elif ("eps_diluted" not in out and "diluted" in label
              and ("per share" in label or "eps" in label) and abs(value) < 1000):
            if "loss" in label and "income" not in label and value > 0:
                value = -value
            out["eps_diluted"] = value
    return out


# ── Finding the release ──────────────────────────────────────────────────────

def pending_release(submissions: dict) -> dict | None:
    """The newest 8-K Item 2.02 filed after the newest 10-Q/10-K, or None."""
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    forms, dates = recent.get("form") or [], recent.get("filingDate") or []
    accns, items = recent.get("accessionNumber") or [], recent.get("items") or []
    release, periodic = None, ""
    for i in range(min(len(forms), len(dates), len(accns))):
        form, day = str(forms[i]).strip(), str(dates[i])[:10]
        if form in PERIODIC and day > periodic:
            periodic = day
        if form in ("8-K", "8-K/A") and "2.02" in str(items[i] if i < len(items) else ""):
            if release is None or day > release["filed"]:
                release = {"filed": day, "accn": accns[i]}
    if release and release["filed"] > periodic:
        release["last_periodic"] = periodic or None
        return release
    return None


def _exhibit_name(index: dict) -> str | None:
    names = [str(i.get("name") or "") for i in ((index or {}).get("directory") or {}).get("item") or []]
    htm = [n for n in names if n.lower().endswith((".htm", ".html"))]
    ranked = [n for n in htm if re.search(r"ex[-_]?99[-_.]?0?1|exhibit[-_]?99[-_.]?1|991", n.lower())]
    ranked += [n for n in htm if re.search(r"ex[-_]?99|exhibit[-_]?99", n.lower()) and n not in ranked]
    return ranked[0] if ranked else None


def fetch(ticker: str, cik: str, get) -> dict | None:
    """One uncached lookup. `get` is requests.get or a stand-in."""
    import fundamentals_engine as fe
    resp = get(f"https://data.sec.gov/submissions/CIK{cik}.json", timeout=10, headers=fe._EDGAR_HEADERS)
    rel = pending_release(resp.json()) if resp.status_code == 200 else None
    if not rel:
        return None
    folder = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{rel['accn'].replace('-', '')}"
    idx = get(folder + "/index.json", timeout=10, headers=_ARCHIVE_HEADERS)
    name = _exhibit_name(idx.json()) if idx.status_code == 200 else None
    figures = {}
    if name:
        doc = get(f"{folder}/{name}", timeout=15, headers=_ARCHIVE_HEADERS)
        if doc.status_code == 200:
            figures = parse_release(doc.text)
    return {"ticker": ticker.upper(), "filed": rel["filed"], "last_periodic": rel["last_periodic"],
            "accn": rel["accn"],
            "url": f"{folder}/{name}" if name else f"{folder}/{rel['accn']}-index.html", **figures}


def latest(ticker: str) -> dict | None:
    """Headline figures from an earnings release not yet followed by a
    10-Q/10-K, or None. Cached; never raises."""
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return None
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(ticker)
        if hit and now - hit[0] < (_TTL_HIT if hit[1] else _TTL_MISS):
            return dict(hit[1]) if hit[1] else None
    result = None
    try:
        import fundamentals_engine as fe
        cik, _name = fe._edgar_cik(ticker)
        result = fetch(ticker, cik, fe._req_module.get) if cik else None
    except Exception as exc:
        logger.info("earnings release %s: %s", ticker, exc)
    with _LOCK:
        if len(_CACHE) > 256:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[ticker] = (now, result or {})
    return dict(result) if result else None


def money(v) -> str:
    if v is None:
        return "—"
    sign, a = ("-" if v < 0 else ""), abs(v)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{sign}${a / div:,.2f}{suf}" if div == 1e9 else f"{sign}${a / div:,.0f}{suf}"
    return f"{sign}${a:,.0f}"


def summary(rel: dict) -> str:
    parts = []
    if rel.get("revenue") is not None:
        parts.append(f"Revenue {money(rel['revenue'])}")
    if rel.get("net_income") is not None:
        parts.append(f"net {'loss' if rel['net_income'] < 0 else 'income'} {money(abs(rel['net_income']))}")
    if rel.get("eps_diluted") is not None:
        parts.append(f"diluted EPS {'-' if rel['eps_diluted'] < 0 else ''}${abs(rel['eps_diluted']):.2f}")
    return ", ".join(parts)
