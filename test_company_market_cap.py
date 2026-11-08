"""Currency and unit regressions for the overview market-cap panel."""
import unittest
from company_market_cap import market_cap


class MarketCapTests(unittest.TestCase):
    def test_provider_millions_become_full_value(self):
        result = market_cap({'marketCapitalization': 200000, 'currency': 'usd'})
        self.assertEqual(result['value'], 200_000_000_000)
        self.assertEqual(result['currency'], 'USD')
        self.assertEqual(result['size_band'], 'Mega cap')

    def test_band_boundaries(self):
        for millions, band in [(50,'Micro cap'),(300,'Small cap'),(2000,'Mid cap'),(10000,'Large cap'),(199999,'Large cap')]:
            self.assertEqual(market_cap({'marketCapitalization':millions,'currency':'USD'})['size_band'],band)

    def test_foreign_currency_never_gets_usd_classification(self):
        result=market_cap({'marketCapitalization':61800000,'currency':'TWD'})
        self.assertEqual(result['currency'],'TWD')
        self.assertIsNone(result['size_band'])

    def test_invalid_values_and_unknown_currency_are_unavailable(self):
        for value in [None,0,-1,'bad',float('inf'),float('nan')]:
            self.assertFalse(market_cap({'marketCapitalization':value,'currency':'USD'})['available'])
        self.assertFalse(market_cap({'marketCapitalization':1000})['available'])
