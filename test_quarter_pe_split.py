"""P/E on the 10-Q drill: trailing EPS across a stock split (KLA, 10-for-1, June 2026)."""
import quarter_facts as qf


def _f(start, end, val, filed, form="10-Q"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


def kla_like():
    eps = [
        # Pre-split quarters, as first filed.
        _f("2025-07-01", "2025-09-30", 8.47, "2025-10-30"),
        _f("2025-10-01", "2025-12-31", 8.85, "2026-01-29"),
        _f("2026-01-01", "2026-03-31", 9.50, "2026-04-30"),
        # FY26 in the 10-K filed after the split: post-split basis, no Q4 alone.
        _f("2025-07-01", "2026-06-30", 3.70, "2026-08-06", "10-K"),
        # Q1 FY27 with the year-ago column restated.
        _f("2026-07-01", "2026-09-30", 1.18, "2026-10-30"),
        _f("2025-07-01", "2025-09-30", 0.847, "2026-10-30"),
    ]
    sh = [
        _f("2024-07-01", "2025-06-30", 133_000_000, "2025-08-07", "10-K"),
        _f("2024-07-01", "2025-06-30", 1_330_000_000, "2026-08-06", "10-K"),
        _f("2025-07-01", "2025-09-30", 132_380_800, "2025-10-30"),
        _f("2025-07-01", "2025-09-30", 1_323_808_000, "2026-10-30"),
        _f("2026-07-01", "2026-09-30", 1_311_064_000, "2026-10-30"),
    ]
    return {"facts": {"us-gaap": {
        "EarningsPerShareDiluted": {"units": {"USD/shares": eps}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": sh}},
    }}}


def test_split_is_found_in_restated_share_counts():
    sp = qf.detect_split(kla_like())
    assert sp == {"ratio": 10.0, "since": "2026-08-06"}


def test_ttm_eps_is_on_one_share_basis():
    facts = kla_like()
    t = qf.ttm_eps(facts, "2026-09-30", qf.detect_split(facts))
    ends = [q["end"] for q in t["quarters"]]
    assert ends == ["2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"]
    by = {q["end"]: q for q in t["quarters"]}
    assert by["2025-12-31"]["eps"] == 0.885 and by["2026-03-31"]["eps"] == 0.95     # pre-split ÷ 10
    assert by["2026-06-30"]["derived"] and abs(by["2026-06-30"]["eps"] - (3.70 - 0.847 - 0.885 - 0.95)) < 1e-6
    assert abs(t["value"] - (0.885 + 0.95 + (3.70 - 0.847 - 0.885 - 0.95) + 1.18)) < 1e-6
    assert "post-split" in t["method"]


def test_ttm_without_split_handling_would_be_wrong():
    # Read as filed, the old quarters are ten times too big and the fiscal Q4
    # derived from the year comes out impossibly negative.
    t = qf.ttm_eps(kla_like(), "2026-09-30", None)
    q4 = next(q for q in t["quarters"] if q["end"] == "2026-06-30")
    assert q4["eps"] < -10


def test_not_enough_quarters_gives_none():
    facts = {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": [
        _f("2026-07-01", "2026-09-30", 1.18, "2026-10-30")]}}}}}
    assert qf.ttm_eps(facts, "2026-09-30", None) is None
    assert qf.detect_split(facts) is None


def test_drill_page_has_pe_card_split_note_and_stale_clear():
    tpl = open("templates/fundamentals.html", encoding="utf-8").read()
    assert "function qdPeCard(ticker)" in tpl and "price / eps" in tpl
    assert "typedEps * 4" in tpl and "estimate" in tpl                     # labelled fallback
    assert "/api/terminal/live/" in tpl and 'id="q-price"' in tpl           # live price, editable
    assert "function qdSplitNote(ticker)" in tpl
    assert "Never leave a previous company's figures sitting under this ticker." in tpl
    assert "qd-chip-kicker\">Saved</span>" in tpl and "is-current" in tpl


def test_prior_year_ttm_is_on_the_same_basis():
    """PEG needs last year's trailing EPS on today's share basis."""
    facts = kla_like()
    sp = qf.detect_split(facts)
    eps = facts["facts"]["us-gaap"]["EarningsPerShareDiluted"]["units"]["USD/shares"]
    for s, e, v, filed in (("2024-10-01", "2024-12-31", 7.0, "2025-01-29"),
                           ("2025-01-01", "2025-03-31", 7.5, "2025-04-30"),
                           ("2025-04-01", "2025-06-30", 8.0, "2025-08-07")):
        eps.append(_f(s, e, v, filed))
    t = qf.ttm_eps(facts, "2025-09-30", sp)
    assert t is not None
    assert [q["end"] for q in t["quarters"]][-1] == "2025-09-30"
    assert round(t["value"], 3) == round(0.7 + 0.75 + 0.8 + 0.847, 3)


def nvda_like():
    """52/53-week quarters: last year's matching quarter ends a day later."""
    q = [  # (start, end, eps, filed, form)
        ("2024-01-29", "2024-04-28", 0.60, "2025-05-28"),
        ("2024-04-29", "2024-07-28", 0.67, "2025-08-27"),
        ("2024-07-29", "2024-10-27", 0.78, "2024-11-20"),
        ("2024-01-29", "2025-01-26", 2.94, "2025-02-26", "10-K"),
        ("2025-01-27", "2025-04-27", 0.76, "2025-05-28"),
        ("2025-04-28", "2025-07-27", 1.08, "2025-08-27"),
        ("2025-07-28", "2025-10-26", 1.30, "2025-11-19"),
        ("2025-01-27", "2026-01-25", 4.90, "2026-02-25", "10-K"),
        ("2026-01-26", "2026-04-26", 2.39, "2026-05-27"),
        ("2026-04-27", "2026-07-26", 2.46, "2026-08-26"),
    ]
    eps = [_f(*row) for row in q]
    rev = [_f(r[0], r[1], 1000 * (i + 1), r[3], *r[4:]) for i, r in enumerate(q)]
    return {"entityName": "NVIDIA", "facts": {"us-gaap": {
        "EarningsPerShareDiluted": {"units": {"USD/shares": eps}},
        "Revenues": {"units": {"USD": rev}},
    }}}


def test_prior_ttm_found_for_52_53_week_quarters():
    w = qf.walkthrough("NVDA", facts=nvda_like())
    assert w["period_end"] == "2026-07-26"
    assert w["ttm_eps"] and w["ttm_eps_prior"], "PEG needs both"
    assert w["ttm_eps_prior"]["quarters"][-1]["end"] == "2025-07-27"
    assert round(w["ttm_eps_prior"]["value"], 2) == round(0.78 + (2.94 - 0.60 - 0.67 - 0.78) + 0.76 + 1.08, 2)


def test_an_old_split_is_not_announced():
    facts = kla_like()
    w_split = qf.detect_split(facts)
    assert w_split
    # Two years after the split, the comparatives are no longer restated for it.
    assert qf._day_gap("2028-09-30", w_split["since"]) > 460
