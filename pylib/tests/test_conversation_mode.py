# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

import json
from dataclasses import dataclass

from anki.consts import CARD_TYPE_REV, QUEUE_TYPE_REV
from anki.conversation.events import apply_missed_targets
from anki.conversation.export import export_conversation_telemetry
from anki.conversation.gateway import ConversationGateway, ConversationProvider
from anki.conversation.glossary import lookup_gloss, rebuild_glossary_from_snapshot
from anki.conversation.keys import read_api_key_file, resolve_openai_api_key
from anki.conversation.plan_reply import (
    FakePlanReplyProvider,
    PlanReplyGateway,
    PlanReplyRequest,
)
from anki.conversation.planner import ConversationPlanner
from anki.conversation.redaction import redact_text
from anki.conversation.settings import (
    ConversationSettings,
    RedactionLevel,
    load_conversation_settings,
    save_conversation_settings,
)
from anki.conversation.snapshot import build_deck_snapshot
from anki.conversation.suggest import apply_suggested_cards, suggestions_from_wrap
from anki.conversation.telemetry import ConversationTelemetryStore
from anki.conversation.topics import get_topic
from anki.conversation.types import (
    ConversationRequest,
    ConversationState,
    ForbiddenConstraints,
    GenerationInstructions,
    ItemId,
    LanguageConstraints,
    MustTarget,
    UserInput,
)
from anki.decks import DeckId
from tests.shared import getEmptyCol


def test_schema_is_created() -> None:
    col = getEmptyCol()
    try:
        ConversationTelemetryStore(col)
        assert (
            col.db.scalar(
                "select count() from sqlite_master where type='table' and name=?",
                "elites_conversation_sessions",
            )
            == 1
        )
        assert (
            col.db.scalar(
                "select count() from sqlite_master where type='table' and name=?",
                "elites_conversation_events",
            )
            == 1
        )
    finally:
        col.close()


def test_redaction_minimal_and_strict() -> None:
    r = redact_text("email a@b.com url https://x.y", RedactionLevel.minimal)
    assert "[REDACTED_EMAIL]" in r.text
    assert "[REDACTED_URL]" in r.text
    r2 = redact_text("call 1234567890", RedactionLevel.strict)
    assert "[REDACTED_NUMBER]" in r2.text


def test_export_telemetry_json_roundtrip() -> None:
    col = getEmptyCol()
    try:
        store = ConversationTelemetryStore(col)
        sid = store.start_session([1])
        store.log_event(
            session_id=sid, turn_index=1, event_type="turn", payload={"x": 1}
        )
        store.end_session(sid, summary={"turns": 1})
        exported = export_conversation_telemetry(col, limit_sessions=10)
        data = json.loads(exported.to_json())
        assert "sessions" in data and "events" in data and "items" in data
        assert len(data["sessions"]) >= 1
        assert len(data["events"]) >= 1
    finally:
        col.close()


def test_export_telemetry_applies_redaction() -> None:
    col = getEmptyCol()
    try:
        store = ConversationTelemetryStore(col)
        sid = store.start_session([1])
        store.log_event(
            session_id=sid,
            turn_index=0,
            event_type="turn",
            payload={
                "user": "email a@b.com",
                "assistant": {"assistant_reply_ko": "url https://x.y"},
            },
        )
        store.end_session(sid, summary={"note": "email a@b.com"})

        exported = export_conversation_telemetry(
            col, limit_sessions=10, redaction_level=RedactionLevel.minimal
        )
        assert exported.events
        payload_json = exported.events[0]["payload_json"]
        assert isinstance(payload_json, str)
        assert "[REDACTED_EMAIL]" in payload_json
        assert "[REDACTED_URL]" in payload_json

        summary_json = exported.sessions[0]["summary_json"]
        assert isinstance(summary_json, str)
        assert "[REDACTED_EMAIL]" in summary_json
    finally:
        col.close()


def test_cli_run_is_fully_automatable_and_writes_db(tmp_path) -> None:
    from anki.conversation import cli

    col = getEmptyCol()
    try:
        collection_path = col.path
        did = col.decks.id("Korean")
        note = col.newNote()
        note["Front"] = "의자"
        note["Back"] = "chair"
        col.addNote(note)
        for card in note.cards():
            card.did = did
            card.flush()

        script_path = tmp_path / "script.json"
        script_path.write_text(
            json.dumps(
                [
                    {
                        "text_ko": "의자",
                        "confidence": "guessing",
                        "events": [
                            {"type": "dont_know", "token": "의자"},
                            {"type": "lookup", "token": "의자", "ms": 120},
                            {"type": "repair_move", "move": "clarify_meaning"},
                        ],
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        provider_script_path = tmp_path / "provider.json"
        provider_script_path.write_text(
            json.dumps(
                [
                    # first response violates budget -> triggers rewrite
                    {
                        "assistant_reply_ko": "고양이 있어요.",
                        "follow_up_question_ko": "뭐가 있어요?",
                        "micro_feedback": {
                            "type": "none",
                            "content_ko": "",
                            "content_en": "",
                        },
                        "suggested_user_intent_en": None,
                        "targets_used": [],
                        "unexpected_tokens": [],
                    },
                    # second response stays in budget
                    {
                        "assistant_reply_ko": "의자 있어요.",
                        "follow_up_question_ko": "뭐가 있어요?",
                        "micro_feedback": {
                            "type": "none",
                            "content_ko": "",
                            "content_en": "",
                        },
                        "suggested_user_intent_en": None,
                        "targets_used": [],
                        "unexpected_tokens": [],
                    },
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # close, then invoke CLI against the collection path
        col.close()

        cli.main(
            [
                "run",
                "--collection",
                collection_path,
                "--deck",
                "Korean",
                "--script",
                str(script_path),
                "--provider",
                "fake",
                "--provider-script",
                str(provider_script_path),
            ]
        )

        # open the same collection and assert a session+event exists
        from anki.collection import Collection

        opened = Collection(collection_path)
        try:
            assert (
                opened.db.scalar("select count() from elites_conversation_sessions")
                == 1
            )
            # dont_know + lookup + repair_move + turn
            assert (
                opened.db.scalar("select count() from elites_conversation_events") == 4
            )
            mastery_json = opened.db.scalar(
                "select mastery_json from elites_conversation_items where item_id=?",
                "lexeme:의자",
            )
            assert isinstance(mastery_json, str)
            mastery = json.loads(mastery_json)
            assert mastery["dont_know"] == 1
            assert mastery["assistant_used"] == 1
            assert mastery["lookup_count"] == 1
            assert mastery["lookup_ms_total"] == 120
            assert mastery["used_guessing"] == 1
            repair = opened.db.scalar(
                "select mastery_json from elites_conversation_items where item_id=?",
                "repair:clarify_meaning",
            )
            assert isinstance(repair, str)
            assert json.loads(repair)["used"] == 1
        finally:
            opened.close()
    finally:
        try:
            col.close()
        except Exception:
            pass


def test_snapshot_extracts_lexemes_from_selected_deck() -> None:
    col = getEmptyCol()
    try:
        did = col.decks.id("Korean")
        col.decks.select(DeckId(did))

        note = col.newNote()
        note["Front"] = "사이"
        note["Back"] = "between"
        col.addNote(note)
        for card in note.cards():
            card.did = did
            card.flush()

        snapshot = build_deck_snapshot(col, [DeckId(did)])
        assert snapshot.deck_ids == (did,)
        assert snapshot.today is not None
        assert any(item.lexeme == "사이" for item in snapshot.items)
    finally:
        col.close()


def test_snapshot_strips_html_in_lexeme_field() -> None:
    col = getEmptyCol()
    try:
        did = col.decks.id("Korean")
        col.decks.select(DeckId(did))

        note = col.newNote()
        note["Front"] = "<b>의자</b>"
        note["Back"] = "chair"
        col.addNote(note)
        for card in note.cards():
            card.did = did
            card.flush()

        snapshot = build_deck_snapshot(col, [DeckId(did)], include_fsrs_metrics=False)
        assert any(item.lexeme == "의자" for item in snapshot.items)
    finally:
        col.close()


def test_api_key_resolution_prefers_env_over_file(tmp_path) -> None:
    import os

    key_file = tmp_path / "key.txt"
    key_file.write_text("sk-file\n", encoding="utf-8")
    assert read_api_key_file(key_file) == "sk-file"

    old = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = " sk-env "
    try:
        assert resolve_openai_api_key(api_key_file=key_file) == "sk-env"
    finally:
        if old is None:
            del os.environ["OPENAI_API_KEY"]
        else:
            os.environ["OPENAI_API_KEY"] = old

    assert resolve_openai_api_key(api_key_file=key_file) == "sk-file"


def test_snapshot_multi_deck_collation() -> None:
    col = getEmptyCol()
    try:
        did1 = col.decks.id("Korean::A")
        did2 = col.decks.id("Korean::B")

        note1 = col.newNote()
        note1["Front"] = "의자"
        note1["Back"] = "chair"
        col.addNote(note1)
        for card in note1.cards():
            card.did = did1
            card.flush()

        note2 = col.newNote()
        note2["Front"] = "책상"
        note2["Back"] = "desk"
        col.addNote(note2)
        for card in note2.cards():
            card.did = did2
            card.flush()

        snapshot = build_deck_snapshot(
            col, [DeckId(did1), DeckId(did2)], include_fsrs_metrics=False
        )
        assert set(snapshot.deck_ids) == {did1, did2}
        lexemes = {i.lexeme for i in snapshot.items}
        assert "의자" in lexemes and "책상" in lexemes
    finally:
        col.close()


def test_snapshot_includes_gloss_when_available() -> None:
    col = getEmptyCol()
    try:
        did = col.decks.id("Korean")
        col.decks.select(DeckId(did))

        note = col.newNote()
        note["Front"] = "의자"
        note["Back"] = "<b>chair</b>"
        col.addNote(note)
        for card in note.cards():
            card.did = did
            card.flush()

        snapshot = build_deck_snapshot(col, [DeckId(did)])
        item = next(i for i in snapshot.items if i.lexeme == "의자")
        assert item.gloss == "chair"
    finally:
        col.close()


def test_planner_emits_must_targets_and_support_budget() -> None:
    col = getEmptyCol()
    try:
        did = col.decks.id("Korean")
        col.decks.select(DeckId(did))
        for lexeme in ["사이", "의자", "책상"]:
            note = col.newNote()
            note["Front"] = lexeme
            note["Back"] = "x"
            col.addNote(note)
            for card in note.cards():
                card.did = did
                card.flush()

        snapshot = build_deck_snapshot(col, [DeckId(did)])
        planner = ConversationPlanner(snapshot)
        state = planner.initial_state(summary="room objects")
        conv_state, constraints, instructions = planner.plan_turn(
            state, UserInput(text_ko="응, 거기에 있어.", confidence="unsure")
        )
        assert conv_state.summary == "room objects"
        assert instructions.safe_mode is True
        assert len(constraints.must_target) == 3
        assert len(constraints.allowed_support) >= 3
    finally:
        col.close()


@dataclass
class _ScriptedProvider(ConversationProvider):
    calls: int = 0

    def generate(self, *, request: ConversationRequest) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {
                "assistant_reply_ko": "고양이 있어요.",
                "follow_up_question_ko": "뭐가 있어요?",
                "micro_feedback": {"type": "none", "content_ko": "", "content_en": ""},
                "suggested_user_intent_en": None,
                "targets_used": [],
                "unexpected_tokens": [],
            }
        return {
            "assistant_reply_ko": "의자 있어요.",
            "follow_up_question_ko": "뭐가 있어요?",
            "micro_feedback": {"type": "none", "content_ko": "", "content_en": ""},
            "suggested_user_intent_en": None,
            "targets_used": [],
            "unexpected_tokens": [],
        }


def test_gateway_rewrites_on_unexpected_tokens() -> None:
    provider = _ScriptedProvider()
    gateway = ConversationGateway(provider=provider, max_rewrites=1)

    constraints = LanguageConstraints(
        must_target=(
            MustTarget(
                id=ItemId("lexeme:의자"),
                type="vocab",
                surface_forms=("의자",),
                priority=1.0,
            ),
        ),
        allowed_support=("의자", "있어요", "뭐가", "있어", "거기", "여기"),
        allowed_grammar=(),
    )
    request = ConversationRequest(
        system_role="Return JSON only.",
        conversation_state=ConversationState(summary="x"),
        user_input=UserInput(text_ko="응"),
        language_constraints=constraints,
        generation_instructions=GenerationInstructions(safe_mode=True),
    )

    resp = gateway.run_turn(request=request)
    assert provider.calls == 2
    assert resp.unexpected_tokens == ()


@dataclass
class _LongReplyProvider(ConversationProvider):
    def generate(self, *, request: ConversationRequest) -> dict:
        # produce many tokens to exceed sentence_length_max
        return {
            "assistant_reply_ko": "의자 " * 100,
            "follow_up_question_ko": "뭐예요?",
            "micro_feedback": {"type": "none", "content_ko": "", "content_en": ""},
            "suggested_user_intent_en": None,
            "targets_used": [],
            "unexpected_tokens": [],
        }


def test_gateway_enforces_sentence_length_max() -> None:
    provider = _LongReplyProvider()
    gateway = ConversationGateway(provider=provider, max_rewrites=0)
    constraints = LanguageConstraints(
        must_target=(
            MustTarget(
                id=ItemId("lexeme:의자"),
                type="vocab",
                surface_forms=("의자",),
                priority=1.0,
            ),
        ),
        allowed_support=("의자", "뭐예요"),
        allowed_grammar=(),
    )
    request = ConversationRequest(
        system_role="Return JSON only.",
        conversation_state=ConversationState(summary="x"),
        user_input=UserInput(text_ko="응"),
        language_constraints=constraints,
        generation_instructions=GenerationInstructions(safe_mode=True),
    )
    # Tighten sentence length max for test
    request = ConversationRequest(
        system_role=request.system_role,
        conversation_state=request.conversation_state,
        user_input=request.user_input,
        language_constraints=LanguageConstraints(
            must_target=constraints.must_target,
            allowed_support=constraints.allowed_support,
            allowed_grammar=constraints.allowed_grammar,
            forbidden=constraints.forbidden.__class__(
                introduce_new_vocab=True, sentence_length_max=5
            ),
        ),
        generation_instructions=request.generation_instructions,
    )
    try:
        gateway.run_turn(request=request)
        assert False, "expected contract violation"
    except ValueError as e:
        assert "sentence_length_max" in str(e)


def test_plan_reply_gateway_rewrites_on_unexpected_tokens() -> None:
    provider = FakePlanReplyProvider(
        scripted=[
            {
                "options_ko": ["고양이 있어요."],
                "notes_en": None,
                "unexpected_tokens": [],
            },
            {"options_ko": ["의자 있어요."], "notes_en": None, "unexpected_tokens": []},
        ]
    )
    gateway = PlanReplyGateway(provider=provider, max_rewrites=1)
    req = PlanReplyRequest(
        system_role="Return JSON only.",
        conversation_state=ConversationState(summary="x"),
        intent_en="There is a chair.",
        language_constraints=LanguageConstraints(
            must_target=(
                MustTarget(
                    id=ItemId("lexeme:의자"),
                    type="vocab",
                    surface_forms=("의자",),
                    priority=1.0,
                ),
            ),
            allowed_support=("의자", "있어요", "뭐예요"),
            allowed_grammar=(),
            forbidden=ForbiddenConstraints(
                introduce_new_vocab=True, sentence_length_max=20
            ),
        ),
        generation_instructions=GenerationInstructions(safe_mode=True),
    )
    resp = gateway.run(request=req)
    assert provider.i == 2
    assert resp.unexpected_tokens == ()


def test_apply_suggested_cards_creates_basic_notes() -> None:
    col = getEmptyCol()
    try:
        suggestions = [
            {"front": "의자", "back": "chair", "tags": ["conv_suggested"]},
        ]
        wrap = {"suggested_cards": suggestions}
        card_suggestions = suggestions_from_wrap(wrap, deck_id=1)
        note_ids = apply_suggested_cards(col, card_suggestions)
        assert len(note_ids) == 1
        assert col.note_count() == 1
        assert "conv_suggested" in col.tags.all()
    finally:
        col.close()


def test_glossary_rebuild_and_lookup() -> None:
    col = getEmptyCol()
    try:
        did = col.decks.id("Korean")
        col.decks.select(DeckId(did))

        note = col.newNote()
        note["Front"] = "의자"
        note["Back"] = "chair"
        col.addNote(note)
        for card in note.cards():
            card.did = did
            card.flush()

        snapshot = build_deck_snapshot(col, [DeckId(did)], include_fsrs_metrics=False)
        updated = rebuild_glossary_from_snapshot(col, snapshot)
        assert updated >= 1
        entry = lookup_gloss(col, "의자")
        assert entry is not None
        assert entry.gloss == "chair"
    finally:
        col.close()


def test_settings_persist_roundtrip() -> None:
    col = getEmptyCol()
    try:
        s = load_conversation_settings(col)
        assert s.model
        save_conversation_settings(
            col,
            ConversationSettings(
                provider="openai",
                model="gpt-5-nano",
                safe_mode=False,
                lexeme_field_index=2,
                gloss_field_index=None,
                snapshot_max_items=123,
            ),
        )
        s2 = load_conversation_settings(col)
        assert s2.provider == "openai"
        assert s2.safe_mode is False
        assert s2.lexeme_field_index == 2
        assert s2.gloss_field_index is None
        assert s2.snapshot_max_items == 123
    finally:
        col.close()


def test_topic_lookup() -> None:
    t = get_topic("room_objects")
    assert t is not None
    assert t.id == "room_objects"


def test_mastery_upsert_and_increment() -> None:
    col = getEmptyCol()
    try:
        store = ConversationTelemetryStore(col)
        store.bump_item(
            item_id="lexeme:의자",
            kind="lexeme",
            value="의자",
            deltas={"dont_know": 1},
        )
        store.bump_item(
            item_id="lexeme:의자",
            kind="lexeme",
            value="의자",
            deltas={"dont_know": 2, "practice_again": 1},
        )
        mastery_json = col.db.scalar(
            "select mastery_json from elites_conversation_items where item_id=?",
            "lexeme:의자",
        )
        assert isinstance(mastery_json, str)
        mastery = json.loads(mastery_json)
        assert mastery["dont_know"] == 3
        assert mastery["practice_again"] == 1
    finally:
        col.close()


def test_hover_does_not_create_mastery_signal(tmp_path) -> None:
    from anki.collection import Collection
    from anki.conversation import cli

    col = getEmptyCol()
    try:
        collection_path = col.path
        did = col.decks.id("Korean")
        note = col.newNote()
        note["Front"] = "의자"
        note["Back"] = "chair"
        col.addNote(note)
        for card in note.cards():
            card.did = did
            card.flush()
        col.close()

        script_path = tmp_path / "script.json"
        script_path.write_text(
            json.dumps(
                [{"text_ko": "응", "events": [{"type": "hover", "token": "의자"}]}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        provider_script_path = tmp_path / "provider.json"
        provider_script_path.write_text(
            json.dumps(
                [
                    {
                        "assistant_reply_ko": "의자 있어요.",
                        "follow_up_question_ko": "뭐가 있어요?",
                        "micro_feedback": {
                            "type": "none",
                            "content_ko": "",
                            "content_en": "",
                        },
                        "suggested_user_intent_en": None,
                        "targets_used": [],
                        "unexpected_tokens": [],
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        cli.main(
            [
                "run",
                "--collection",
                collection_path,
                "--deck",
                "Korean",
                "--script",
                str(script_path),
                "--provider",
                "fake",
                "--provider-script",
                str(provider_script_path),
            ]
        )

        opened = Collection(collection_path)
        try:
            mastery_json = opened.db.scalar(
                "select mastery_json from elites_conversation_items where item_id=?",
                "lexeme:의자",
            )
            # hover shouldn't create dont_know/practice_again/mark_confusing, but assistant_used will exist
            assert isinstance(mastery_json, str)
            mastery = json.loads(mastery_json)
            assert mastery.get("hover") is None
            assert mastery.get("dont_know") is None
        finally:
            opened.close()
    finally:
        try:
            col.close()
        except Exception:
            pass


def test_planner_prioritizes_mastery_weak_items() -> None:
    col = getEmptyCol()
    try:
        did = col.decks.id("Korean")
        col.decks.select(DeckId(did))
        for lexeme in ["의자", "책상"]:
            note = col.newNote()
            note["Front"] = lexeme
            note["Back"] = "x"
            col.addNote(note)
            for card in note.cards():
                card.did = did
                card.flush()

        snapshot = build_deck_snapshot(col, [DeckId(did)], include_fsrs_metrics=False)
        planner = ConversationPlanner(snapshot)
        state = planner.initial_state(summary="x")

        store = ConversationTelemetryStore(col)
        store.bump_item(
            item_id="lexeme:책상",
            kind="lexeme",
            value="책상",
            deltas={"dont_know": 3},
        )
        mastery = store.get_mastery_bulk(["lexeme:의자", "lexeme:책상"])

        _, constraints, _ = planner.plan_turn(
            state,
            UserInput(text_ko="응"),
            must_target_count=1,
            mastery=mastery,
        )
        assert constraints.must_target[0].surface_forms[0] == "책상"
    finally:
        col.close()


def test_planner_prioritizes_lookup_heavy_items() -> None:
    col = getEmptyCol()
    try:
        did = col.decks.id("Korean")
        col.decks.select(DeckId(did))
        for lexeme in ["의자", "책상"]:
            note = col.newNote()
            note["Front"] = lexeme
            note["Back"] = "x"
            col.addNote(note)
            for card in note.cards():
                card.did = did
                card.flush()

        snapshot = build_deck_snapshot(col, [DeckId(did)], include_fsrs_metrics=False)
        planner = ConversationPlanner(snapshot)
        state = planner.initial_state(summary="x")

        store = ConversationTelemetryStore(col)
        store.bump_item(
            item_id="lexeme:책상",
            kind="lexeme",
            value="책상",
            deltas={"lookup_count": 2, "lookup_ms_total": 4000},
        )
        mastery = store.get_mastery_bulk(["lexeme:의자", "lexeme:책상"])

        _, constraints, _ = planner.plan_turn(
            state,
            UserInput(text_ko="응"),
            must_target_count=1,
            mastery=mastery,
        )
        assert constraints.must_target[0].surface_forms[0] == "책상"
    finally:
        col.close()


def test_planner_prioritizes_overdue_review_cards() -> None:
    col = getEmptyCol()
    try:
        did = col.decks.id("Korean")
        col.decks.select(DeckId(did))
        # create two lexemes
        for lexeme in ["의자", "책상"]:
            note = col.newNote()
            note["Front"] = lexeme
            note["Back"] = "x"
            col.addNote(note)
            for card in note.cards():
                card.did = did
                card.flush()

        # set both cards to review, but make one more overdue
        cards = col.db.all(
            "select c.id, n.flds from cards c join notes n on n.id=c.nid where c.did=?",
            did,
        )
        today = col.sched.today
        for cid, flds in cards:
            lexeme = str(flds).split("\x1f")[0]
            card = col.getCard(int(cid))
            card.type = CARD_TYPE_REV
            card.queue = QUEUE_TYPE_REV
            card.ivl = 10
            if lexeme == "책상":
                card.due = today - 10  # more overdue
            else:
                card.due = today - 1
            card.flush()

        snapshot = build_deck_snapshot(col, [DeckId(did)], include_fsrs_metrics=False)
        planner = ConversationPlanner(snapshot)
        state = planner.initial_state(summary="x")
        _, constraints, _ = planner.plan_turn(
            state,
            UserInput(text_ko="응"),
            must_target_count=1,
            mastery={},
        )
        assert constraints.must_target[0].surface_forms[0] == "책상"
    finally:
        col.close()


def test_planner_micro_spacing_reuses_due_targets() -> None:
    col = getEmptyCol()
    try:
        did = col.decks.id("Korean")
        col.decks.select(DeckId(did))
        for lexeme in ["의자", "책상", "가방", "문"]:
            note = col.newNote()
            note["Front"] = lexeme
            note["Back"] = "x"
            col.addNote(note)
            for card in note.cards():
                card.did = did
                card.flush()

        snapshot = build_deck_snapshot(col, [DeckId(did)], include_fsrs_metrics=False)
        planner = ConversationPlanner(snapshot)
        state = planner.initial_state(summary="x")

        # Turn 1: pick first by lexical order (with equal mastery), schedule reuse after 2 turns
        _, c1, _ = planner.plan_turn(
            state,
            UserInput(text_ko="응"),
            must_target_count=1,
            mastery={},
            reuse_delay_turns=2,
        )
        first = c1.must_target[0].surface_forms[0]
        planner.observe_turn(
            state,
            constraints=c1,
            user_input=UserInput(text_ko="응"),
            assistant_reply_ko="네.",
            follow_up_question_ko="뭐예요?",
        )

        # Turn 2/3: other targets
        _, c2, _ = planner.plan_turn(
            state,
            UserInput(text_ko="응"),
            must_target_count=1,
            mastery={},
            reuse_delay_turns=2,
        )
        planner.observe_turn(
            state,
            constraints=c2,
            user_input=UserInput(text_ko="응"),
            assistant_reply_ko="네.",
            follow_up_question_ko="뭐예요?",
        )
        _, c3, _ = planner.plan_turn(
            state,
            UserInput(text_ko="응"),
            must_target_count=1,
            mastery={},
            reuse_delay_turns=2,
        )

        # Turn 3 should have reused the first target (due)
        assert c3.must_target[0].surface_forms[0] == first
    finally:
        col.close()


def test_observe_turn_returns_missed_targets() -> None:
    col = getEmptyCol()
    try:
        did = col.decks.id("Korean")
        col.decks.select(DeckId(did))
        note = col.newNote()
        note["Front"] = "의자"
        note["Back"] = "chair"
        col.addNote(note)
        for card in note.cards():
            card.did = did
            card.flush()
        snapshot = build_deck_snapshot(col, [DeckId(did)], include_fsrs_metrics=False)
        planner = ConversationPlanner(snapshot)
        state = planner.initial_state(summary="x")
        _, constraints, _ = planner.plan_turn(
            state, UserInput(text_ko="응"), must_target_count=1, mastery={}
        )
        missed = planner.observe_turn(
            state,
            constraints=constraints,
            user_input=UserInput(text_ko="응"),
            assistant_reply_ko="네.",
            follow_up_question_ko="뭐예요?",
        )
        assert any(m.startswith("lexeme:") for m in missed)
    finally:
        col.close()


def test_planner_emits_allowed_grammar_patterns() -> None:
    col = getEmptyCol()
    try:
        did = col.decks.id("Korean")
        col.decks.select(DeckId(did))
        note = col.newNote()
        note["Front"] = "사이"
        note["Back"] = "between"
        col.addNote(note)
        for card in note.cards():
            card.did = did
            card.flush()

        snapshot = build_deck_snapshot(col, [DeckId(did)], include_fsrs_metrics=False)
        planner = ConversationPlanner(snapshot)
        state = planner.initial_state(summary="x")
        _, constraints, _ = planner.plan_turn(
            state,
            UserInput(text_ko="응"),
            must_target_count=1,
            mastery={},
        )
        assert any("사이에" in gp.pattern for gp in constraints.allowed_grammar)
    finally:
        col.close()


def test_planner_can_emit_collocation_targets() -> None:
    col = getEmptyCol()
    try:
        did = col.decks.id("Korean")
        col.decks.select(DeckId(did))
        note = col.newNote()
        note["Front"] = "사이"
        note["Back"] = "between"
        col.addNote(note)
        for card in note.cards():
            card.did = did
            card.flush()

        snapshot = build_deck_snapshot(col, [DeckId(did)], include_fsrs_metrics=False)
        planner = ConversationPlanner(snapshot)
        state = planner.initial_state(summary="x")
        _, constraints, _ = planner.plan_turn(
            state,
            UserInput(text_ko="응"),
            must_target_count=2,
            mastery={},
        )
        assert any(t.type == "collocation" for t in constraints.must_target)
    finally:
        col.close()


def test_planner_emits_new_grammar_and_collocation_patterns() -> None:
    col = getEmptyCol()
    try:
        did = col.decks.id("Korean")
        col.decks.select(DeckId(did))
        note = col.newNote()
        note["Front"] = "안"
        note["Back"] = "not"
        col.addNote(note)
        for card in note.cards():
            card.did = did
            card.flush()

        snapshot = build_deck_snapshot(col, [DeckId(did)], include_fsrs_metrics=False)
        planner = ConversationPlanner(snapshot)
        state = planner.initial_state(summary="x")
        _, constraints, _ = planner.plan_turn(
            state,
            UserInput(text_ko="응"),
            must_target_count=2,
            mastery={},
        )
        assert any(
            t.id == ItemId("colloc:~하면_안_돼요") for t in constraints.must_target
        )
        assert any("안 돼요" in gp.pattern for gp in constraints.allowed_grammar)
    finally:
        col.close()


def test_collocation_requires_all_tokens_to_count_used() -> None:
    col = getEmptyCol()
    try:
        did = col.decks.id("Korean")
        col.decks.select(DeckId(did))
        note = col.newNote()
        note["Front"] = "사이"
        note["Back"] = "between"
        col.addNote(note)
        for card in note.cards():
            card.did = did
            card.flush()

        snapshot = build_deck_snapshot(col, [DeckId(did)], include_fsrs_metrics=False)
        planner = ConversationPlanner(snapshot)

        state1 = planner.initial_state(summary="x")
        _, c1, _ = planner.plan_turn(
            state1, UserInput(text_ko="응"), must_target_count=2, mastery={}
        )
        colloc1 = next(t for t in c1.must_target if t.type == "collocation")
        missed1 = planner.observe_turn(
            state1,
            constraints=c1,
            user_input=UserInput(text_ko="응"),
            assistant_reply_ko=f"{colloc1.surface_forms[0]}",
            follow_up_question_ko="",
        )
        assert str(colloc1.id) in missed1

        state2 = planner.initial_state(summary="x")
        _, c2, _ = planner.plan_turn(
            state2, UserInput(text_ko="응"), must_target_count=2, mastery={}
        )
        colloc2 = next(t for t in c2.must_target if t.type == "collocation")
        both = " ".join(colloc2.surface_forms)
        missed2 = planner.observe_turn(
            state2,
            constraints=c2,
            user_input=UserInput(text_ko="응"),
            assistant_reply_ko=both,
            follow_up_question_ko="",
        )
        assert str(colloc2.id) not in missed2
    finally:
        col.close()


def test_apply_missed_targets_records_non_lexeme_items() -> None:
    col = getEmptyCol()
    try:
        store = ConversationTelemetryStore(col)
        sid = store.start_session([1])
        cache = store.load_mastery_cache([])
        apply_missed_targets(
            telemetry=store,
            mastery_cache=cache,
            missed_item_ids=(
                "lexeme:의자",
                "gram:n1_n2_사이에_있다",
                "colloc:사이에_있어요",
                "repair:clarify",
            ),
        )

        for item_id, kind, value in (
            ("lexeme:의자", "lexeme", "의자"),
            ("gram:n1_n2_사이에_있다", "grammar", "gram:n1_n2_사이에_있다"),
            ("colloc:사이에_있어요", "collocation", "colloc:사이에_있어요"),
            ("repair:clarify", "repair", "clarify"),
        ):
            row = col.db.first(
                "select kind, value, mastery_json from elites_conversation_items where item_id=?",
                item_id,
            )
            assert row is not None
            got_kind, got_value, mastery_json = row
            assert got_kind == kind
            assert got_value == value
            mastery = json.loads(mastery_json)
            assert mastery["missed_target"] == 1

    finally:
        col.close()
