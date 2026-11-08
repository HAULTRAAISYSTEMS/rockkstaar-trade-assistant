"""Normalize Finnhub profile2 capitalization (millions) without guessing currency."""
from datetime import datetime, timezone
import math
import re


def market_cap(profile):
    profile = profile if isinstance(profile, dict) else {}
    try:
        value = float(profile.get('marketCapitalization')) * 1_000_000
    except (ValueError, TypeError, OverflowError):
        value = 0
    currency = str(profile.get('currency') or '').strip().upper()
    if not math.isfinite(value) or value <= 0:
        return {'available': False, 'message': 'Market cap is unavailable from the connected source.'}
    if not re.fullmatch('[A-Z]{3}', currency):
        return {'available': False, 'message': 'Market cap is unavailable because its currency could not be verified.'}
    band = None
    if currency == 'USD':
        band = next(label for floor, label in [(200e9, 'Mega cap'), (10e9, 'Large cap'),
                     (2e9, 'Mid cap'), (300e6, 'Small cap'), (50e6, 'Micro cap'), (0, 'Nano cap')] if value >= floor)
    return {'available': True, 'value': value, 'currency': currency, 'size_band': band,
            'source': 'Finnhub company profile', 'fetched_at': datetime.now(timezone.utc).isoformat(),
            'source_url': str(profile.get('weburl') or '')}


def fetch_market_cap(ticker):
    from finnhub_ttm import _finnhub_get
    return market_cap(_finnhub_get('/stock/profile2', {'symbol': ticker}))
