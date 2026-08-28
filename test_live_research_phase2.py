import sqlite3
import unittest
import research_feed as core
import research_feed_phase2 as rf
SCHEMA="""CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT,is_admin INTEGER);CREATE TABLE research_posts(id TEXT PRIMARY KEY,ticker TEXT,company_name TEXT,headline TEXT,research_notes TEXT,category TEXT,sentiment TEXT,source_name TEXT,source_url TEXT,tradestaar_take TEXT,take_origin TEXT,status TEXT,should_notify INTEGER,notification_status TEXT,author_user_id INTEGER,created_at TEXT,updated_at TEXT,published_at TEXT,priority TEXT DEFAULT 'Medium',catalyst_type TEXT DEFAULT 'BREAKING',source_published_at TEXT,reviewed_at TEXT,reviewed_by_user_id INTEGER);CREATE TABLE research_metrics(id TEXT PRIMARY KEY,post_id TEXT,metric_type TEXT,label TEXT,actual_value REAL,expected_value REAL,previous_value REAL,unit TEXT,period TEXT,comparison TEXT,notes TEXT,sort_order INTEGER);CREATE TABLE research_saved_posts(user_id INTEGER,post_id TEXT,saved_at TEXT,PRIMARY KEY(user_id,post_id));CREATE TABLE research_alert_preferences(user_id INTEGER,ticker TEXT,enabled INTEGER,created_at TEXT,updated_at TEXT,PRIMARY KEY(user_id,ticker));"""
ADMIN={'id':1,'is_admin':1};USER={'id':2,'is_admin':0};BASE=dict(ticker='CRWD',company_name='CrowdStrike',headline='CrowdStrike reports results',research_notes='Revenue beat expectations while guidance changed.',category='Earnings',sentiment='Neutral',source_name='Company IR',source_url='https://example.com/earnings',tradestaar_take='Quarter beat; guidance is the next focus.',take_origin='manual',should_notify=True);METRICS=[{'metric_type':'revenue','label':'Revenue','actual_value':1.2,'expected_value':1.1,'unit':'B','comparison':'beat'}]
def db():
 c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;c.executescript(SCHEMA);c.execute("INSERT INTO users VALUES (1,'admin',1)");c.execute("INSERT INTO users VALUES (2,'member',0)");return c
class Phase2ServiceTests(unittest.TestCase):
 def setUp(self):self.c=db()
 def tearDown(self):self.c.close()
 def test_update_preserves_published_status_and_replaces_metrics(self):
  p=core.create_draft(BASE,ADMIN,METRICS,self.c);core.publish_post(p,ADMIN,self.c);rf.update_post(p,dict(BASE,headline='Updated'),ADMIN,[{'metric_type':'eps','label':'EPS','actual_value':1.2,'expected_value':1,'comparison':'beat'}],self.c);row=self.c.execute('SELECT status,headline FROM research_posts WHERE id=?',(p,)).fetchone();self.assertEqual((row['status'],row['headline']),('published','Updated'));self.assertEqual(self.c.execute('SELECT metric_type FROM research_metrics WHERE post_id=?',(p,)).fetchone()['metric_type'],'eps')
 def test_search_watchlist_saved_filters(self):
  p=core.create_draft(BASE,ADMIN,[],self.c);core.publish_post(p,ADMIN,self.c);n=core.create_draft(dict(BASE,ticker='NVDA',company_name='NVIDIA',headline='AI partnership'),ADMIN,[],self.c);core.publish_post(n,ADMIN,self.c);rf.set_bookmark(2,p,True,self.c);self.assertEqual(rf.list_published(search='Crowd',conn=self.c)[0]['id'],p);self.assertEqual(rf.list_published(watchlist_tickers=['NVDA'],conn=self.c)[0]['id'],n);self.assertTrue(rf.list_published(saved_by_user=2,user_id=2,conn=self.c)[0]['saved'])
 def test_alert_preferences_are_user_scoped(self):
  rf.set_alert_preference(2,'crwd',True,self.c);self.assertEqual(rf.get_alert_preferences(2,conn=self.c),{'CRWD':True});self.assertEqual(rf.get_alert_preferences(1,conn=self.c),{})
 def test_delete_requires_admin(self):
  p=core.create_draft(BASE,ADMIN,[],self.c)
  with self.assertRaises(core.ResearchPermissionError):rf.delete_post(p,USER,self.c)
  rf.delete_post(p,ADMIN,self.c);self.assertIsNone(self.c.execute('SELECT 1 FROM research_posts WHERE id=?',(p,)).fetchone())
if __name__=='__main__':unittest.main()
