# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .contract import check_response_against_request
from .openai import LLMOutputParseError, OpenAIResponsesJsonClient
from .types import ConversationRequest, ConversationResponse, MustTarget
from .validation import _JOSA_SUFFIXES, tokenize_for_validation, validate_tokens


class ConversationProvider(ABC):
    @abstractmethod
    def generate(self, *, request: ConversationRequest) -> dict[str, Any]:
        """Return a parsed JSON object matching ConversationResponse."""


@dataclass
class OpenAIConversationProvider(ConversationProvider):
    api_key: str
    model: str = "gpt-4o-mini"
    timeout_s: float | tuple[float, float] = (5.0, 60.0)  # Fast models respond quickly
    max_output_tokens: int = 256
    _client: OpenAIResponsesJsonClient = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = OpenAIResponsesJsonClient(
            api_key=self.api_key,
            model=self.model,
            timeout_s=self.timeout_s,
            max_output_tokens=self.max_output_tokens,
        )

    def generate(self, *, request: ConversationRequest) -> dict[str, Any]:
        return self._client.request_json_with_user_text(
            system_role=request.system_role, user_text=request.to_prompt_text()
        )


@dataclass
class ConversationGateway:
    provider: ConversationProvider
    max_rewrites: int = 2

    def run_turn(self, *, request: ConversationRequest) -> ConversationResponse:
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
                response = ConversationResponse.from_json_dict(raw)
            except Exception as e:
                last_error = e
                if attempt >= self.max_rewrites:
                    raise
                request = _rewrite_request(request, reason=f"invalid_json:{e}")
                continue

            computed_targets_used = _targets_used_in_text(
                response.assistant_reply_ko, request.language_constraints.must_target
            )
            if computed_targets_used != response.targets_used:
                response = ConversationResponse(
                    assistant_reply_ko=response.assistant_reply_ko,
                    micro_feedback=response.micro_feedback,
                    suggested_user_intent_en=response.suggested_user_intent_en,
                    suggested_user_reply_ko=response.suggested_user_reply_ko,
                    suggested_user_reply_en=response.suggested_user_reply_en,
                    targets_used=computed_targets_used,
                    unexpected_tokens=response.unexpected_tokens,
                    word_glosses=response.word_glosses,
                )

            if request.generation_instructions.safe_mode:
                if request.language_constraints.must_target and not response.targets_used:
                    if attempt >= self.max_rewrites:
                        return ConversationResponse(
                            assistant_reply_ko=response.assistant_reply_ko,
                            micro_feedback=response.micro_feedback,
                            suggested_user_intent_en=response.suggested_user_intent_en,
                            suggested_user_reply_ko=response.suggested_user_reply_ko,
                            suggested_user_reply_en=response.suggested_user_reply_en,
                            targets_used=response.targets_used,
                            unexpected_tokens=response.unexpected_tokens,
                            word_glosses=response.word_glosses,
                        )
                    request = _rewrite_request(
                        request, reason="missing_targets"
                    )
                    continue

                assistant_validation = validate_tokens(
                    response.assistant_reply_ko, request.language_constraints
                )
                assistant_unexpected = assistant_validation.unexpected_tokens
                suggested_unexpected: tuple[str, ...] = ()
                if isinstance(response.suggested_user_reply_ko, str) and response.suggested_user_reply_ko.strip():
                    suggested_validation = validate_tokens(
                        response.suggested_user_reply_ko, request.language_constraints
                    )
                    suggested_unexpected = suggested_validation.unexpected_tokens
                    extra_suggested = [
                        token
                        for token in suggested_unexpected
                        if token not in assistant_unexpected
                    ]
                    if extra_suggested:
                        if attempt >= self.max_rewrites:
                            return ConversationResponse(
                                assistant_reply_ko=response.assistant_reply_ko,
                                micro_feedback=response.micro_feedback,
                                suggested_user_intent_en=response.suggested_user_intent_en,
                                suggested_user_reply_ko=response.suggested_user_reply_ko,
                                suggested_user_reply_en=response.suggested_user_reply_en,
                                targets_used=response.targets_used,
                                unexpected_tokens=assistant_unexpected,
                                word_glosses=response.word_glosses,
                            )
                        request = _rewrite_request(
                            request,
                            reason=f"unexpected_tokens_suggested_reply:{','.join(extra_suggested)}",
                        )
                        continue
                unexpected_unique = tuple(
                    dict.fromkeys(list(assistant_unexpected) + list(suggested_unexpected))
                )
                require_new_vocab = request.language_constraints.require_new_vocab
                allow_new_vocab = (
                    not request.language_constraints.forbidden.introduce_new_vocab
                )
                if not unexpected_unique:
                    if require_new_vocab:
                        if attempt >= self.max_rewrites:
                            return ConversationResponse(
                                assistant_reply_ko=response.assistant_reply_ko,
                                micro_feedback=response.micro_feedback,
                                suggested_user_intent_en=response.suggested_user_intent_en,
                                suggested_user_reply_ko=response.suggested_user_reply_ko,
                                suggested_user_reply_en=response.suggested_user_reply_en,
                                targets_used=response.targets_used,
                                unexpected_tokens=response.unexpected_tokens,
                                word_glosses=response.word_glosses,
                            )
                        request = _rewrite_request(request, reason="missing_new_word")
                        continue
                else:
                    unexpected_glosses = dict(response.word_glosses)
                    missing_glosses = [
                        token
                        for token in unexpected_unique
                        if not unexpected_glosses.get(token)
                    ]
                    too_many_unexpected = allow_new_vocab and len(unexpected_unique) > 1
                    if require_new_vocab and len(unexpected_unique) != 1:
                        too_many_unexpected = True
                    if allow_new_vocab and (too_many_unexpected or missing_glosses):
                        if attempt >= self.max_rewrites:
                            return ConversationResponse(
                                assistant_reply_ko=response.assistant_reply_ko,
                                micro_feedback=response.micro_feedback,
                                suggested_user_intent_en=response.suggested_user_intent_en,
                                suggested_user_reply_ko=response.suggested_user_reply_ko,
                                suggested_user_reply_en=response.suggested_user_reply_en,
                                targets_used=response.targets_used,
                                unexpected_tokens=unexpected_unique,
                                word_glosses=response.word_glosses,
                            )
                        reason = "unexpected_tokens"
                        if too_many_unexpected:
                            reason = "unexpected_tokens_limit"
                        elif missing_glosses:
                            reason = "missing_unexpected_glosses"
                        request = _rewrite_request(request, reason=reason)
                        continue

                    if not allow_new_vocab:
                        if attempt >= self.max_rewrites:
                            return ConversationResponse(
                                assistant_reply_ko=response.assistant_reply_ko,
                                micro_feedback=response.micro_feedback,
                                suggested_user_intent_en=response.suggested_user_intent_en,
                                suggested_user_reply_ko=response.suggested_user_reply_ko,
                                suggested_user_reply_en=response.suggested_user_reply_en,
                                targets_used=response.targets_used,
                                unexpected_tokens=unexpected_unique,
                                word_glosses=response.word_glosses,
                            )
                        request = _rewrite_request(
                            request,
                            reason=f"unexpected_tokens:{','.join(unexpected_unique)}",
                        )
                        continue

                    response = ConversationResponse(
                        assistant_reply_ko=response.assistant_reply_ko,
                        micro_feedback=response.micro_feedback,
                        suggested_user_intent_en=response.suggested_user_intent_en,
                        suggested_user_reply_ko=response.suggested_user_reply_ko,
                        suggested_user_reply_en=response.suggested_user_reply_en,
                        targets_used=response.targets_used,
                        unexpected_tokens=assistant_unexpected,
                        word_glosses=response.word_glosses,
                    )

                if response.unexpected_tokens != assistant_unexpected:
                    response = ConversationResponse(
                        assistant_reply_ko=response.assistant_reply_ko,
                        micro_feedback=response.micro_feedback,
                        suggested_user_intent_en=response.suggested_user_intent_en,
                        suggested_user_reply_ko=response.suggested_user_reply_ko,
                        suggested_user_reply_en=response.suggested_user_reply_en,
                        targets_used=response.targets_used,
                        unexpected_tokens=assistant_unexpected,
                        word_glosses=response.word_glosses,
                    )

            violation = check_response_against_request(
                request=request, response=response
            )
            if violation is not None:
                if attempt >= self.max_rewrites:
                    if violation.reason == "repeated_suggested_user_reply":
                        prev = (
                            request.conversation_state.last_suggested_user_reply_ko
                            or ""
                        ).strip()
                        alt_ko, alt_en = _fallback_non_repeating_suggested_reply(
                            prev=prev, current=response.suggested_user_reply_ko or ""
                        )
                        return ConversationResponse(
                            assistant_reply_ko=response.assistant_reply_ko,
                            micro_feedback=response.micro_feedback,
                            suggested_user_intent_en=response.suggested_user_intent_en,
                            suggested_user_reply_ko=alt_ko,
                            suggested_user_reply_en=alt_en,
                            targets_used=response.targets_used,
                            unexpected_tokens=response.unexpected_tokens,
                            word_glosses=response.word_glosses,
                        )
                    raise ValueError(f"contract violation: {violation.reason}")
                request = _rewrite_request(
                    request, reason=f"contract:{violation.reason}"
                )
                continue

            return response

        assert last_error is not None
        raise last_error


def _rewrite_request(
    request: ConversationRequest, *, reason: str
) -> ConversationRequest:
    allow_new_vocab = not request.language_constraints.forbidden.introduce_new_vocab
    require_new_vocab = request.language_constraints.require_new_vocab
    if require_new_vocab:
        vocab_directive = (
            "Introduce exactly ONE new content word (with glosses), and no other new words."
        )
    elif allow_new_vocab:
        vocab_directive = (
            "You may introduce at most ONE new content word (with glosses)."
        )
    else:
        vocab_directive = "Do not introduce any new content words."
    system_role = _with_rewrite_directive(
        system_role=request.system_role,
        reason=reason,
        directive=(
            "Return ONLY a valid JSON object matching the schema. "
            + vocab_directive
        ),
    )
    return ConversationRequest(
        system_role=system_role,
        conversation_state=request.conversation_state,
        user_input=request.user_input,
        language_constraints=request.language_constraints,
        generation_instructions=request.generation_instructions,
    )


def _with_rewrite_directive(*, system_role: str, reason: str, directive: str) -> str:
    # Avoid unbounded prompt growth if multiple rewrites are requested.
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


def _fallback_non_repeating_suggested_reply(*, prev: str, current: str) -> tuple[str, str]:
    def norm(s: str) -> str:
        s = (s or "").strip()
        s = s.rstrip(".!?\u3002\uff01\uff1f")
        s = " ".join(s.split())
        return s

    prev_n = norm(prev)
    cur_n = norm(current)

    # Keep this tiny and in BASE_ALLOWED_SUPPORT/validation allowlist.
    candidates: list[tuple[str, str]] = [
        ("네.", "Yes."),
        ("아니요.", "No."),
        ("맞아요.", "That's right."),
        ("아니에요.", "That's not right."),
    ]
    for ko, en in candidates:
        n = norm(ko)
        if n and n != prev_n and n != cur_n:
            return ko, en
    # As a last resort, return the current value unchanged.
    ko = (current or "").strip() or "네."
    en = "Yes." if norm(ko) == norm("네.") else "Okay."
    return ko, en


def _targets_used_in_text(
    text: str, must_targets: tuple[MustTarget, ...]
) -> tuple[str, ...]:
    tokens = tokenize_for_validation(text)
    used: list[str] = []
    for target in must_targets:
        surface_forms = tuple(getattr(target, "surface_forms", ()) or ())
        if not surface_forms:
            continue
        if getattr(target, "type", "") == "collocation":
            if all(_has_surface_form(tokens, sf) for sf in surface_forms):
                used.append(str(getattr(target, "id")))
        else:
            if any(_has_surface_form(tokens, sf) for sf in surface_forms):
                used.append(str(getattr(target, "id")))
    return tuple(used)


def _has_surface_form(tokens: list[str], surface_form: str) -> bool:
    for token in tokens:
        if token == surface_form:
            return True
        for suffix in _JOSA_SUFFIXES:
            if token.endswith(suffix) and len(token) > len(suffix):
                stem = token[: -len(suffix)]
                if stem == surface_form:
                    return True
    return False
