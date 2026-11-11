"""Level 2 + Time & Sales from the Schwab streamer."""
import json
import time

import market_depth as md


def test_parse_and_merge_books():
    nasdaq = {"key": "NVDA", "1": 1, "2": [{"0": 187.5, "1": 1200, "2": 2, "3": [{"0": "NSDQ", "1": 800}, {"0": "ARCX", "1": 400}]},
                                         {"0": 187.49, "1": 300, "2": 1, "3": [{"0": "EDGX", "1": 300}]}],
              "3": [{"0": 187.52, "1": 500, "2": 1, "3": [{"0": "NSDQ", "1": 500}]}]}
    nyse = {"key": "NVDA", "2": [{"0": 187.5, "1": 100, "2": 1, "3": [{"0": "NYSE", "1": 100}]}], "3": []}
    book = md.merge_books([md.parse_book(nasdaq), md.parse_book(nyse)])
    assert [b["price"] for b in book["bids"]] == [187.5, 187.49]
    assert book["bids"][0]["size"] == 1300 and book["bids"][0]["count"] == 3
    assert [m["id"] for m in book["bids"][0]["mms"]] == ["NSDQ", "ARCX", "NYSE"]
    assert book["asks"][0] == {"price": 187.52, "size": 500, "count": 1, "mms": [{"id": "NSDQ", "size": 500}]}


def test_tape_from_level_one_trades():
    st = md.SymbolState("NVDA")
    st.on_level_one({"key": "NVDA", "1": 187.50, "2": 187.52, "3": 187.51, "8": 1_000_000, "9": 100, "35": 1})
    assert st.tape == []                                  # first snapshot is not a print
    st.on_level_one({"key": "NVDA", "3": 187.52, "8": 1_000_200, "9": 200, "35": 2})
    st.on_level_one({"key": "NVDA", "3": 187.50, "8": 1_000_300, "9": 100, "35": 3})
    st.on_level_one({"key": "NVDA", "3": 187.51, "8": 1_005_300, "9": 100, "35": 4})   # conflated
    st.on_level_one({"key": "NVDA", "1": 187.49})                                       # quote only
    assert [(p["side"], p["size"], p["combined"]) for p in st.tape] == [
        ("mid", 5000, True), ("sell", 100, False), ("buy", 200, False)]
    assert st.buy_vol == 200 and st.sell_vol == 100


def test_wants_expire_and_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "DIR", tmp_path)
    for s in ("AAPL", "MSFT", "NVDA", "TSLA", "AMD", "META"):
        md.request_symbol(s)
    assert len(md.wanted_symbols()) == md.MAX_SYMBOLS
    assert md.wanted_symbols(now=time.time() + md.WANT_TTL + 5) == []


def test_snapshot_and_api(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "DIR", tmp_path)
    monkeypatch.setattr(md, "ensure_started", lambda: None)
    md.set_status("live", "ok")
    st = md.SymbolState("NVDA")
    st.on_level_one({"key": "NVDA", "1": 1.0, "2": 1.1, "3": 1.05, "8": 10, "35": 1})
    (tmp_path / "NVDA.json").write_text(json.dumps(st.to_json()))
    import web_app
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
    d = c.get("/api/depth/nvda").get_json()
    assert d["ok"] and d["symbol"] == "NVDA" and d["status"]["state"] == "live" and d["quote"]["bid"] == 1.0
    assert (tmp_path / "want_NVDA").exists()
    assert c.get("/api/depth/..%2Fetc").status_code in (400, 404)


class _FakeWS:
    def __init__(self, inbox):
        self.inbox, self.sent = list(inbox), []

    def send(self, m):
        self.sent.append(json.loads(m)["requests"][0])

    def receive(self, timeout=None):
        return self.inbox.pop(0) if self.inbox else None

    def close(self):
        pass


def test_streamer_logs_in_subscribes_and_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "DIR", tmp_path)
    monkeypatch.setattr(md, "_streamer_info", lambda uid: ({"streamerSocketUrl": "wss://x", "schwabClientCustomerId": "c",
                                                            "schwabClientCorrelId": "r", "schwabClientChannel": "N9",
                                                            "schwabClientFunctionId": "APIAPP"}, "TOKEN"))
    ws = _FakeWS([json.dumps({"response": [{"service": "ADMIN", "command": "LOGIN", "content": {"code": 0}}]}),
                  json.dumps({"data": [{"service": "NASDAQ_BOOK", "content": [{"key": "NVDA", "2": [{"0": 10, "1": 5, "2": 1, "3": []}], "3": []}]}]})])
    import simple_websocket
    monkeypatch.setattr(simple_websocket.Client, "connect", classmethod(lambda cls, url: ws))
    s = md._Streamer(1)
    s.connect()
    assert ws.sent[0]["command"] == "LOGIN" and ws.sent[0]["parameters"]["Authorization"] == "TOKEN"
    s.subscribe(["NVDA"])
    assert {r["service"] for r in ws.sent[1:]} == {"LEVELONE_EQUITIES", "NASDAQ_BOOK", "NYSE_BOOK"}
    s.pump(); s.flush()
    assert json.loads((tmp_path / "NVDA.json").read_text())["book"]["bids"][0]["price"] == 10
