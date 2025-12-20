# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from dataclasses import dataclass, field

from .bands import (
    FSRS5_DEFAULT_DECAY,
    RetrievabilityBand,
    classify_item,
    compute_retrievability,
)
from .collocations import select_collocation_targets
from .grammar import select_grammar_patterns
from .settings import ConversationSettings
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
    "안",
    "못",
    "좀",
    "더",
    "해주세요",
    "주세요",
    "해요",
    "해",
    "했어요",
    "할까요",
    "싶어요",
    "돼",
    "되요",
    "돼요",
    "맞아",
)


@dataclass
class NewWordState:
    lexeme: str
    gloss: str
    introduced_turn: int
    current_stage: int  # 1=comprehension, 2=highlighted, 3=scaffolded, 4=graduated
    exposure_count: int = 0
    successful_uses: int = 0


@dataclass
class PlannerState:
    conversation_summary: str
    last_assistant_turn_ko: str = ""
    last_user_turn_ko: str = ""
    last_suggested_user_reply_ko: str = ""
    turn_index: int = 0
    scheduled_reuse: dict[str, int] = field(default_factory=dict)
    last_must_target_ids: tuple[str, ...] = ()
    new_word_states: dict[str, NewWordState] = field(default_factory=dict)
    last_debug_vocab: dict[str, dict[str, object]] = field(default_factory=dict)


class ConversationPlanner:
    def __init__(
        self, snapshot: DeckSnapshot, *, settings: ConversationSettings | None = None
    ) -> None:
        self._snapshot = snapshot
        self._settings = settings or ConversationSettings()

    def initial_state(
        self, *, summary: str, topic_id: str | None = None
    ) -> PlannerState:
        if topic_id:
            summary = f"{summary} (topic={topic_id})"
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

        thresholds = (
            self._settings.band_cold_threshold,
            self._settings.band_fragile_threshold,
            self._settings.band_stretch_threshold,
        )
        item_by_id: dict[str, object] = {
            str(i.item_id): i for i in self._snapshot.items
        }
        band_by_id: dict[str, RetrievabilityBand] = {}
        r_by_id: dict[str, float | None] = {}
        for item_id, item in item_by_id.items():
            m = mastery.get(item_id, {}) if mastery else {}
            stability = getattr(item, "stability", None)
            last_review_date = getattr(item, "last_review_date", None)
            today = self._snapshot.today
            decay = getattr(item, "decay", None) or FSRS5_DEFAULT_DECAY
            if (
                isinstance(stability, (int, float))
                and stability > 0
                and isinstance(last_review_date, int)
                and isinstance(today, int)
            ):
                elapsed = max(0.0, float(today - last_review_date))
                r = compute_retrievability(float(stability), elapsed, float(decay))
                band = classify_item(r, m, thresholds=thresholds)
                r_by_id[item_id] = r
            else:
                band = RetrievabilityBand.STRETCH
                r_by_id[item_id] = None
            band_by_id[item_id] = band

        candidates = [
            i
            for i in self._snapshot.items
            if band_by_id.get(str(i.item_id), RetrievabilityBand.STRETCH)
            != RetrievabilityBand.COLD
        ]
        candidates.sort(
            key=lambda i: (
                -_candidate_score(
                    self._snapshot.today,
                    i,
                    mastery.get(str(i.item_id), {}) if mastery else {},
                ),
                i.lexeme,
            )
        )
        items_by_band: dict[RetrievabilityBand, list[object]] = {
            RetrievabilityBand.FRAGILE: [],
            RetrievabilityBand.STRETCH: [],
            RetrievabilityBand.SUPPORT: [],
        }
        for item in candidates:
            b = band_by_id.get(
                str(getattr(item, "item_id")), RetrievabilityBand.STRETCH
            )
            if b in items_by_band:
                items_by_band[b].append(item)
        stretch_lexemes = [
            getattr(i, "lexeme") for i in items_by_band[RetrievabilityBand.STRETCH]
        ]
        support_lexemes = [
            getattr(i, "lexeme") for i in items_by_band[RetrievabilityBand.SUPPORT]
        ]
        fragile_lexemes = [
            getattr(i, "lexeme") for i in items_by_band[RetrievabilityBand.FRAGILE]
        ]

        debug_vocab: dict[str, dict[str, object]] = {}
        for item_id, item in item_by_id.items():
            band = band_by_id.get(item_id, RetrievabilityBand.STRETCH)
            if band == RetrievabilityBand.COLD:
                continue
            lexeme = getattr(item, "lexeme", "")
            if not isinstance(lexeme, str) or not lexeme:
                continue
            debug_vocab[lexeme] = {"band": band.value, "r": r_by_id.get(item_id)}
        for nw in state.new_word_states.values():
            if 1 <= int(nw.current_stage) <= 4:
                debug_vocab[nw.lexeme] = {
                    "band": RetrievabilityBand.NEW.value,
                    "r": None,
                    "stage": int(nw.current_stage),
                }
        state.last_debug_vocab = debug_vocab

        # 1) due items first (micro-spacing)
        due_ids = [
            item_id
            for item_id, due_turn in state.scheduled_reuse.items()
            if due_turn <= state.turn_index
        ]
        due_ids.sort()

        must_targets: list[MustTarget] = []
        used_lexemes: set[str] = set()
        fragile_count = 0

        active_new_words: list[NewWordState] = []
        if self._settings.allow_new_words:
            active_new_words = [
                nw
                for nw in state.new_word_states.values()
                if 1 <= int(nw.current_stage) <= 3 and nw.lexeme not in used_lexemes
            ]
            active_new_words.sort(
                key=lambda s: (s.current_stage, s.introduced_turn, s.lexeme)
            )
        reserved_new_word_slots = 1 if active_new_words else 0
        target_budget = max(0, must_target_count - reserved_new_word_slots)

        for item_id in due_ids:
            if band_by_id.get(item_id) == RetrievabilityBand.COLD:
                continue
            lexeme = item_id.removeprefix("lexeme:")
            if lexeme in used_lexemes:
                continue
            scaffolding_required = band_by_id.get(item_id) in (
                RetrievabilityBand.FRAGILE,
                RetrievabilityBand.NEW,
            )
            if band_by_id.get(item_id) == RetrievabilityBand.FRAGILE:
                fragile_count += 1
            must_targets.append(
                MustTarget(
                    id=ItemId(item_id),
                    type="vocab",
                    surface_forms=(lexeme,),
                    priority=1.0,
                    scaffolding_required=scaffolding_required,
                )
            )
            used_lexemes.add(lexeme)
            if len(must_targets) >= target_budget:
                break

        # 2) primary targets from STRETCH
        for lexeme in stretch_lexemes:
            if len(must_targets) >= target_budget:
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

        # 3) at most 1 FRAGILE per turn (scaffolded)
        if len(must_targets) < target_budget and fragile_count < 1 and fragile_lexemes:
            for lexeme in fragile_lexemes:
                if len(must_targets) >= target_budget:
                    break
                if lexeme in used_lexemes:
                    continue
                must_targets.append(
                    MustTarget(
                        id=ItemId(f"lexeme:{lexeme}"),
                        type="vocab",
                        surface_forms=(lexeme,),
                        priority=1.0,
                        scaffolding_required=True,
                    )
                )
                used_lexemes.add(lexeme)
                fragile_count += 1
                break

        # 4) fill any remaining slots from SUPPORT
        for lexeme in support_lexemes:
            if len(must_targets) >= target_budget:
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

        # 5) optionally add one new-word pipeline target
        if active_new_words and len(must_targets) < must_target_count:
            nw = next(
                (w for w in active_new_words if w.lexeme not in used_lexemes), None
            )
            if nw is not None:
                must_targets.append(
                    MustTarget(
                        id=ItemId(f"lexeme:{nw.lexeme}"),
                        type="new_word",
                        surface_forms=(nw.lexeme,),
                        priority=0.9,
                        scaffolding_required=True,
                        exposure_stage=int(nw.current_stage),
                        gloss=nw.gloss,
                    )
                )
                used_lexemes.add(nw.lexeme)

        # 3) optionally add collocation targets if there is room
        lexical_targets = tuple(
            t.surface_forms[0] for t in must_targets if t.type == "vocab"
        )
        for colloc in select_collocation_targets(
            lexical_targets=lexical_targets, max_targets=1
        ):
            if len(must_targets) >= must_target_count:
                break
            must_targets.append(colloc)
        # For the AI: only include deck vocabulary (no BASE_ALLOWED_SUPPORT)
        # The system prompt tells the AI it can use basic particles freely
        allowed_support_seed = tuple(
            dict.fromkeys(tuple(support_lexemes) + tuple(stretch_lexemes[:20]))
        )
        allowed_support_seed = allowed_support_seed[:allowed_support_count]
        allowed_support_for_ai = tuple(
            dict.fromkeys(
                allowed_support_seed
                + tuple(sf for t in must_targets for sf in t.surface_forms)
            )
        )

        constraints = LanguageConstraints(
            must_target=tuple(must_targets),
            allowed_support=allowed_support_for_ai,
            allowed_grammar=select_grammar_patterns(
                must_targets=tuple(sf for t in must_targets for sf in t.surface_forms)
            ),
            forbidden=ForbiddenConstraints(
                introduce_new_vocab=not self._settings.allow_new_words,
                sentence_length_max=20,
            ),
            # Note: BASE_ALLOWED_SUPPORT is still used for validation in gateway.py
            # but we don't pass it to the AI - the prompt tells it to use particles freely
        )

        instructions = GenerationInstructions(
            register="해요체",
            safe_mode=True,
            provide_micro_feedback=True,
            provide_suggested_english_intent=True,
            max_corrections=1,
        )

        conv_state = ConversationState(
            summary=state.conversation_summary,
            last_assistant_turn_ko=state.last_assistant_turn_ko,
            last_user_turn_ko=user_input.text_ko,
            last_suggested_user_reply_ko=state.last_suggested_user_reply_ko,
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
    ) -> list[str]:
        user_tokens = set(tokenize_for_validation(user_input.text_ko))
        assistant_tokens = set(tokenize_for_validation(assistant_reply_ko))
        missed: list[str] = []
        successful_new_words: set[str] = set()
        for target in constraints.must_target:
            item_id = str(target.id)
            if target.type == "collocation":
                used = all(
                    sf in user_tokens or sf in assistant_tokens
                    for sf in target.surface_forms
                )
            else:
                used = any(
                    sf in user_tokens or sf in assistant_tokens
                    for sf in target.surface_forms
                )
            if not used:
                # recycle next turn to fight avoidance
                state.scheduled_reuse[item_id] = min(
                    state.scheduled_reuse.get(item_id, state.turn_index + 1),
                    state.turn_index + 1,
                )
                missed.append(item_id)
            elif target.type == "new_word":
                lexeme = target.surface_forms[0] if target.surface_forms else ""
                if lexeme:
                    successful_new_words.add(lexeme)

        # Advance new-word pipeline based on observed usage.
        for lexeme, nw in list(state.new_word_states.items()):
            if nw.current_stage >= 4:
                continue
            if lexeme in assistant_tokens:
                # exposure_count is initialized to 1 on the turn the word is first seen;
                # advance stages only on subsequent exposures.
                if nw.introduced_turn != state.turn_index:
                    nw.exposure_count += 1
                if nw.current_stage == 1 and nw.exposure_count >= 2:
                    nw.current_stage = 2
                elif nw.current_stage == 2 and nw.exposure_count >= 3:
                    nw.current_stage = 3
            if lexeme in user_tokens and nw.current_stage >= 3:
                nw.successful_uses += 1
                if nw.successful_uses >= 1:
                    nw.current_stage = 4
            if lexeme in successful_new_words and nw.current_stage >= 3:
                # assistant used it during stage 3: still requires user success to graduate
                pass
        return missed


def _rustiness(stability: float | None) -> float:
    if stability is None:
        return 0.0
    return 1.0 / (1.0 + max(stability, 0.0))


def _candidate_score(today: int | None, item: object, mastery: dict[str, int]) -> float:
    stability = getattr(item, "stability", None)
    rustiness = _rustiness(stability)
    dont_know = mastery.get("dont_know", 0)
    practice_again = mastery.get("practice_again", 0)
    missed_target = mastery.get("missed_target", 0)
    lookup_count = mastery.get("lookup_count", 0)
    lookup_ms_total = mastery.get("lookup_ms_total", 0)
    if not isinstance(dont_know, int):
        dont_know = 0
    if not isinstance(practice_again, int):
        practice_again = 0
    if not isinstance(missed_target, int):
        missed_target = 0
    if not isinstance(lookup_count, int):
        lookup_count = 0
    if not isinstance(lookup_ms_total, int):
        lookup_ms_total = 0
    overdue_score = _overdue_score(today, item)

    difficulty_score = 0.0
    difficulty = getattr(item, "difficulty", None)
    if isinstance(difficulty, (int, float)):
        # FSRS difficulty is higher => harder; keep the weight small to avoid overpowering stability/overdue.
        difficulty_score = max(0.0, min(1.0, float(difficulty) / 10.0)) * 0.1

    avg_lookup_ms = (lookup_ms_total / lookup_count) if lookup_count > 0 else 0.0
    lookup_score = (
        min(2.0, float(lookup_count)) * 0.05 + min(2.0, avg_lookup_ms / 1500.0) * 0.05
    )

    return (
        rustiness
        + overdue_score
        + dont_know * 0.5
        + practice_again * 0.25
        + missed_target * 0.2
        + difficulty_score
        + lookup_score
    )


def _overdue_score(today: int | None, item: object) -> float:
    if today is None:
        return 0.0
    due = getattr(item, "due", None)
    ivl = getattr(item, "ivl", None)
    queue = getattr(item, "card_queue", None)
    if not isinstance(due, int) or not isinstance(ivl, int) or ivl <= 0:
        return 0.0
    if queue not in (2,):  # QUEUE_TYPE_REV
        return 0.0
    overdue_days = max(0, today - due)
    # normalize by interval to avoid always preferring long-interval cards
    ratio = min(2.0, overdue_days / ivl)
    return ratio * 0.2
