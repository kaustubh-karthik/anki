from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from .types import (
    ConversationState,
    ForbiddenConstraints,
    GenerationInstructions,
    LanguageConstraints,
)
from .validation import validate_tokens


@dataclass(frozen=True, slots=True)
class PlanReplyRequest:
    system_role: str
    conversation_state: ConversationState
    intent_en: str
    language_constraints: LanguageConstraints
    generation_instructions: GenerationInstructions

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "conversation_state": {
                "summary": self.conversation_state.summary,
                "last_assistant_turn_ko": self.conversation_state.last_assistant_turn_ko,
                "last_user_turn_ko": self.conversation_state.last_user_turn_ko,
            },
            "intent_en": self.intent_en,
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
                "tone": self.generation_instructions.tone,
                "register": self.generation_instructions.register,
                "safe_mode": self.generation_instructions.safe_mode,
            },
        }


@dataclass(frozen=True, slots=True)
class PlanReplyResponse:
    options_ko: tuple[str, ...]
    notes_en: str | None = None
    unexpected_tokens: tuple[str, ...] = ()

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "PlanReplyResponse":
        if not isinstance(data, dict):
            raise ValueError("plan-reply response must be a JSON object")
        options = data.get("options_ko")
        if not isinstance(options, list) or not options or not all(
            isinstance(x, str) and x.strip() for x in options
        ):
            raise ValueError("options_ko must be a non-empty list of strings")
        if len(options) > 5:
            raise ValueError("options_ko too long")
        notes_en = data.get("notes_en")
        if notes_en is not None and not isinstance(notes_en, str):
            raise ValueError("notes_en must be a string or null")
        unexpected = data.get("unexpected_tokens", [])
        if not isinstance(unexpected, list) or not all(isinstance(x, str) for x in unexpected):
            raise ValueError("unexpected_tokens must be a list of strings")
        return cls(options_ko=tuple(options), notes_en=notes_en, unexpected_tokens=tuple(unexpected))

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "options_ko": list(self.options_ko),
            "notes_en": self.notes_en,
            "unexpected_tokens": list(self.unexpected_tokens),
        }


class PlanReplyProvider(ABC):
    @abstractmethod
    def generate(self, *, request: PlanReplyRequest) -> dict[str, Any]:
        """Return a parsed JSON object matching PlanReplyResponse."""


@dataclass(slots=True)
class PlanReplyGateway:
    provider: PlanReplyProvider
    max_rewrites: int = 2

    def run(self, *, request: PlanReplyRequest) -> PlanReplyResponse:
        last_error: Exception | None = None
        for attempt in range(self.max_rewrites + 1):
            raw = self.provider.generate(request=request)
            try:
                response = PlanReplyResponse.from_json_dict(raw)
            except Exception as e:
                last_error = e
                if attempt >= self.max_rewrites:
                    raise
                request = _rewrite_request(request, reason=f"invalid_json:{e}")
                continue

            if request.generation_instructions.safe_mode:
                unexpected: list[str] = []
                for option in response.options_ko:
                    v = validate_tokens(option, "", request.language_constraints)
                    unexpected.extend(v.unexpected_tokens)
                unexpected_unique = tuple(dict.fromkeys(unexpected))
                if unexpected_unique:
                    if attempt >= self.max_rewrites:
                        return PlanReplyResponse(
                            options_ko=response.options_ko,
                            notes_en=response.notes_en,
                            unexpected_tokens=unexpected_unique,
                        )
                    request = _rewrite_request(
                        request,
                        reason=f"unexpected_tokens:{','.join(unexpected_unique)}",
                    )
                    continue

            # enforce sentence length budget across each option
            max_tokens = request.language_constraints.forbidden.sentence_length_max
            if max_tokens > 0:
                for opt in response.options_ko:
                    if len(opt.split()) > max_tokens:
                        if attempt >= self.max_rewrites:
                            raise ValueError("contract violation: sentence_length_max")
                        request = _rewrite_request(request, reason="sentence_length_max")
                        break
                else:
                    return response
                continue

            return response

        assert last_error is not None
        raise last_error


def _rewrite_request(request: PlanReplyRequest, *, reason: str) -> PlanReplyRequest:
    system_role = (
        request.system_role
        + "\n\n"
        + "Rewrite required: your previous output violated the contract ("
        + reason
        + "). Return ONLY a valid JSON object with keys: options_ko (list[str]), notes_en (string|null), unexpected_tokens (list[str]). "
        + "Do not introduce unexpected tokens."
    )
    return PlanReplyRequest(
        system_role=system_role,
        conversation_state=request.conversation_state,
        intent_en=request.intent_en,
        language_constraints=request.language_constraints,
        generation_instructions=request.generation_instructions,
    )


@dataclass(slots=True)
class FakePlanReplyProvider(PlanReplyProvider):
    scripted: list[dict[str, Any]]
    i: int = 0

    def generate(self, *, request: PlanReplyRequest) -> dict[str, Any]:
        if self.i >= len(self.scripted):
            return {"options_ko": ["네, 알겠어요."], "notes_en": None, "unexpected_tokens": []}
        out = self.scripted[self.i]
        self.i += 1
        return json.loads(json.dumps(out, ensure_ascii=False))

