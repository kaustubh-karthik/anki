from __future__ import annotations

from dataclasses import dataclass, field

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
from .validation import tokenize_for_validation

BASE_ALLOWED_SUPPORT: tuple[str, ...] = (
    # Minimal Korean glue vocabulary to make safe-mode usable.
    "이",
    "가",
    "은",
    "는",
    "을",
    "를",
    "에",
    "에서",
    "로",
    "으로",
    "와",
    "과",
    "랑",
    "하고",
    "도",
    "만",
    "그리고",
    "그래서",
    "근데",
    "그런데",
    "네",
    "응",
    "아니요",
    "맞아요",
    "아니에요",
    "있어요",
    "없어요",
    "있어",
    "없어",
    "뭐",
    "뭐가",
    "뭐예요",
    "어디",
    "어디예요",
    "여기",
    "거기",
    "저기",
    "지금",
    "오늘",
    "내일",
    "좋아요",
    "싫어요",
)


@dataclass(slots=True)
class PlannerState:
    conversation_summary: str
    last_assistant_turn_ko: str = ""
    last_user_turn_ko: str = ""
    turn_index: int = 0
    scheduled_reuse: dict[str, int] = field(default_factory=dict)
    last_must_target_ids: tuple[str, ...] = ()


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
        mastery: dict[str, dict[str, int]] | None = None,
        reuse_delay_turns: int = 3,
    ) -> tuple[ConversationState, LanguageConstraints, GenerationInstructions]:
        state.turn_index += 1

        candidates = list(self._snapshot.items)
        candidates.sort(
            key=lambda i: (
                -_priority_score(i.stability, mastery.get(str(i.item_id), {}) if mastery else {}),
                i.lexeme,
            )
        )
        lexemes = [item.lexeme for item in candidates]

        # 1) due items first (micro-spacing)
        due_ids = [
            item_id
            for item_id, due_turn in state.scheduled_reuse.items()
            if due_turn <= state.turn_index
        ]
        due_ids.sort()

        must_targets: list[MustTarget] = []
        used_lexemes: set[str] = set()

        for item_id in due_ids:
            lexeme = item_id.removeprefix("lexeme:")
            if lexeme in used_lexemes:
                continue
            must_targets.append(
                MustTarget(
                    id=ItemId(item_id),
                    type="vocab",
                    surface_forms=(lexeme,),
                    priority=1.0,
                )
            )
            used_lexemes.add(lexeme)
            if len(must_targets) >= must_target_count:
                break

        # 2) fill remaining slots by priority
        for lexeme in lexemes:
            if len(must_targets) >= must_target_count:
                break
            if lexeme in used_lexemes:
                continue
            must_targets.append(
                MustTarget(
                    id=ItemId(f"lexeme:{lexeme}"),
                    type="vocab",
                    surface_forms=(lexeme,),
                    priority=1.0,
                )
            )
            used_lexemes.add(lexeme)
        allowed_support = tuple(
            dict.fromkeys(BASE_ALLOWED_SUPPORT + tuple(lexemes[:allowed_support_count]))
        )

        constraints = LanguageConstraints(
            must_target=tuple(must_targets),
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
        state.last_must_target_ids = tuple(str(t.id) for t in must_targets)
        for t in must_targets:
            state.scheduled_reuse[str(t.id)] = state.turn_index + reuse_delay_turns
        return conv_state, constraints, instructions

    def observe_turn(
        self,
        state: PlannerState,
        *,
        constraints: LanguageConstraints,
        user_input: UserInput,
        assistant_reply_ko: str,
        follow_up_question_ko: str,
    ) -> None:
        user_tokens = set(tokenize_for_validation(user_input.text_ko))
        assistant_tokens = set(
            tokenize_for_validation(assistant_reply_ko)
            + tokenize_for_validation(follow_up_question_ko)
        )
        for target in constraints.must_target:
            item_id = str(target.id)
            used = any(sf in user_tokens or sf in assistant_tokens for sf in target.surface_forms)
            if not used:
                # recycle next turn to fight avoidance
                state.scheduled_reuse[item_id] = min(
                    state.scheduled_reuse.get(item_id, state.turn_index + 1),
                    state.turn_index + 1,
                )


def _rustiness(stability: float | None) -> float:
    if stability is None:
        return 0.0
    return 1.0 / (1.0 + max(stability, 0.0))


def _priority_score(stability: float | None, mastery: dict[str, int]) -> float:
    rustiness = _rustiness(stability)
    dont_know = mastery.get("dont_know", 0)
    practice_again = mastery.get("practice_again", 0)
    if not isinstance(dont_know, int):
        dont_know = 0
    if not isinstance(practice_again, int):
        practice_again = 0
    return rustiness + dont_know * 0.5 + practice_again * 0.25
