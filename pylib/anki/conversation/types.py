# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, NewType, TypedDict

JsonDict = dict[str, Any]
JsonList = list[Any]
ItemId = NewType("ItemId", str)


@dataclass(frozen=True)
class MustTarget:
    id: ItemId
    type: Literal["vocab", "grammar", "collocation", "repair", "new_word"]
    surface_forms: tuple[str, ...]
    priority: float
    scaffolding_required: bool = False
    exposure_stage: int | None = None
    gloss: str | None = None


@dataclass(frozen=True)
class GrammarPattern:
    id: ItemId
    pattern: str


@dataclass(frozen=True)
class ForbiddenConstraints:
    introduce_new_vocab: bool = True
    sentence_length_max: int = 20


@dataclass(frozen=True)
class LanguageConstraints:
    must_target: tuple[MustTarget, ...] = ()
    allowed_support: tuple[str, ...] = ()
    allowed_grammar: tuple[GrammarPattern, ...] = ()
    forbidden: ForbiddenConstraints = field(default_factory=ForbiddenConstraints)


@dataclass(frozen=True)
class GenerationInstructions:
    conversation_goal: str = "Continue the conversation naturally and keep it going."
    tone: str = "friendly"
    register: str = "해요체"
    provide_micro_feedback: bool = True
    provide_suggested_english_intent: bool = True
    max_corrections: int = 1
    safe_mode: bool = True


@dataclass(frozen=True)
class ConversationState:
    summary: str
    last_assistant_turn_ko: str = ""
    last_user_turn_ko: str = ""


@dataclass(frozen=True)
class UserInput:
    text_ko: str
    confidence: Literal["confident", "unsure", "guessing"] | None = None


@dataclass(frozen=True)
class ConversationRequest:
    system_role: str
    conversation_state: ConversationState
    user_input: UserInput
    language_constraints: LanguageConstraints
    generation_instructions: GenerationInstructions

    def to_prompt_text(self) -> str:
        def dedupe(items: list[str]) -> list[str]:
            seen: set[str] = set()
            out: list[str] = []
            for item in items:
                if item in seen:
                    continue
                seen.add(item)
                out.append(item)
            return out

        allowed_support = list(self.language_constraints.allowed_support)
        target_words: list[str] = []
        for t in self.language_constraints.must_target:
            target_words.extend(list(t.surface_forms))

        allowed_content_words = dedupe(allowed_support + target_words)
        target_words = dedupe(target_words)
        target_ids = dedupe([str(t.id) for t in self.language_constraints.must_target])

        allowed_str = ", ".join(allowed_content_words)
        target_str = ", ".join(target_words)
        target_ids_str = ", ".join(target_ids)

        targets_lines: list[str] = []
        for t in self.language_constraints.must_target:
            forms = ", ".join(t.surface_forms)
            targets_lines.append(f"- {t.id}: {{{forms}}}")

        new_vocab_allowed = not self.language_constraints.forbidden.introduce_new_vocab
        max_len = self.language_constraints.forbidden.sentence_length_max

        last_assistant = (self.conversation_state.last_assistant_turn_ko or "").strip()
        user_text = (self.user_input.text_ko or "").strip()

        return (
            f"Last assistant (KO): {last_assistant}\n"
            f"User (KO): {user_text}\n\n"
            f"For content words (nouns/verbs/adjectives/adverbs), use ONLY these Korean words: "
            f"{{{allowed_str}}}\n"
            f"Prioritize using these target words when natural: {{{target_str}}}\n"
            f"Valid target IDs (for targets_used): {{{target_ids_str}}}\n"
            f"New vocab allowed: {str(new_vocab_allowed).lower()}\n"
            f"Max tokens (approx): {max_len}\n\n"
            "Targets (use IDs in targets_used if used):\n"
            + ("\n".join(targets_lines) if targets_lines else "- (none)")
            + "\n\nReturn ONLY the JSON object."
        )


MicroFeedbackType = Literal["none", "correction", "praise"]


class MicroFeedbackDict(TypedDict):
    type: MicroFeedbackType
    content_ko: str
    content_en: str


@dataclass(frozen=True)
class ConversationResponse:
    assistant_reply_ko: str
    micro_feedback: MicroFeedbackDict | None = None
    suggested_user_intent_en: str | None = None
    targets_used: tuple[str, ...] = ()
    unexpected_tokens: tuple[str, ...] = ()
    word_glosses: tuple[tuple[str, str], ...] = ()  # (word, english_gloss) pairs

    @classmethod
    def from_json_dict(cls, data: JsonDict) -> "ConversationResponse":
        if not isinstance(data, dict):
            raise ValueError("response must be a JSON object")
        assistant_reply_ko = _required_str(data, "assistant_reply_ko")
        micro_feedback = data.get("micro_feedback")
        if micro_feedback is not None:
            if not isinstance(micro_feedback, dict):
                raise ValueError("micro_feedback must be an object or null")
            fb_type = micro_feedback.get("type")
            if fb_type not in ("none", "correction", "praise"):
                raise ValueError(
                    "micro_feedback.type must be one of: none, correction, praise"
                )
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

        # word_glosses can be dict (from LLM) or list of pairs
        raw_glosses = data.get("word_glosses", {})
        word_glosses: list[tuple[str, str]] = []
        if isinstance(raw_glosses, dict):
            for word, gloss in raw_glosses.items():
                if isinstance(word, str) and isinstance(gloss, str):
                    word_glosses.append((word, gloss))
        elif isinstance(raw_glosses, list):
            for item in raw_glosses:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    w, g = item
                    if isinstance(w, str) and isinstance(g, str):
                        word_glosses.append((w, g))

        return cls(
            assistant_reply_ko=assistant_reply_ko,
            micro_feedback=micro_feedback,
            suggested_user_intent_en=suggested_user_intent_en,
            targets_used=tuple(targets_used),
            unexpected_tokens=tuple(unexpected_tokens),
            word_glosses=tuple(word_glosses),
        )

    def to_json_dict(self) -> JsonDict:
        return {
            "assistant_reply_ko": self.assistant_reply_ko,
            "micro_feedback": self.micro_feedback,
            "suggested_user_intent_en": self.suggested_user_intent_en,
            "targets_used": list(self.targets_used),
            "unexpected_tokens": list(self.unexpected_tokens),
            "word_glosses": {word: gloss for word, gloss in self.word_glosses},
        }


def _required_str(data: JsonDict, key: str) -> str:
    val = data.get(key)
    if not isinstance(val, str) or not val:
        raise ValueError(f"{key} must be a non-empty string")
    return val
