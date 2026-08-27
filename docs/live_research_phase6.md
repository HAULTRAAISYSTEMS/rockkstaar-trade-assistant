# Live Research Phase 6 — Provider-Assisted Intelligence

Phase 6 converts verified provider/primary-source information into admin-reviewable research drafts.

## Safety boundary

- Ingestion has no publish, realtime-announcement, or notification API.
- Every provider suggestion goes through `research_feed.create_draft()`.
- `take_origin=provider`, `status=draft`, and `should_notify=false` are enforced for ingestion suggestions.
- Optional Phase 5 Tradestaar Take generation creates a separate AI draft through `generate_take_draft()`.
- Provider or AI failure cannot publish content.
- An authenticated admin must use the existing explicit publication path later.

## Sources reused

Phase 6 intentionally does not introduce a paid provider. It provides adapters for normalized metadata already returned by Tradestaar's existing `news_fetcher` (Finnhub first when `FINNHUB_API_KEY` is configured) and is designed for SEC/company Investor Relations normalized items. Existing SEC request pacing and `SEC_USER_AGENT` remain in `smart_money.py`.

Primary sources should be preferred for earnings facts. News-provider summaries are discovery/secondary evidence and must not be used to invent missing earnings values.

## Earnings

`earnings_metrics()` accepts explicit reported/estimate/previous values and returns the existing Live Research structured metric format. Missing numbers remain `None`; beat/miss/inline is calculated only when both actual and expected values are supplied.

Additional material metrics (guidance, segment results, margins, growth, buybacks/dividends) can be supplied through the existing validated `metrics` list. No absent value is inferred.

## Idempotency

Each provider item receives a SHA-256 fingerprint from provider + stable external ID + source URL. The marker is stored in the private draft notes. Reprocessing the same item returns `duplicate` instead of creating another suggestion.

## Production scheduling

This module is deliberately ingestion logic, not a scheduler. Production polling should be invoked by an external scheduler/Render Cron Job rather than a scheduler inside the Flask web process. Provider-specific network polling entry points should be reviewed before deployment and must respect provider rate limits.

## Configuration

No new paid service or credential is introduced in Phase 6 core. Existing configuration remains relevant:

- `FINNHUB_API_KEY` — existing Finnhub news source (optional, but recommended for fast provider news)
- `SEC_USER_AGENT` — existing SEC identification (recommended/required operationally for SEC requests)
- `TRADESTAAR_TAKE_API_KEY` — required only when optional AI Tradestaar Take generation is enabled
- `TRADESTAAR_TAKE_MODEL` — optional
- `TRADESTAAR_TAKE_BASE_URL` — optional

No production migration is added by Phase 6; it reuses the Phase 1 Live Research tables.
