"""A price is three claims, and the page used to make all three silently.

The number with the dollar sign in front of it is the most believed thing on
the page. `fetch_live_data` has four sources and the dict it returned named
none of them, carried no timestamp even where the feed sent one, and computed
a "day change" against a previous close whose date was known on exactly one
of the four paths.

These tests cover what each of those omissions actually produced — and the
two places where the omission was not a gap but a wrong number.
"""
import datetime as dt

import pytest

import quote_provenance as qp


ET = qp._et_tz()


def at(y, m, d, hh, mm=0):
    return dt.datetime(y, m, d, hh, mm, tzinfo=ET)


# A Wednesday, mid-session.
OPEN_WED = at(2026, 10, 28, 11, 0)
# The Sunday after a Friday close.
SUNDAY = at(2026, 11, 1, 11, 0)


def quote(**kw):
    base = {
        "price_source": "finnhub",
        "current_price": 412.18,
        "prev_close": 408.00,
        "prev_close_date": "2026-10-27",
        "quote_time": int(at(2026, 10, 28, 10, 58).timestamp()),
    }
    base.update(kw)
    return base


class TestTheSessionIsPartOfThePrice:
    @pytest.mark.parametrize("hour, code", [
        (3, "closed"), (5, "pre_market"), (10, "regular"),
        (16, "regular"), (17, "after_hours"), (21, "closed"),
    ])
    def test_weekday_sessions(self, hour, code):
        assert qp.session(at(2026, 10, 28, hour))["code"] == code

    def test_a_weekend_is_not_merely_closed(self):
        """'Market closed' on a Tuesday night means the day is over. On a
        Sunday it means the price on screen is two days old, and the page
        needs to be able to say the second thing."""
        assert qp.session(SUNDAY)["code"] == "weekend"
        assert "Weekend" in qp.session(SUNDAY)["label"]
        assert qp.session(at(2026, 10, 28, 21))["code"] == "closed"

    def test_saturday_at_market_hours_is_still_the_weekend(self):
        assert qp.session(at(2026, 10, 31, 11))["code"] == "weekend"


class TestTheTimestampTheFeedsAlreadySend:
    """Finnhub's /quote returns `t`; Polygon's snapshot returns lastTrade.t in
    nanoseconds. Neither was read, so the app could not say how old a price
    was — the same loss as the earnings `hour` field flattened to 'TBD'."""

    def test_epoch_seconds_are_read(self):
        d = qp.describe(quote(), now=OPEN_WED)
        assert d["timed"] is True
        assert d["age_seconds"] == 120
        assert d["as_of"] == "10:58 AM ET"

    def test_polygon_nanoseconds_are_read_as_the_same_moment(self):
        secs = int(at(2026, 10, 28, 10, 58).timestamp())
        a = qp.describe(quote(quote_time=secs), now=OPEN_WED)
        b = qp.describe(quote(price_source="polygon", quote_time=secs * 10**9),
                        now=OPEN_WED)
        assert a["as_of"] == b["as_of"] == "10:58 AM ET"

    def test_polygon_milliseconds_too(self):
        secs = int(at(2026, 10, 28, 10, 58).timestamp())
        d = qp.describe(quote(price_source="polygon", quote_time=secs * 1000),
                        now=OPEN_WED)
        assert d["as_of"] == "10:58 AM ET"

    def test_an_untimed_price_says_so_rather_than_implying_now(self):
        d = qp.describe(quote(quote_time=None), now=OPEN_WED)
        assert d["timed"] is False
        assert d["age_seconds"] is None
        assert d["live"] is False
        assert "how old it is" in d["caveat"]

    @pytest.mark.parametrize("junk", [0, -1, "", "later", None, True, False, [1]])
    def test_junk_timestamps_are_treated_as_absent_not_as_1970(self, junk):
        d = qp.describe(quote(quote_time=junk), now=OPEN_WED)
        assert d["timed"] is False


class TestFridaysCloseOnASunday:
    def test_it_is_not_called_live(self):
        friday = int(at(2026, 10, 30, 16, 0).timestamp())
        d = qp.describe(quote(quote_time=friday), now=SUNDAY)
        assert d["live"] is False
        assert d["session"] == "weekend"

    def test_and_the_page_is_given_the_day_and_time_to_show(self):
        friday = int(at(2026, 10, 30, 16, 0).timestamp())
        d = qp.describe(quote(quote_time=friday), now=SUNDAY)
        assert d["as_of"] == "Fri 4:00 PM ET"
        assert "last price from Fri 4:00 PM ET" in d["caveat"]

    def test_a_price_from_last_month_gets_a_date_not_a_weekday(self):
        old = int(at(2026, 9, 3, 15, 30).timestamp())
        d = qp.describe(quote(quote_time=old), now=OPEN_WED)
        assert d["as_of"] == "Sep 3 3:30 PM ET"


class TestAStalledFetchDuringTheSession:
    def test_an_hour_old_price_is_stale_while_the_market_is_open(self):
        hour_ago = int(at(2026, 10, 28, 10, 0).timestamp())
        d = qp.describe(quote(quote_time=hour_ago), now=OPEN_WED)
        assert d["stale"] is True
        assert d["live"] is False
        assert "has not refreshed" in d["caveat"]

    def test_but_the_same_age_overnight_is_just_the_last_price(self):
        """Nothing traded, so nothing is wrong. Calling that stale would cry
        wolf every evening."""
        close = int(at(2026, 10, 27, 16, 0).timestamp())
        d = qp.describe(quote(quote_time=close), now=at(2026, 10, 27, 22, 0))
        assert d["stale"] is False
        assert d["live"] is False

    def test_a_fresh_quote_in_session_carries_no_caveat_at_all(self):
        d = qp.describe(quote(), now=OPEN_WED)
        assert d["live"] is True
        assert d["stale"] is False
        assert d["caveat"] == ""

    def test_an_old_quote_in_a_thin_session_is_not_live_but_not_stale_either(self):
        """Pre-market and after hours can go hours without a trade. That is
        not a broken fetch, so it is not called stale — but it is not 'Live'
        with a green dot either, which is what after-hours used to show."""
        dawn = int(at(2026, 10, 28, 3, 33).timestamp())
        d = qp.describe(quote(quote_time=dawn), now=at(2026, 10, 28, 18, 0))
        assert d["session"] == "after_hours"
        assert d["live"] is False
        assert d["stale"] is False
        assert "last trade this feed has is from" in d["caveat"]

    def test_a_recent_premarket_quote_is_live(self):
        tick = int(at(2026, 10, 28, 8, 55).timestamp())
        d = qp.describe(quote(quote_time=tick), now=at(2026, 10, 28, 9, 0))
        assert d["session"] == "pre_market"
        assert d["live"] is True

    def test_a_stored_snapshot_is_never_live(self):
        d = qp.describe(quote(data_source="stale_snapshot"), now=OPEN_WED)
        assert d["live"] is False
        assert d["stale"] is True
        assert "last price the app stored" in d["caveat"]


class TestWhatTheDayChangeIsMeasuredAgainst:
    def test_a_dated_baseline_gives_a_day_change_the_app_can_defend(self):
        c = qp.day_change(quote())
        assert c["known"] is True
        assert c["change"] == 4.18
        assert c["pct"] == 1.02
        assert c["baseline_verified"] is True
        assert c["why_not"] == ""

    def test_an_undated_baseline_still_computes_but_admits_what_it_cannot_know(self):
        """Three of the four fetch paths never set prev_close_date. After a
        Monday holiday `previousClose` is Friday's, and a 6% 'day change' is
        really 6% over four days."""
        c = qp.day_change(quote(prev_close_date=None))
        assert c["known"] is True
        assert c["pct"] == 1.02
        assert c["baseline_verified"] is False
        assert "may not be yesterday" in c["why_not"]

    def test_the_caveat_reaches_the_page(self):
        d = qp.describe(quote(prev_close_date=None), now=OPEN_WED)
        assert any("more than one session" in n for n in d["caveats"])

    def test_no_previous_close_means_no_day_change_rather_than_zero(self):
        c = qp.day_change(quote(prev_close=None))
        assert c["known"] is False
        assert c["change"] is None and c["pct"] is None
        assert "nothing to measure against" in c["why_not"]

    def test_no_price_means_no_day_change(self):
        c = qp.day_change(quote(current_price=None))
        assert c["known"] is False
        assert "no current price" in c["why_not"]

    def test_a_flat_day_is_a_real_reading_not_a_missing_one(self):
        c = qp.day_change(quote(current_price=408.00))
        assert c["known"] is True
        assert c["change"] == 0.0 and c["pct"] == 0.0


class TestTheSourceIsNamed:
    @pytest.mark.parametrize("key, name", [
        ("polygon", "Polygon"), ("finnhub", "Finnhub"),
        ("yahoo_chart", "Yahoo chart"), ("yfinance", "Yahoo Finance"),
    ])
    def test_every_fetch_path_has_an_entry(self, key, name):
        assert qp.SOURCES[key]["name"] == name

    def test_an_unnamed_source_is_reported_as_unknown_not_hidden(self):
        d = qp.describe(quote(price_source=None), now=OPEN_WED)
        assert d["source_name"] == "Unknown source"
        assert d["live"] is False

    def test_only_trade_feeds_can_be_called_live(self):
        """The Yahoo chart meta is a delayed snapshot. It can be current, but
        the app has no way to show that it is, so it does not claim it."""
        d = qp.describe(quote(price_source="yahoo_chart"), now=OPEN_WED)
        assert d["live"] is False
        assert qp.SOURCES["yahoo_chart"]["quotes_trades"] is False

    def test_finnhub_is_recorded_as_having_no_previous_session_range(self):
        """Which is why its h and l must not be stored as prev_day_high and
        prev_day_low — the bug this module was written alongside."""
        assert qp.SOURCES["finnhub"]["prev_day_range"] is False
        assert qp.SOURCES["polygon"]["prev_day_range"] is True


class TestItSurvivesWhateverTheCallerPasses:
    @pytest.mark.parametrize("junk", [None, {}, [], "string", 0])
    def test_nothing_raises(self, junk):
        d = qp.describe(junk)
        assert d["live"] is False
        assert qp.day_change(junk)["known"] is False

    def test_an_empty_quote_says_no_price_arrived(self):
        assert "No price arrived" in qp.describe({})["caveat"]


class TestWhatTheMislabelActuallyDidToTheScoring:
    """Not a cosmetic label. Three rules read prev_day_high / prev_day_low as
    the PREVIOUS session's range. Fed today's high and low instead, two of
    them became unsatisfiable and one became almost always true."""

    import_err = None
    try:
        import scoring
    except Exception as _e:          # pragma: no cover
        import_err = _e

    BASE = {
        "trade_bias": "Long Bias", "gap_pct": 5.0, "rel_volume": 2.0,
        "current_price": 100.0, "prev_close": 95.0,
    }

    def test_a_demand_block_is_reachable_with_a_real_previous_high(self):
        d = dict(self.BASE, prev_day_high=98.0, prev_day_low=93.0)
        assert self.scoring.compute_order_block(d) == "Demand"

    def test_but_never_when_todays_high_is_stored_there(self):
        """Price cannot exceed the session high that contains it, so
        `current > prev_day_high` is false by construction."""
        d = dict(self.BASE, prev_day_high=101.5, prev_day_low=97.0)  # today's
        assert d["current_price"] < d["prev_day_high"]
        assert self.scoring.compute_order_block(d) == "Neutral"

    def test_gap_and_go_goes_the_same_way(self):
        real = dict(self.BASE, prev_day_high=98.0, prev_day_low=93.0)
        today = dict(self.BASE, prev_day_high=101.5, prev_day_low=97.0)
        assert self.scoring.compute_setup_type(real) == "Gap and Go"
        assert self.scoring.compute_setup_type(today) != "Gap and Go"

    def test_and_range_break_fires_on_the_wrong_data_instead(self):
        """`abs(current - prev_high) / prev_high < 0.025` is nearly always
        true against today's own high — price sits just under it most of the
        session — so the app kept announcing a range break."""
        today = dict(self.BASE, gap_pct=1.0, rel_volume=1.5,
                     prev_day_high=101.5, prev_day_low=97.0)
        assert self.scoring.compute_setup_type(today) == "Range Break"


class TestTheFetchPathsActuallyCarryThis:
    """A provenance module that describes fields nobody populates is theatre."""

    def test_finnhub_no_longer_files_todays_range_as_yesterdays(self):
        import re
        from pathlib import Path
        src = Path("data_fetcher.py").read_text()
        body = src[src.index("def _fetch_finnhub_quote"):
                   src.index("def _polygon_get")]
        # The whole bug: d["h"] / d["l"] written into prev_day_*.
        assert not re.search(r'prev_day_(high|low)"\]\s*=\s*round\(float\(d\[', body)
        assert 'out["day_high"]' in body
        assert 'out["day_low"]' in body
        assert 'out["quote_time"]' in body
        assert '"price_source":  "finnhub"' in body

    def test_the_chart_api_cannot_pass_a_previous_close_off_as_a_live_price(self):
        """`regularMarketPrice or chartPreviousClose` produced a price that
        looked live, matched prev_close to the cent, and showed a day change
        of exactly 0.00%."""
        from pathlib import Path
        src = Path("data_fetcher.py").read_text()
        body = src[src.index("def _fetch_price_via_chart_api"):]
        assert 'meta.get("regularMarketPrice") or meta.get("chartPreviousClose")' \
            not in body

    def test_every_source_key_the_fetcher_writes_is_one_this_module_knows(self):
        import re
        from pathlib import Path
        src = Path("data_fetcher.py").read_text()
        written = set(re.findall(r'"price_source":\s*"([a-z_]+)"', src))
        written |= set(re.findall(r'\["price_source"\]\s*=\s*"([a-z_]+)"', src))
        assert written, "no source is recorded anywhere"
        assert written <= set(qp.SOURCES), written - set(qp.SOURCES)

    def test_the_new_fields_survive_the_database_round_trip(self):
        """They are no use if a stored snapshot comes back without them."""
        from pathlib import Path
        src = Path("database.py").read_text()
        for col in ("price_source", "quote_time", "day_high", "day_low", "day_open"):
            assert f'("{col}",' in src, f"{col}: no migration entry"
            assert f":{col}" in src, f"{col}: not bound in the upsert"
            assert f"excluded.{col}" in src, f"{col}: not updated on conflict"
