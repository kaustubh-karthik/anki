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
        allowed.update(request.language_constraints.allowed_stretch)
        allowed.update(request.language_constraints.reinforced_words)

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

        statement = " ".join(reply_tokens) + "."
        question = pick_allowed(("뭐예요", "뭐"), "뭐예요") + "?"
        assistant_reply_ko = f"{statement} {question}"

        # Suggested user reply: a short non-question, and avoid repeating the last suggestion.
        prev_suggested = (
            request.conversation_state.last_suggested_user_reply_ko or ""
        ).strip()
        suggested_user_reply_ko = "네."
        if prev_suggested == suggested_user_reply_ko:
            suggested_user_reply_ko = "아니요." if "아니요" in allowed else "네."

        used_tokens = set(tokenize_for_validation(assistant_reply_ko))
        targets_used: list[str] = []
        for mt in request.language_constraints.must_target:
            if all(token in used_tokens for token in mt.surface_forms):
                targets_used.append(str(mt.id))

        # Provide deterministic placeholder glosses for supported vocab so that
        # the UI can still show something in offline mode.
        # (Online providers are expected to return real English glosses.)
        required_stems = set(request.language_constraints.allowed_support)
        required_stems.update(request.language_constraints.allowed_stretch)
        required_stems.update(request.language_constraints.reinforced_words)
        for mt in request.language_constraints.must_target:
            required_stems.update(mt.surface_forms)
        word_glosses: dict[str, str] = {}
        for token in used_tokens:
            if token in required_stems:
                word_glosses[token] = "(gloss unavailable offline)"

        return {
            "assistant_reply_ko": assistant_reply_ko,
            "micro_feedback": {
                "type": "none",
                "content_ko": "",
                "content_en": "Feedback unavailable in local mode.",
            },
            "suggested_user_intent_en": None,
            "suggested_user_reply_ko": suggested_user_reply_ko,
            "suggested_user_reply_en": "No." if suggested_user_reply_ko == "아니요." else "Yes.",
            "targets_used": targets_used,
            "unexpected_tokens": [],
            "word_glosses": word_glosses,
        }
