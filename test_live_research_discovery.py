from datetime import datetime, timezone
import live_research_discovery as d

def now(): return datetime.now(timezone.utc).isoformat()
def event(**kw):
    row={"id":"mrvl-q2","ticker":"MRVL","company_name":"Marvell Technology","headline":"Marvell reports quarterly results and earnings beat estimates","summary":"Marvell reported quarterly revenue and EPS results.","url":"https://investor.marvell.com/q2","published_at":now(),"source":"Marvell IR","source_kind":"primary"}; row.update(kw); return row

def test_non_priority_mrvl_earnings_is_discovered():
    items,stats=d.discover_market_news(fetchers=(("test",lambda:[event()]),))
    assert [x.ticker for x in items]==["MRVL"] and items[0].category=="Earnings" and stats["tickers_resolved"]==1

def test_primary_source_beats_secondary_for_same_event():
    secondary=event(id="wire",url="https://news.example/mrvl",source="Wire",source_kind="provider")
    primary=event(id="ir",url="https://investor.marvell.com/q2",source_kind="primary")
    items,_=d.discover_market_news(fetchers=(("test",lambda:[secondary,primary]),))
    assert len(items)==1 and items[0].source_kind=="primary"

def test_low_value_generic_article_filtered():
    row=event(headline="Top stocks to watch after the bell")
    items,stats=d.discover_market_news(fetchers=(("test",lambda:[row]),))
    assert items==[] and stats["low_importance"]==1

def test_malformed_provider_data_skipped():
    items,stats=d.discover_market_news(fetchers=(("test",lambda:[{"headline":"earnings beat"}]),))
    assert items==[] and stats["malformed"]==1

def test_provider_failure_does_not_corrupt_run():
    def boom(): raise TimeoutError()
    items,stats=d.discover_market_news(fetchers=(("bad",boom),("good",lambda:[event()])) )
    assert len(items)==1 and stats["provider_failures"]==["bad:TimeoutError"]

def test_old_news_is_filtered():
    row=event(published_at="2020-01-01T00:00:00+00:00")
    items,stats=d.discover_market_news(fetchers=(("test",lambda:[row]),))
    assert items==[] and stats["stale"]==1
