import live_research_runner as runner


class FakeConn:
    def __init__(self):
        self.committed = False
        self.closed = False
    def execute(self, sql, params=None):
        class Cursor:
            def fetchone(self):
                return {"id": 7, "username": "admin", "is_admin": 1}
        return Cursor()
    def commit(self): self.committed = True
    def close(self): self.closed = True


class News:
    articles = ({"headline": "NVIDIA announces update", "source": "Company News", "url": "https://example.com/nvda", "summary": "Verified provider summary", "published_at": "2026-08-27T00:00:00Z"},)


def test_runner_uses_phase6_draft_ingestion(monkeypatch):
    conn = FakeConn()
    captured = {}
    monkeypatch.setattr(runner, "get_db", lambda: conn)
    monkeypatch.setattr(runner, "fetch_headlines", lambda ticker: News())
    def fake_ingest(items, actor, db):
        captured["items"] = list(items)
        captured["actor"] = actor
        return {"created": 1, "duplicates": 0, "skipped": 0, "errors": []}
    monkeypatch.setattr(runner, "ingest", fake_ingest)
    result = runner.run(["NVDA"])
    assert result["created"] == 1
    assert captured["actor"]["is_admin"] is True
    assert captured["items"][0].ticker == "NVDA"
    assert conn.committed and conn.closed


def test_runner_provider_failure_is_contained(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(runner, "get_db", lambda: conn)
    monkeypatch.setattr(runner, "fetch_headlines", lambda ticker: (_ for _ in ()).throw(RuntimeError("provider down")))
    result = runner.run(["NVDA"])
    assert result["created"] == 0
    assert result["skipped"] == 1
    assert result["errors"] == ["NVDA:RuntimeError"]
    assert conn.committed and conn.closed


def test_default_universe_includes_nvda(monkeypatch):
    monkeypatch.delenv("LIVE_RESEARCH_TICKERS", raising=False)
    assert "NVDA" in runner._tickers_from_env()
