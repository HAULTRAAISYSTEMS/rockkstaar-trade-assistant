import sqlite3
import unittest

import research_feed as rf
import research_feed_phase2 as svc
import live_research_realtime as realtime

ADMIN={'id':1,'is_admin':1}

def db():
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
    c.executescript('''
    CREATE TABLE research_posts(id TEXT PRIMARY KEY,ticker TEXT,company_name TEXT,headline TEXT,research_notes TEXT,category TEXT,sentiment TEXT,source_name TEXT,source_url TEXT,tradestaar_take TEXT,take_origin TEXT,status TEXT,should_notify INTEGER,notification_status TEXT,author_user_id INTEGER,created_at TEXT,updated_at TEXT,published_at TEXT,priority TEXT DEFAULT 'Medium',catalyst_type TEXT DEFAULT 'BREAKING',source_published_at TEXT,reviewed_at TEXT,reviewed_by_user_id INTEGER);
    CREATE TABLE research_metrics(id TEXT PRIMARY KEY,post_id TEXT,metric_type TEXT,label TEXT,actual_value REAL,expected_value REAL,previous_value REAL,unit TEXT,period TEXT,comparison TEXT,notes TEXT,sort_order INTEGER);
    CREATE TABLE research_saved_posts(user_id INTEGER,post_id TEXT,saved_at TEXT);
    CREATE TABLE research_alert_preferences(user_id INTEGER,ticker TEXT,enabled INTEGER,created_at TEXT,updated_at TEXT);
    ''');return c

def payload(ticker='NVDA',origin='manual'):
    return {'ticker':ticker,'company_name':'NVIDIA','headline':'Research update','research_notes':'Material update for testing.','category':'Breaking News','sentiment':'Bullish','take_origin':origin}

class Phase4RealtimeTests(unittest.TestCase):
    def test_incremental_feed_excludes_drafts(self):
        c=db(); draft=rf.create_draft(payload('NVDA','provider'),ADMIN,conn=c); pub=rf.create_draft(payload('AMD'),ADMIN,conn=c); rf.publish_post(pub,ADMIN,conn=c)
        posts=realtime.list_incremental(user_id=1,conn=c)
        self.assertEqual([pub],[p['id'] for p in posts]); self.assertNotIn(draft,[p['id'] for p in posts])

    def test_incremental_since_returns_only_newer_published_rows(self):
        c=db(); first=rf.create_draft(payload('AMD'),ADMIN,conn=c); rf.publish_post(first,ADMIN,conn=c)
        c.execute("UPDATE research_posts SET published_at='2026-01-01T00:00:00+00:00' WHERE id=?",(first,));c.commit()
        second=rf.create_draft(payload('NVDA'),ADMIN,conn=c); rf.publish_post(second,ADMIN,conn=c)
        c.execute("UPDATE research_posts SET published_at='2026-01-02T00:00:00+00:00' WHERE id=?",(second,));c.commit()
        posts=realtime.list_incremental(since='2026-01-01T12:00:00+00:00',user_id=1,conn=c)
        self.assertEqual([second],[p['id'] for p in posts])

    def test_announcement_contains_no_research_body(self):
        event=realtime.announce_published('abc',ticker='NVDA')
        self.assertEqual('research.published',event['type']);self.assertEqual('abc',event['post_id']);self.assertNotIn('research_notes',event);self.assertNotIn('tradestaar_take',event)

    def test_provider_draft_does_not_announce_itself(self):
        c=db(); post=rf.create_draft(payload('NVDA','provider'),ADMIN,conn=c)
        rows=realtime.list_incremental(user_id=1,conn=c)
        self.assertFalse(rows);self.assertEqual('draft',dict(c.execute('SELECT status FROM research_posts WHERE id=?',(post,)).fetchone())['status'])

if __name__=='__main__': unittest.main()
