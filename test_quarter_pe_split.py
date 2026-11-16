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
