## Decision: Deterministic planner controls every LLM turn

**Date:** 2025-02-14
**Context:** Needed to avoid drift and guarantee FSRS-aligned reinforcement.
**Alternatives Considered:** Prompt-only guardrails; server-side planner.
**Reasoning:** Client-local deterministic planner keeps latency low, respects privacy, and is unit-testable.

## Decision: Store conversation telemetry in dedicated tables

**Date:** 2025-02-14
**Context:** Need to track mastery signals without mutating user notes or confusing Sync.
**Alternatives Considered:** Embedding JSON inside card fields; separate sqlite file.
**Reasoning:** Namespaced tables integrate with existing collection DB, keep backup/export simple, and avoid collisions with add-ons.

## Decision: Conversation mastery does not alter FSRS scheduler (for now)

**Date:** 2025-12-18
**Context:** Conversation telemetry produces useful “mastery” signals, but FSRS scheduling should remain stable and predictable.
**Alternatives Considered:** Feeding mastery into FSRS stability/difficulty updates, auto-tagging/suspending cards, or adjusting due dates.
**Reasoning:** Keep mastery scoped to conversation mode until there is clear evidence and a controlled experiment design; avoid surprising scheduling changes and data migrations.
