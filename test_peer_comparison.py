"""A ratio is only an argument next to someone else's.

Return on equity of -11.2% says nothing alone. Against a peer group running
28% it is the entire case. The fundamentals engine already computed every
ratio for one company at a time; this is the column beside them.

The thing these tests exist to hold is the refusal. A number labelled
"Industry: 46.7%" cannot be checked, looks identical whether it came from
two hundred companies or a stale cache, and is extremely sensitive to who
someone decided belongs in the bucket — which is the usual complaint about
Intel's growth comparison in the first place. So: the peers are named, the
median says how many companies it is built from, and below four readable
peers there is no median at all.
"""
import pytest

import peer_comparison as pc


def peer(ticker, **ratios):
    base = {"ticker": ticker, "roe": 25.0, "roic": 15.0, "net_margin": 20.0,
            "operating_margin": 25.0, "gross_margin": 55.0,
            "revenue_growth": 10.0, "debt_to_equity": 0.3, "current_ratio": 2.0}
    base.update(ratios)
    return base


def group(n=8, **ratios):
    return [peer(f"P{i}", **ratios) for i in range(n)]


INTEL = {"ticker": "INTC", "company_name": "Intel", "roe": -11.2, "roic": -3.1,
         "net_margin": -5.6, "operating_margin": -8.0, "gross_margin": 32.7,
         "revenue_growth": -0.53, "debt_to_equity": 0.84, "current_ratio": 1.31}


class TestTheComparisonItself:
    def test_a_company_behind_on_everything_is_marked_behind(self):
        c = pc.compare(INTEL, group())
        assert c["usable"] is True
        assert "Return on equity" in c["behind"]
        assert c["ahead"] == []

    def test_the_median_is_the_median_not_the_mean(self):
        """One peer at 500% ROE should not drag the comparison with it."""
        peers = group(7) + [peer("OUTLIER", roe=500.0)]
        row = next(r for r in pc.compare(INTEL, peers)["rows"] if r["key"] == "roe")
        assert row["median"] == 25.0

    def test_lower_is_better_for_debt(self):
        """Ranking the wrong way round here would call a safe balance sheet
        a weakness. Peers at 1.2x, the company at 0.1x."""
        row = next(r for r in pc.compare(dict(INTEL, debt_to_equity=0.1),
                                         group(debt_to_equity=1.2))["rows"]
                   if r["key"] == "debt_to_equity")
        assert row["verdict"] == "ahead"

    def test_and_more_debt_than_the_group_is_behind(self):
        row = next(r for r in pc.compare(INTEL, group())["rows"]
                   if r["key"] == "debt_to_equity")
        assert row["verdict"] == "behind"      # 0.84x against a 0.3x median

    def test_slightly_less_debt_is_not_a_finding(self):
        """0.05x against a 0.3x median is a rounding difference in risk, and
        calling it a strength would be the same inflation as calling every
        small gap an outlier."""
        row = next(r for r in pc.compare(dict(INTEL, debt_to_equity=0.05),
                                         group())["rows"]
                   if r["key"] == "debt_to_equity")
        assert row["verdict"] == "in_line"

    def test_in_line_is_a_real_verdict(self):
        """Collapsing this into better/worse would make every company an
        outlier on something."""
        same = dict(INTEL, roe=26.0)
        row = next(r for r in pc.compare(same, group())["rows"] if r["key"] == "roe")
        assert row["verdict"] == "in_line"

    def test_each_ratio_has_its_own_idea_of_a_big_gap(self):
        """3 points of ROE is noise; 3 points of net margin is not."""
        c = pc.compare(dict(INTEL, roe=28.0, net_margin=26.0), group())
        rows = {r["key"]: r for r in c["rows"]}
        assert rows["roe"]["verdict"] == "in_line"
        assert rows["net_margin"]["verdict"] == "ahead"

    def test_the_peer_set_is_returned_so_it_can_be_shown(self):
        c = pc.compare(INTEL, group(5))
        assert c["peers"] == ["P0", "P1", "P2", "P3", "P4"]
        assert c["peer_count"] == 5


class TestThePercentile:
    def test_worst_in_group_is_zero_not_missing(self):
        """Zero is a reading. It is also falsy, which is how it gets rendered
        as an em dash by a template that checks truthiness."""
        row = next(r for r in pc.compare(INTEL, group())["rows"] if r["key"] == "roe")
        assert row["percentile"] == 0
        assert row["percentile"] is not None

    def test_best_in_group_is_one_hundred(self):
        row = next(r for r in pc.compare(dict(INTEL, roe=999.0), group())["rows"]
                   if r["key"] == "roe")
        assert row["percentile"] == 100

    def test_level_with_everyone_is_the_middle(self):
        """Ties count as half, so this does not land at 0 or 100 depending on
        which way the list happened to be sorted."""
        row = next(r for r in pc.compare(dict(INTEL, roe=25.0), group())["rows"]
                   if r["key"] == "roe")
        assert row["percentile"] == 50


class TestItRefusesToCompareOnTooLittle:
    def test_three_peers_is_not_an_industry(self):
        c = pc.compare(INTEL, group(3))
        assert c["usable"] is False
        assert all(r["verdict"] == "no_comparison" for r in c["rows"])
        assert "only 3 of 3 peers" in c["rows"][0]["why_not"]

    def test_no_peers_says_so(self):
        c = pc.compare(INTEL, [])
        assert c["usable"] is False
        assert "no peer group available" in c["rows"][0]["why_not"]

    def test_a_peer_missing_one_ratio_is_not_counted_as_zero(self):
        """Averaging a missing value in as zero is how a peer group quietly
        becomes wrong."""
        peers = group(7) + [peer("BLANK", roe=None)]
        row = next(r for r in pc.compare(INTEL, peers)["rows"] if r["key"] == "roe")
        assert row["median"] == 25.0
        assert row["peers_used"] == 7
        assert row["peers_unreadable"] == ["BLANK"]

    def test_losing_one_ratio_does_not_lose_the_others(self):
        peers = [peer(f"P{i}", roe=None) for i in range(8)]
        c = pc.compare(INTEL, peers)
        rows = {r["key"]: r for r in c["rows"]}
        assert rows["roe"]["verdict"] == "no_comparison"
        assert rows["net_margin"]["verdict"] == "behind"
        assert c["usable"] is True

    def test_the_count_behind_every_median_is_reported(self):
        row = next(r for r in pc.compare(INTEL, group(6))["rows"] if r["key"] == "roe")
        assert row["peers_used"] == 6

    def test_a_ratio_the_subject_lacks_is_skipped_not_guessed(self):
        c = pc.compare(dict(INTEL, roe=None), group())
        assert "roe" not in {r["key"] for r in c["rows"]}


class TestItSurvivesRealData:
    @pytest.mark.parametrize("junk", [None, {}, "nonsense", 0, []])
    def test_a_junk_subject_does_not_raise(self, junk):
        assert pc.compare(junk, group())["usable"] is False

    @pytest.mark.parametrize("junk", [None, "nonsense", 0, [None], [{}], [{"x": 1}]])
    def test_junk_peers_do_not_raise(self, junk):
        pc.compare(INTEL, junk)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), "12%", True, None])
    def test_values_that_are_not_numbers_are_absent(self, bad):
        assert pc._num(bad) is None

    def test_a_peer_group_larger_than_the_cap_is_trimmed(self):
        assert pc.compare(INTEL, group(50))["peer_count"] == pc.MAX_PEERS


class TestTheWrittenRead:
    def test_it_names_the_peers_it_used(self):
        lines = pc.written_read("Intel", pc.compare(INTEL, group(5)))
        assert "P0" in lines[0] and "5 peers" in lines[0]

    def test_it_leads_with_the_worst_gap_measured_per_ratio(self):
        lines = pc.written_read("Intel", pc.compare(INTEL, group()))
        assert "Intel trails the group on" in lines[1]

    def test_it_quotes_both_numbers_not_just_the_verdict(self):
        lines = " ".join(pc.written_read("Intel", pc.compare(INTEL, group())))
        assert "-8.0%" in lines and "25.0%" in lines

    def test_a_company_that_leads_is_described_as_leading(self):
        strong = {k: (v * -1 if isinstance(v, (int, float)) else v)
                  for k, v in INTEL.items()}
        strong.update(ticker="X", company_name="X", debt_to_equity=0.02,
                      current_ratio=5.0)
        lines = " ".join(pc.written_read("X", pc.compare(strong, group())))
        assert "leads on" in lines

    def test_an_unremarkable_company_is_told_so_plainly(self):
        same = {k: v for k, v in INTEL.items()}
        same.update(roe=25.0, roic=15.0, net_margin=20.0, operating_margin=25.0,
                    gross_margin=55.0, revenue_growth=10.0,
                    debt_to_equity=0.3, current_ratio=2.0)
        lines = " ".join(pc.written_read("X", pc.compare(same, group())))
        assert "within the normal range" in lines

    def test_with_no_comparison_it_says_that_rather_than_nothing(self):
        lines = pc.written_read("Intel", pc.compare(INTEL, group(2)))
        assert "no peer comparison" in lines[0].lower()
        assert "too few" in lines[0]

    def test_it_mentions_the_ratios_it_could_not_compare(self):
        peers = [peer(f"P{i}", current_ratio=None) for i in range(8)]
        lines = " ".join(pc.written_read("Intel", pc.compare(INTEL, peers)))
        assert "No peer median for" in lines and "current ratio" in lines

    def test_every_claim_traces_to_a_row(self):
        """The read is generated from the table rather than written by a
        model, which is what stops it describing a company the data does not
        support. Each ratio it names must be present as a row."""
        c = pc.compare(INTEL, group())
        labels = {r["label"].lower() for r in c["rows"]}
        text = " ".join(pc.written_read("Intel", c)).lower()
        mentioned = {l for l in labels if l in text}
        assert mentioned and mentioned <= labels


class TestTheBuilderNeverInvents:
    def test_without_a_key_it_says_so_instead_of_comparing(self, monkeypatch):
        import fundamentals_engine as fe
        monkeypatch.setattr(fe, "_FMP_API_KEY", "")
        out = pc.build("INTC", INTEL)
        assert out["usable"] is False
        assert "FMP_API_KEY" in out["why_not"]
        assert out["rows"] == []

    def test_a_provider_failure_is_reported_not_swallowed(self, monkeypatch):
        import fundamentals_engine as fe
        monkeypatch.setattr(fe, "_FMP_API_KEY", "key")
        monkeypatch.setattr(pc, "fetch_peer_tickers",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("502")))
        out = pc.build("INTC", INTEL)
        assert out["usable"] is False
        assert "could not be fetched" in out["why_not"]

    def test_no_peers_returned_is_distinguished_from_thin_peers(self, monkeypatch):
        import fundamentals_engine as fe
        monkeypatch.setattr(fe, "_FMP_API_KEY", "key")
        monkeypatch.setattr(pc, "fetch_peer_tickers", lambda *a, **k: [])
        out = pc.build("INTC", INTEL)
        assert "no peer group was returned" in out["why_not"]

    def test_a_good_path_produces_a_read(self, monkeypatch):
        import fundamentals_engine as fe
        monkeypatch.setattr(fe, "_FMP_API_KEY", "key")
        monkeypatch.setattr(pc, "fetch_peer_tickers",
                            lambda *a, **k: [f"P{i}" for i in range(8)])
        monkeypatch.setattr(pc, "fetch_metrics", lambda t: peer(t))
        out = pc.build("INTC", INTEL)
        assert out["usable"] is True
        assert out["read"] and "P0" in out["read"][0]
