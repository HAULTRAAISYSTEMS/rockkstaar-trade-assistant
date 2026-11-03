"""A calendar that said nothing reports this week, in earnings season.

The Intel calendar read one source: Nasdaq's unkeyed, scraped
api.nasdaq.com/api/calendar/earnings. That endpoint refuses some hosting
ranges outright, and when it does, _nasdaq_cal swallows the error at debug
level and returns []. The card then rendered

    EARNINGS 0      MACRO 5      WATCHLIST 0
    "No verified events match these filters."

on a Monday in November with AMD, AMGN, ANET and SHOP all reporting inside
48 hours. A dead feed and a quiet market looked identical, and the only
evidence was a log line nobody was printing.

Two defects, one symptom:

  - No fallback. One scraped endpoint, no key, no second source.
  - The consensus was being thrown away even when Nasdaq did answer:
    epsForecast arrives as "$1.68", float() raised on the dollar sign, and
    every row reported no published estimate.
"""
from datetime import date, timedelta
from unittest.mock import patch

import pytest

import intel_engine as ie


class TestTheConsensusSurvivesItsCurrencySymbol:
    @pytest.mark.parametrize("raw, expected", [
        ("$1.68", 1.68),
        ("1.68", 1.68),
        ("$1,234.50", 1234.50),
        ("($0.42)", -0.42),          # accounting negative: a forecast loss
        ("-0.42", -0.42),
        (2, 2.0),
        (0.0, 0.0),
    ])
    def test_it_reads_what_the_feed_sends(self, raw, expected):
        assert ie._parse_money(raw) == expected

    @pytest.mark.parametrize("blank", [None, "", "  ", "--", "-", "N/A", "n/a"])
    def test_absent_is_none(self, blank):
        assert ie._parse_money(blank) is None

    def test_a_breakeven_forecast_is_a_number_not_a_gap(self):
        """0.00 is a real consensus, and routine for exactly the pre-profit
        names where the estimate carries the most information."""
        assert ie._parse_money("$0.00") == 0.0
        assert ie._parse_money("$0.00") is not None

    def test_garbage_does_not_raise(self):
        assert ie._parse_money("later") is None
        assert ie._parse_money("$$$") is None

    def test_the_nasdaq_path_uses_it(self):
        """float() on "$1.68" raised, and the except swallowed it into None
        for every row on the market-wide slate."""
        from pathlib import Path
        src = Path("intel_engine.py").read_text()
        body = src[src.index("def _earnings_from_nasdaq"):]
        body = body[:body.index("\ndef ")]
        assert "_parse_money(eps_raw)" in body
        assert "float(eps_raw)" not in body


class TestADeadFeedIsNotAQuietMarket:
    @staticmethod
    def _finnhub_payload(today):
        d = (today + timedelta(days=1)).isoformat()
        return {"earningsCalendar": [
            {"symbol": "AMD",  "date": d, "hour": "amc",
             "epsEstimate": 1.68, "revenueEstimate": 9_000_000_000,
             "quarter": 3, "year": 2026},
            {"symbol": "SHOP", "date": d, "hour": "bmo",
             "epsEstimate": 0.33, "revenueEstimate": 2_800_000_000,
             "quarter": 3, "year": 2026},
            {"symbol": "TINYCO", "date": d, "hour": "",
             "epsEstimate": None, "revenueEstimate": None},
        ]}

    def test_nasdaq_silence_falls_through_to_finnhub(self, monkeypatch):
        today = ie._today_et()
        monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
        monkeypatch.setattr(ie, "_earnings_from_nasdaq", lambda *a, **k: [])
        monkeypatch.setattr(ie, "_get_watchlist_tickers", lambda: [])
        monkeypatch.setattr(ie, "_fh_is_rate_limited", lambda: False)
        monkeypatch.setattr(ie, "_company_name", lambda t: t)

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                import json
                return json.dumps(TestADeadFeedIsNotAQuietMarket._finnhub_payload(today)).encode()
        monkeypatch.setattr(ie, "_fh_urlopen", lambda *a, **k: _Resp())

        rows = ie.fetch_earnings_radar(limit=12)
        tickers = {r["ticker"] for r in rows}
        assert "AMD" in tickers and "SHOP" in tickers

    def test_the_coverage_proxy_drops_the_uncovered_name(self, monkeypatch):
        """Finnhub's calendar has no market cap, so the Large/Mid tier test
        cannot be reproduced. A published consensus is the stand-in."""
        today = ie._today_et()
        monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
        monkeypatch.setattr(ie, "_earnings_from_nasdaq", lambda *a, **k: [])
        monkeypatch.setattr(ie, "_get_watchlist_tickers", lambda: [])
        monkeypatch.setattr(ie, "_fh_is_rate_limited", lambda: False)
        monkeypatch.setattr(ie, "_company_name", lambda t: t)

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                import json
                return json.dumps(TestADeadFeedIsNotAQuietMarket._finnhub_payload(today)).encode()
        monkeypatch.setattr(ie, "_fh_urlopen", lambda *a, **k: _Resp())

        assert "TINYCO" not in {r["ticker"] for r in ie.fetch_earnings_radar(limit=12)}

    def test_a_working_nasdaq_is_not_second_guessed(self, monkeypatch):
        """The fallback is for silence, not for a thin day. If Nasdaq answers
        at all, that answer stands."""
        today = ie._today_et()
        real = [{"ticker": "AAPL", "date": today.isoformat(), "days_away": 0,
                 "time_label": "AMC", "on_watchlist": False, "market_cap": 3e12}]
        monkeypatch.setattr(ie, "_earnings_from_nasdaq", lambda *a, **k: list(real))
        monkeypatch.setattr(ie, "_get_watchlist_tickers", lambda: [])
        called = {"finnhub": False}
        def _boom(*a, **k):
            called["finnhub"] = True
            return [], []
        monkeypatch.setattr(ie, "_earnings_from_finnhub", _boom)
        rows = ie.fetch_earnings_radar(limit=12)
        assert called["finnhub"] is False
        assert {r["ticker"] for r in rows} == {"AAPL"}

    def test_no_key_means_a_logged_warning_not_a_crash(self, monkeypatch, caplog):
        import logging
        monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
        monkeypatch.setattr(ie, "_earnings_from_nasdaq", lambda *a, **k: [])
        monkeypatch.setattr(ie, "_get_watchlist_tickers", lambda: [])
        with caplog.at_level(logging.WARNING):
            assert ie.fetch_earnings_radar(limit=12) == []
        assert any("FINNHUB_API_KEY" in r.message % r.args if r.args else
                   "FINNHUB_API_KEY" in r.message for r in caplog.records)


class TestTheFailureIsVisibleInTheLogs:
    def test_a_dead_nasdaq_logs_at_warning(self):
        """It was debug. The card's only source going dark was invisible at
        the level production actually runs at."""
        from pathlib import Path
        src = Path("intel_engine.py").read_text()
        body = src[src.index("def _nasdaq_cal"):]
        body = body[:body.index("\ndef ")]
        assert 'logger.warning("intel/nasdaq/%s %s failed' in body
        assert "logger.debug" not in body


class TestMarketWideModeReadsOneCallDifferently:
    def test_it_does_not_add_a_request(self):
        """The Finnhub call never had a symbol filter — the narrowing always
        happened locally, after the response. Market-wide is a second reader
        of the same call, not a second call."""
        from pathlib import Path
        src = Path("intel_engine.py").read_text()
        body = src[src.index("def _earnings_from_finnhub"):]
        body = body[:body.index("\ndef ")]
        # The URL itself, not the prose around it — the docstring says
        # "no symbol= filter" and would match a naive search.
        url = body[body.index('f"https://finnhub.io'):]
        url = url[:url.index("\n    )")]
        assert "calendar/earnings" in url
        assert "symbol=" not in url
        assert "not market_wide and ticker not in tickers_wanted" in body

    def test_the_horizon_is_tightened_for_the_card(self):
        """120 days of every reporting company is not a seven-day calendar."""
        from pathlib import Path
        src = Path("intel_engine.py").read_text()
        assert "market_wide=True, horizon_days=7" in src


class TestBothSurfacesShareOneFallback:
    """The first attempt at this fix went into fetch_earnings_radar only, and
    the page the user was actually looking at — the Earnings & Catalyst
    Calendar — runs a different function entirely. It kept showing zero. One
    helper now, used by both, so the next change cannot land on one surface
    and miss the other."""

    def test_only_one_place_calls_nasdaq_for_the_market_wide_slate(self):
        from pathlib import Path
        src = Path("intel_engine.py").read_text()
        calls = [l for l in src.splitlines()
                 if "_earnings_from_nasdaq(today" in l and "def " not in l]
        assert len(calls) == 1, calls

    def test_the_calendar_goes_through_the_helper(self):
        from pathlib import Path
        src = Path("intel_engine.py").read_text()
        body = src[src.index("def fetch_earnings_calendar"):]
        body = body[:body.index("\ndef ")]
        assert "_market_wide_earnings(today, wl_set, present)" in body

    def test_the_radar_goes_through_the_helper(self):
        from pathlib import Path
        src = Path("intel_engine.py").read_text()
        body = src[src.index("def fetch_earnings_radar"):]
        body = body[:body.index("\ndef ")]
        assert "_market_wide_earnings(today, watchlist, set())" in body

    def test_the_helper_names_the_source_it_used(self, monkeypatch):
        """So /api/intel/debug can say which feed answered instead of leaving
        an empty card unexplained."""
        import intel_engine as ie
        today = ie._today_et()
        monkeypatch.setattr(ie, "_earnings_from_nasdaq",
                            lambda *a, **k: [{"ticker": "AAPL"}])
        rows, source = ie._market_wide_earnings(today, set(), set())
        assert source == "nasdaq_marketwide" and rows

    def test_no_source_is_reported_honestly(self, monkeypatch):
        import intel_engine as ie
        monkeypatch.setattr(ie, "_earnings_from_nasdaq", lambda *a, **k: [])
        monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
        rows, source = ie._market_wide_earnings(ie._today_et(), set(), set())
        assert rows == [] and source == "none"
