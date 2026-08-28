import sqlite3, unittest
import research_feed as rf
from tradestaar_take import generate_take_draft, build_verified_context, TakeProviderError, TakeDataError
ADMIN={'id':1,'is_admin':1}
class FakeProvider:
    name='fake'
    def __init__(self,result=None,error=None): self.result=result;self.error=error
    def generate(self,c):
        if self.error: raise self.error
        return self.result

def db():
 c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;c.executescript('''CREATE TABLE research_posts(id TEXT PRIMARY KEY,ticker TEXT,company_name TEXT,headline TEXT,research_notes TEXT,category TEXT,sentiment TEXT,source_name TEXT,source_url TEXT,tradestaar_take TEXT,take_origin TEXT,status TEXT,should_notify INTEGER,notification_status TEXT,author_user_id INTEGER,created_at TEXT,updated_at TEXT,published_at TEXT,priority TEXT DEFAULT 'Medium',catalyst_type TEXT DEFAULT 'BREAKING',source_published_at TEXT,reviewed_at TEXT,reviewed_by_user_id INTEGER);CREATE TABLE research_metrics(id TEXT PRIMARY KEY,post_id TEXT,metric_type TEXT,label TEXT,actual_value REAL,expected_value REAL,previous_value REAL,unit TEXT,period TEXT,comparison TEXT,notes TEXT,sort_order INTEGER);''');return c

def data(): return {'ticker':'NVDA','company_name':'NVIDIA','headline':'Verified update','research_notes':'Admin verified research.','category':'Earnings','sentiment':'Bullish','sources':[{'id':'earnings','label':'Company earnings release','url':'https://investor.example.com/release','facts':['Revenue was $10.0B.','Company guidance was $11.0B.']}],'metrics':[{'metric_type':'Revenue','label':'Revenue','actual_value':10,'expected_value':9,'unit':'B','comparison':'Beat'}]}
class Phase5Tests(unittest.TestCase):
 def test_ai_take_is_always_draft_and_notifications_off(self):
  c=db();r=generate_take_draft(data(),ADMIN,FakeProvider({'take':'Revenue beat the supplied estimate.','source_ids':['earnings'],'provider':'fake','model':'test'}),c);row=dict(c.execute('SELECT * FROM research_posts WHERE id=?',(r['post_id'],)).fetchone());self.assertEqual('draft',row['status']);self.assertEqual('ai',row['take_origin']);self.assertEqual(0,row['should_notify']);self.assertIsNone(row['published_at'])
 def test_provider_failure_creates_no_post(self):
  c=db()
  with self.assertRaises(TakeProviderError): generate_take_draft(data(),ADMIN,FakeProvider(error=TakeProviderError('down')),c)
  self.assertEqual(0,c.execute('SELECT COUNT(*) FROM research_posts').fetchone()[0])
 def test_missing_verified_sources_rejected_before_provider(self):
  d=data();d['sources']=[]
  with self.assertRaises(TakeDataError): build_verified_context(d)
 def test_invalid_source_url_rejected(self):
  d=data();d['sources'][0]['url']='javascript:alert(1)'
  with self.assertRaises(Exception): build_verified_context(d)
 def test_source_attribution_preserved(self):
  c=db();r=generate_take_draft(data(),ADMIN,FakeProvider({'take':'Verified summary.','source_ids':['earnings'],'provider':'fake','model':'test'}),c);self.assertEqual('earnings',r['sources'][0]['id']);self.assertEqual('https://investor.example.com/release',r['sources'][0]['url']);row=dict(c.execute('SELECT source_name,source_url FROM research_posts WHERE id=?',(r['post_id'],)).fetchone());self.assertIn('Company earnings release',row['source_name']);self.assertEqual('https://investor.example.com/release',row['source_url'])
 def test_no_auto_publish_api_exists(self):
  import tradestaar_take as t;self.assertFalse(hasattr(t,'publish_take'));self.assertFalse(hasattr(t,'announce_published'))
 def test_unknown_provider_source_id_is_rejected(self):
  c=db()
  with self.assertRaises(TakeProviderError): generate_take_draft(data(),ADMIN,FakeProvider({'take':'Bad citation.','source_ids':['made-up'],'provider':'fake','model':'test'}),c)
  self.assertEqual(0,c.execute('SELECT COUNT(*) FROM research_posts').fetchone()[0])
if __name__=='__main__':unittest.main()
