# Conversation Practice Mode Architecture

## Layers

1. **Presentation (Qt/React)**
   - `qt/aqt/conversation_dialog.py` orchestrates deck selection, session lifecycle, and injects a WebEngine view hosting the React UI.
   - `ts/src/conversation/` renders the chat transcript, progressive disclosure helpers, repair buttons, and end-of-session wrap-up.
2. **Orchestration (Python)**
   - `pylib/anki/conversation/session.py` coordinates planner, gateway, telemetry, and persistence.
   - `pylib/anki/conversation/settings.py` loads/stores provider credentials, privacy toggles, and dictionary packs.
3. **Core Services**
   - `snapshot.py`: deterministic snapshot of deck content + FSRS metrics.
   - `planner.py`: pure function module that emits per-turn constraints.
   - `gateway.py`: provider abstraction + JSON contract enforcement.
   - `telemetry.py`: persistence + aggregation of mastery signals.
4. **Data & Integrations**
   - Extends SQLite schema with `conversation_events`, `conversation_items`, `conversation_sessions` (namespaced tables) via migrations handled in `pylib/anki/dbschema.py`.
   - Reads FSRS fields via existing scheduler APIs without mutating scheduler state; writes conversation mastery separately.

## Request/Response Contract (Summary)

### Request payload (client → LLM)

```json
{
    "system_role": "...",
    "conversation_state": {
        "summary": "...",
        "last_assistant_turn_ko": "...",
        "last_user_turn_ko": "..."
    },
    "user_input": {
        "text_ko": "...",
        "confidence": "unsure"
    },
    "language_constraints": {
        "must_target": [
            {
                "id": "vocab_사이",
                "surface_forms": ["사이", "사이에"],
                "priority": 0.82
            }
        ],
        "allowed_support": ["의자", "책상", "있다"],
        "allowed_grammar": [
            {
                "id": "gram_N_사이에_있다",
                "pattern": "N1와 N2 사이에 N3이/가 있다"
            }
        ],
        "forbidden": { "introduce_new_vocab": true, "sentence_length_max": 20 }
    },
    "generation_instructions": {
        "conversation_goal": "Continue the conversation naturally and keep it going.",
        "tone": "friendly",
        "register": "해요체",
        "provide_follow_up_question": true,
        "provide_micro_feedback": true,
        "provide_suggested_english_intent": true,
        "max_corrections": 1
    }
}
```

### Response payload (LLM → client)

```json
{
    "assistant_reply_ko": "아, 의자와 책상 사이에 가방이 있구나.",
    "follow_up_question_ko": "책 위에는 뭐가 있어?",
    "micro_feedback": {
        "type": "correction",
        "content_ko": "자연스럽게 말하면 이렇게 해요: 의자와 책상 사이에 있어요.",
        "content_en": "A more natural way to say it is: it's between the chair and the desk."
    },
    "suggested_user_intent_en": "It's between the chair and the desk.",
    "targets_used": ["vocab_사이"],
    "unexpected_tokens": []
}
```

If `unexpected_tokens` is non-empty, the gateway automatically requests a rewrite unless the UI is configured to auto-gloss instead.

## Data Schema Sketch

| Table                   | Key Fields                                                                                         | Purpose                                                        |
| ----------------------- | -------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `conversation_sessions` | `id`, `deck_ids`, `started`, `ended`, `summary_json`                                               | Session metadata + wrap-up summary.                            |
| `conversation_events`   | `id`, `session_id`, `turn_index`, `event_type`, `payload_json`                                     | Hover/click/double-tap/confidence events with timestamps.      |
| `conversation_items`    | `item_id`, `type`, `lexeme`, `grammar_pattern`, `fsrs_snapshot`, `mastery_stats_json`, `last_seen` | Long-lived mastery metrics aggregated per lexeme/grammar item. |

## Control Flow Diagram

```
[Deck Picker]
     |
     v
[Snapshot Builder] --> [Planner State]
     |                      |
     v                      v
[Conversation UI] --> [LLM Gateway] --> [LLM Provider]
     ^                      |
     |                      v
[Telemetry Store] <----- [Validator]
```

## Privacy & Performance Considerations

- All prompts redact personal note fields unless user whitelists them; sensitive data stays local.
- Hover gloss uses bundled dictionary + deck-derived mapping; only nuance/explanations trigger an LLM call when user explicitly asks.
- Planner and telemetry modules avoid heavy allocations; caching snapshot + dictionary lookups prevents redundant work.
