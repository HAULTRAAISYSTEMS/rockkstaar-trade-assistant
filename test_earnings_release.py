"""Earnings press release figures shown before the 10-Q lands."""
from datetime import datetime, timezone

import earnings_release as er
import inbox_feeds

# Business Wire layout: "$" and ")" in their own cells, units in a header row.
RELEASE = """<html><body><p>CoreWeave Reports Strong Third Quarter 2026 Results</p>
<table>
<tr><td>(In millions, except percentages and per share amounts)</td></tr>
<tr><td>Three Months Ended September 30,</td></tr>
<tr><td></td><td>2026</td><td>2025</td></tr>
<tr><td>Revenue</td><td>$</td><td>3,661</td><td></td><td>$</td><td>1,365</td></tr>
<tr><td>Operating income (loss)</td><td>$</td><td>207</td><td>$</td><td>52</td></tr>
<tr><td>Net loss</td><td>$</td><td>(709</td><td>)</td><td>$</td><td>(110</td><td>)</td></tr>
<tr><td>Net loss margin</td><td>(19</td><td>)%</td></tr>
<tr><td>Basic net loss per share</td><td>$</td><td>(1.27</td><td>)</td></tr>
<tr><td>Diluted net loss per share</td><td>$</td><td>(1.27</td><td>)</td><td>$</td><td>(0.22</td><td>)</td></tr>
<tr><td>Adjusted net loss</td><td>$</td><td>(529</td><td>)</td></tr>
</table></body></html>"""


def test_parse_release_reads_headline_figures():
    out = er.parse_release(RELEASE)
    assert out == {"revenue": 3661e6, "net_income": -709e6, "eps_diluted": -1.27}
    assert er.summary(out) == "Revenue $3.66B, net loss $709M, diluted EPS -$1.27"


def test_parse_release_skips_amounts_with_no_stated_units():
    doc = "<table><tr><td>Revenue</td><td>3,661</td></tr><tr><td>Diluted EPS</td><td>0.50</td></tr></table>"
    assert er.parse_release(doc) == {"eps_diluted": 0.5}


def _subs(rows):
    keys = ("form", "filingDate", "accessionNumber", "items")
    return {"filings": {"recent": {k: [r[i] for r in rows] for i, k in enumerate(keys)}}}


def test_pending_only_when_release_is_newer_than_last_10q():
    subs = _subs([("8-K", "2026-11-10", "0001-26-1", "2.02,9.01"),
                  ("10-Q", "2026-08-12", "0001-26-0", ""),
                  ("8-K", "2026-08-11", "0001-25-9", "2.02,9.01")])
    rel = er.pending_release(subs)
    assert rel == {"filed": "2026-11-10", "accn": "0001-26-1", "last_periodic": "2026-08-12"}
    subs = _subs([("10-Q", "2026-11-12", "a", ""), ("8-K", "2026-11-10", "b", "2.02")])
    assert er.pending_release(subs) is None


class _Resp:
    def __init__(self, payload=None, text="", status=200):
        self._p, self.text, self.status_code = payload, text, status

    def json(self):
        return self._p


def test_fetch_follows_8k_to_exhibit():
    calls = []

    def get(url, **kw):
        calls.append(url)
        if "submissions" in url:
            return _Resp(_subs([("8-K", "2026-11-10", "0001769628-26-000300", "2.02,9.01"),
                                ("10-Q", "2026-08-12", "x", "")]))
        if url.endswith("index.json"):
            return _Resp({"directory": {"item": [{"name": "crwv-20261110.htm"}, {"name": "coreweave3q26earningspress.htm"},
                                                 {"name": "ex991.htm"}]}})
        return _Resp(text=RELEASE)

    rel = er.fetch("CRWV", "0001769628", get)
    assert rel["filed"] == "2026-11-10" and rel["net_income"] == -709e6
    assert calls[-1].endswith("/1769628/000176962826000300/ex991.htm")


def test_watchlist_notification_once_per_release():
    sent = []

    def add(kind, title, body, **kw):
        if kw["key"] in {k for *_x, k in sent}:
            return False
        sent.append((kw["user_id"], title, body, kw["key"]))
        return True

    rel = {"ticker": "CRWV", "filed": "2026-11-10", "accn": "a1", "revenue": 3661e6, "net_income": -709e6, "eps_diluted": -1.27}
    now = datetime(2026, 11, 11, 3, 0, tzinfo=timezone.utc)
    watch = {7: {"CRWV", "AAPL"}}
    lookup = lambda t: rel if t == "CRWV" else None
    assert inbox_feeds.releases(watch, now=now, latest=lookup, add=add) == 1
    assert inbox_feeds.releases(watch, now=now, latest=lookup, add=add) == 0
    uid, title, body, _k = sent[0]
    assert uid == 7 and title == "CRWV reported results" and body.startswith("Revenue $3.66B, net loss $709M")
    old = dict(rel, filed="2026-10-01", accn="a0")
    assert inbox_feeds.releases(watch, now=now, latest=lambda t: old, add=add) == 0


def test_banner_wiring():
    js = open("static/js/research_workbench.js", encoding="utf-8").read()
    assert "d.release?releaseCard(d.release)" in js and "NEW RESULTS ANNOUNCED" in js
    html = open("templates/research.html", encoding="utf-8").read()
    assert "quarter.release" in html and "release_money" in html
