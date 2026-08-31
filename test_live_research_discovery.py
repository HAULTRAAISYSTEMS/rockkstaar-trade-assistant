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

# --- Ticker resolution across provider shapes -------------------------------
# Regression: Finnhub's market-news feed supplies the symbol in `related`, not
# `ticker`. resolve_ticker() read only ticker/symbol, so every Finnhub general
# news story fell through to the regex, failed, and was counted as "malformed"
# (~102 discarded per production run).

def finnhub_row(**kw):
    row={"id":1,"headline":"Nvidia reports quarterly results and earnings beat estimates",
         "summary":"Nvidia reported quarterly revenue and EPS above estimates.",
         "url":"https://news.example/nvda","published_at":now(),"source":"Reuters","related":"NVDA"}
    row.update(kw); return row

def test_finnhub_related_field_resolves_ticker():
    assert d.resolve_ticker(finnhub_row())=="NVDA"

def test_finnhub_story_is_ingested_not_discarded():
    items,stats=d.discover_market_news(fetchers=(("finnhub-market",lambda:[finnhub_row()]),))
    assert [x.ticker for x in items]==["NVDA"]
    assert stats["malformed"]==0 and stats["no_ticker"]==0

def test_related_takes_first_well_formed_symbol():
    assert d.resolve_ticker(finnhub_row(related="NVDA,AMD,INTC"))=="NVDA"
    assert d.resolve_ticker(finnhub_row(related=",,AMD"))=="AMD"

def test_related_as_list_is_supported():
    assert d.resolve_ticker(finnhub_row(related=["AMD","NVDA"]))=="AMD"
    assert d.resolve_ticker(finnhub_row(related=None,tickers=["MSFT"]))=="MSFT"

def test_explicit_ticker_field_still_wins():
    assert d.resolve_ticker(finnhub_row(ticker="AAPL",related="NVDA"))=="AAPL"

def test_garbage_related_value_does_not_resolve():
    for junk in ("", "   ", "not-a-symbol-at-all", "TOOLONGSYMBOL", 12345):
        assert d.resolve_ticker(finnhub_row(related=junk, headline="Company reports results",
                                            summary="No symbol anywhere."))==""

def test_missing_ticker_is_reported_separately_from_malformed():
    """A story with every field but no resolvable symbol is 'no_ticker', not 'malformed'."""
    row=finnhub_row(related="",headline="Chip demand rises across the sector",
                    summary="Sector commentary with no company attached.")
    items,stats=d.discover_market_news(fetchers=(("finnhub-market",lambda:[row]),))
    assert items==[] and stats["no_ticker"]==1 and stats["malformed"]==0

def test_genuinely_unparseable_row_is_still_malformed():
    items,stats=d.discover_market_news(fetchers=(("test",lambda:[{"related":"NVDA"}]),))
    assert items==[] and stats["malformed"]==1 and stats["no_ticker"]==0
