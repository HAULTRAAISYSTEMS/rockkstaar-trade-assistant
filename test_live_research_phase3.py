import unittest
from unittest.mock import patch

try:
    from flask import Flask, session
    import stock_research_integration as integration
    from live_research_routes import create_live_research_blueprint
    HAS_FLASK=True
except ImportError:
    HAS_FLASK=False


@unittest.skipUnless(HAS_FLASK,'Flask not installed in execution environment')
class StockResearchIntegrationTests(unittest.TestCase):
    def test_stock_page_is_augmented_without_replacing_existing_content(self):
        app=Flask(__name__); app.secret_key='test'
        @app.get('/stock/<ticker>')
        def stock(ticker): return '<html><head></head><body><main><nav><a>Overview</a></nav><div id="legacy">Legacy stock detail</div></main></body></html>'
        integration.install_stock_research(app)
        response=app.test_client().get('/stock/NVDA')
        text=response.get_data(as_text=True)
        self.assertIn('Legacy stock detail',text)
        self.assertIn('id="stock-research-panel"',text)
        self.assertIn('data-ticker="NVDA"',text)
        self.assertIn('href="#stock-research-panel">Research</a>',text)
        self.assertIn('/static/js/stock_research.js',text)

    def test_non_stock_page_is_untouched(self):
        app=Flask(__name__); app.secret_key='test'
        @app.get('/other')
        def other(): return '<html><head></head><body><main>Other</main></body></html>'
        integration.install_stock_research(app)
        text=app.test_client().get('/other').get_data(as_text=True)
        self.assertNotIn('stock-research-panel',text)

    def test_ticker_api_uses_published_query_and_returns_alert_state(self):
        app=Flask(__name__); app.secret_key='test'; app.config['TESTING']=True
        def current_user(): return {'id':7,'is_admin':0}
        def admin(fn): return fn
        app.register_blueprint(create_live_research_blueprint(require_admin=admin,current_user=current_user,tracked_tickers=lambda uid:[]))
        published=[{'id':'p1','ticker':'NVDA','status':'published','metrics':[],'saved':False}]
        with patch('live_research_routes.svc.list_published',return_value=published) as query, patch('live_research_routes.svc.get_alert_preferences',return_value={'NVDA':True}):
            data=app.test_client().get('/api/live-research/posts?ticker=NVDA').get_json()
            self.assertEqual(data['posts'],published)
            self.assertTrue(data['alert_prefs']['NVDA'])
            self.assertEqual(query.call_args.kwargs['ticker'],'NVDA')

if __name__=='__main__': unittest.main()
