"""Value-zone badges show at once: saved in the database, served while stale,
and written into the Watchlists page."""
import time

import pytest

import database

Z = {"available": True, "ticker": "MRVL", "zone": "accumulate", "zone_label": "Accumulate",
     "price": 60.0, "accum_top": 74.0, "load_top": 55.0, "bull": 104.0}


@pytest.fixture
def legacy(monkeypatch):
    import web_app  # noqa: F401
    import app as legacy
    legacy._VALUE_ZONES.clear()
    legacy._VALUE_ZONES_BUILDING.clear()
    store = {}
    monkeypatch.setattr(database, "get_value_zones_cache_many",
                        lambda ts: {t: store[t] for t in ts if t in store})
    monkeypatch.setattr(database, "save_value_zones_cache",
                        lambda t, z, at: store.__setitem__(t, (at, z)))
    builds = []

    def fake_build(t):
        builds.append(t)
        out = dict(Z, ticker=t)
        legacy._store_value_zones(t, out)
        return out
    monkeypatch.setattr(legacy, "build_value_zones", fake_build)
    legacy._store = store
    legacy._builds = builds
    yield legacy
    legacy._VALUE_ZONES.clear()


def _wait(legacy, n):
    for _ in range(100):
        if len(legacy._builds) >= n and not legacy._VALUE_ZONES_BUILDING:
            return
        time.sleep(0.02)


def test_a_restart_reads_the_saved_badge_instead_of_rebuilding(legacy):
    legacy._store["MRVL"] = (time.time(), Z)          # saved by an earlier worker
    assert legacy._value_zones_cached("MRVL")["zone"] == "accumulate"
    assert legacy._builds == []


def test_a_stale_badge_is_served_while_it_rebuilds(legacy):
    legacy._VALUE_ZONES["MRVL"] = (time.time() - legacy._VALUE_ZONES_TTL - 60, Z)
    assert legacy._value_zones_cached("MRVL")["zone"] == "accumulate"
    _wait(legacy, 1)
    assert legacy._builds == ["MRVL"]
    assert time.time() - legacy._VALUE_ZONES["MRVL"][0] < 5
    assert "MRVL" in legacy._store                    # the rebuild was saved


def test_a_failed_rebuild_keeps_the_last_good_badge(legacy, monkeypatch):
    def boom(t):
        legacy._builds.append(t)
        raise RuntimeError("fundamentals down")
    monkeypatch.setattr(legacy, "build_value_zones", boom)
    legacy._VALUE_ZONES["MRVL"] = (time.time() - legacy._VALUE_ZONES_TTL - 60, Z)
    legacy._value_zones_cached("MRVL")
    _wait(legacy, 1)
    assert legacy._VALUE_ZONES["MRVL"][1]["available"]


def test_never_built_is_pending_and_starts_a_build(legacy):
    assert legacy._value_zones_cached("NEWT") is None
    _wait(legacy, 1)
    assert legacy._builds == ["NEWT"]


def test_api_answers_from_the_saved_badges(legacy, monkeypatch):
    monkeypatch.setattr(legacy, "_auth_required", lambda: False)
    legacy._store["MRVL"] = (time.time(), Z)
    legacy._store["NVDA"] = (time.time(), dict(Z, ticker="NVDA"))
    d = legacy.app.test_client().get("/api/thesis/zones?tickers=MRVL,NVDA").get_json()
    assert d["pending"] == [] and set(d["zones"]) == {"MRVL", "NVDA"}


def test_zone_preload_restates_at_the_stored_price(legacy):
    legacy._store["MRVL"] = (time.time(), Z)
    out = legacy._zone_preload(["MRVL", "NEWT"], {"MRVL": {"price": 50.0}})
    assert set(out) == {"MRVL"} and out["MRVL"]["zone"] == "load"


def test_badge_script_paints_the_preload_first():
    js = open("static/js/value_badges.js", encoding="utf-8").read()
    tpl = open("templates/watchlists.html", encoding="utf-8").read()
    assert "window.VZ_PRELOAD" in js and "paint(pre[t])" in js and "tries < 14" in js
    assert "window.VZ_PRELOAD = {{ (zone_preload or {})|tojson }}" in tpl
