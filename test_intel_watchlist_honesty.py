"""Two tiles and one badge that spoke with more confidence than they had.

Intel's first KPI — the gold one, with a pulsing green live dot on it — read
mkt.regime_label and fell back to the word "Monitoring". An empty market
cache carries regime_label "Neutral", so the tile read

    ● Market regime
      Neutral

off no market data at all, with a live indicator on it. And "Monitoring" is a
reassuring word for "we have nothing".

The catalyst and earnings counts had the same shape: a cold feed produced 0,
which reads as "nothing happening today" rather than "the feed has not
loaded" — and the route already knows which it is.

A price alert's "TARGET HIT" is a present-tense claim computed from whatever
price the table holds. On a Sunday that is Friday's close. The crossing is
real; when it happened was missing.
"""
from pathlib import Path

import pytest

import market_engine as me


INTEL = Path("templates/intel.html").read_text()
WATCHLISTS = Path("templates/watchlists.html").read_text()
APP = Path("app.py").read_text()


class TestTheRegimeTileWillNotReportAnUnreadMarket:
    def test_it_checks_the_fallback_flag(self):
        assert "not mkt.fallback" in INTEL

    def test_the_reassuring_fallback_word_is_gone(self):
        """"Monitoring" described the app's intent, not the market's state."""
        assert "else 'Monitoring'" not in INTEL
        assert "'Not read yet'" in INTEL

    def test_the_live_dot_is_not_shown_over_a_non_reading(self):
        assert "{% if regime_known %}<span class=\"live-dot\"></span>" in INTEL
        assert "dead-dot" in INTEL

    def test_the_hollow_dot_is_dashed_like_everything_else_the_app_does_not_know(self):
        assert "border:1px dashed" in INTEL

    def test_an_empty_context_is_what_this_protects_against(self):
        """The input that produced "Neutral" with a live dot."""
        empty = me._empty_context()
        assert empty["regime_label"] == "Neutral"
        assert empty["fallback"] is True


class TestACountOfZeroIsNotTheSameAsNoFeed:
    def test_the_catalyst_tile_distinguishes_them(self):
        assert "news_loaded" in INTEL
        assert 'id="intel-live-count">{% if news_loaded %}{{ news|length }}{% else %}—' in INTEL

    def test_the_earnings_tile_does_too(self):
        assert "earn_loaded" in INTEL
        assert 'id="intel-earnings-count">{% if earn_loaded %}{{ earnings|length }}{% else %}—' in INTEL

    def test_it_shows_the_reason_the_route_already_worked_out(self):
        assert "intel_status.message" in INTEL
        assert "earnings_status.message" in INTEL
        assert "intel-kpi-note" in INTEL

    def test_the_tone_split_follows_the_catalyst_feed(self):
        """0 / 0 bullish-bearish off a cold feed is a reading of nothing."""
        assert 'id="intel-tone-counts">{% if news_loaded %}' in INTEL


class TestAnAlertSaysWhenItWasCrossed:
    def test_the_route_attaches_the_quote_provenance(self):
        body = APP[APP.index("    price_alerts   = get_price_alerts(uid)"):]
        body = body[:body.index("return render_template")]
        assert "quote_provenance" in body
        assert 'alert["quote"]' in body

    def test_a_hit_off_a_non_live_price_is_dated_rather_than_asserted(self):
        assert "TARGET HIT · AS OF" in WATCHLISTS
        assert "a.triggered and a.quote and not a.quote.live" in WATCHLISTS

    def test_a_hit_off_a_live_price_still_reads_plainly(self):
        assert '{% elif a.triggered %}<span class="badge badge-pos">TARGET HIT</span>' in WATCHLISTS

    def test_the_latest_price_line_names_its_source_and_time(self):
        assert "a.quote.source_name" in WATCHLISTS
        assert "time unknown" in WATCHLISTS

    def test_the_dated_badge_is_dashed(self):
        assert ".badge.is-asof{border-style:dashed" in WATCHLISTS

    def test_the_crossing_itself_is_not_suppressed(self):
        """A stale price that crossed the target did cross it. Hiding the hit
        would be its own kind of lie — the fix is the date, not the silence."""
        assert "is still a hit" in WATCHLISTS


class TestThePagesStillRender:
    @pytest.fixture
    def client(self):
        import web_app
        c = web_app.app.test_client()
        with c.session_transaction() as sess:
            sess["user_id"] = 1
        return c

    def test_intel_renders(self, client):
        assert client.get("/intel").status_code == 200

    def test_watchlists_renders(self, client):
        assert client.get("/watchlists").status_code == 200

    def test_intel_says_not_read_yet_when_the_market_is_cold(self, client, monkeypatch):
        import app as legacy
        monkeypatch.setattr(legacy, "_get_mkt_ctx", me._empty_context)
        html = client.get("/intel").get_data(as_text=True)
        assert "Not read yet" in html
        assert "dead-dot" in html


class TestTheRefreshDoesNotOverwriteTheDash:
    """The server renders a dash when the feed has not loaded. The refresh
    then wrote its count over the top — so a refresh that returned nothing
    produced a confident "0" sitting inside a dashed tile, above a note
    saying no reports were returned. Both endpoints already reported `ok`
    honestly; the client was the part not listening."""

    def test_there_is_one_helper_rather_than_three_assignments(self):
        assert "function setKpi(" in INTEL

    def test_a_feed_that_did_not_answer_leaves_a_dash(self):
        body = INTEL[INTEL.index("function setKpi("):]
        body = body[:body.index("function refreshEarningsRadar")]
        assert "if(!ok){" in body
        assert r"el.textContent='—'" in body      # an em dash, not a count
        assert "tile.classList.add('is-unread')" in body

    def test_a_feed_that_answered_clears_the_unread_state_and_its_note(self):
        body = INTEL[INTEL.index("function setKpi("):]
        body = body[:body.index("function refreshEarningsRadar")]
        assert "tile.classList.remove('is-unread')" in body
        assert "note.remove()" in body

    def test_a_real_zero_is_still_written(self):
        """Zero catalysts from a feed that answered is a reading, and the
        same mistake in reverse would be hiding it."""
        body = INTEL[INTEL.index("function setKpi("):]
        body = body[:body.index("function refreshEarningsRadar")]
        assert "el.textContent=value" in body

    @pytest.mark.parametrize("call", [
        "setKpi('intel-live-count',data.ok,data.count)",
        "setKpi('intel-earnings-count',data.ok,data.count)",
    ])
    def test_every_count_goes_through_it(self, call):
        assert call in INTEL

    def test_no_tile_is_still_assigned_directly(self):
        for direct in ("getElementById('intel-live-count').textContent=",
                       "getElementById('intel-earnings-count').textContent=",
                       "getElementById('intel-tone-counts').innerHTML="):
            assert direct not in INTEL, direct

    def test_the_endpoints_report_ok_honestly(self):
        """Which is what makes the client fix possible rather than a guess."""
        assert '"ok": bool(enriched)' in APP
        assert '"ok": bool(earnings)' in APP
