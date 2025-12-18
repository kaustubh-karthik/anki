# Feature: Conversation Practice Mode

## Purpose

Deliver a built-in conversational practice surface (Korean-first) that leverages Anki's FSRS data, deterministic planning, and constrained LLM generation to keep learners in-flow while reinforcing weak vocabulary and grammar pulled from their decks.

## Scope

- Included:
  - Deck selection UI that builds a deterministic "deck snapshot" for conversation planning.
  - Client-side planner that surfaces `must_target`, `allowed_support`, and grammar constraints each turn.
  - Chat-style UI with hover/click/double-tap token interactions, progressive disclosure for help, and end-of-session wrap-up.
  - Strict JSON LLM protocol, enforcement pipeline, and provider abstraction.
  - Persistence of conversation mastery metrics and integration hooks for FSRS.
- Explicitly excluded:
  - Mobile UI parity (desktop-first, but architecture must not block it).
  - Server-side session orchestration (remains client-local/offline-first, except optional LLM calls).
  - Automatic speech recognition or TTS.

## Constraints

- Performance: Token popups must respond <50 ms via local dictionaries/cache; chat UI cannot block Qt event loop; planner computations <10 ms per turn on typical decks.
- Compatibility: Works with existing deck schemas without migrating user notes; planner/metadata stored in new tables or JSON fields namespaced to avoid conflicts.
- Security: Only the structured request payload is sent to LLM; redaction for personal data; offline fallback (local-only) must be possible.
- Known limitations: Initial rollout supports Korean only, text-only interaction, and desktop Qt builds.

## Related Files / Modules

- `qt/aqt/conversation/` (new) — Qt controllers, dock/window wiring, telemetry handling.
- `ts/src/conversation/` — React chat UI rendered via WebView.
- `pylib/anki/conversation/` — Planner orchestration, metadata persistence, FSRS bridge.
- `rslib/src/scheduler/` — Extension points for FSRS data access (read-only).
- `tests/conversation/` — Unit/integration tests for planner + protocol enforcement.

## Architecture Overview

### Components

- **Deck Snapshot Builder:** Compiles FSRS, card fields, tags, lexeme/grammar mappings for selected deck(s).
- **Deterministic Planner:** Pure-Python module that consumes snapshot + telemetry to emit per-turn constraints and behavior flags.
- **Conversation UI:** React widget with tokenized transcript, hover gloss, click/double-tap telemetry, optional scaffolding panes.
- **LLM Gateway:** Thin service that enforces contract (JSON schema validation, unexpected token rewrite loop) and abstracts providers.
- **Telemetry Store:** Persists per-lexeme mastery, per-session summaries, and event logs for analysis/FSRS feedback.

### Data Flow

1. User selects deck(s); Deck Snapshot Builder queries `Collection` to create a cached snapshot with lexeme+grammar metadata.
2. Deterministic Planner initializes session state, selects `must_target`/`allowed_support`, and emits initial instructions.
3. UI sends user turn telemetry + planner payload to LLM Gateway; gateway calls LLM with strict JSON prompt.
4. Gateway validates response (schema + unexpected tokens), rewrites if necessary, and returns structured payload to UI.
5. UI renders assistant turn, logs token interactions; telemetry updates planner + mastery store; planner prepares next turn.
6. Session wrap-up summarises progress, optional Anki card suggestions are generated, and metadata persisted.

### External Dependencies

- **LLM Provider** (OpenAI, Anthropic, etc.) via provider interface for Korean generation.
- **Local dictionary** (e.g., CC-CEDICT derivative / custom Korean lexicon) packaged or user-supplied for hover gloss.

## Implementation Plan

### Step 1: Establish documentation + data contracts

**Goal:** Create schemas, JSON contracts, and module scaffolding docs to align engineering teams.
**Files Touched:**

- `elites/docs/01_feature_plan_conversation_practice_mode.md`
- `docs/` (developer-facing specs)
  **Details:**
- Finalize JSON request/response shapes, telemetry event schema, and metadata tables.
- Document enforcement rules (unexpected tokens rewrite pipeline, privacy guarantees).
- Output developer-ready spec consumed by future steps.
  **Status:** ✅ Completed

### Step 2: Deck Snapshot Builder prototype

**Goal:** Query selected deck(s) and materialize a deterministic snapshot with FSRS + lexical metadata.
**Files Touched:**

- `pylib/anki/conversation/snapshot.py`
- `pylib/tests/test_conversation_mode.py`
  **Details:**
- Reuse `Collection` APIs to gather cards/notes/tags.
- Map note fields to lexemes (configurable field mapping) and attach FSRS stats.
- Cache snapshot per session; expose as pure data for planner.
  **Status:** ✅ Completed

### Step 3: Planner core module

**Goal:** Build deterministic planner that consumes snapshot + telemetry to emit turn-level constraints.
**Files Touched:**

- `pylib/anki/conversation/planner.py`
- `pylib/tests/test_conversation_mode.py`
  **Details:**
- Implement scoring that balances FSRS stability, recency, and conversation mastery signals.
- Emit `must_target`, `allowed_support`, `allowed_grammar`, and forbidden flags.
- Support micro-spacing logic (ensure reappearance inside session) and avoidance tracking.
  **Status:** ✅ Completed

### Step 4: Telemetry store + mastery metrics

**Goal:** Persist per-lexeme/grammar telemetry and per-session summaries without altering user notes.
**Files Touched:**

- `pylib/anki/conversation/telemetry.py`
- `pylib/tests/test_conversation_mode.py`
  **Details:**
- Add namespaced tables (e.g., `conversation_events`, `conversation_mastery`).
- Track hover vs. click vs. double-tap vs. confidence signals; avoid counting hover-only curiosity.
- Provide aggregation helpers for planner and end-of-session wrap.
  **Status:** ✅ Completed

### Step 5: LLM gateway + provider abstraction

**Goal:** Enforce JSON contract, abstract LLM vendors, and provide rewrite loop for unexpected tokens.
**Files Touched:**

- `pylib/anki/conversation/gateway.py`
- `pylib/anki/conversation/openai.py`
- `pylib/tests/test_conversation_mode.py`
  **Details:**
- Compose prompt using system role + planner payload + conversation summary.
- Validate responses against JSON schema; if `unexpected_tokens` non-empty, auto-request rewrite or annotate.
- Provide configurable provider interface (OpenAI/Anthropic/local) with rate limiting + logging.
  **Status:** ✅ Completed

### Step 6: Desktop UI scaffold (Svelte + Qt)

**Goal:** Build flow-mode chat UI with token interactions rendered inside Qt WebEngine.
**Files Touched:**

- `qt/aqt/conversation.py`
- `ts/routes/conversation/+page.svelte`
  **Details:**
- Implement hover (gloss), click (don't know), double-tap (practice later) interactions.
- Add progressive disclosure controls (Hint, Explain, Translate, Plan Reply scaffolding panel).
- Hook telemetry events to Qt bridge and maintain latency budget.
  **Status:** 🟨 In progress

### Step 7: Session lifecycle + wrap-up

**Goal:** Manage session start/end, deck selection UI, and wrap-up summary with suggested cards.
**Files Touched:**

- `pylib/anki/conversation/wrap.py`
- `pylib/anki/conversation/suggest.py`
- `pylib/anki/conversation/cli.py`
- `pylib/tests/test_conversation_mode.py`
  **Details:**
- Deck picker that builds snapshot; session controller that sequences planner/gateway/UI loop.
- End session summary (3 strengths, 2 reinforcements, 1 suggested card) plus optional "Add to deck" flow.
- Persist session summary + integrate with FSRS tagging (without auto-editing cards unless user confirms).
  **Status:** 🟨 In progress

### Step 8: Privacy, settings, and telemetry exports

**Goal:** Provide settings UI and guardrails for data sharing, offline mode, and logging.
**Files Touched:**

- `pylib/anki/conversation/settings.py`
- `pylib/anki/conversation/redaction.py`
- `pylib/anki/conversation/export.py`
- `pylib/anki/conversation/cli.py`
- `pylib/tests/test_conversation_mode.py`
  **Details:**
- Let users choose LLM provider, API keys, redaction levels, offline dictionary packs.
- Document what data leaves device; support "local-only" warning/fallback.
- Add export tooling for telemetry (for debugging) respecting privacy toggles.
  **Status:** 🟨 In progress

## Progress Log

### 2025-02-14 — Codex

- Created initial feature plan document and documentation scaffolding under `elites/docs`.
- Marked Step 1 as in progress while the rest remain unstarted.
- Highlighted required modules and integration points across Qt, TS, and Python layers.

### 2025-12-18 — Codex

- Implemented backend-only MVP modules: deck snapshot, deterministic planner, telemetry tables/store, and LLM gateway with safe-mode rewrite gate.
- Added a CLI runner for fully automated text-only sessions and a fake provider for offline execution.
- Added pytest coverage for schema creation, snapshot extraction, planner output, and gateway rewrite behavior.
- Enhanced the deck snapshot to include FSRS `stability`/`difficulty` via `compute_memory_state()`, and updated planner ordering to prioritize low-stability items.
- Added a baseline Korean \"glue\" vocabulary so safe-mode token budgeting is usable without constant rewrites.
- Added a no-UI end-to-end CLI test that exercises rewrite gating and verifies DB persistence, and enabled `ANKI_TEST_MODE` during python tests to disable fuzz and keep runs deterministic.
- Implemented session wrap-up output (strengths/reinforce/suggested cards) and deck-derived gloss extraction for offline-first token popups.
- Added deterministic micro-spacing/avoidance in the planner (`observe_turn()` + scheduled reuse), and expanded telemetry signals/tests (hover does not pollute mastery).
- Added backend-only \"Plan my reply\" contract + CLI command and extended telemetry with confidence + lookup timing + repair moves; wrap scoring now incorporates these signals for reinforcement selection.
- Implemented a no-UI card suggestion pipeline: wrap-derived suggestions can be applied to a chosen deck via CLI, creating tagged Basic notes deterministically.
- Refactored OpenAI JSON calls into a shared client, wired plan-reply OpenAI provider, and added an offline glossary cache with CLI rebuild/lookup for instant token popups without network.
- Added an initial desktop UI scaffold: a SvelteKit `conversation` page and a Qt dialog that loads it via `load_sveltekit_page()` with basic bridge commands (`init`, `start`, `turn`, `gloss`). (TS tests not runnable in this environment due to missing Node toolchain.)
- Expanded the UI bridge: token click/double-click now logs `dont_know`/`practice_again` events, and the web UI can request wrap summaries and apply suggestions through Qt bridge commands.
- Wired \"Plan my reply\" into the desktop UI via a new bridge command that calls the backend plan-reply gateway (requires API key present locally).
- Expanded the Svelte conversation page to support topic id, confidence selection, mark-confusing, wrap refresh, and apply-suggestions via bridge commands (TS tests not runnable here).
- Added persisted conversation settings stored in collection config (CLI get/set) and implemented deterministic missed-target detection to penalize avoidance in mastery signals.
- Added deterministic topic IDs (backend) and threaded optional `topic_id` through CLI/Qt session start into planner state, keeping topic control client-side.

### 2025-12-18 — Codex

- Refactored event logging + mastery updates into `pylib/anki/conversation/events.py` and moved the shared system role prompt to `pylib/anki/conversation/prompts.py`.
- Re-ran backend pytest suite (no-human-input): `112 passed`.

### 2025-12-18 — Codex

- Tightened collocation accounting: a collocation target now counts as “used” only if all surface-form tokens appear, and missed targets are now persisted for `lexeme:*`, `gram:*`, `colloc:*`, and `repair:*`.
- Added snapshot builder configurability to persisted settings + CLI (`lexeme_field_index`, `gloss_field_index`, `snapshot_max_items`) and expanded tests accordingly.

### 2025-12-18 — Codex

- Expanded planner scoring to incorporate additional mastery signals (`missed_target`, `lookup_*`) and FSRS `difficulty` (small weight), and added regression tests for the new prioritization behavior.

### 2025-12-18 — Codex

- Expanded built-in Korean grammar/collocation catalogs and added regression tests to ensure the planner emits the new patterns deterministically.

### 2025-12-18 — Codex

- Fixed a real-world deck snapshot bug: lexeme extraction now strips HTML in the source field (matching gloss extraction), with a regression test.

### 2025-12-18 — Codex

- Refactored the backend CLI to default to persisted `ConversationSettings` when flags are omitted, and centralized OpenAI key resolution (`OPENAI_API_KEY`/`ANKI_OPENAI_API_KEY` → `gpt-api.txt` fallback).
- Added opt-in redaction for telemetry exports (defaulting to the saved redaction level) and unit tests to verify exported JSON is redacted deterministically.
- Ran `./ninja check` successfully (format/lints/mypy/pytest).

### 2025-12-18 — Codex

- Added a reusable backend `ConversationSession` controller (`pylib/anki/conversation/session.py`) to centralize per-turn orchestration (planner → gateway → telemetry) and keep CLI/desktop integration consistent.
- Refactored `anki-conversation run` to use `ConversationSession` and added unit coverage for running a full session without any UI.
- Ran `./ninja check` successfully (full build including aqt tests).

### 2025-12-18 — Codex

- Added a deterministic offline `LocalConversationProvider` that implements the same strict JSON contract and safe-mode budgeting (no network/API key required).
- Wired the local provider into `anki-conversation` (`--provider local`) and the desktop bridge (when `ConversationSettings.provider == "local"`), enabling local-only smoke testing.
- Added unit coverage to ensure the local provider obeys safe-mode constraints, and verified with `./ninja check`.

### 2025-12-18 — Codex

- Updated the desktop `conversation` page to store assistant responses as structured per-turn objects, enabling per-message controls without losing context.
- Added progressive disclosure UI controls under each assistant turn: Hint (shows `targets_used`/`unexpected_tokens`), Explain (shows `micro_feedback`), Translate (currently surfaces `suggested_user_intent_en`), plus Practice-targets and message-level Mark-confusing events.
- Ran `./ninja check` successfully after UI changes.

## Open Issues

- [ ] Decide on default local dictionary source and licensing (offline-first, redistributable).
- [ ] Clarify how planner handles multi-deck selection with conflicting templates.
- [ ] Evaluate whether FSRS mastery signals should influence core scheduler or stay scoped to conversation mode.
- [ ] Expand grammar/collocation catalog beyond the current minimal built-ins.
- [x] Centralize key resolution and keep `gpt-api.txt` as an optional dev convenience (do not persist secrets in config).
- [ ] Implement a real “Translate” assist (separate from `suggested_user_intent_en`) with a strict JSON contract + tests.

## Agent Instructions (MANDATORY)

- Read this document end-to-end before touching code.
- Do **not** re-plan completed steps; append updates instead.
- For every code change:
  - Update relevant step status (⬜/🟨/✅/❌) and details.
  - Append a dated entry to the Progress Log.
- Never delete or rewrite historical log entries; add new context only.
- If blocked, mark the step ❌ with a concise reason and propose next actions in Open Issues.
