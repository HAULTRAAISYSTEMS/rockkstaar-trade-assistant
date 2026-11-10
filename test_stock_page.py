"""Stock page rebuild: one plan, a price ladder, a plain "what has to happen"
list, tabs instead of thirty stacked cards, and the live print in the header."""
import os

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import pytest

import stock_page as sp

CEG = {
    "ticker": "CEG", "current_price": 276.09, "trade_bias": "Long Bias",
    "ema_20_daily": 260.16, "ema_50_daily": 266.34, "ema_200_daily": 280.18,
    "fib_high": 309.80, "fib_low": 247.20, "fib_236": 295.03, "fib_382": 285.89,
    "fib_50": 278.50, "fib_618": 271.11, "fib_705": 265.67, "fib_786": 260.60,
    "entry_zone_low": 268.40, "entry_zone_high": 273.82, "stop_level": 252.88,
    "target_1": 309.80, "target_2": 327.79, "risk_reward": 1.7, "catalyst_score": 3,
    "daily_trend": "Bearish", "daily_lh_ll": True,
}


class TestLadder:
    def test_high_to_low(self):
        prices = [r.get("top") or r["price"] for r in sp.price_ladder(CEG)]
        assert prices == sorted(prices, reverse=True)

    def test_swing_high_and_target_1_are_one_row(self):
        rows = [r for r in sp.price_ladder(CEG) if r["price"] == 309.80]
        assert len(rows) == 1
        assert rows[0]["kind"] == "target" and "Swing high" in rows[0]["label"]

    def test_entry_zone_is_a_range(self):
        entry = next(r for r in sp.price_ladder(CEG) if r["kind"] == "entry")
        assert (entry["price"], entry["top"]) == (268.40, 273.82)

    def test_missing_levels_are_skipped(self):
        assert sp.price_ladder({"ticker": "X", "stop_level": None, "target_1": 0}) == []


class TestWatch:
    def test_reads_like_the_screenshot(self):
        texts = [w["text"] for w in sp.watch_items(CEG)]
        assert any("Reclaim the 200 EMA at $280.18" in t for t in texts)
        assert any("lower highs / lower lows" in t for t in texts)
        assert any("pullback into $268.40–$273.82" in t for t in texts)
        assert any(t.startswith("Risk/reward 1.7:1") for t in texts)

    def test_ok_flags(self):
        items = {w["text"].split(" ")[0]: w["ok"] for w in sp.watch_items(CEG)}
        assert items["Reclaim"] is False and items["Catalyst"] is True

    def test_no_price_no_list(self):
        assert sp.watch_items({"ticker": "X"}) == []


class TestLiveOverlay:
    def test_overlay_uses_the_live_print(self):
        s = sp.overlay_live({"current_price": 270.0, "gap_pct": 0.1},
                            {"price": 276.4, "change_pct": 1.234, "prev_close": 273.0, "source": "Schwab real-time"})
        assert s["current_price"] == 276.4 and s["gap_pct"] == 1.23 and s["prev_close"] == 273.0
        assert s["live_quote"]["source"] == "Schwab real-time"

    def test_no_quote_leaves_the_snapshot(self):
        assert sp.overlay_live({"current_price": 270.0}, None) == {"current_price": 270.0}


def test_fib_setup_label_names_the_nearer_level():
    """276.09 is within 2% of both 61.8% (271.11) and 50% (278.50); 50% is nearer."""
    import scoring
    data = dict(CEG, fib_direction="bullish", rel_volume=1.0, gap_pct=0.0, catalyst_score=0,
                daily_lh_ll=False, daily_hh_hl=False, in_demand_zone=False, in_supply_zone=False)
    label = scoring.compute_swing_setup_type(data)
    assert label != "Near 61.8% Retracement"
    near_618 = dict(data, current_price=271.5)
    assert scoring.compute_swing_setup_type(near_618) in ("Near 61.8% Retracement", "Pullback to 20 EMA",
                                                          "Pullback to 50 EMA", "Order Block Test")


# ── Page ─────────────────────────────────────────────────────────────────────

class _Row(dict):
    """A stored row: every column exists, most are empty."""
    def __missing__(self, key):
        return None


@pytest.fixture
def page(monkeypatch):
    import app as legacy
    import web_app
    stock = dict(CEG, swing_score=5, avg_volume=3_000_000, rel_volume=1.1, news_headlines=[], catalyst_tags=[], demand_zones=[], supply_zones=[], zone_ai_reasons=[], last_updated="2026-11-09 21:20:00", gap_pct=1.2, prev_close=272.8,
                 swing_plan_valid=True, ticker_state="ready")
    monkeypatch.setattr(legacy, "get_stock_data", lambda t: _Row(stock))
    monkeypatch.setattr(sp, "live_quote", lambda t: {"price": 276.40, "change_pct": 1.32, "prev_close": 272.8,
                                                     "source": "Schwab real-time", "epoch": 1, "as_of": "9:25:00 PM ET"})
    import data_fetcher
    monkeypatch.setattr(data_fetcher, "_fetch_price_via_chart_api", lambda t: None)
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    resp = c.get("/stock/CEG")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_page_has_hero_decision_row_and_tabs(page):
    assert 'id="sd-hero"' in page and 'class="sd-decision"' in page
    for tab in ("levels", "news", "thesis", "fundamentals", "tools"):
        assert f'data-tab="{tab}"' in page and f'id="sd-panel-{tab}"' in page


def test_live_print_in_the_header(page):
    assert ">$276.40<" in page and "Schwab real-time" in page


def test_one_plan_not_two(page):
    assert "AI Institutional Plan" not in page
    assert page.count(">The Plan") == 1


def test_ladder_and_watch_render(page):
    assert 'id="sd-ladder"' in page and 'data-level="252.88"' in page
    assert "Reclaim the 200 EMA" in page


def test_ids_the_live_poll_patches_are_kept(page):
    for el in ("detail-sfa-badge", "detail-exec-badge", "detail-bias-tag", "detail-swing-score",
               "detail-cat-score", "detail-cur-price", "detail-gap", "detail-poll-time",
               "detail-coach-card", "ps-panel", "notes", "plan", "company-overview-card"):
        assert f'id="{el}"' in page, el
