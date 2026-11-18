"""The Watchlists board: each section takes tickers typed straight into it."""
from datetime import date

import pytest


@pytest.fixture
def board(monkeypatch):
    import app as legacy
    import web_app
    lists = {1: {"id": 1, "name": "BUY & HOLD", "user_id": 1}}
    members = {1: ["NVDA", "KLAC"]}
    sections = {1: {"id": 7, "name": "Foundation", "tickers": ["NVDA"]}}
    moves = []

    def structure(wl):
        placed = [t for s in sections.values() for t in s["tickers"]]
        return {"sections": list(sections.values()),
                "unsectioned": [t for t in members[wl] if t not in placed]}

    def move(wl, t, sid):
        if str(sid) != "7":
            return False
        moves.append((t, sid))
        for s in sections.values():
            if t in s["tickers"]:
                s["tickers"].remove(t)
        sections[1]["tickers"].append(t)
        return True

    monkeypatch.setattr(legacy, "get_all_watchlists", lambda uid: list(lists.values()))
    monkeypatch.setattr(legacy, "get_active_wl_id", lambda: 1)
    monkeypatch.setattr(legacy, "get_watchlist_by_id", lambda wl: lists.get(wl))
    monkeypatch.setattr(legacy, "get_watchlist_stocks", lambda wl: list(members.get(wl, [])))
    monkeypatch.setattr(legacy, "add_ticker_to_watchlist", lambda wl, t: members[wl].append(t))
    monkeypatch.setattr(legacy, "upsert_loading_placeholder", lambda t: None)
    monkeypatch.setattr(legacy, "_onboard_ticker_bg", lambda t, uid: None)
    monkeypatch.setattr(legacy, "get_watchlist_structure", structure)
    monkeypatch.setattr(legacy, "move_watchlist_ticker", move)
    monkeypatch.setattr(legacy, "get_price_alerts", lambda uid: [])
    monkeypatch.setattr(legacy, "_zone_preload", lambda tickers, quotes: {})
    monkeypatch.setattr(legacy, "get_all_stock_data", lambda: [
        {"ticker": "NVDA", "current_price": 110.0, "prev_close": 100.0, "earnings_date": None},
        {"ticker": "KLAC", "current_price": 95.0, "prev_close": 100.0, "earnings_date": None},
    ])
    web_app.app.config["WTF_CSRF_ENABLED"] = False
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    yield c, members, sections, moves
    web_app.app.config["WTF_CSRF_ENABLED"] = True


def test_ticker_typed_into_a_section_lands_in_that_section(board):
    c, members, sections, moves = board
    r = c.post("/watchlist/add", data={"tickers": "skhy", "watchlist_id": "1", "section_id": "7",
                                       "next": "/watchlists#sec-7"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/watchlists#sec-7")
    assert "SKHY" in members[1] and ("SKHY", "7") in moves
    assert "SKHY" in sections[1]["tickers"]


def test_ticker_already_in_the_list_moves_to_the_section(board):
    c, members, sections, moves = board
    c.post("/watchlist/add", data={"tickers": "KLAC", "watchlist_id": "1", "section_id": "7"})
    assert members[1].count("KLAC") == 1 and ("KLAC", "7") in moves


def test_no_section_leaves_it_unsorted(board):
    c, members, sections, moves = board
    c.post("/watchlist/add", data={"tickers": "AAPL", "watchlist_id": "1"})
    assert "AAPL" in members[1] and not moves


def test_board_renders_columns_with_quick_add_and_prices(board):
    c, *_ = board
    html = c.get("/watchlists").get_data(as_text=True)
    assert 'id="sec-7"' in html and 'id="sec-none"' in html
    assert 'name="section_id" value="7"' in html and "Add to Foundation…" in html
    assert ">110.00<" in html and "+10.00%" in html and "-5.00%" in html
    assert "te-fold" in html and "data-fold=\"close\"" in html
    assert "te-col-new" in html


def test_board_quotes_flags_earnings_within_two_weeks():
    import app as legacy
    rows = {"A": {"current_price": 10, "prev_close": 8, "earnings_date": "2026-11-24"},
            "B": {"current_price": 10, "prev_close": None, "earnings_date": "2027-02-01"}}
    q = legacy._board_quotes(["A", "B", "C"], rows, today=date(2026, 11, 17))
    assert q["A"]["chg"] == pytest.approx(25.0) and q["A"]["er_days"] == 7
    assert q["B"]["chg"] is None and q["B"]["er_days"] is None
    assert q["C"]["price"] is None


def test_scanner_rows_sort_strongest_setup_first():
    import app as legacy
    q = {"A": {"setup": 4}, "B": {"setup": 9}, "C": {"setup": None}, "D": {"setup": 9}}
    assert legacy._scanner_rows(["A", "B", "C", "D"], q) == ["B", "D", "A", "C"]


def test_board_quotes_carry_the_scanner_fields():
    import app as legacy
    rows = {"A": {"current_price": 10, "prev_close": 9, "setup_score": "7", "catalyst_score": 5,
                  "setup_type": "No Setup", "trade_bias": "Bullish", "rel_volume": "1.8"}}
    a = legacy._board_quotes(["A"], rows)["A"]
    assert a["setup"] == 7 and a["catalyst"] == 5 and a["setup_type"] == ""
    assert a["bias"] == "Bullish" and a["rvol"] == 1.8
