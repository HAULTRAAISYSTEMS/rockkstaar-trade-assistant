"""Validation helpers shared by watchlist entry points."""

import re


_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


def parse_watchlist_symbols(raw: str | None) -> list[str]:
    """Return unique, normalized US-style symbols in the entered order."""
    symbols: list[str] = []
    seen: set[str] = set()
    for value in re.split(r"[\s,]+", (raw or "").upper().strip()):
        symbol = value.strip()
        if symbol and _SYMBOL_RE.fullmatch(symbol) and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)
    return symbols
