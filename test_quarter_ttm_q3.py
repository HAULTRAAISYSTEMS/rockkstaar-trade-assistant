"""Trailing EPS for a calendar-year filer at Q3.

A December-year company never files a Q4 10-Q: its Q4 is the 10-K year less
Q1-Q3. At a September quarter end, last year's Q1 ended ~550 days earlier, so
the facts read for the trailing sum must reach back that far.
"""
from quarter_facts import ttm_eps


def q(start, end, val, filed):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": "10-Q"}


def facts():
    eps = [
        q("2025-01-01", "2025-03-31", 2.81, "2025-04-25"),
        q("2025-04-01", "2025-06-30", 2.31, "2025-07-24"),
        q("2025-07-01", "2025-09-30", 2.87, "2025-10-30"),
        {"start": "2025-01-01", "end": "2025-12-31", "val": 10.91, "filed": "2026-02-04", "form": "10-K"},
        q("2026-01-01", "2026-03-31", 2.95, "2026-04-24"),
        q("2026-04-01", "2026-06-30", 2.60, "2026-07-23"),
        q("2026-07-01", "2026-09-30", 2.12, "2026-10-29"),
    ]
    return {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": eps}}}}}


def test_q3_trailing_eps_derives_q4_from_the_annual():
    t = ttm_eps(facts(), "2026-09-30")
    assert t is not None, "calendar-year Q3 must still get a trailing EPS"
    assert [x["end"] for x in t["quarters"]] == ["2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"]
    assert t["quarters"][0]["derived"] is True
    assert round(t["value"], 2) == round((10.91 - 2.81 - 2.31 - 2.87) + 2.95 + 2.60 + 2.12, 2)


def test_earlier_quarters_still_work():
    for end in ("2026-03-31", "2026-06-30"):
        t = ttm_eps(facts(), end)
        assert t is not None and t["quarters"][-1]["end"] == end and len(t["quarters"]) == 4


def test_available_quarters_lists_10q_quarters_newest_first():
    from quarter_facts import available_quarters
    f = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        q("2026-07-01", "2026-09-30", 102e9, "2026-10-29"),
        q("2026-04-01", "2026-06-30", 96e9, "2026-07-23"),
        q("2026-01-01", "2026-03-31", 90e9, "2026-04-24"),
        {"start": "2025-01-01", "end": "2025-12-31", "val": 350e9, "filed": "2026-02-04"},
        q("2025-07-01", "2025-09-30", 82e9, "2025-10-30"),
    ]}}}}}
    assert available_quarters(f) == ["2026-09-30", "2026-06-30", "2026-03-31", "2025-09-30"]
