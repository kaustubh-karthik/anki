from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from anki.collection import Collection
from anki.decks import DeckId

from .export import export_conversation_telemetry
from .gateway import ConversationGateway, ConversationProvider, OpenAIConversationProvider
from .glossary import lookup_gloss, rebuild_glossary_from_snapshot
from .plan_reply import (
    FakePlanReplyProvider,
    OpenAIPlanReplyProvider,
    PlanReplyGateway,
    PlanReplyRequest,
)
from .planner import ConversationPlanner
from .redaction import redact_text
from .settings import ConversationSettings, RedactionLevel
from .snapshot import build_deck_snapshot
from .suggest import apply_suggested_cards, suggestions_from_wrap
from .telemetry import ConversationTelemetryStore
from .types import ConversationRequest, GenerationInstructions, UserInput
from .validation import tokenize_for_validation
from .wrap import compute_session_wrap

SYSTEM_ROLE = (
    "You are a Korean conversation partner for a learner. "
    "Speak naturally, concisely, and politely. "
    "Follow the provided constraints exactly. "
    "Return output strictly in the requested JSON format, and no prose outside it."
)


class FakeConversationProvider(ConversationProvider):
    """Deterministic provider for offline testing."""

    def __init__(self, scripted: list[dict[str, Any]]):
        self._scripted = scripted
        self._i = 0

    def generate(self, *, request: ConversationRequest) -> dict[str, Any]:
        if self._i >= len(self._scripted):
            return {
                "assistant_reply_ko": "네, 알겠어요.",
                "follow_up_question_ko": "다음은 뭐예요?",
                "micro_feedback": {"type": "none", "content_ko": "", "content_en": ""},
                "suggested_user_intent_en": None,
                "targets_used": [],
                "unexpected_tokens": [],
            }
        item = self._scripted[self._i]
        self._i += 1
        return item


@dataclass(slots=True)
class ScriptTurn:
    user_text_ko: str
    confidence: str | None = None
    events: list[dict[str, Any]] | None = None


def _load_script(path: Path) -> list[ScriptTurn]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit("script must be a JSON list")
    turns: list[ScriptTurn] = []
    for entry in data:
        if not isinstance(entry, dict):
            raise SystemExit("each script entry must be an object")
        text = entry.get("text_ko")
        if not isinstance(text, str) or not text:
            raise SystemExit("each script entry must have non-empty text_ko")
        conf = entry.get("confidence")
        if conf is not None and conf not in ("confident", "unsure", "guessing"):
            raise SystemExit("confidence must be confident|unsure|guessing")
        events = entry.get("events")
        if events is not None and not (
            isinstance(events, list) and all(isinstance(e, dict) for e in events)
        ):
            raise SystemExit("events must be a list of objects")
        turns.append(ScriptTurn(user_text_ko=text, confidence=conf, events=events))
    return turns


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="anki-conversation")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run a text-only conversation session")
    run.add_argument("--collection", required=True, help="Path to .anki2 file")
    run.add_argument("--deck", action="append", required=True, help="Deck name (repeatable)")
    run.add_argument("--script", required=True, help="Path to JSON script")
    run.add_argument(
        "--provider",
        choices=["fake", "openai"],
        default="fake",
        help="LLM provider (default: fake)",
    )
    run.add_argument("--api-key-file", default="gpt-api.txt")
    run.add_argument("--model", default="gpt-5-nano")
    run.add_argument("--redaction", choices=[e.value for e in RedactionLevel], default="minimal")
    run.add_argument("--safe-mode", action=argparse.BooleanOptionalAction, default=True)
    run.add_argument(
        "--provider-script",
        help="JSON file with scripted assistant responses (fake provider only)",
    )

    snap = sub.add_parser("snapshot", help="Print a deterministic deck snapshot as JSON")
    snap.add_argument("--collection", required=True)
    snap.add_argument("--deck", action="append", required=True)

    export = sub.add_parser("export-telemetry", help="Export stored conversation telemetry as JSON")
    export.add_argument("--collection", required=True)
    export.add_argument("--limit-sessions", type=int, default=100)

    plan = sub.add_parser("plan-reply", help="Generate 2-3 Korean reply options from English intent")
    plan.add_argument("--collection", required=True)
    plan.add_argument("--deck", action="append", required=True)
    plan.add_argument("--intent-en", required=True)
    plan.add_argument("--provider", choices=["fake", "openai"], default="fake")
    plan.add_argument("--provider-script", help="JSON file with scripted plan outputs (fake provider only)")
    plan.add_argument("--api-key-file", default="gpt-api.txt")
    plan.add_argument("--model", default="gpt-5-nano")
    plan.add_argument("--safe-mode", action=argparse.BooleanOptionalAction, default=True)

    apply_sug = sub.add_parser("apply-suggestions", help="Apply suggested cards from the most recent session wrap")
    apply_sug.add_argument("--collection", required=True)
    apply_sug.add_argument("--deck", required=True, help="Target deck name for added notes")
    apply_sug.add_argument("--limit-sessions", type=int, default=1)

    gloss = sub.add_parser("gloss", help="Lookup a lexeme gloss from the offline glossary cache")
    gloss.add_argument("--collection", required=True)
    gloss.add_argument("--lexeme", required=True)

    rebuild = sub.add_parser("rebuild-glossary", help="Rebuild glossary cache from selected deck(s)")
    rebuild.add_argument("--collection", required=True)
    rebuild.add_argument("--deck", action="append", required=True)

    args = parser.parse_args(argv)

    if args.cmd == "run":
        _cmd_run(args)
    elif args.cmd == "snapshot":
        _cmd_snapshot(args)
    elif args.cmd == "export-telemetry":
        _cmd_export(args)
    elif args.cmd == "plan-reply":
        _cmd_plan_reply(args)
    elif args.cmd == "apply-suggestions":
        _cmd_apply_suggestions(args)
    elif args.cmd == "gloss":
        _cmd_gloss(args)
    elif args.cmd == "rebuild-glossary":
        _cmd_rebuild_glossary(args)


def _cmd_run(args: argparse.Namespace) -> None:
    col = Collection(args.collection)
    try:
        deck_ids: list[DeckId] = []
        for deck_name in args.deck:
            did = col.decks.id_for_name(deck_name)
            if not did:
                raise SystemExit(f"deck not found: {deck_name}")
            deck_ids.append(DeckId(did))

        snapshot = build_deck_snapshot(col, deck_ids)
        planner = ConversationPlanner(snapshot)
        telemetry = ConversationTelemetryStore(col)
        session_id = telemetry.start_session(list(snapshot.deck_ids))

        turns = _load_script(Path(args.script))
        lexeme_set = {item.lexeme for item in snapshot.items}
        snapshot_item_ids = [str(item.item_id) for item in snapshot.items]
        mastery_cache = telemetry.load_mastery_cache(snapshot_item_ids)

        provider: ConversationProvider
        if args.provider == "fake":
            scripted = []
            if args.provider_script:
                scripted = json.loads(Path(args.provider_script).read_text(encoding="utf-8"))
                if not isinstance(scripted, list):
                    raise SystemExit("--provider-script must be a JSON list")
            provider = FakeConversationProvider(scripted=scripted)
        else:
            api_key = Path(args.api_key_file).read_text(encoding="utf-8").strip()
            provider = OpenAIConversationProvider(api_key=api_key, model=args.model)

        settings = ConversationSettings(
            provider=args.provider,
            model=args.model,
            safe_mode=bool(args.safe_mode),
            redaction_level=RedactionLevel(args.redaction),
        )
        gateway = ConversationGateway(provider=provider, max_rewrites=settings.max_rewrites)

        state = planner.initial_state(summary="Conversation practice")
        transcript: list[dict[str, Any]] = []
        for turn in turns:
            redacted = redact_text(turn.user_text_ko, settings.redaction_level)
            user_input = UserInput(text_ko=redacted.text, confidence=turn.confidence)  # type: ignore[arg-type]
            conv_state, constraints, instructions = planner.plan_turn(
                state, user_input, mastery=mastery_cache
            )
            instructions = GenerationInstructions(
                conversation_goal=instructions.conversation_goal,
                tone=instructions.tone,
                register=instructions.register,
                provide_follow_up_question=instructions.provide_follow_up_question,
                provide_micro_feedback=instructions.provide_micro_feedback,
                provide_suggested_english_intent=instructions.provide_suggested_english_intent,
                max_corrections=instructions.max_corrections,
                safe_mode=settings.safe_mode,
            )
            request = ConversationRequest(
                system_role=SYSTEM_ROLE,
                conversation_state=conv_state,
                user_input=user_input,
                language_constraints=constraints,
                generation_instructions=instructions,
            )
            response = gateway.run_turn(request=request)

            # Deterministic usage signals from text-only mode (no UI required).
            for token in tokenize_for_validation(user_input.text_ko):
                if token in lexeme_set:
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
            for token in tokenize_for_validation(response.assistant_reply_ko):
                if token in lexeme_set:
                    telemetry.bump_item_cached(
                        mastery_cache,
                        item_id=f"lexeme:{token}",
                        kind="lexeme",
                        value=token,
                        deltas={"assistant_used": 1},
                    )

            if turn.events:
                for event in turn.events:
                    etype = event.get("type")
                    token = event.get("token")
                    if isinstance(etype, str):
                        telemetry.log_event(
                            session_id=session_id,
                            turn_index=state.turn_index,
                            event_type=etype,
                            payload=event,
                        )
                        if isinstance(token, str) and token:
                            if etype in ("dont_know", "practice_again", "mark_confusing"):
                                telemetry.bump_item_cached(
                                    mastery_cache,
                                    item_id=f"lexeme:{token}",
                                    kind="lexeme",
                                    value=token,
                                    deltas={etype: 1},
                                )
                        if etype == "lookup":
                            ms = event.get("ms")
                            if isinstance(ms, int) and ms >= 0 and isinstance(token, str) and token:
                                telemetry.bump_item_cached(
                                    mastery_cache,
                                    item_id=f"lexeme:{token}",
                                    kind="lexeme",
                                    value=token,
                                    deltas={"lookup_count": 1, "lookup_ms_total": ms},
                                )
                        if etype == "repair_move":
                            move = event.get("move")
                            if isinstance(move, str) and move:
                                telemetry.bump_item_cached(
                                    mastery_cache,
                                    item_id=f"repair:{move}",
                                    kind="repair",
                                    value=move,
                                    deltas={"used": 1},
                                )

            telemetry.log_event(
                session_id=session_id,
                turn_index=state.turn_index,
                event_type="turn",
                payload={"user": user_input.text_ko, "assistant": response.to_json_dict()},
            )
            transcript.append(
                {
                    "turn_index": state.turn_index,
                    "user_input": user_input.text_ko,
                    "assistant": response.to_json_dict(),
                }
            )
            state.last_assistant_turn_ko = response.assistant_reply_ko
            planner.observe_turn(
                state,
                constraints=constraints,
                user_input=user_input,
                assistant_reply_ko=response.assistant_reply_ko,
                follow_up_question_ko=response.follow_up_question_ko,
            )

        wrap = compute_session_wrap(snapshot=snapshot, mastery=mastery_cache)
        summary = {"turns": len(turns), "wrap": wrap}
        telemetry.end_session(session_id, summary=summary)
        print(
            orjson.dumps(
                {
                    "session_id": session_id,
                    "transcript": transcript,
                    "wrap": wrap,
                }
            ).decode("utf-8")
        )
    finally:
        col.close()


def _cmd_snapshot(args: argparse.Namespace) -> None:
    col = Collection(args.collection)
    try:
        deck_ids: list[DeckId] = []
        for deck_name in args.deck:
            did = col.decks.id_for_name(deck_name)
            if not did:
                raise SystemExit(f"deck not found: {deck_name}")
            deck_ids.append(DeckId(did))
        snapshot = build_deck_snapshot(col, deck_ids)
        print(
            orjson.dumps(
                {
                    "deck_ids": list(snapshot.deck_ids),
                    "today": snapshot.today,
                    "items": [
                        {
                            "item_id": str(i.item_id),
                            "lexeme": i.lexeme,
                            "gloss": i.gloss,
                            "source_note_id": i.source_note_id,
                            "source_card_id": i.source_card_id,
                            "due": i.due,
                            "ivl": i.ivl,
                            "reps": i.reps,
                            "lapses": i.lapses,
                            "stability": i.stability,
                            "difficulty": i.difficulty,
                        }
                        for i in snapshot.items
                    ],
                }
            ).decode("utf-8")
        )
    finally:
        col.close()


def _cmd_export(args: argparse.Namespace) -> None:
    col = Collection(args.collection)
    try:
        exported = export_conversation_telemetry(col, limit_sessions=args.limit_sessions)
        print(exported.to_json())
    finally:
        col.close()


def _cmd_plan_reply(args: argparse.Namespace) -> None:
    col = Collection(args.collection)
    try:
        deck_ids: list[DeckId] = []
        for deck_name in args.deck:
            did = col.decks.id_for_name(deck_name)
            if not did:
                raise SystemExit(f"deck not found: {deck_name}")
            deck_ids.append(DeckId(did))

        snapshot = build_deck_snapshot(col, deck_ids)
        planner = ConversationPlanner(snapshot)
        state = planner.initial_state(summary="Conversation practice")
        # Use planner constraints for this moment; intent is separate from user_input.
        conv_state, constraints, instructions = planner.plan_turn(
            state, UserInput(text_ko=""), mastery={}
        )
        instructions = GenerationInstructions(
            conversation_goal=instructions.conversation_goal,
            tone=instructions.tone,
            register=instructions.register,
            provide_follow_up_question=instructions.provide_follow_up_question,
            provide_micro_feedback=instructions.provide_micro_feedback,
            provide_suggested_english_intent=instructions.provide_suggested_english_intent,
            max_corrections=instructions.max_corrections,
            safe_mode=bool(args.safe_mode),
        )

        provider: object
        if args.provider == "fake":
            scripted = []
            if args.provider_script:
                scripted = json.loads(Path(args.provider_script).read_text(encoding="utf-8"))
                if not isinstance(scripted, list):
                    raise SystemExit("--provider-script must be a JSON list")
            provider = FakePlanReplyProvider(scripted=scripted)
        else:
            api_key = Path(args.api_key_file).read_text(encoding="utf-8").strip()
            provider = OpenAIPlanReplyProvider(api_key=api_key, model=args.model)

        gateway = PlanReplyGateway(provider=provider)  # type: ignore[arg-type]
        req = PlanReplyRequest(
            system_role=SYSTEM_ROLE,
            conversation_state=conv_state,
            intent_en=args.intent_en,
            language_constraints=constraints,
            generation_instructions=instructions,
        )
        resp = gateway.run(request=req)
        print(orjson.dumps(resp.to_json_dict()).decode("utf-8"))
    finally:
        col.close()


def _cmd_apply_suggestions(args: argparse.Namespace) -> None:
    col = Collection(args.collection)
    try:
        did = col.decks.id_for_name(args.deck)
        if not did:
            raise SystemExit(f"deck not found: {args.deck}")

        rows = col.db.all(
            "select summary_json from elites_conversation_sessions where summary_json is not null order by id desc limit ?",
            args.limit_sessions,
        )
        if not rows:
            raise SystemExit("no session summaries found")

        # Use most recent summary that contains a wrap.
        summary_json = None
        for (sj,) in rows:
            if isinstance(sj, str) and sj:
                summary_json = sj
                break
        if not summary_json:
            raise SystemExit("no valid session wrap found")
        summary = json.loads(summary_json)
        wrap = summary.get("wrap", {})
        suggestions = suggestions_from_wrap(wrap, deck_id=int(did))
        created = apply_suggested_cards(col, suggestions)
        print(orjson.dumps({"created_note_ids": created}).decode("utf-8"))
    finally:
        col.close()


def _cmd_gloss(args: argparse.Namespace) -> None:
    col = Collection(args.collection)
    try:
        entry = lookup_gloss(col, args.lexeme)
        if entry is None:
            print(orjson.dumps({"found": False}).decode("utf-8"))
        else:
            print(orjson.dumps({"found": True, "lexeme": entry.lexeme, "gloss": entry.gloss}).decode("utf-8"))
    finally:
        col.close()


def _cmd_rebuild_glossary(args: argparse.Namespace) -> None:
    col = Collection(args.collection)
    try:
        deck_ids: list[DeckId] = []
        for deck_name in args.deck:
            did = col.decks.id_for_name(deck_name)
            if not did:
                raise SystemExit(f"deck not found: {deck_name}")
            deck_ids.append(DeckId(did))
        snapshot = build_deck_snapshot(col, deck_ids)
        count = rebuild_glossary_from_snapshot(col, snapshot)
        print(orjson.dumps({"updated": count}).decode("utf-8"))
    finally:
        col.close()


if __name__ == "__main__":
    main()
