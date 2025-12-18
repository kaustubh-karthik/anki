from __future__ import annotations

from dataclasses import dataclass

from .types import ConversationRequest, ConversationResponse
from .validation import tokenize_for_validation


@dataclass(frozen=True, slots=True)
class ContractViolation:
    reason: str


def check_response_against_request(
    *, request: ConversationRequest, response: ConversationResponse
) -> ContractViolation | None:
    forbidden = request.language_constraints.forbidden

    if forbidden.sentence_length_max > 0:
        tokens = tokenize_for_validation(response.assistant_reply_ko) + tokenize_for_validation(
            response.follow_up_question_ko
        )
        if len(tokens) > forbidden.sentence_length_max:
            return ContractViolation(reason="sentence_length_max")

    if request.generation_instructions.provide_follow_up_question and not response.follow_up_question_ko:
        return ContractViolation(reason="missing_follow_up_question")

    if request.generation_instructions.max_corrections == 0 and response.micro_feedback:
        if response.micro_feedback.get("type") == "correction":
            return ContractViolation(reason="max_corrections")

    return None

