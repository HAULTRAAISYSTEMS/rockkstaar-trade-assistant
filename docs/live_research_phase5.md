# Tradestaar Live Research — Phase 5: Tradestaar Take

Phase 5 adds an isolated AI-provider adapter for generating an AI-assisted Tradestaar Take from admin-verified research facts.

## Safety boundary

- AI is not a source of financial facts.
- Input must contain at least one verified source with explicit facts.
- Structured metrics pass through the existing Phase 1 validation.
- Provider citations must reference only source IDs supplied by the admin-verified context.
- Generated Takes are persisted with `take_origin=ai`, `status=draft`, and `should_notify=0`.
- The adapter exposes no publish, realtime announcement, or notification operation.
- An authenticated admin must use the existing separate publication workflow after review.

## Provider isolation

`TakeProvider` is the interface. `OpenAICompatibleTakeProvider` is one implementation and can be replaced without changing research persistence or publication controls.

## Environment variables

- `TRADESTAAR_TAKE_API_KEY` — required when the HTTP AI adapter is actually used.
- `TRADESTAAR_TAKE_MODEL` — optional; defaults to `gpt-5-mini`.
- `TRADESTAAR_TAKE_BASE_URL` — optional; defaults to `https://api.openai.com/v1` and permits a compatible provider later.

No credentials belong in source control.

## Validation

Focused suite:

```bash
python -m unittest test_tradestaar_take_phase5.py
```

Then run the existing repository suite in the normal Tradestaar environment. Provider network calls are mocked/faked in focused tests and must not be required for unit testing.
