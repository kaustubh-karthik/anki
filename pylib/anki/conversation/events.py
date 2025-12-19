# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from typing import Any, Iterable

from .telemetry import ConversationTelemetryStore, MasteryCache
from .types import ConversationResponse, UserInput
from .validation import tokenize_for_validation


def bump_user_used_lexemes(
    *,
    telemetry: ConversationTelemetryStore,
    mastery_cache: MasteryCache,
    lexeme_set: set[str],
    user_input: UserInput,
) -> None:
    for token in tokenize_for_validation(user_input.text_ko):
        if token not in lexeme_set:
            continue
        telemetry.bump_item_cached(
            mastery_cache,
            item_id=f"lexeme:{token}",
            kind="lexeme",
            value=token,
            deltas={"user_used": 1},
        )
        if user_input.confidence == "unsure":
            telemetry.bump_item_cached(
                mastery_cache,
                item_id=f"lexeme:{token}",
                kind="lexeme",
                value=token,
                deltas={"used_unsure": 1},
            )
        elif user_input.confidence == "guessing":
            telemetry.bump_item_cached(
                mastery_cache,
                item_id=f"lexeme:{token}",
                kind="lexeme",
                value=token,
                deltas={"used_guessing": 1},
            )


def bump_assistant_used_lexemes(
    *,
    telemetry: ConversationTelemetryStore,
    mastery_cache: MasteryCache,
    lexeme_set: set[str],
    response: ConversationResponse,
) -> None:
    for token in tokenize_for_validation(response.assistant_reply_ko):
        if token not in lexeme_set:
            continue
        telemetry.bump_item_cached(
            mastery_cache,
            item_id=f"lexeme:{token}",
            kind="lexeme",
            value=token,
            deltas={"assistant_used": 1},
        )


def record_event_from_payload(
    *,
    telemetry: ConversationTelemetryStore,
    mastery_cache: MasteryCache,
    session_id: int,
    turn_index: int,
    payload: dict[str, Any],
) -> None:
    etype = payload.get("type")
    if not isinstance(etype, str) or not etype:
        raise ValueError("event.type must be a non-empty string")

    telemetry.log_event(
        session_id=session_id,
        turn_index=turn_index,
        event_type=etype,
        payload=payload,
    )

    token = payload.get("token")
    if etype in ("dont_know", "practice_again", "mark_confusing"):
        if isinstance(token, str) and token:
            telemetry.bump_item_cached(
                mastery_cache,
                item_id=f"lexeme:{token}",
                kind="lexeme",
                value=token,
                deltas={etype: 1},
            )
        return

    if etype == "lookup":
        ms = payload.get("ms")
        if isinstance(ms, int) and ms >= 0 and isinstance(token, str) and token:
            telemetry.bump_item_cached(
                mastery_cache,
                item_id=f"lexeme:{token}",
                kind="lexeme",
                value=token,
                deltas={"lookup_count": 1, "lookup_ms_total": ms},
            )
        return

    if etype == "repair_move":
        move = payload.get("move")
        if isinstance(move, str) and move:
            telemetry.bump_item_cached(
                mastery_cache,
                item_id=f"repair:{move}",
                kind="repair",
                value=move,
                deltas={"used": 1},
            )
        return

    if etype == "words_known":
        tokens = payload.get("tokens", [])
        if isinstance(tokens, list):
            for token in tokens:
                if isinstance(token, str) and token:
                    telemetry.bump_item_cached(
                        mastery_cache,
                        item_id=f"lexeme:{token}",
                        kind="lexeme",
                        value=token,
                        deltas={"user_understood": 1},
                    )
        return

    if etype == "sentence_translated":
        tokens = payload.get("tokens", [])
        if isinstance(tokens, list):
            for token in tokens:
                if isinstance(token, str) and token:
                    telemetry.bump_item_cached(
                        mastery_cache,
                        item_id=f"lexeme:{token}",
                        kind="lexeme",
                        value=token,
                        deltas={"dont_know": 1},
                    )
        return


def record_turn_event(
    *,
    telemetry: ConversationTelemetryStore,
    session_id: int,
    turn_index: int,
    user_input: UserInput,
    response: ConversationResponse,
) -> None:
    telemetry.log_event(
        session_id=session_id,
        turn_index=turn_index,
        event_type="turn",
        payload={"user": user_input.text_ko, "assistant": response.to_json_dict()},
    )


def apply_missed_targets(
    *,
    telemetry: ConversationTelemetryStore,
    mastery_cache: MasteryCache,
    missed_item_ids: Iterable[str],
) -> None:
    for item_id in missed_item_ids:
        kind: str | None = None
        value: str | None = None
        if item_id.startswith("lexeme:"):
            kind = "lexeme"
            value = item_id.removeprefix("lexeme:")
        elif item_id.startswith("gram:"):
            kind = "grammar"
            value = item_id
        elif item_id.startswith("colloc:"):
            kind = "collocation"
            value = item_id
        elif item_id.startswith("repair:"):
            kind = "repair"
            value = item_id.removeprefix("repair:")
        if kind is None or value is None or not value:
            continue
        telemetry.bump_item_cached(
            mastery_cache,
            item_id=item_id,
            kind=kind,
            value=value,
            deltas={"missed_target": 1},
        )
