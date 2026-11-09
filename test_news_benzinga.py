"""Benzinga (via Alpaca) as a second primary newswire, combined with Finnhub.

fetch_headlines used to stop at the first provider that answered, so a
story Finnhub missed was never seen, and a Finnhub outage dropped straight
to the slower backups. Now both keyed wires are asked together and merged.
"""
import io
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import news_fetcher as nf
from news_fetcher import CatalystNews

NOW = datetime.now(timezone.utc)


def _iso(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


ALPACA_PAYLOAD = {"news": [
    {"headline": "Constellation Energy inks nuclear PPA with Microsoft", "source": "benzinga",
     "url": "https://www.benzinga.com/1", "summary": "s", "created_at": _iso(5),
     "symbols": ["CEG", "MSFT"], "images": [{"size": "large", "url": "L"}, {"size": "small", "url": "S"}]},
    {"headline": "CEG shares climb premarket", "source": "benzinga", "url": "https://www.benzinga.com/2",
     "summary": "", "created_at": _iso(40), "symbols": ["CEG"], "images": []},
]}


@pytest.fixture
def alpaca(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    seen = []

    def fake_urlopen(req, timeout=4):
        seen.append(req.full_url if hasattr(req, "full_url") else req)
        return io.BytesIO(json.dumps(ALPACA_PAYLOAD).encode())
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def test_alpaca_news_parses_benzinga_stories(alpaca):
    rows = nf.fetch_alpaca_news(["ceg"])
    assert "symbols=CEG" in alpaca[0] and "v1beta1/news" in alpaca[0]
    assert rows[0]["headline"].startswith("Constellation") and rows[0]["image"] == "S"
    assert rows[0]["symbols"] == ["CEG", "MSFT"] and rows[0]["source"] == "Benzinga"


def test_no_alpaca_keys_means_no_call(monkeypatch):
    for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert nf.fetch_alpaca_news(["CEG"]) == []


def _cn(source, items):
    arts = tuple(nf._article(h, source=source, published_at=_iso(m)) for h, m in items)
    return CatalystNews(headlines=[a["headline"] for a in arts], summary="", categories=[],
                        freshness_minutes=min(m for _, m in items), source=source, articles=arts)


def test_merge_combines_dedupes_and_sorts_newest_first():
    fh = _cn("finnhub", [("CEG wins big contract", 30), ("Utilities rally", 90)])
    bz = _cn("alpaca_benzinga", [("CEG  wins big contract", 25), ("CEG upgraded at Jefferies", 3)])
    m = nf._merge_news([fh, bz])
    assert m.source == "finnhub+alpaca_benzinga"
    assert m.headlines == ["CEG upgraded at Jefferies", "CEG wins big contract", "Utilities rally"]
    assert m.freshness_minutes == 3


def test_one_wire_down_the_other_still_answers(monkeypatch):
    monkeypatch.setattr(nf, "_try_finnhub", lambda t: None)
    monkeypatch.setattr(nf, "_try_alpaca_news", lambda t: _cn("alpaca_benzinga", [("CEG story", 4)]))
    r = nf.fetch_headlines("CEG")
    assert r.source == "alpaca_benzinga" and r.headlines == ["CEG story"]


def test_both_wires_are_combined(monkeypatch):
    monkeypatch.setattr(nf, "_try_finnhub", lambda t: _cn("finnhub", [("A", 10)]))
    monkeypatch.setattr(nf, "_try_alpaca_news", lambda t: _cn("alpaca_benzinga", [("B", 2)]))
    r = nf.fetch_headlines("CEG")
    assert r.source == "finnhub+alpaca_benzinga" and r.headlines == ["B", "A"]


def test_backups_still_run_when_both_wires_are_empty(monkeypatch):
    monkeypatch.setattr(nf, "_try_finnhub", lambda t: None)
    monkeypatch.setattr(nf, "_try_alpaca_news", lambda t: None)
    monkeypatch.setattr(nf, "_try_newsapi", lambda t: None)
    monkeypatch.setattr(nf, "_try_polygon", lambda t: _cn("polygon", [("P", 60)]))
    assert nf.fetch_headlines("CEG").source == "polygon"


def test_status_lists_benzinga(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k"); monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setenv("FINNHUB_API_KEY", "f")
    assert nf.news_source_status()["configured_sources"][:2] == ["Finnhub", "Benzinga (Alpaca)"]


def test_provider_label():
    import app as legacy
    assert legacy._news_provider_label("finnhub+alpaca_benzinga") == "Finnhub + Benzinga"
