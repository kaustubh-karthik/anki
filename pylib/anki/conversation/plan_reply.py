# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .openai import LLMOutputParseError, OpenAIResponsesJsonClient
from .types import (
    ConversationState,
    ForbiddenConstraints,
    GenerationInstructions,
    LanguageConstraints,
)
from .validation import validate_tokens


@dataclass(frozen=True)
class PlanReplyRequest:
    system_role: str
    conversation_state: ConversationState
    draft_ko: str
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
        allowed_stretch = list(self.language_constraints.allowed_stretch)
        reinforced_words = list(self.language_constraints.reinforced_words)
        target_words: list[str] = []
        for t in self.language_constraints.must_target:
            if t.type == "vocab":
                target_words.extend(list(t.surface_forms))

        allowed_content_words = dedupe(
            allowed_support + allowed_stretch + reinforced_words + target_words
        )
        target_words = dedupe(target_words)
        reinforced_words = dedupe(reinforced_words)
        stretch_words = dedupe(allowed_stretch)
        support_words = dedupe(allowed_support)

        allowed_str = ", ".join(allowed_content_words)
        target_str = ", ".join(target_words)
        reinforced_str = ", ".join(reinforced_words)
        stretch_str = ", ".join(stretch_words)
        support_str = ", ".join(support_words)

        targets_lines: list[str] = []
        for t in self.language_constraints.must_target:
            forms = ", ".join(t.surface_forms)
            targets_lines.append(f"- {t.id}: {{{forms}}}")

        new_vocab_allowed = not self.language_constraints.forbidden.introduce_new_vocab
        max_len = self.language_constraints.forbidden.sentence_length_max

        last_assistant = (self.conversation_state.last_assistant_turn_ko or "").strip()
        draft = (self.draft_ko or "").strip()

        return (
            f"Last assistant (KO): {last_assistant}\n"
            f"User draft (KO): {draft}\n\n"
            "Task: Rewrite the user draft into a natural Korean reply that fits the conversation.\n"
            "- Preserve the user's intended meaning as much as possible.\n"
            "- If the draft is already good, return improved/natural variants.\n"
            "- Keep replies short and natural, and do NOT ask a question.\n\n"
            f"For content words, use ONLY these Korean words: {{{allowed_str}}}\n"
            f"Target words (prefer at least one if natural): {{{target_str}}}\n"
            f"Reinforced words (use if they fit naturally): {{{reinforced_str}}}\n"
            f"Stretch words: {{{stretch_str}}}\n"
            f"Support words: {{{support_str}}}\n"
            f"New vocab allowed: {str(new_vocab_allowed).lower()}\n"
            f"Max tokens (approx): {max_len}\n\n"
            "Targets (use IDs in notes if relevant):\n"
            + ("\n".join(targets_lines) if targets_lines else "- (none)")
            + "\n\nReturn ONLY the JSON object."
        )


@dataclass(frozen=True)
class PlanReplyResponse:
    options_ko: tuple[str, ...]
    notes_en: str | None = None
    unexpected_tokens: tuple[str, ...] = ()

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "PlanReplyResponse":
        if not isinstance(data, dict):
            raise ValueError("plan-reply response must be a JSON object")
        options = data.get("options_ko")
        if (
            not isinstance(options, list)
            or not options
            or not all(isinstance(x, str) and x.strip() for x in options)
        ):
            raise ValueError("options_ko must be a non-empty list of strings")
        if len(options) > 5:
            raise ValueError("options_ko too long")
        notes_en = data.get("notes_en")
        if notes_en is not None and not isinstance(notes_en, str):
            raise ValueError("notes_en must be a string or null")
        unexpected = data.get("unexpected_tokens", [])
        if not isinstance(unexpected, list) or not all(
            isinstance(x, str) for x in unexpected
        ):
            raise ValueError("unexpected_tokens must be a list of strings")
        return cls(
            options_ko=tuple(options),
            notes_en=notes_en,
            unexpected_tokens=tuple(unexpected),
        )

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


@dataclass
class PlanReplyGateway:
    provider: PlanReplyProvider
    max_rewrites: int = 2

    def run(self, *, request: PlanReplyRequest) -> PlanReplyResponse:
        last_error: Exception | None = None
        for attempt in range(self.max_rewrites + 1):
            try:
                raw = self.provider.generate(request=request)
            except LLMOutputParseError as e:
                last_error = e
                if attempt >= self.max_rewrites:
                    raise
                request = _rewrite_request(request, reason=f"invalid_json:{e.reason}")
                continue
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
                    v = validate_tokens(option, request.language_constraints)
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

            if any("?" in opt for opt in response.options_ko):
                if attempt >= self.max_rewrites:
                    raise ValueError("contract violation: options_must_not_be_questions")
                request = _rewrite_request(request, reason="options_must_not_be_questions")
                continue

            # enforce sentence length budget across each option
            max_tokens = request.language_constraints.forbidden.sentence_length_max
            if max_tokens > 0:
                for opt in response.options_ko:
                    if len(opt.split()) > max_tokens:
                        if attempt >= self.max_rewrites:
                            raise ValueError("contract violation: sentence_length_max")
                        request = _rewrite_request(
                            request, reason="sentence_length_max"
                        )
                        break
                else:
                    return response
                continue

            return response

        assert last_error is not None
        raise last_error


def _rewrite_request(request: PlanReplyRequest, *, reason: str) -> PlanReplyRequest:
    system_role = _with_rewrite_directive(
        system_role=request.system_role,
        reason=reason,
        directive=(
            "Return ONLY a valid JSON object with keys: options_ko (list[str]), notes_en (string|null), unexpected_tokens (list[str]). "
            "Do not introduce unexpected tokens."
        ),
    )
    return PlanReplyRequest(
        system_role=system_role,
        conversation_state=request.conversation_state,
        draft_ko=request.draft_ko,
        language_constraints=request.language_constraints,
        generation_instructions=request.generation_instructions,
    )


def _with_rewrite_directive(*, system_role: str, reason: str, directive: str) -> str:
    marker = "\n\nRewrite required:"
    if marker in system_role:
        system_role = system_role.split(marker, 1)[0]
    return (
        system_role
        + marker
        + " your previous output violated the contract ("
        + reason
        + "). "
        + directive
    )


@dataclass
class FakePlanReplyProvider(PlanReplyProvider):
    scripted: list[dict[str, Any]]
    i: int = 0

    def generate(self, *, request: PlanReplyRequest) -> dict[str, Any]:
        if self.i >= len(self.scripted):
            return {
                "options_ko": ["네, 알겠어요."],
                "notes_en": None,
                "unexpected_tokens": [],
            }
        out = self.scripted[self.i]
        self.i += 1
        return out


@dataclass
class OpenAIPlanReplyProvider(PlanReplyProvider):
    api_key: str
    model: str = "gpt-4o-mini"
    timeout_s: float | tuple[float, float] = (5.0, 60.0)
    max_output_tokens: int = 256
    _client: OpenAIResponsesJsonClient = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = OpenAIResponsesJsonClient(
            api_key=self.api_key,
            model=self.model,
            timeout_s=self.timeout_s,
            max_output_tokens=self.max_output_tokens,
        )

    def generate(self, *, request: PlanReplyRequest) -> dict[str, Any]:
        return self._client.request_json_with_user_text(
            system_role=request.system_role, user_text=request.to_prompt_text()
        )
