"""Offline regression coverage; runnable with Python's standard unittest."""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import telegram_policy as policy
from migrations.m0008_telegram_policy import upgrade


class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, timeout=10)
        self.conn.row_factory = sqlite3.Row
    def execute(self, *args):
        return self.conn.execute(*args)
    def commit(self):
        self.conn.commit()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.conn.close()


class TelegramPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = self.tmp.name + '/test.db'
        self.patch = patch.object(policy, '_db', lambda: DB(path))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        env = patch.dict(os.environ, TELEGRAM_BOT_TOKEN='test', TELEGRAM_CHAT_ID='test')
        env.start()
        self.addCleanup(env.stop)
        with policy._db() as conn:
            upgrade(conn)
            conn.commit()
        self.transport = patch.object(policy, '_transport', return_value=True)
        self.send = self.transport.start()
        self.addCleanup(self.transport.stop)

    def test_persistent_identity_and_cap(self):
        self.assertTrue(policy.send_once('a', 'message', '2026-09-16', 'setup', 1))
        self.assertFalse(policy.send_once('a', 'message', '2026-09-17', 'setup', 1))
        self.assertFalse(policy.send_once('b', 'message', '2026-09-16', 'setup', 1))
        self.assertTrue(policy.send_once('b', 'message', '2026-09-17', 'setup', 1))
        self.assertEqual(self.send.call_count, 2)

    def test_failed_send_is_retryable(self):
        self.send.return_value = False
        self.assertFalse(policy.send_once('a', 'm', '2026-09-16', 'setup'))
        self.send.return_value = True
        self.assertTrue(policy.send_once('a', 'm', '2026-09-16', 'setup'))

    def test_concurrent_workers_send_once(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: policy.send_once('same', 'm', '2026-09-16', 'setup'), range(4)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.send.call_count, 1)

    def test_setup_scope_and_noise_filters(self):
        def opp(ticker, score, **extra):
            return dict(ticker=ticker, scan_score=score, above_vwap=True, breaking_high=True,
                        vwap=99, intraday_high=102, primary_tag='BREAKOUT', reason='Volume', **extra)
        now = datetime(2026,9,16,10,tzinfo=ZoneInfo('America/New_York'))
        policy.send_setups([opp('WATCH',7), opp('OTHER',7),opp('EXCEPTIONAL',10),opp('LOW',4)], {'WATCH'}, now)
        self.assertEqual(self.send.call_count,2)
        policy.send_setups([opp('WATCH',10),opp('EXCEPTIONAL',10)], {'WATCH'}, now)
        self.assertEqual(self.send.call_count,2)
        policy.send_setups([opp('NEW',10)],set(),now.replace(hour=18))
        self.assertEqual(self.send.call_count,2)
        policy.send_setups([opp('NEW',10),opp('NEW2',10)],set(),now)
        self.assertEqual(self.send.call_count,3)

    def engine(self, now):
        return SimpleNamespace(_et_now=lambda:now,
            fetch_economic_calendar=lambda:[{'date':'2026-09-21','event':'Test <macro>','impact':'HIGH','is_today':False}],
            fetch_earnings_calendar=lambda **kw:{'coming_up':[{'ticker':'WATCH','date':'2026-09-22'}]},
            fetch_dividends=lambda:[{'ticker':'WATCH','ex_date':'2026-09-23'}],
            fetch_stock_splits=lambda:[{'ticker':'OTHER','eff_date':'2026-09-24'}],
            fetch_market_news=lambda:[], fetch_macro_environment=lambda:{})

    @patch.object(policy, 'watchlist', return_value={'WATCH'})
    def test_sunday_calendar_once_and_no_standalone_dividends(self, _):
        now = datetime(2026,9,20,17,tzinfo=ZoneInfo('America/New_York'))
        self.assertEqual(policy.check_intel(self.engine(now)),[])
        engine=self.engine(now.replace(hour=18))
        self.assertEqual(policy.check_intel(engine),[{'type':'weekly'}])
        self.assertEqual(policy.check_intel(engine),[])
        message=self.send.call_args.args[0]
        self.assertIn('WATCH',message)
        self.assertNotIn('OTHER',message)
        self.assertIn('&lt;macro&gt;',message)
        self.assertEqual(self.send.call_count,1)

    @patch.object(policy, 'watchlist', return_value={'WATCH'})
    def test_weekday_catchup_and_grouped_macro(self, _):
        engine=self.engine(datetime(2026,9,21,7,tzinfo=ZoneInfo('America/New_York')))
        engine.fetch_economic_calendar=lambda:[{'date':'2026-09-21','event':e,'impact':'HIGH','is_today':True} for e in ['CPI','FOMC']]
        results=policy.check_intel(engine)
        self.assertEqual([r['type'] for r in results],['weekly','economic'])
        self.assertEqual(policy.check_intel(engine),[])
        self.assertIn('CPI',self.send.call_args.args[0])
        self.assertIn('FOMC',self.send.call_args.args[0])

    @patch.object(policy, 'watchlist', return_value={'WATCH'})
    def test_cached_calendar_date_and_extreme_conditions(self, _):
        engine = self.engine(datetime(2026,9,21,7,tzinfo=ZoneInfo('America/New_York')))
        # The source cached is_today=False before midnight, but date is today.
        engine.fetch_macro_environment = lambda: {'vix_level':36, 'yield_change_bps':16}
        results = policy.check_intel(engine)
        self.assertEqual([r['type'] for r in results], ['weekly','economic','volatility'])
        self.assertEqual(policy.check_intel(engine), [])

    @patch.object(policy, 'watchlist', return_value={'WATCH'})
    def test_news_is_relevant_grouped_and_capped(self, _):
        engine = self.engine(datetime(2026,9,16,10,tzinfo=ZoneInfo('America/New_York')))
        engine.fetch_market_news = lambda: [
            {'ticker':'UNRELATED','impact':'CRITICAL','headline':'Unrelated acquisition'},
            {'ticker':'WATCH','impact':'HIGH','headline':'Watchlist <event>'},
            {'ticker':'SPY','impact':'CRITICAL','headline':'Broad market event'}]
        policy.check_intel(engine)
        message = self.send.call_args.args[0]
        self.assertNotIn('UNRELATED', message)
        self.assertIn('Watchlist &lt;event&gt;', message)
        self.assertIn('Broad market event', message)
        before = self.send.call_count
        engine.fetch_market_news = lambda: [{'ticker':'WATCH','impact':'HIGH','headline':'Another event'}]
        policy.check_intel(engine)
        self.assertEqual(self.send.call_count, before)


if __name__ == '__main__':
    unittest.main()
