# Historical Event Data Foundation

Stage 42 creates a research-only canonical event layer over existing persisted provenance. It does not alter the Production Decision Engine, publish probabilities, download mass news, or invent retrospective forecasts.

## Point-in-time policy

- `available_from` is mandatory for validated events.
- A surprise event has no pre-event countdown and becomes visible only at or after occurrence/publication.
- A scheduled event may have `days_until_scheduled_event` only after the schedule itself was known (`available_from`).
- Events without a trustworthy publication timestamp remain `manual_review` and are excluded from predictive timelines.
- Crisis episodes are explainability labels, not causal or predictive facts.

## Provenance

The first materialization reads `event_calendar` and validated `sber_events`. The source catalog records official CBR/MOEX/issuer/Fed/ECB entry points and license limitations. Bulk news ingestion is intentionally disabled.

## Commands

```bash
python -m moex_analytics.cli build-historical-event-foundation
python -m moex_analytics.cli validate-historical-events
python -m moex_analytics.cli historical-event-status
```
