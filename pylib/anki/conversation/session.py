# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from anki.collection import Collection
from anki.decks import DeckId

from .events import (
    apply_missed_targets,
    bump_assistant_used_lexemes,
    bump_user_used_lexemes,
    record_event_from_payload,
    record_turn_event,
)
from .gateway import ConversationGateway, ConversationProvider
from .planner import ConversationPlanner, PlannerState
from .prompts import SYSTEM_ROLE
from .redaction import redact_text
from .settings import ConversationSettings
from .snapshot import DeckSnapshot, build_deck_snapshot
from .telemetry import ConversationTelemetryStore, MasteryCache
from .types import ConversationRequest, ConversationResponse, UserInput
from .wrap import compute_session_wrap


@dataclass(frozen=True)
class TurnResult:
    user_input: UserInput
    response: ConversationResponse


@dataclass
class ConversationSession:
    snapshot: DeckSnapshot
    planner: ConversationPlanner
    telemetry: ConversationTelemetryStore
    gateway: ConversationGateway
    mastery_cache: MasteryCache
    state: PlannerState
    session_id: int
    lexeme_set: set[str]
    settings: ConversationSettings
    system_role: str = SYSTEM_ROLE

    @classmethod
    def start(
        cls,
        *,
        col: Collection,
        deck_ids: list[DeckId],
        settings: ConversationSettings,
        provider: ConversationProvider,
        topic_id: str | None = None,
        summary: str = "Conversation practice",
    ) -> "ConversationSession":
        snapshot = build_deck_snapshot(
            col,
            deck_ids,
            lexeme_field_index=settings.lexeme_field_index,
            lexeme_field_names=settings.lexeme_field_names,
            gloss_field_index=settings.gloss_field_index,
            gloss_field_names=settings.gloss_field_names,
            max_items=settings.snapshot_max_items,
        )
        planner = ConversationPlanner(snapshot)
        telemetry = ConversationTelemetryStore(col)
        session_id = telemetry.start_session(list(snapshot.deck_ids))

        snapshot_item_ids = [str(item.item_id) for item in snapshot.items]
        mastery_cache = telemetry.load_mastery_cache(snapshot_item_ids)

        gateway = ConversationGateway(
            provider=provider, max_rewrites=settings.max_rewrites
        )
        state = planner.initial_state(summary=summary, topic_id=topic_id)

        return cls(
            snapshot=snapshot,
            planner=planner,
            telemetry=telemetry,
            gateway=gateway,
            mastery_cache=mastery_cache,
            state=state,
            session_id=session_id,
            lexeme_set={i.lexeme for i in snapshot.items},
            settings=settings,
        )

    def run_turn(self, *, text_ko: str, confidence: str | None = None) -> TurnResult:
        redacted = redact_text(text_ko, self.settings.redaction_level)
        user_input = UserInput(text_ko=redacted.text, confidence=confidence)  # type: ignore[arg-type]

        conv_state, constraints, instructions = self.planner.plan_turn(
            self.state, user_input, mastery=self.mastery_cache
        )
        instructions = replace(instructions, safe_mode=self.settings.safe_mode)

        request = ConversationRequest(
            system_role=self.system_role,
            conversation_state=conv_state,
            user_input=user_input,
            language_constraints=constraints,
            generation_instructions=instructions,
        )
        response = self.gateway.run_turn(request=request)

        bump_user_used_lexemes(
            telemetry=self.telemetry,
            mastery_cache=self.mastery_cache,
            lexeme_set=self.lexeme_set,
            user_input=user_input,
        )
        bump_assistant_used_lexemes(
            telemetry=self.telemetry,
            mastery_cache=self.mastery_cache,
            lexeme_set=self.lexeme_set,
            response=response,
        )

        record_turn_event(
            telemetry=self.telemetry,
            session_id=self.session_id,
            turn_index=self.state.turn_index,
            user_input=user_input,
            response=response,
        )

        self.state.last_assistant_turn_ko = response.assistant_reply_ko
        missed = self.planner.observe_turn(
            self.state,
            constraints=constraints,
            user_input=user_input,
            assistant_reply_ko=response.assistant_reply_ko,
            follow_up_question_ko=response.follow_up_question_ko,
        )
        apply_missed_targets(
            telemetry=self.telemetry,
            mastery_cache=self.mastery_cache,
            missed_item_ids=missed,
        )

        return TurnResult(user_input=user_input, response=response)

    def log_event(
        self, payload: dict[str, Any], *, turn_index: int | None = None
    ) -> None:
        record_event_from_payload(
            telemetry=self.telemetry,
            mastery_cache=self.mastery_cache,
            session_id=self.session_id,
            turn_index=self.state.turn_index if turn_index is None else turn_index,
            payload=payload,
        )

    def wrap(self) -> dict[str, Any]:
        return compute_session_wrap(snapshot=self.snapshot, mastery=self.mastery_cache)

    def end(self, *, summary: dict[str, Any] | None = None) -> dict[str, Any]:
        wrap = self.wrap()
        if summary is None:
            summary = {}
        payload = {"wrap": wrap, **summary}
        self.telemetry.end_session(self.session_id, summary=payload)
        return wrap
