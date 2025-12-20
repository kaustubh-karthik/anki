# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from dataclasses import dataclass

from .types import ConversationRequest, ConversationResponse
from .validation import _JOSA_SUFFIXES, tokenize_for_validation


@dataclass(frozen=True)
class ContractViolation:
    reason: str


def _required_gloss_tokens(
    *, request: ConversationRequest, response: ConversationResponse
) -> set[str]:
    """Tokens that should have an English gloss entry.

    We require glosses for any *used* token that corresponds to deck-supported
    vocabulary (allowed_support/must_target), including tokens with common
    particles attached (eg, "날씨는" when "날씨" is in allowed_support).
    """

    required_stems: set[str] = set(request.language_constraints.allowed_support)
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


def check_response_against_request(
    *, request: ConversationRequest, response: ConversationResponse
) -> ContractViolation | None:
    forbidden = request.language_constraints.forbidden
    allowed_target_ids = {str(t.id) for t in request.language_constraints.must_target}

    if forbidden.sentence_length_max > 0:
        tokens = tokenize_for_validation(response.assistant_reply_ko)
        if len(tokens) > forbidden.sentence_length_max:
            return ContractViolation(reason="sentence_length_max")

    if response.targets_used:
        invalid = [tid for tid in response.targets_used if tid not in allowed_target_ids]
        if invalid:
            sample = ",".join(invalid[:8])
            return ContractViolation(reason=f"invalid_targets_used:{sample}")

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

    return None
