"""Level 2 and Time & Sales from the Schwab streamer.

Schwab allows one streamer connection per user, and the web service runs
several gunicorn workers. So one worker — whichever takes the file lock in
/tmp — holds the single connection; every worker serves browsers from the
snapshot files it writes:

    /tmp/tsdepth/want_<SYM>   touched by any worker when a browser asks for SYM
    /tmp/tsdepth/<SYM>.json   book + tape + quote, rewritten ≤ 4×/s by the leader
    /tmp/tsdepth/status.json  connection state for the panel's status line

The leader subscribes only to symbols someone asked for in the last 30 s
(at most MAX_SYMBOLS) and drops the connection after a minute with no asks.

Level 2 comes from NASDAQ_BOOK and NYSE_BOOK (the whole book on each tick).
Schwab's streamer has no time-and-sales service, so the tape is built from
LEVELONE_EQUITIES trade updates: a print is recorded whenever the trade time
or total volume moves. Level-one updates can be conflated in fast markets, so
when volume grew by more than the last trade size the print carries the
combined volume and is flagged `combined` — the tape never invents prints.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DIR = Path(os.environ.get("TSDEPTH_DIR", "/tmp/tsdepth"))
MAX_SYMBOLS = 5
WANT_TTL = 30          # seconds a browser ask keeps a symbol subscribed
IDLE_CLOSE = 60        # seconds with no asks before the connection closes
FLUSH_EVERY = 0.25
TAPE_KEEP = 250
GAP_RESET_MS = 10_000  # a trade this long after the last one only re-baselines volume
STATUS_EVERY = 3       # leader heartbeat, so the panel can tell "quiet" from "stalled"
BOOK_LEVELS = 15
_SYM = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_L1_FIELDS = "0,1,2,3,4,5,8,9,12,16,34,35"   # sym bid ask last bidsz asksz vol lastsz prevclose lastid quote/trade ms
_BOOKS = ("NASDAQ_BOOK", "NYSE_BOOK")

_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


def clean_symbol(sym) -> str | None:
    s = str(sym or "").strip().upper()
    return s if _SYM.match(s) else None


# ── Files shared between workers ────────────────────────────────────────────

def _write_json(path: Path, data: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp%d" % os.getpid())
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def request_symbol(sym: str) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / f"want_{sym}").touch()


def wanted_symbols(now: float | None = None) -> list[str]:
    now = now or time.time()
    out = []
    try:
        for p in DIR.glob("want_*"):
            age = now - p.stat().st_mtime
            if age <= WANT_TTL:
                out.append((p.stat().st_mtime, p.name[5:]))
            elif age > 600:
                p.unlink(missing_ok=True)
    except FileNotFoundError:
        return []
    return [s for _t, s in sorted(out, reverse=True)[:MAX_SYMBOLS]]


def set_status(state: str, msg: str = "") -> None:
    _write_json(DIR / "status.json", {"state": state, "msg": msg, "at": time.time()})


def snapshot(sym: str) -> dict:
    """What the panel shows for SYM: book, tape, quote and connection status."""
    data = _read_json(DIR / f"{sym}.json") or {}
    status = _read_json(DIR / "status.json") or {"state": "starting", "msg": "", "at": 0}
    return {"symbol": sym, "status": status, "updated": data.get("updated"),
            "book": data.get("book") or {"bids": [], "asks": []},
            "tape": data.get("tape") or [], "quote": data.get("quote") or {},
            "stats": data.get("stats") or {}}


# ── Message handling (pure; tested) ─────────────────────────────────────────

def parse_book(content: dict, levels: int = BOOK_LEVELS) -> dict:
    """One NASDAQ_BOOK / NYSE_BOOK entry → {bids, asks, time}. Each level:
    price, size (shares), count of market makers, and who they are."""
    def side(raw):
        out = []
        for lv in (raw or [])[:levels]:
            try:
                price = float(lv.get("0"))
                size = int(lv.get("1") or 0)
            except (TypeError, ValueError):
                continue
            mms = [{"id": str(m.get("0") or ""), "size": int(m.get("1") or 0)} for m in (lv.get("3") or [])]
            out.append({"price": price, "size": size, "count": int(lv.get("2") or len(mms)), "mms": mms[:6]})
        return out
    return {"bids": side(content.get("2")), "asks": side(content.get("3")), "time": content.get("1")}


def merge_books(books: list[dict], levels: int = BOOK_LEVELS) -> dict:
    """NASDAQ and NYSE books for one symbol, merged by price level."""
    def merge(key, reverse):
        by: dict[float, dict] = {}
        for b in books:
            for lv in b.get(key) or []:
                cur = by.setdefault(lv["price"], {"price": lv["price"], "size": 0, "count": 0, "mms": []})
                cur["size"] += lv["size"]
                cur["count"] += lv["count"]
                cur["mms"] = (cur["mms"] + lv["mms"])[:6]
        return sorted(by.values(), key=lambda x: x["price"], reverse=reverse)[:levels]
    return {"bids": merge("bids", True), "asks": merge("asks", False)}


class SymbolState:
    """Live state for one symbol; turns level-one changes into tape prints."""

    def __init__(self, sym: str):
        self.sym = sym
        self.q: dict = {}
        self.books: dict[str, dict] = {}
        self.tape: list[dict] = []
        self.dirty = False
        self.buy_vol = self.sell_vol = 0

    def on_level_one(self, c: dict) -> None:
        prev_vol, prev_t = self.q.get("volume"), self.q.get("trade_ms")
        for f, name in (("1", "bid"), ("2", "ask"), ("3", "last"), ("4", "bid_size"), ("5", "ask_size"),
                        ("8", "volume"), ("9", "last_size"), ("12", "prev_close"), ("16", "last_id"),
                        ("35", "trade_ms")):
            if f in c and c[f] is not None:
                self.q[name] = c[f]
        self.dirty = True
        vol, t = self.q.get("volume"), self.q.get("trade_ms")
        traded = ("35" in c and t != prev_t) or ("8" in c and prev_vol is not None and vol is not None and vol > prev_vol)
        if not traded or self.q.get("last") is None or prev_vol is None and prev_t is None:
            return
        # After a reconnect or a long quiet spell the volume jump spans many
        # trades we never saw; that is a new baseline, not a print.
        if prev_t and t and t - prev_t > GAP_RESET_MS:
            return
        last_size = int(self.q.get("last_size") or 0)
        delta = int(vol - prev_vol) if (vol is not None and prev_vol is not None) else last_size
        size = max(delta, last_size) if delta > 0 else last_size
        if size <= 0:
            return
        price = float(self.q["last"])
        bid, ask = self.q.get("bid"), self.q.get("ask")
        side = "mid"
        if ask is not None and price >= float(ask):
            side = "buy"
        elif bid is not None and price <= float(bid):
            side = "sell"
        if side == "buy":
            self.buy_vol += size
        elif side == "sell":
            self.sell_vol += size
        self.tape.insert(0, {"t": t or int(time.time() * 1000), "price": price, "size": size, "side": side,
                             "ex": self.q.get("last_id") or "", "combined": delta > last_size > 0})
        del self.tape[TAPE_KEEP:]

    def rebaseline(self) -> None:
        """Forget the last volume/trade time (keeps the tape on screen)."""
        for k in ("volume", "trade_ms"):
            self.q.pop(k, None)

    def on_book(self, service: str, c: dict) -> None:
        self.books[service] = parse_book(c)
        self.dirty = True

    def to_json(self) -> dict:
        q = self.q
        return {"updated": time.time(), "book": merge_books(list(self.books.values())), "tape": self.tape[:120],
                "quote": {k: q.get(k) for k in ("bid", "ask", "last", "bid_size", "ask_size", "volume",
                                                "prev_close", "trade_ms")},
                "stats": {"buy_vol": self.buy_vol, "sell_vol": self.sell_vol}}


# ── The single streamer connection (leader worker only) ─────────────────────

def _streamer_info(user_id: int) -> tuple[dict, str]:
    import schwab
    if not schwab.load_tokens(user_id):
        raise PermissionError("Schwab isn't connected or its login expired — reconnect it in Account → Brokers")
    prefs = schwab._get("/userPreference", user_id=user_id)
    if isinstance(prefs, list):
        prefs = prefs[0] if prefs else {}
    info = (prefs.get("streamerInfo") or [{}])[0]
    if not info.get("streamerSocketUrl"):
        raise RuntimeError("Schwab returned no streamer details")
    tokens = schwab.load_tokens(user_id) or {}      # after _get, which may have refreshed it
    if not tokens.get("access_token"):
        raise PermissionError("Schwab isn't connected or its login expired — reconnect it in Account → Brokers")
    return info, tokens["access_token"]


class _Streamer:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.ws = None
        self.info: dict = {}
        self.req = 0
        self.subs: list[str] = []
        self.states: dict[str, SymbolState] = {}

    def _send(self, service, command, params=None):
        self.req += 1
        body = {"requests": [{"requestid": str(self.req), "service": service, "command": command,
                              "SchwabClientCustomerId": self.info.get("schwabClientCustomerId"),
                              "SchwabClientCorrelId": self.info.get("schwabClientCorrelId"),
                              "parameters": params or {}}]}
        self.ws.send(json.dumps(body))

    def connect(self):
        import simple_websocket
        self.info, token = _streamer_info(self.user_id)
        self.ws = simple_websocket.Client.connect(self.info["streamerSocketUrl"])
        self._send("ADMIN", "LOGIN", {"Authorization": token,
                                      "SchwabClientChannel": self.info.get("schwabClientChannel"),
                                      "SchwabClientFunctionId": self.info.get("schwabClientFunctionId")})
        deadline = time.time() + 10
        while time.time() < deadline:
            msg = self.ws.receive(timeout=2)
            if not msg:
                continue
            for r in json.loads(msg).get("response") or []:
                if r.get("command") == "LOGIN":
                    code = (r.get("content") or {}).get("code")
                    if code == 0:
                        self.subs = []
                        for st in self.states.values():
                            st.rebaseline()
                        return
                    raise RuntimeError("Schwab streamer login failed: %s" % (r.get("content") or {}).get("msg"))
        raise TimeoutError("Schwab streamer login timed out")

    def close(self):
        try:
            if self.ws:
                self._send("ADMIN", "LOGOUT")
                self.ws.close()
        except Exception:
            pass
        self.ws, self.subs = None, []

    def subscribe(self, syms: list[str]):
        if syms == self.subs:
            return
        if syms:
            keys = ",".join(syms)
            self._send("LEVELONE_EQUITIES", "SUBS", {"keys": keys, "fields": _L1_FIELDS})
            for svc in _BOOKS:
                self._send(svc, "SUBS", {"keys": keys, "fields": "0,1,2,3"})
        else:
            for svc in ("LEVELONE_EQUITIES",) + _BOOKS:
                self._send(svc, "UNSUBS", {"keys": ",".join(self.subs)})
        for s in syms:
            self.states.setdefault(s, SymbolState(s))
        self.subs = list(syms)

    def pump(self, timeout=0.25):
        msg = self.ws.receive(timeout=timeout)
        if msg is None:
            return
        data = json.loads(msg)
        for d in data.get("data") or []:
            svc = d.get("service")
            for c in d.get("content") or []:
                st = self.states.get(str(c.get("key") or "").upper())
                if not st:
                    continue
                if svc == "LEVELONE_EQUITIES":
                    st.on_level_one(c)
                elif svc in _BOOKS:
                    st.on_book(svc, c)
        for r in data.get("response") or []:
            code = (r.get("content") or {}).get("code")
            if code not in (0, None):
                logger.info("schwab streamer %s %s: %s", r.get("service"), r.get("command"), r.get("content"))

    def flush(self):
        for s in self.subs:
            st = self.states.get(s)
            if st and st.dirty:
                _write_json(DIR / f"{s}.json", st.to_json())
                st.dirty = False


def _leader_loop(lock_fh):
    import schwab
    user_id = schwab.quotes_user_id()
    stream, idle_since, backoff, last_flush, last_beat = _Streamer(user_id), None, 2, 0.0, 0.0
    set_status("idle", "Waiting for a symbol")
    while True:
        try:
            want = wanted_symbols()
            if not want:
                idle_since = idle_since or time.time()
                if stream.ws and time.time() - idle_since > IDLE_CLOSE:
                    stream.close()
                    set_status("idle", "No one is watching; disconnected")
                if not stream.ws:
                    time.sleep(1)
                    continue
            else:
                idle_since = None
            if not stream.ws:
                set_status("connecting", "Connecting to Schwab")
                stream.connect()
                set_status("live", "Schwab streamer connected")
                backoff = 2
            stream.subscribe(want)
            stream.pump()
            if time.time() - last_flush >= FLUSH_EVERY:
                stream.flush()
                last_flush = time.time()
            if time.time() - last_beat >= STATUS_EVERY:
                set_status("live", "Schwab streamer connected")
                last_beat = time.time()
        except PermissionError as exc:
            stream.close()
            set_status("not_connected", str(exc))
            time.sleep(15)
        except Exception as exc:
            logger.warning("schwab streamer: %s", exc)
            stream.close()
            set_status("error", "Reconnecting to Schwab")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


def ensure_started() -> None:
    """Start the streamer in this worker if no other worker holds it."""
    global _thread
    if _thread and _thread.is_alive():
        return
    with _thread_lock:
        if _thread and _thread.is_alive():
            return
        DIR.mkdir(parents=True, exist_ok=True)
        fh = open(DIR / "leader.lock", "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return            # another worker is the leader
        _thread = threading.Thread(target=_leader_loop, args=(fh,), name="schwab-depth", daemon=True)
        _thread.start()
