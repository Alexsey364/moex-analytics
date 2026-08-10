# Event-Conditioned Analog Intelligence

Stage 46 adds a secondary event view to historical analogs. It records which validated, PIT-safe event categories coincided with each episode and compares all, event-free and event-conditioned subsets. Coincidence is not represented as causation.

Surprise events are eligible only when `available_from` is no later than the historical analog date. Scheduled-event proximity is read only from PIT-safe timeline records. Empty or small event subsets remain `insufficient_data`; the engine never falls back to the unconditioned population while labelling it event-conditioned.

Current event novelty is based on the count of matching historical episode dates. Fewer than five matches lowers analog confidence. This is research metadata only and does not change production decisions, production models or the probability gate.

## Reproduced run

At cutoff `2026-08-07`, the run linked 1,700 PIT-safe event-profile records to historical analog episodes and created 1,710 conditional distributions. Of these, 1,488 passed the minimum effective-sample rule and 222 remained explicitly `insufficient_data`. Profiles comprised 809 central-bank, 809 market and 82 corporate coincidences. No surprise-event profile had `available_from` after its analog date.

The current cutoff had no event category with five comparable historical episodes, so all nine current contexts are marked `event_context_novel` with `lower_analog_confidence`. This is a confidence limitation, not evidence that events caused subsequent returns.
