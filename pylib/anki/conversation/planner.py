from __future__ import annotations

from dataclasses import dataclass

from .snapshot import DeckSnapshot
from .types import (
    ConversationState,
    ForbiddenConstraints,
    GenerationInstructions,
    ItemId,
    LanguageConstraints,
    MustTarget,
    UserInput,
)


@dataclass(slots=True)
class PlannerState:
    conversation_summary: str
    last_assistant_turn_ko: str = ""
    last_user_turn_ko: str = ""
    turn_index: int = 0


class ConversationPlanner:
    def __init__(self, snapshot: DeckSnapshot) -> None:
        self._snapshot = snapshot

    def initial_state(self, *, summary: str) -> PlannerState:
        return PlannerState(conversation_summary=summary)

    def plan_turn(
        self,
        state: PlannerState,
        user_input: UserInput,
        *,
        must_target_count: int = 3,
        allowed_support_count: int = 60,
    ) -> tuple[ConversationState, LanguageConstraints, GenerationInstructions]:
        state.turn_index += 1

        lexemes = [item.lexeme for item in self._snapshot.items]

        must_targets = tuple(
            MustTarget(
                id=ItemId(f"lexeme:{lexeme}"),
                type="vocab",
                surface_forms=(lexeme,),
                priority=1.0,
            )
            for lexeme in lexemes[:must_target_count]
        )
        allowed_support = tuple(dict.fromkeys(lexemes[:allowed_support_count]))

        constraints = LanguageConstraints(
            must_target=must_targets,
            allowed_support=allowed_support,
            allowed_grammar=(),
            forbidden=ForbiddenConstraints(introduce_new_vocab=True, sentence_length_max=20),
        )

        instructions = GenerationInstructions(
            register="해요체",
            safe_mode=True,
            provide_follow_up_question=True,
            provide_micro_feedback=True,
            provide_suggested_english_intent=True,
            max_corrections=1,
        )

        conv_state = ConversationState(
            summary=state.conversation_summary,
            last_assistant_turn_ko=state.last_assistant_turn_ko,
            last_user_turn_ko=user_input.text_ko,
        )
        state.last_user_turn_ko = user_input.text_ko
        return conv_state, constraints, instructions
