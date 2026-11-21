"""Ticker → sector fund for the Command Center globe.

The globe groups stock pins onto sector "continents". A ticker's sector comes
from, in order:

1. market_engine's built-in map plus EXTRA below (no I/O);
2. sectors the app already saved while researching a company
   (stock_data.company_sector / fundamentals_cache);
3. this module's own table, filled by a background lookup for tickers still
   unknown (yfinance company info via market_engine.get_sector_for_ticker_bg).

Nothing here blocks a page on the network: unknown tickers resolve in a
daemon thread and appear on the next page load.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Names the built-in market_engine map lacks; common on growth/AI watchlists.
EXTRA: dict[str, str] = {
    # AI infrastructure, cloud, software
    "APLD": "XLK", "NBIS": "XLK", "IREN": "XLK", "CRWV": "XLK", "SMCI": "XLK", "DELL": "XLK",
    "ANET": "XLK", "CIEN": "XLK", "HPE": "XLK", "IBM": "XLK", "SHOP": "XLK", "APP": "XLK",
    "U": "XLK", "DUOL": "XLK", "MDB": "XLK", "TEAM": "XLK", "WDAY": "XLK", "INTU": "XLK",
    "UBER": "XLK", "MSTR": "XLK", "IONQ": "XLK", "RGTI": "XLK", "QBTS": "XLK", "QUBT": "XLK",
    "ONDS": "XLK", "S": "XLK", "FTNT": "XLK", "CYBR": "XLK", "TOST": "XLK", "HUBS": "XLK",
    "MARA": "XLK", "RIOT": "XLK", "CLSK": "XLK", "CORZ": "XLK", "CIFR": "XLK", "WULF": "XLK",
    "HUT": "XLK", "BTDR": "XLK", "GLXY": "XLF",
    # Semiconductors & photonics
    "TSM": "SMH", "ARM": "SMH", "CRDO": "SMH", "ALAB": "SMH", "COHR": "SMH", "LITE": "SMH",
    "AAOI": "SMH", "POET": "SMH", "NVTS": "SMH", "WOLF": "SMH", "ON": "SMH", "MPWR": "SMH",
    "NXPI": "SMH", "ADI": "SMH", "MCHP": "SMH", "SNPS": "SMH", "CDNS": "SMH", "AMKR": "SMH",
    "SITM": "SMH", "SOXL": "SMH", "SMH": "SMH", "SOXX": "SMH", "TER": "SMH", "ENTG": "SMH",
    "WDC": "SMH", "STX": "SMH", "SNDK": "SMH", "AEHR": "SMH", "RMBS": "SMH", "MTSI": "SMH",
    # Financials & fintech
    "SOFI": "XLF", "NU": "XLF", "AFRM": "XLF", "UPST": "XLF", "C": "XLF", "SCHW": "XLF",
    "BLK": "XLF", "AXP": "XLF", "BRK.B": "XLF", "IBKR": "XLF", "CME": "XLF", "ICE": "XLF",
    "KKR": "XLF", "BX": "XLF", "APO": "XLF", "CRCL": "XLF",
    # Industrials, aerospace & defense, space
    "RKLB": "XLI", "ASTS": "XLC", "LUNR": "XLI", "ACHR": "XLI", "JOBY": "XLI", "RDW": "XLI",
    "VRT": "XLI", "ETN": "XLI", "GE": "XLI", "GEV": "XLI", "CAT": "XLI", "DE": "XLI",
    "BA": "XLI", "LMT": "XLI", "NOC": "XLI", "RTX": "XLI", "GD": "XLI", "HON": "XLI",
    "PWR": "XLI", "AXON": "XLI", "KTOS": "XLI", "AVAV": "XLI", "UPS": "XLI", "FDX": "XLI",
    # Energy, nuclear & power
    "CCJ": "XLE", "UEC": "XLE", "LEU": "XLE", "OXY": "XLE", "EOG": "XLE", "FSLR": "XLE",
    "ENPH": "XLE", "BE": "XLI", "OKLO": "XLU", "SMR": "XLU", "NNE": "XLU", "CEG": "XLU",
    "VST": "XLU", "TLN": "XLU", "NEE": "XLU", "NRG": "XLU", "DUK": "XLU", "SO": "XLU",
    # Healthcare
    "HIMS": "XLV", "TEM": "XLV", "RXRX": "XLV", "ISRG": "XLV", "ILMN": "XLV", "VRTX": "XLV",
    "REGN": "XLV", "AMGN": "XLV", "GILD": "XLV", "NVO": "XLV", "TMO": "XLV", "DHR": "XLV",
    "ABT": "XLV", "MDT": "XLV", "CRSP": "XLV", "OSCR": "XLV",
    # Consumer
    "BABA": "XLY", "PDD": "XLY", "MELI": "XLY", "CMG": "XLY", "LULU": "XLY", "ABNB": "XLY",
    "BKNG": "XLY", "DASH": "XLY", "RIVN": "XLY", "LCID": "XLY", "NIO": "XLY", "GME": "XLY",
    "CVNA": "XLY", "CELH": "XLY", "PG": "XLY", "KO": "XLY", "PEP": "XLY",
    # Communication & media
    "ROKU": "XLC", "SPOT": "XLC", "TTD": "XLC", "RBLX": "XLC", "SNAP": "XLC", "TMUS": "XLC",
    "VZ": "XLC", "CMCSA": "XLC", "WBD": "XLC", "EA": "XLC", "TTWO": "XLC",
    # Materials
    "MP": "XLB", "FCX": "XLB", "NEM": "XLB", "ALB": "XLB", "LIN": "XLB", "USAR": "XLB",
    # Real estate / data-centre REITs
    "EQIX": "XLRE", "DLR": "XLRE", "AMT": "XLRE", "PLD": "XLRE", "O": "XLRE",
}

# Company-profile sector names (Yahoo/FMP style) → sector fund.
_NAME_TO_ETF = {
    "technology": "XLK", "information technology": "XLK",
    "communication services": "XLC", "communication": "XLC",
    "consumer cyclical": "XLY", "consumer discretionary": "XLY",
    "consumer defensive": "XLY", "consumer staples": "XLY",
    "financial services": "XLF", "financial": "XLF", "financials": "XLF",
    "healthcare": "XLV", "health care": "XLV",
    "energy": "XLE", "basic materials": "XLB", "materials": "XLB",
    "industrials": "XLI", "utilities": "XLU", "real estate": "XLRE",
}


def etf_for(sector: str | None, industry: str | None = None) -> str:
    """Sector fund for a profile's sector/industry text; '' when unknown."""
    ind = (industry or "").lower()
    if "semiconductor" in ind:
        return "SMH"
    return _NAME_TO_ETF.get((sector or "").strip().lower(), "")


def _ensure(conn) -> None:
    from database import ensure_schema

    def _create(c):
        c.execute("""CREATE TABLE IF NOT EXISTS ticker_sectors (
            ticker TEXT PRIMARY KEY, etf TEXT NOT NULL, sector TEXT, updated_at TEXT)""")
    ensure_schema(conn, "globe_sectors", ["ticker_sectors"], _create)


def builtin_map() -> dict[str, str]:
    try:
        from market_engine import _TICKER_SECTOR, SECTOR_ETFS
        m = dict(_TICKER_SECTOR)
        m.update({etf: etf for etf in SECTOR_ETFS})
    except Exception:
        m = {}
    m.update(EXTRA)
    return m


def saved_map() -> dict[str, str]:
    """Sectors the app already knows from company profiles and this table."""
    out: dict[str, str] = {}
    try:
        from database import get_db
        conn = get_db()
        try:
            try:
                for r in conn.execute("SELECT ticker, company_sector, company_industry FROM stock_data "
                                      "WHERE company_sector IS NOT NULL").fetchall():
                    etf = etf_for(r["company_sector"], r["company_industry"])
                    if etf:
                        out[str(r["ticker"]).upper()] = etf
            except Exception as exc:
                logger.debug("globe sectors: stock_data: %s", exc)
            try:
                for r in conn.execute("SELECT ticker, data_json FROM fundamentals_cache").fetchall():
                    try:
                        d = json.loads(r["data_json"])
                    except Exception:
                        continue
                    etf = etf_for(d.get("sector"), d.get("industry"))
                    if etf:
                        out.setdefault(str(r["ticker"]).upper(), etf)
            except Exception as exc:
                logger.debug("globe sectors: fundamentals_cache: %s", exc)
            try:
                _ensure(conn)
                for r in conn.execute("SELECT ticker, etf FROM ticker_sectors").fetchall():
                    out.setdefault(str(r["ticker"]).upper(), r["etf"])
            except Exception as exc:
                logger.debug("globe sectors: ticker_sectors: %s", exc)
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("globe sectors: %s", exc)
    return out


def sector_map() -> dict[str, str]:
    """Everything known: saved profiles first, built-ins win on conflict
    (the built-in map is hand-checked; e.g. semis vs. 'Technology')."""
    m = saved_map()
    m.update(builtin_map())
    return m


_TRIED: set[str] = set()
_LOCK = threading.Lock()


def resolve_unknown_async(tickers, known: dict[str, str] | None = None, limit: int = 40) -> int:
    """Look up sectors for tickers not yet known, in a daemon thread.
    Each ticker is tried once per process. Returns how many were queued."""
    known = known if known is not None else sector_map()
    todo = []
    with _LOCK:
        for t in tickers or []:
            u = str(t or "").upper().strip()
            if not u or u in known or u in _TRIED or not u.replace(".", "").replace("-", "").isalnum():
                continue
            _TRIED.add(u)
            todo.append(u)
            if len(todo) >= limit:
                break
    if not todo:
        return 0

    def _run(batch):
        try:
            from market_engine import get_sector_for_ticker_bg
        except Exception:
            return
        from database import get_db
        for t in batch:
            try:
                etf, name = get_sector_for_ticker_bg(t)
            except Exception:
                etf, name = "", ""
            if not etf:
                continue
            conn = get_db()
            try:
                _ensure(conn)
                conn.execute(
                    "INSERT INTO ticker_sectors (ticker, etf, sector, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(ticker) DO UPDATE SET etf = excluded.etf, sector = excluded.sector, "
                    "updated_at = excluded.updated_at",
                    (t, etf, name, datetime.now(timezone.utc).isoformat()))
                conn.commit()
            except Exception as exc:
                logger.debug("globe sectors: save %s: %s", t, exc)
            finally:
                conn.close()

    threading.Thread(target=_run, args=(todo,), daemon=True, name="globe_sectors").start()
    return len(todo)
