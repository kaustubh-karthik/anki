# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from dataclasses import dataclass

from anki.conversation.gateway import ConversationGateway, ConversationProvider
from anki.conversation.planner import ConversationPlanner
from anki.conversation.snapshot import build_deck_snapshot
from anki.conversation.telemetry import ConversationTelemetryStore
from anki.conversation.types import (
    ConversationRequest,
    ConversationState,
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
        assert any(item.lexeme == "사이" for item in snapshot.items)
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
        must_target=(MustTarget(id=ItemId("lexeme:의자"), type="vocab", surface_forms=("의자",), priority=1.0),),
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
