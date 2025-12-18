# Project Context

## Product Snapshot

- **Codebase:** Fork of Anki (Qt/Python frontend, Rust backend, TypeScript/React for web components)
- **Primary Goal:** Add a Conversation Practice Mode tightly integrated with FSRS-powered spaced repetition while keeping Anki's offline-first philosophy.
- **Stakeholders:** Language learners (Korean initial target), add-on ecosystem developers, core maintainers concerned with performance/privacy.

## Technical Baseline

- **Frontend:** `qt/aqt` (PyQt6) for desktop UI, `ts/src` for TypeScript/React components embedded via Qt WebEngine.
- **Backend/Core:** `rslib` (Rust) exposed via `pylib/anki/` bindings; scheduling uses FSRS with metadata stored on cards/notes.
- **Data:** Decks/notes/cards live in SQLite DB accessible through `Collection` APIs. Add-ons typically hook into `aqt` module events.

## Current Limitations

- Conversations are handled outside Anki (external apps/add-ons) and lack tight FSRS integration.
- No deterministic planner exists for constraining LLM behavior using deck content.
- User telemetry (hover, click, repairs) is not captured today, so conversation proficiency is invisible to the scheduler.

## Opportunities

- Reuse FSRS maturity signals to prioritize conversational targets.
- Embed a modern chat UI using existing Qt WebEngine scaffolding (same approach as the add-on manager/react UI work).
- Persist per-lexeme conversation mastery alongside cards without altering user data unexpectedly (namespaced fields/tables).
- Provide a formal protocol so any LLM provider can plug in while respecting privacy and deterministic planning.
