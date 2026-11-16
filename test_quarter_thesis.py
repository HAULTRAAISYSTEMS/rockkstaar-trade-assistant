"""Long-term thesis: the holder's own sell rules, checked every quarter."""
import pytest

import quarter_thesis as qt

KLAC = {"rev": 4136015000, "revP": 3209696000, "cogs": 1577791000, "cogsP": 1243070000,
        "opinc": 1861670000, "ni": 1548588000, "niy": 1548588000, "cfo": 1530939000,
        "ca": 12573415000, "cl": 4555086000, "eps": 1.18, "epsP": 0.85,
        "sh": 1311064000, "shP": 1323808000}


def shrinking(rev, revP, opinc):
    return {"rev": rev, "revP": revP, "opinc": opinc, "cogs": rev * 0.4,
            "niy": 100, "cfo": 100}


def test_metrics_from_the_drill_figures():
    m = qt.metrics(KLAC)
    assert round(m["rev_growth"], 3) == 0.289
    assert round(m["op_margin"], 3) == 0.450
    assert round(m["cash_conv"], 2) == 0.99
    assert m["share_growth"] < 0


def test_a_healthy_quarter_leaves_the_thesis_intact():
    r = qt.evaluate(qt.default_thesis(), KLAC, [])
    assert r["tripped"] == 0 and r["warning"] == 0
    assert {x["status"] for x in r["rules"]} == {"ok"}


def test_one_bad_quarter_is_a_warning_not_a_trip():
    bad = shrinking(90, 100, 30)        # revenue -10%, op margin 33%
    r = qt.evaluate(qt.default_thesis(), bad, [])
    by = {x["key"]: x for x in r["rules"]}
    assert by["rev_shrinks"]["status"] == "warning"
    assert by["op_margin"]["status"] == "warning"
    assert r["tripped"] == 0


def test_a_streak_across_saved_quarters_trips():
    now = shrinking(90, 100, 30)
    older = [shrinking(95, 100, 30)]    # also shrinking, the quarter before
    r = qt.evaluate(qt.default_thesis(), now, older)
    by = {x["key"]: x for x in r["rules"]}
    assert by["rev_shrinks"]["status"] == "tripped" and by["rev_shrinks"]["run"] == 2


def test_a_good_quarter_in_between_breaks_the_streak():
    now = shrinking(90, 100, 30)
    older = [dict(KLAC), shrinking(95, 100, 30)]
    by = {x["key"]: x for x in qt.evaluate(qt.default_thesis(), now, older)["rules"]}
    assert by["rev_shrinks"]["status"] == "warning"


def test_missing_figures_say_so():
    by = {x["key"]: x for x in qt.evaluate(qt.default_thesis(), {"rev": 1}, [])["rules"]}
    assert by["cash_conv"]["status"] == "no_data"


def test_clean_keeps_only_known_rules_and_bounds_streaks():
    t = qt.clean({"why": "x" * 5000, "rules": [
        {"key": "op_margin", "on": True, "value": "40", "streak": 99},
        {"key": "made_up", "on": True, "value": 1}]})
    assert len(t["why"]) == 2000
    by = {r["key"]: r for r in t["rules"]}
    assert "made_up" not in by and by["op_margin"]["value"] == 40 and by["op_margin"]["streak"] == 8


def test_period_key_reads_the_date():
    assert qt.period_key("Quarter ended 2026-09-30") == "2026-09-30"
    assert qt.period_key("Q3 FY26") == ""


@pytest.fixture
def client(monkeypatch):
    import app as legacy
    import web_app
    store = {}
    monkeypatch.setattr(legacy, "get_user_setting", lambda uid, key, default=None: store.get((uid, key), default))
    monkeypatch.setattr(legacy, "set_user_setting", lambda uid, key, value: store.__setitem__((uid, key), value))
    saved = [
        {"id": 1, "ticker": "KLAC", "period": "Quarter ended 2026-06-30", "figures": shrinking(95, 100, 30)},
        {"id": 2, "ticker": "KLAC", "period": "Quarter ended 2026-09-30", "figures": dict(KLAC)},
        {"id": 3, "ticker": "META", "period": "Quarter ended 2026-06-30", "figures": shrinking(50, 100, 1)},
    ]
    monkeypatch.setattr(legacy, "get_quarter_entries", lambda uid: saved)
    web_app.app.config["WTF_CSRF_ENABLED"] = False
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    yield c
    web_app.app.config["WTF_CSRF_ENABLED"] = True


def test_api_round_trip_and_check(client):
    r = client.get("/api/quarter/thesis/KLAC").get_json()
    assert r["saved"] is False and r["thesis"]["rules"]
    assert client.post("/api/quarter/thesis/KLAC/check", json={"figures": KLAC}).get_json()["saved"] is False

    t = r["thesis"]
    t["why"] = "Chip inspection leader"
    assert client.post("/api/quarter/thesis/KLAC", json=t).get_json()["saved"]

    # This quarter shrinking; the saved June quarter (KLAC only) also shrank;
    # the saved September quarter is the one being checked and is excluded.
    d = client.post("/api/quarter/thesis/KLAC/check",
                    json={"figures": shrinking(90, 100, 30),
                          "period": "Quarter ended 2026-09-30"}).get_json()
    assert d["why"] == "Chip inspection leader"
    assert d["history_quarters"] == 1
    by = {x["key"]: x for x in d["rules"]}
    assert by["rev_shrinks"]["status"] == "tripped"


def test_api_rejects_bad_tickers(client):
    assert client.get("/api/quarter/thesis/%3Cscript%3E").status_code == 400


def test_page_has_the_thesis_card():
    html = open("templates/fundamentals.html").read()
    for needle in ("function qdThesisCheck(", "Write my thesis", "qd-thesis", "Save thesis"):
        assert needle in html
