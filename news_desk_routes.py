"""News desk APIs: catalyst heat map, earnings reaction replay, explain-this-move."""
from __future__ import annotations

import re

from flask import Blueprint, jsonify

import news_desk

_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
DEFAULT_UNIVERSE = ["SPY", "QQQ", "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA", "AMD", "AVGO", "PLTR"]


def create_news_desk_blueprint(*, current_user, tracked_tickers):
    bp = Blueprint("news_desk", __name__)

    def uid():
        return int((current_user() or {}).get("id") or 0)

    def ok_ticker(t):
        t = (t or "").upper().strip()
        return t if _TICKER.match(t) else ""

    @bp.get("/api/news-desk/heatmap")
    def heatmap():
        try:
            tickers = list(tracked_tickers(uid()) or [])
        except Exception:
            tickers = []
        tickers = [t for t in tickers if ok_ticker(t)] or DEFAULT_UNIVERSE
        resp = jsonify(news_desk.heat_map(tickers))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @bp.get("/api/news-desk/replay/<ticker>")
    def replay(ticker):
        t = ok_ticker(ticker)
        if not t:
            return jsonify({"ok": False, "error": "Bad ticker."}), 400
        return jsonify(news_desk.earnings_replay(t))

    @bp.get("/api/news-desk/explain/<ticker>")
    def explain(ticker):
        t = ok_ticker(ticker)
        if not t:
            return jsonify({"ok": False, "error": "Bad ticker."}), 400
        return jsonify(news_desk.explain_move(t))

    return bp
