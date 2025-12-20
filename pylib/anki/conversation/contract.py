# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from dataclasses import dataclass

from .types import ConversationRequest, ConversationResponse
from .validation import _BASE_ALLOWED_SUPPORT, _JOSA_SUFFIXES, tokenize_for_validation


@dataclass(frozen=True)
class ContractViolation:
    reason: str


def _norm_for_repeat_check(text: str) -> str:
    t = (text or "").strip()
    # Normalize common end punctuation and whitespace so "네" and "네." compare equal.
    t = t.rstrip(".!?\u3002\uff01\uff1f")
    t = " ".join(t.split())
    return t


def _required_gloss_tokens(
    *, request: ConversationRequest, response: ConversationResponse
) -> set[str]:
    """Tokens that should have an English gloss entry.

    We require glosses for any *used* token that corresponds to deck-supported
    vocabulary (allowed_support/must_target), including tokens with common
    particles attached (eg, "날씨는" when "날씨" is in allowed_support).
    """

    required_stems: set[str] = set(request.language_constraints.allowed_support)
    required_stems.update(request.language_constraints.allowed_stretch)
    required_stems.update(request.language_constraints.reinforced_words)
    for mt in request.language_constraints.must_target:
        required_stems.update(mt.surface_forms)

    tokens = tokenize_for_validation(response.assistant_reply_ko)

    required_tokens: set[str] = set()
    for token in tokens:
        if token.isdigit():
            continue
        if token in required_stems:
            required_tokens.add(token)
            continue
        for suffix in _JOSA_SUFFIXES:
            if token.endswith(suffix) and len(token) > len(suffix):
                stem = token[: -len(suffix)]
                if stem in required_stems:
                    required_tokens.add(token)
                    break

    return required_tokens


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _content_tokens(tokens: list[str]) -> set[str]:
    return {t for t in tokens if t and t not in _BASE_ALLOWED_SUPPORT}


def check_response_against_request(
    *, request: ConversationRequest, response: ConversationResponse
) -> ContractViolation | None:
    forbidden = request.language_constraints.forbidden
    allowed_target_ids = {str(t.id) for t in request.language_constraints.must_target}

    if response.micro_feedback is None or not (
        isinstance(response.micro_feedback.get("content_en"), str)
        and response.micro_feedback["content_en"].strip()
    ):
        return ContractViolation(reason="missing_micro_feedback_en")

    if not isinstance(response.suggested_user_reply_ko, str) or not (
        response.suggested_user_reply_ko.strip()
    ):
        return ContractViolation(reason="missing_suggested_user_reply_ko")

    if not isinstance(response.suggested_user_reply_en, str) or not (
        response.suggested_user_reply_en.strip()
    ):
        return ContractViolation(reason="missing_suggested_user_reply_en")

    if "?" in response.suggested_user_reply_ko:
        return ContractViolation(reason="suggested_user_reply_must_not_be_question")

    prev_suggested = (
        request.conversation_state.last_suggested_user_reply_ko or ""
    ).strip()
    if prev_suggested and _norm_for_repeat_check(
        response.suggested_user_reply_ko
    ) == _norm_for_repeat_check(prev_suggested):
        return ContractViolation(reason="repeated_suggested_user_reply")

    if forbidden.sentence_length_max > 0:
        tokens = tokenize_for_validation(response.assistant_reply_ko)
        if len(tokens) > forbidden.sentence_length_max:
            return ContractViolation(reason="sentence_length_max")

    if response.targets_used:
        invalid = [tid for tid in response.targets_used if tid not in allowed_target_ids]
        if invalid:
            sample = ",".join(invalid[:8])
            return ContractViolation(reason=f"invalid_targets_used:{sample}")

    primary_target_ids = {
        str(t.id) for t in request.language_constraints.must_target if t.type == "vocab"
    }
    if primary_target_ids and not (
        set(response.targets_used) & primary_target_ids
    ):
        return ContractViolation(reason="missing_target_word")

    if request.generation_instructions.max_corrections == 0 and response.micro_feedback:
        if response.micro_feedback.get("type") == "correction":
            return ContractViolation(reason="max_corrections")

    required_tokens = _required_gloss_tokens(request=request, response=response)
    if required_tokens:
        glosses = dict(response.word_glosses)
        missing = [t for t in sorted(required_tokens) if not glosses.get(t)]
        if missing:
            # Keep the reason short; it is embedded into the rewrite instruction.
            sample = ",".join(missing[:8])
            return ContractViolation(reason=f"missing_word_glosses:{sample}")

    prev = (request.conversation_state.last_assistant_turn_ko or "").strip()
    cur = (response.assistant_reply_ko or "").strip()
    if prev and cur:
        prev_tokens = tokenize_for_validation(prev)
        cur_tokens = tokenize_for_validation(cur)
        if prev_tokens and cur_tokens:
            if len(prev_tokens) >= 4 and len(cur_tokens) >= 4:
                lexical_sim = _jaccard_similarity(set(prev_tokens), set(cur_tokens))
                if lexical_sim >= request.generation_instructions.lexical_similarity_max:
                    return ContractViolation(reason="lexical_similarity")

            prev_content = _content_tokens(prev_tokens)
            cur_content = _content_tokens(cur_tokens)
            if len(prev_content) >= 2 and len(cur_content) >= 2:
                semantic_sim = _jaccard_similarity(prev_content, cur_content)
                if (
                    semantic_sim
                    >= request.generation_instructions.semantic_similarity_max
                ):
                    return ContractViolation(reason="semantic_similarity")

    return None
