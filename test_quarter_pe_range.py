"""Five-year P/E range: price at each quarter end over trailing EPS then."""
from datetime import datetime, timezone

import quarter_facts as qf
from test_quarter_pe_split import nvda_like


def _bars(prices):
    ts = [int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) for d, _ in prices]
    return {"timestamps": ts, "closes": [p for _, p in prices]}


def test_ttm_history_follows_the_filers_own_quarter_ends():
    h = qf.ttm_history(nvda_like(), "2026-07-26")
    ends = [p["end"] for p in h]
    assert ends == sorted(ends)
    assert "2025-07-27" in ends and "2026-07-26" in ends     # 52/53-week dates, exact
    last = h[-1]
    assert round(last["eps"], 2) == round(1.30 + (4.90 - 0.76 - 1.08 - 1.30) + 2.39 + 2.46, 2)


def test_pe_range_uses_the_close_on_or_before_each_quarter_end():
    from app import pe_range_from
    points = [{"end": f"202{y}-0{m}-28", "eps": 2.0 + y * 0.1 + m * 0.01} for y in (2, 3, 4) for m in (3, 6, 9)]
    prices = [(p["end"], 40.0 + i) for i, p in enumerate(points)]
    r = pe_range_from(points, _bars(prices))
    assert r["enough"] and r["count"] == 9
    assert r["low"] <= r["p25"] <= r["median"] <= r["p75"] <= r["high"]
    assert r["points"][0]["pe"] == round(40.0 / points[0]["eps"], 2)


def test_negative_eps_and_missing_prices_are_skipped():
    from app import pe_range_from
    points = [{"end": "2024-03-31", "eps": -1}, {"end": "2024-06-30", "eps": 1},
              {"end": "2030-01-01", "eps": 1}]
    r = pe_range_from(points, _bars([("2024-06-28", 20.0)]))
    assert [p["end"] for p in r["points"]] == ["2024-06-30"]
    assert r["enough"] is False


def test_endpoint(monkeypatch):
    import app as legacy
    import web_app
    monkeypatch.setattr(legacy, "_thesis_daily_bars", lambda t: _bars(
        [(f"202{y}-0{m}-28", 50.0) for y in (2, 3, 4) for m in (3, 6, 9)]))
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    pts = [{"end": f"202{y}-0{m}-28", "eps": 1 + y + m / 10} for y in (2, 3, 4) for m in (3, 6, 9)]
    web_app.app.config["WTF_CSRF_ENABLED"] = False
    try:
        d = c.post("/api/quarter/pe-range/KLAC", json={"points": pts}).get_json()
    finally:
        web_app.app.config["WTF_CSRF_ENABLED"] = True
    assert d["enough"] and d["ticker"] == "KLAC"


def test_page_has_the_range_and_the_peg_warning():
    html = open("templates/fundamentals.html").read()
    for needle in ("function qdPeRangeHtml(", "5-year P/E range", "growth >50% — unreliable",
                   "/api/quarter/pe-range/"):
        assert needle in html
