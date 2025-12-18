# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .gateway import ConversationProvider
from .types import ConversationRequest
from .validation import tokenize_for_validation


@dataclass
class LocalConversationProvider(ConversationProvider):
    """Deterministic, offline ConversationProvider implementation.

    This exists to enable fully automated testing and a local-only fallback mode
    without requiring any external LLM calls.
    """

    def generate(self, *, request: ConversationRequest) -> dict[str, Any]:
        allowed = set(request.language_constraints.allowed_support)

        def pick_allowed(preferred: tuple[str, ...], fallback: str) -> str:
            for token in preferred:
                if token in allowed:
                    return token
            return fallback

        target_surface: str | None = None
        if request.language_constraints.must_target:
            mt = request.language_constraints.must_target[0]
            if mt.surface_forms:
                target_surface = mt.surface_forms[0]

        reply_tokens: list[str] = []
        if target_surface:
            reply_tokens.append(target_surface)

        reply_tail = pick_allowed(("있어요", "있어"), "네")
        if reply_tail != "네" and reply_tokens:
            reply_tokens.append(reply_tail)

        if not reply_tokens:
            reply_tokens = ["네"]

        assistant_reply_ko = " ".join(reply_tokens) + "."

        follow_up_question_ko = pick_allowed(("뭐예요", "뭐"), "뭐예요") + "?"

        used_tokens = set(
            tokenize_for_validation(assistant_reply_ko)
            + tokenize_for_validation(follow_up_question_ko)
        )
        targets_used: list[str] = []
        for mt in request.language_constraints.must_target:
            if all(token in used_tokens for token in mt.surface_forms):
                targets_used.append(str(mt.id))

        return {
            "assistant_reply_ko": assistant_reply_ko,
            "follow_up_question_ko": follow_up_question_ko,
            "micro_feedback": {"type": "none", "content_ko": "", "content_en": ""},
            "suggested_user_intent_en": None,
            "targets_used": targets_used,
            "unexpected_tokens": [],
        }
