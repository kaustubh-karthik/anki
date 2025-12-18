from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, NewType, TypedDict

JsonDict = dict[str, Any]
JsonList = list[Any]
ItemId = NewType("ItemId", str)


@dataclass(frozen=True, slots=True)
class MustTarget:
    id: ItemId
    type: Literal["vocab", "grammar", "collocation", "repair"]
    surface_forms: tuple[str, ...]
    priority: float


@dataclass(frozen=True, slots=True)
class GrammarPattern:
    id: ItemId
    pattern: str


@dataclass(frozen=True, slots=True)
class ForbiddenConstraints:
    introduce_new_vocab: bool = True
    sentence_length_max: int = 20


@dataclass(frozen=True, slots=True)
class LanguageConstraints:
    must_target: tuple[MustTarget, ...] = ()
    allowed_support: tuple[str, ...] = ()
    allowed_grammar: tuple[GrammarPattern, ...] = ()
    forbidden: ForbiddenConstraints = field(default_factory=ForbiddenConstraints)


@dataclass(frozen=True, slots=True)
class GenerationInstructions:
    conversation_goal: str = "Continue the conversation naturally and keep it going."
    tone: str = "friendly"
    register: str = "해요체"
    provide_follow_up_question: bool = True
    provide_micro_feedback: bool = True
    provide_suggested_english_intent: bool = True
    max_corrections: int = 1
    safe_mode: bool = True


@dataclass(frozen=True, slots=True)
class ConversationState:
    summary: str
    last_assistant_turn_ko: str = ""
    last_user_turn_ko: str = ""


@dataclass(frozen=True, slots=True)
class UserInput:
    text_ko: str
    confidence: Literal["confident", "unsure", "guessing"] | None = None


@dataclass(frozen=True, slots=True)
class ConversationRequest:
    system_role: str
    conversation_state: ConversationState
    user_input: UserInput
    language_constraints: LanguageConstraints
    generation_instructions: GenerationInstructions

    def to_json_dict(self) -> JsonDict:
        return {
            "system_role": self.system_role,
            "conversation_state": {
                "summary": self.conversation_state.summary,
                "last_assistant_turn_ko": self.conversation_state.last_assistant_turn_ko,
                "last_user_turn_ko": self.conversation_state.last_user_turn_ko,
            },
            "user_input": {
                "text_ko": self.user_input.text_ko,
                "confidence": self.user_input.confidence,
            },
            "language_constraints": {
                "must_target": [
                    {
                        "id": str(item.id),
                        "type": item.type,
                        "surface_forms": list(item.surface_forms),
                        "priority": item.priority,
                    }
                    for item in self.language_constraints.must_target
                ],
                "allowed_support": list(self.language_constraints.allowed_support),
                "allowed_grammar": [
                    {"id": str(item.id), "pattern": item.pattern}
                    for item in self.language_constraints.allowed_grammar
                ],
                "forbidden": {
                    "introduce_new_vocab": self.language_constraints.forbidden.introduce_new_vocab,
                    "sentence_length_max": self.language_constraints.forbidden.sentence_length_max,
                },
            },
            "generation_instructions": {
                "conversation_goal": self.generation_instructions.conversation_goal,
                "tone": self.generation_instructions.tone,
                "register": self.generation_instructions.register,
                "provide_follow_up_question": self.generation_instructions.provide_follow_up_question,
                "provide_micro_feedback": self.generation_instructions.provide_micro_feedback,
                "provide_suggested_english_intent": self.generation_instructions.provide_suggested_english_intent,
                "max_corrections": self.generation_instructions.max_corrections,
                "safe_mode": self.generation_instructions.safe_mode,
            },
        }


MicroFeedbackType = Literal["none", "correction", "praise"]


class MicroFeedbackDict(TypedDict):
    type: MicroFeedbackType
    content_ko: str
    content_en: str


@dataclass(frozen=True, slots=True)
class ConversationResponse:
    assistant_reply_ko: str
    follow_up_question_ko: str
    micro_feedback: MicroFeedbackDict | None = None
    suggested_user_intent_en: str | None = None
    targets_used: tuple[str, ...] = ()
    unexpected_tokens: tuple[str, ...] = ()

    @classmethod
    def from_json_dict(cls, data: JsonDict) -> "ConversationResponse":
        if not isinstance(data, dict):
            raise ValueError("response must be a JSON object")
        assistant_reply_ko = _required_str(data, "assistant_reply_ko")
        follow_up_question_ko = _required_str(data, "follow_up_question_ko")
        micro_feedback = data.get("micro_feedback")
        if micro_feedback is not None:
            if not isinstance(micro_feedback, dict):
                raise ValueError("micro_feedback must be an object or null")
            fb_type = micro_feedback.get("type")
            if fb_type not in ("none", "correction", "praise"):
                raise ValueError("micro_feedback.type must be one of: none, correction, praise")
            content_ko = micro_feedback.get("content_ko", "")
            content_en = micro_feedback.get("content_en", "")
            if not isinstance(content_ko, str) or not isinstance(content_en, str):
                raise ValueError("micro_feedback.content_ko/content_en must be strings")
            micro_feedback = {
                "type": fb_type,
                "content_ko": content_ko,
                "content_en": content_en,
            }

        suggested_user_intent_en = data.get("suggested_user_intent_en")
        if suggested_user_intent_en is not None and not isinstance(
            suggested_user_intent_en, str
        ):
            raise ValueError("suggested_user_intent_en must be a string or null")

        targets_used = data.get("targets_used", [])
        if not isinstance(targets_used, list) or not all(
            isinstance(x, str) for x in targets_used
        ):
            raise ValueError("targets_used must be a list of strings")

        unexpected_tokens = data.get("unexpected_tokens", [])
        if not isinstance(unexpected_tokens, list) or not all(
            isinstance(x, str) for x in unexpected_tokens
        ):
            raise ValueError("unexpected_tokens must be a list of strings")

        return cls(
            assistant_reply_ko=assistant_reply_ko,
            follow_up_question_ko=follow_up_question_ko,
            micro_feedback=micro_feedback,
            suggested_user_intent_en=suggested_user_intent_en,
            targets_used=tuple(targets_used),
            unexpected_tokens=tuple(unexpected_tokens),
        )

    def to_json_dict(self) -> JsonDict:
        return {
            "assistant_reply_ko": self.assistant_reply_ko,
            "follow_up_question_ko": self.follow_up_question_ko,
            "micro_feedback": self.micro_feedback,
            "suggested_user_intent_en": self.suggested_user_intent_en,
            "targets_used": list(self.targets_used),
            "unexpected_tokens": list(self.unexpected_tokens),
        }


def _required_str(data: JsonDict, key: str) -> str:
    val = data.get(key)
    if not isinstance(val, str) or not val:
        raise ValueError(f"{key} must be a non-empty string")
    return val
