"""World exchanges on the Command Center globe: sessions, holidays, relay,
index pulse and setup-score rings."""
import html as _html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import globe_markets as gm

ROOT = Path(__file__).parent


def _at(iso):
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


def _state(code, iso):
    return gm.session_state(gm.BY_CODE[code], _at(iso))


@pytest.fixture
def client():
    import web_app
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
    return c


# ── Sessions and calendars ───────────────────────────────────────────────
def test_nyse_is_a_holiday_on_thanksgiving_not_open():
    st = _state("NYSE", "2026-11-26T11:00:00-05:00")
    assert st["status"] == "holiday" and st["holiday"] == "Thanksgiving Day"
    # Next open is the Friday early-close session.
    assert st["opens_at"] == int(_at("2026-11-27T09:30:00-05:00").timestamp())
    fri = _state("NYSE", "2026-11-27T12:00:00-05:00")
    assert fri["status"] == "open" and "13:00" in (fri["note"] or "")
    assert fri["closes_at"] == int(_at("2026-11-27T13:00:00-05:00").timestamp())


def test_nyse_calendar_comes_from_the_execution_schedule():
    import exchange_schedule as es
    for year, days in es.HOLIDAYS.items():
        for md in days:
            assert f"{year}-{md}" in gm.BY_CODE["NYSE"]["holidays"]


def test_tokyo_lunch_break_and_last_close_skip_the_break():
    st = _state("TSE", "2026-11-24T12:00:00+09:00")
    assert st["status"] == "lunch"
    assert st["opens_at"] == int(_at("2026-11-24T12:30:00+09:00").timestamp())
    # A lunch break is not a close: the relay still points at the last full day.
    assert st["last_close_at"] < int(_at("2026-11-24T09:00:00+09:00").timestamp())


def test_tokyo_labor_thanksgiving_day_is_closed():
    st = _state("TSE", "2026-11-23T10:00:00+09:00")
    assert st["status"] == "holiday" and "Thanksgiving" in st["holiday"]


def test_hong_kong_half_day_ends_at_noon():
    st = _state("HKEX", "2026-12-24T11:00:00+08:00")
    assert st["status"] == "open"
    assert st["closes_at"] == int(_at("2026-12-24T12:00:00+08:00").timestamp())
    after = _state("HKEX", "2026-12-24T13:30:00+08:00")
    assert after["status"] == "closed"


def test_b3_close_follows_us_daylight_time():
    winter = _state("B3", "2026-11-23T12:00:00-03:00")
    assert winter["closes_at"] == int(_at("2026-11-23T17:55:00-03:00").timestamp())
    summer = _state("B3", "2027-04-15T12:00:00-03:00")
    assert summer["closes_at"] == int(_at("2027-04-15T16:55:00-03:00").timestamp())
    ash = _state("B3", "2027-02-10T11:00:00-03:00")
    assert ash["status"] == "closed"
    assert ash["opens_at"] == int(_at("2027-02-10T13:00:00-03:00").timestamp())


def test_unpublished_calendars_say_so_instead_of_guessing():
    st = _state("NSE", "2027-01-05T11:00:00+05:30")
    assert st["verified"] is False and st["verified_through"] == "2026-12-31"
    assert _state("NSE", "2026-11-24T11:00:00+05:30")["status"] == "holiday"


def test_every_exchange_has_coordinates_index_and_source():
    for ex in gm.EXCHANGES:
        assert ex["index"][0] and ex["index"][1] and ex["source"]
        assert -90 <= ex["lat"] <= 90 and -180 <= ex["lon"] <= 180
        for key in ex["holidays"]:
            datetime.strptime(key, "%Y-%m-%d")
    codes = set(gm.BY_CODE)
    for a, b in gm.RELAY:
        assert a in codes and b in codes


# ── Index pulse ──────────────────────────────────────────────────────────
def test_parse_chart_reads_price_change_and_spark():
    res = {"meta": {"regularMarketPrice": 102.0, "chartPreviousClose": 100.0,
                    "regularMarketTime": 1764000000, "currency": "JPY"},
           "indicators": {"quote": [{"close": [100.0, None, 101.0] + [101.5] * 100 + [102.0]}]}}
    q = gm._parse_chart(res)
    assert q["change_pct"] == 2.0 and q["price"] == 102.0 and q["prev_close"] == 100.0
    assert 2 <= len(q["spark"]) <= 49 and q["spark"][-1] == 102.0
    assert gm._parse_chart({"meta": {"regularMarketPrice": 5}}) is None


def test_index_pulse_never_blocks_and_drops_stale_quotes(monkeypatch):
    import time
    monkeypatch.setitem(gm._PULSE, "data", {
        "TSE": {"price": 1, "change_pct": 0.5, "fetched_at": int(time.time())},
        "LSE": {"price": 1, "change_pct": 0.1, "fetched_at": int(time.time()) - 7200},
    })
    out = gm.index_pulse()   # TRADESTAAR_NO_BACKGROUND=1 in tests: no thread
    assert "TSE" in out and "LSE" not in out


# ── Page + API ───────────────────────────────────────────────────────────
def test_api_globe_markets(client):
    d = client.get("/api/globe/markets").get_json()
    assert {e["code"] for e in d["exchanges"]} == set(gm.BY_CODE)
    assert d["relay"] and "server_epoch" in d and "Yahoo" in d["index_source"]
    for e in d["exchanges"]:
        assert e["status"] in ("open", "lunch", "closed", "holiday")


def test_page_hands_markets_and_scores_to_the_globe(client):
    page = client.get("/").get_data(as_text=True)
    m = re.search(r'data-markets="([^"]*)"', page)
    assert m, "globe is missing its markets payload"
    assert len(json.loads(_html.unescape(m.group(1)))["exchanges"]) == len(gm.EXCHANGES)
    s = re.search(r'data-scores="([^"]*)"', page)
    assert s and isinstance(json.loads(_html.unescape(s.group(1))), dict)
    assert 'id="cmd27-exch"' in page and "lg-row-relay" in page and "lg-row-score" in page


def test_score_map_reads_the_scanner_and_skips_unscored(monkeypatch):
    class _Rows:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows
    class _Conn:
        def execute(self, sql, params):
            assert "IN(?,?,?)" in sql.replace(" ", "")
            return _Rows([{"ticker": "NVDA", "swing_score": 9, "swing_setup_type": "Pullback"},
                          {"ticker": "AMD", "swing_score": None, "swing_setup_type": ""}])
        def close(self): pass
    import database
    monkeypatch.setattr(database, "get_db", lambda: _Conn())
    out = gm.score_map(["nvda", "AMD", "XYZ"])
    assert out == {"NVDA": {"s": 9, "g": "A", "setup": "Pullback"}}


# ── Globe wiring (JS) ────────────────────────────────────────────────────
def test_globe_reads_server_sessions_and_draws_the_relay():
    js = (ROOT / "static/js/command_center_3d.js").read_text(encoding="utf-8")
    assert "data-markets" in js and "function setMarkets(" in js
    assert "Follow the Sun" in js and "mode === 'flow'" in js
    assert "function exTag(" in js and "' · HOLIDAY'" in js and "' · OPENS '" in js
    # Old local-clock guess stays only as the fallback.
    assert "if (!RELAY.length || !Object.keys(MK).length)" in js


def test_globe_exchange_panel_and_score_rings_are_wired():
    js = (ROOT / "static/js/command_center_3d.js").read_text(encoding="utf-8")
    assert "function openExch(" in js and "function exchAt(" in js and "openExch(exc)" in js
    assert "Holiday calendar not loaded past" in js
    assert "Setup score " in js and "scS.s >= 8" in js
    css = (ROOT / "static/css/command_center_picture.css").read_text(encoding="utf-8")
    assert ".cmd27-exch {" in css and ".cmd27-legend.has-relay .lg-row-relay" in css
