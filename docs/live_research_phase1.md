# Tradestaar Live Research Feed — Phase 1

Phase 1 is the persistence/domain foundation only. It intentionally adds no routes, templates, navigation, WebSockets, provider ingestion, AI calls, notification delivery, deployment changes, or automatic publication.

## Safety contract

- Research publication is an explicit admin-only service operation.
- `create_draft()` always writes `status='draft'`, including AI/provider-originated content.
- `list_published()` always filters `status='published'`.
- Source URLs accept only `http` and `https`.
- Ticker/category/sentiment/metric comparison values are allowlisted.
- User IDs for future bookmarks/alerts must come from authenticated server context when Phase 2 adds routes.

## Migration

`migration_runner.py` records applied versions in `schema_migrations` and applies `migrations.m0001_live_research_feed` once. The migration uses the existing database wrapper and portable SQL for SQLite/PostgreSQL.

Tables:

- `research_posts`
- `research_metrics`
- `research_saved_posts`
- `research_alert_preferences`

Phase 1 does not wire `run_migrations()` into application startup. That integration belongs to the next approved phase so this branch cannot alter a deployed database merely by importing Phase 1 code.

## Local validation

This connected GitHub environment cannot execute the repository test suite. That is an environment limitation, not a passing or failing result.

From a local checkout of `feature/live-research-feed` with the project environment installed:

```bash
python -m unittest test_research_feed.py
python -m unittest discover
```

If the project normally uses pytest, also run:

```bash
pytest -q
```

Do not point migration validation at production. For migration-runner integration testing, use a disposable/local database and required local environment variables.
