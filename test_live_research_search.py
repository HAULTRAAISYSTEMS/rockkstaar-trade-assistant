import live_research_search as s

class Conn:
    def __init__(self,row=None): self.row=row; self.committed=False
    def execute(self,*a,**k):
        row=self.row
        class C:
            def fetchone(self): return row
        return C()
    def commit(self): self.committed=True
    def rollback(self): pass
    def close(self): pass

class News:
    source='finnhub'
    articles=({'headline':'Marvell reports earnings beat','summary':'Revenue exceeded consensus estimates.','url':'https://example.com/mrvl','source':'Provider','published_at':'2026-08-27T20:00:00+00:00'},)

def test_ticker_lookup(monkeypatch):
    monkeypatch.setattr(s,'_edgar_cik',lambda t:('0001','Marvell Technology, Inc.') if t=='MRVL' else (None,None))
    assert s.resolve_company('MRVL')==('MRVL','Marvell Technology, Inc.')

def test_company_name_lookup(monkeypatch):
    import fundamentals_engine as fe
    fe._edgar_tickers_cache={'1':{'ticker':'MRVL','title':'Marvell Technology, Inc.'}}
    monkeypatch.setattr(s,'_edgar_cik',lambda t:('0001','Apple Inc.') if t=='AAPL' else (None,None))
    assert s.resolve_company('Marvell Technology')[0]=='MRVL'

def test_no_result_handling(monkeypatch):
    monkeypatch.setattr(s,'resolve_company',lambda q:(None,None))
    assert s.search('not a company')['results']==[]

def test_malformed_provider_response_skipped(monkeypatch):
    monkeypatch.setattr(s,'fetch_headlines',lambda t:type('N',(),{'source':'x','articles':({'headline':'x'},None)})())
    assert s._provider_items('MRVL','Marvell')==[]

def test_duplicate_detection_shown(monkeypatch):
    monkeypatch.setattr(s,'resolve_company',lambda q:('MRVL','Marvell'))
    monkeypatch.setattr(s,'_provider_items',lambda *a:[s.ProviderItem('x','1','MRVL','Marvell','Earnings','Provider','https://example.com/x',facts=('fact',))])
    monkeypatch.setattr(s,'_sec_items',lambda *a:[])
    out=s.search('MRVL',Conn({'id':'p1','status':'draft','headline':'Earnings'}))
    assert out['results'][0]['existing']['id']=='p1'

def test_create_is_draft_only_and_existing_is_not_duplicated(monkeypatch):
    monkeypatch.setattr(s,'resolve_company',lambda q:('MRVL','Marvell'))
    conn=Conn({'id':'p1','status':'published','headline':'Existing'})
    called=[]; monkeypatch.setattr(s,'create_suggestion',lambda *a,**k:called.append(1))
    out=s.create_draft_from_result({'ticker':'MRVL','source_url':'https://example.com/x','headline':'x','source_name':'Provider','summary':'fact'}, {'id':1,'is_admin':True}, conn)
    assert out['status']=='existing' and called==[]

def test_new_result_delegates_to_phase6_draft_boundary(monkeypatch):
    monkeypatch.setattr(s,'resolve_company',lambda q:('MRVL','Marvell'))
    conn=Conn(None); captured=[]
    def create(item,actor,db,**kwargs): captured.append((item,kwargs)); return {'status':'draft','post_id':'d1'}
    monkeypatch.setattr(s,'create_suggestion',create)
    out=s.create_draft_from_result({'ticker':'MRVL','source_url':'https://example.com/x','headline':'MRVL earnings','source_name':'Provider','summary':'verified fact','category':'Earnings'}, {'id':1,'is_admin':True}, conn)
    assert out['status']=='draft' and captured[0][0].ticker=='MRVL' and captured[0][1]['as_draft'] is True and conn.committed
