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

# --- Catalyst classification --------------------------------------------------
# Regression: the keyword lists only matched very literal phrasings, so common
# wire-service wording fell through and was dropped as low_importance.
# Baseline before this fix was 2/20 on the headlines below.

import pytest

CATALYST_HEADLINES = [
    ("guidance_raise", "Microsoft lifts full-year guidance"),
    ("guidance_raise", "Broadcom boosts guidance for fiscal 2026"),
    ("guidance_raise", "Costco hikes full-year outlook"),
    ("guidance_raise", "Salesforce raises FY27 revenue forecast"),
    ("guidance_raise", "Delta upgrades its full-year profit outlook"),
    ("guidance_cut", "Nike trims full-year guidance"),
    ("guidance_cut", "Intel slashes outlook for the year"),
    ("guidance_cut", "FedEx cuts full-year forecast"),
    ("guidance_cut", "Target warns on full-year profit"),
    ("earnings_beat", "Nvidia tops revenue estimates"),
    ("earnings_beat", "Alphabet reports better-than-expected results"),
    ("earnings_miss", "Boeing posts wider-than-expected loss"),
    ("earnings_miss", "Ford results come in light"),
    ("acquisition_merger", "Pfizer to acquire biotech in $4 billion deal"),
    ("acquisition_merger", "Chevron strikes deal for Hess"),
    ("fda", "FDA approves Lilly's obesity drug"),
    ("analyst_upgrade", "Morgan Stanley double-upgrades Tesla"),
    ("analyst_downgrade", "Goldman cuts Apple to neutral"),
    ("partnership_deal", "Palantir and Boeing announce partnership"),
    ("government_contract", "Lockheed awarded $2B Army contract"),
]

@pytest.mark.parametrize("expected,headline", CATALYST_HEADLINES)
def test_common_wire_phrasings_are_classified(expected, headline):
    assert d.classify(headline, "") == expected

@pytest.mark.parametrize("headline", [
    "Top stocks to watch after the bell",
    "3 stocks to buy right now",
    "Better buy: Apple vs Microsoft",
    "Stock market today: Dow rises 200 points",
    "Why these stocks are moving",
    "Market roundup: energy leads gains",
    "The company upgraded its data center servers",
    "Shareholder alert: law firm investigation on behalf of investors",
    "Five things to know before the open",
])
def test_noise_headlines_are_not_ingested(headline):
    """Broadening the vocabulary must not start admitting filler."""
    assert d.importance(d.classify(headline, ""), headline) <= 0

def test_highest_weight_catalyst_wins_when_several_fire():
    """'upgrades its full-year outlook' is both analyst-ish and a guidance raise.
    Guidance (weight 4) must beat analyst upgrade (weight 3)."""
    assert d.classify("Delta upgrades its full-year profit outlook", "") == "guidance_raise"

def test_product_upgrade_is_not_an_analyst_upgrade():
    """Bare 'upgrade' used to fire on infrastructure news."""
    assert d.classify("The company upgraded its data center servers", "") != "analyst_upgrade"
