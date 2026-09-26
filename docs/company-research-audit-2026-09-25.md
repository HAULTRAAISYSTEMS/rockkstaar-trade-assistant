# Company research accuracy audit — 2026-09-25

Scope: audit the AEIS screenshots against SEC facts and source documents, then repair shared research logic. This is not a certification of every provider value or every supported ticker.

## Verified AEIS findings

SEC company facts: https://data.sec.gov/api/xbrl/companyfacts/CIK0000927003.json

FY2025 figures (USD): revenue 1,798,800,000; consolidated net income 148,400,000; gross profit 677,400,000; operating income 168,000,000; operating cash flow 233,300,000; property/equipment purchases 107,400,000. Thus gross margin 37.7%, operating margin 9.3%, and free cash flow 125,900,000. The prior history mixed provider TTM margins into this fiscal-year row and omitted net income because the filer switched to the ProfitLoss concept.

The annual release also reports continuing-operations figures, which differ from consolidated totals. Do not treat those differences as extraction errors:
https://ir.advancedenergy.com/news/advanced-energy-reports-fourth-quarter-and-full-year-2025-results/579d65f4-6a23-471e-8f5c-ec1be349785b

The September 2024 8-K describes a term-loan prepayment and expanded revolver, with unchanged covenants. Item 2.04 alone did not justify the app's assertion of a covenant breach:
https://www.sec.gov/Archives/edgar/data/927003/000155837024012709/aeis-20240909x8k.htm

## Shared repairs

- Display the actual 40-point score scale and coverage, with proportional progress bar.
- Request quote, profile, related news and upcoming earnings for the searched ticker rather than relying on watchlist membership.
- Use one quote and its original timestamp for the header and valuation panel. Provider ratios can update separately.
- Keep annual margins separate from TTM metrics; distinguish annual period, SEC latest financial report period, and quote time.
- Merge compatible net-income concepts across years; never substitute comprehensive income for net income.
- Deduplicate quarterly amendments and require four consecutive quarters for computed TTM values.
- Describe broad 8-K categories without inventing a departure, default, or missed filing extension; disclose the actual returned-index coverage.
- Add source-linked recent SEC filings to the company's changes, and reject past calendar earnings dates.

## Limitations and verification

Profile summaries and valuation ratios remain third-party inputs, not independently verified company assertions. Earnings dates may be estimated. Missing feeds remain unavailable, never zero or proof of no developments. The recent SEC submissions index may cover less than three years; the UI now states that limitation. Related news may mention competitors and is not automatically a direct catalyst.

Regression coverage includes concept changes, annual-versus-TTM separation, missing/duplicate quarters, filing-category semantics, incomplete filing coverage, untracked tickers, quote consistency, past earnings dates and unavailable-data rendering. The full suite is run before delivery; deployment is verified separately.
