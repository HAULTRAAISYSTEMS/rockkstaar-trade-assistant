# Wealth Architect — implementation plan

Long-term investing workspace: "Where should my next investment dollar go?"
Built beside the trading tools; nothing existing is replaced.

## What already exists and is reused

| Need | Existing piece |
|---|---|
| Live prices | `market_engine.live_quotes` (Schwab → Alpaca → Yahoo shared feed); `stock_data.current_price` as a dated fallback |
| Daily history (splits/dividends adjusted) | `market_data.fetch_chart_bars(ticker, "1d", range)` |
| Sector / industry | `fundamentals_cache.data_json` (`sector`, `industry`) |
| Financial gate inputs | `fundamentals_engine.get_fundamentals`, `quarter_facts` (SEC companyfacts), 10-Q drill checks, PEG, 5-year P/E range |
| Buy zones | Value zones (Load / Accumulate / Fair / Pricey) from `thesis.zone_summary`, persisted in `value_zones_cache` |
| Thesis rules | `quarter_thesis` (break rules, streaks over saved quarters) |
| Broker data | `schwab.get_account_summary` (positions, average price, cash) — authorized OAuth integration; used for **reconciliation**, never presented as a transaction sync |
| Auth / users | `current_user()`; every Wealth Architect row carries `user_id` and every query filters on it |
| Pattern | Flask blueprint (`create_*_blueprint(current_user=...)`) registered in `web_app.py` |

## Stages (each a separate, mergeable PR)

1. **Ledger + Dashboard + Cost Basis Lab**
   - Tables `wa_portfolios`, `wa_transactions`.
   - Pure lot engine (`wealth_ledger.py`): buys, sells (FIFO lots, short/long-term realized), fees, dividends, reinvested dividends, interest, withholding, splits (ratio or shares received), transfers in/out, deposits/withdrawals, zero-share and oversell handling.
   - Manual entry, Schwab CSV import (preview → commit, duplicate-safe), reconciliation against the Schwab connection when connected.
   - Dashboard: market value, cost basis, unrealized/realized, dividends, cash, concentration, sector exposure; every price shows its source and time, stale prices are flagged, missing prices are excluded and listed — never filled in.
   - Cost Basis Lab (`wealth_calc.simulate_purchase`), with an explicit note that a lower average cost does not raise expected return: the purchase's own outcome depends only on its own price.
2. **Scoreboard**: XIRR (money-weighted), time-weighted return, annualized return, drawdowns, S&P 500 (SPY) comparison, growth over time; contributions shown separately from gains.
3. **M.C.F.F. engine**: Moat, CEO, Future scored by the investor (AI drafts allowed, always labeled "interpretation"); Financials scored from SEC/fundamentals data with source links and as-of dates. BUY ZONE only when all four gates pass and the price is inside the investor's buy zone.
4. **Next Dollar engine**: ranks the investor's own candidates (and "hold cash") against their gates, buy zones, concentration and sector overlap; conservative/base/optimistic scenarios use explicit, editable growth and multiple assumptions. Decision support, not advice.
5. **Seasonal Planner**: monthly/seasonal contributions per portfolio, one-time deposits, annual goals, IRA limits from a dated table (2027: $7,500, $1,100 catch-up 50+, per IRS IR-2026-136), Jan 1–Apr 15 prior-year designation, progress and cash reserve.

## Guardrails
- No placeholder value is shown as live; every number states where it came from.
- No buy signal from a falling price or a falling average cost.
- No price above the investor's buy zone is called a buy.
- Portfolios and schedules are user data; nothing personal is hard-coded.
