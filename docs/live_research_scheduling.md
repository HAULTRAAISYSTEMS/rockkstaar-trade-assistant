# Live Research scheduling

Phase 6 left production polling as a deployment step:

> This module is deliberately ingestion logic, not a scheduler. Production polling
> should be invoked by an external scheduler/Render Cron Job rather than a
> scheduler inside the Flask web process.

That step was never completed, so `live_research_runner.py` had never run in
production and the feed never updated. This document covers the scheduler and
the optional auto-publish gate.

## Stage one — scheduled ingestion into the review queue

`render.yaml` declares a `live-research-runner` cron job on a `*/15 * * * *`
schedule (UTC). Each run:

1. `discover_market_news()` — market-wide catalyst sweep across configured providers.
2. `fetch_headlines(ticker)` for each ticker in `LIVE_RESEARCH_TICKERS`.
3. `ingest(...)` — creates `research_posts` rows with status `incoming`.
4. `autopublish.auto_publish(...)` — a no-op unless explicitly enabled.

Items land in the admin review queue. The public feed renders only
`list_published(...)`, so **nothing appears until an admin approves it**. Bulk
approve handles up to 100 posts per action.

### Why not a thread inside the web process

`Procfile` runs gunicorn with `--max-requests 100 --max-requests-jitter 20`, which
recycles the worker roughly every 80–120 requests. A background thread would be
killed and restarted on an unpredictable cadence, producing duplicated or skipped
sweeps. A separate cron service has its own lifecycle and its failures are
isolated from request serving.

### Deploying it

Either commit `render.yaml` and connect it as a Blueprint, or create the cron job
by hand in the dashboard:

- **Type:** Cron Job
- **Schedule:** `*/15 * * * *`
- **Build:** `pip install -r requirements.txt`
- **Start:** `python live_research_runner.py`

The blueprint declares only the cron job. Render leaves undeclared services
alone, so the existing web service keeps its dashboard configuration.

`DATABASE_URL` **must** point at the same database as the web service — use
"Add from database" rather than pasting a connection string. A cron job with its
own database writes rows nobody ever sees.

The runner requires at least one admin user to exist (`_admin_actor` raises
otherwise); ingested posts are attributed to the lowest-id admin.

### Verifying

Run it locally first:

```bash
python live_research_runner.py --tickers NVDA,AAPL
```

It prints a JSON summary:

```json
{"drafts_created": 4, "duplicates": 11, "events_discovered": 22,
 "auto_published": 0, "auto_publish_enabled": false, "provider_failures": []}
```

`duplicates` climbing while `drafts_created` stays near zero is the healthy
steady state — the fingerprint check is doing its job. `provider_failures` names
the provider and exception type per failure; one failing provider never aborts
the run.

## Stage two — the auto-publish gate (default OFF)

`live_research_autopublish.py` allows a narrow class of item to skip review. It
is inert unless `LIVE_RESEARCH_AUTO_PUBLISH` is set to `1`/`true`/`yes`/`on`.

### What it will and will not publish

An item must pass **every** check:

| Check | Requirement |
|---|---|
| Status | `incoming` |
| Priority | `Critical` or `High` |
| Catalyst | `8-K`, `10-Q`, `10-K`, or `SEC FILING` |
| Source host | `sec.gov` (or a subdomain) |
| Provenance | carries an `[ingestion:...]` fingerprint marker |
| Ticker | resolves via `normalize_ticker` |
| Attribution | headline and source name both present |
| Origin | not AI-generated (`take_origin != 'ai'`) |
| Notification | `should_notify` is false |
| Freshness | within `LIVE_RESEARCH_AUTO_PUBLISH_MAX_AGE_HOURS` (default 6) |

Per-run output is capped by `LIVE_RESEARCH_AUTO_PUBLISH_LIMIT` (default 10) so a
provider glitch cannot flood the feed. Every check is deny-by-default: an
unexpected row shape returns `evaluation_error:<Type>` and the item stays queued.

### Why only SEC primary sources

`priority_level()` in `live_research_ingestion.py` derives Critical/High from
catalyst type and source kind. That measures **importance, not accuracy** — a
malformed merger headline still scores Critical. Priority alone can't gate
publication.

An 8-K or Form 4 on `sec.gov` is different in kind: a filed fact rather than an
interpretation of one. Reviewing it adds little. A Reuters or Bloomberg headline
is journalism and gets reviewed, which is why no wire service appears in
`_PRIMARY_HOSTS`. Adding one defeats the gate.

### Audit trail

Auto-published rows get `\n[auto-published:primary-source]` appended to
`research_notes` and `reviewed_by_user_id` set to the runner's admin. An
auto-publication is therefore always distinguishable from a human approval.

### Recommended rollout

1. Run stage one for about a week.
2. Check queue volume and how many items are SEC-primary. If review is a
   two-minute morning pass, stop here — the gate buys little.
3. If volume is genuinely heavy, set `LIVE_RESEARCH_AUTO_PUBLISH=1` and watch what
   posts for a few days with `LIVE_RESEARCH_AUTO_PUBLISH_LIMIT` low.

## Guardrails this does not touch

The Phase 5/6 boundaries remain intact and their tests still pass:

- `live_research_ingestion` exposes no `publish_post` or `announce_published`.
- `tradestaar_take` exposes no `publish_take` or `announce_published`.
- AI takes are always drafts with notifications off, and are explicitly excluded
  from auto-publication.
- `research_feed.publish_post` still asserts admin and still accepts only `draft`
  rows.

The gate is an additional, opt-in path for provider-ingested regulatory filings —
not a removal of the review boundary.
