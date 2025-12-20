# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import orjson

from anki.collection import Collection
from anki.decks import DeckId

from .export import export_conversation_telemetry
from .gateway import (
    ConversationProvider,
    OpenAIConversationProvider,
)
from .glossary import import_glossary_file, lookup_gloss, rebuild_glossary_from_snapshot
from .keys import resolve_openai_api_key
from .local_provider import LocalConversationProvider
from .openai import OpenAIResponsesJsonClient
from .plan_reply import (
    FakePlanReplyProvider,
    OpenAIPlanReplyProvider,
    PlanReplyGateway,
    PlanReplyRequest,
)
from .planner import ConversationPlanner
from .prompts import PLAN_REPLY_SYSTEM_ROLE, SYSTEM_ROLE
from .redaction import redact_text
from .session import ConversationSession
from .settings import (
    ConversationSettings,
    RedactionLevel,
    load_conversation_settings,
    save_conversation_settings,
)
from .snapshot import build_deck_snapshot
from .suggest import apply_reinforced_cards, reinforced_cards_from_wrap
from .telemetry import ConversationTelemetryStore
from .types import (
    ConversationRequest,
    ConversationState,
    ForbiddenConstraints,
    GenerationInstructions,
    LanguageConstraints,
    UserInput,
)
from .validation import tokenize_for_validation
from .wrap import compute_session_wrap


class FakeConversationProvider(ConversationProvider):
    """Deterministic provider for offline testing."""

    def __init__(self, scripted: list[dict[str, Any]]):
        self._scripted = scripted
        self._i = 0

    def generate(self, *, request: ConversationRequest) -> dict[str, Any]:
        def with_placeholder_glosses(item: dict[str, Any]) -> dict[str, Any]:
            if isinstance(item.get("word_glosses"), dict):
                out = dict(item)
            else:
                assistant_reply_ko = item.get("assistant_reply_ko", "")
                if not isinstance(assistant_reply_ko, str):
                    out = dict(item)
                else:
                    allowed = set(request.language_constraints.allowed_support)
                    for mt in request.language_constraints.must_target:
                        allowed.update(mt.surface_forms)
                    tokens = set(tokenize_for_validation(assistant_reply_ko))
                    glosses = {
                        t: "(gloss unavailable offline)" for t in tokens if t in allowed
                    }
                    out = dict(item)
                    out["word_glosses"] = glosses

            # Keep scripted fixtures backward-compatible with newer schema requirements.
            fb = out.get("micro_feedback")
            if not isinstance(fb, dict):
                fb = {"type": "none", "content_ko": "", "content_en": ""}
            if not isinstance(fb.get("content_en"), str) or not fb["content_en"].strip():
                fb = dict(fb)
                fb["content_en"] = "Feedback unavailable in fake provider mode."
            out["micro_feedback"] = fb
            if not isinstance(out.get("suggested_user_reply_ko"), str) or not str(
                out.get("suggested_user_reply_ko") or ""
            ).strip():
                out["suggested_user_reply_ko"] = "네."
            if not isinstance(out.get("suggested_user_reply_en"), str) or not str(
                out.get("suggested_user_reply_en") or ""
            ).strip():
                out["suggested_user_reply_en"] = "Yes."
            assistant_reply_ko = item.get("assistant_reply_ko", "")
            if not isinstance(assistant_reply_ko, str):
                return out
            return out

        if self._i >= len(self._scripted):
            # Avoid repeating suggestions if the caller is looping in a tight vocab budget.
            prev_suggested = (
                request.conversation_state.last_suggested_user_reply_ko or ""
            ).strip()
            fallback_suggested_ko = "네."
            fallback_suggested_en = "Yes."
            if prev_suggested == fallback_suggested_ko:
                fallback_suggested_ko = "아니요."
                fallback_suggested_en = "No."
            return {
                "assistant_reply_ko": "네, 알겠어요.",
                "micro_feedback": {
                    "type": "none",
                    "content_ko": "",
                    "content_en": "Feedback unavailable in fake provider mode.",
                },
                "suggested_user_intent_en": None,
                "suggested_user_reply_ko": fallback_suggested_ko,
                "suggested_user_reply_en": fallback_suggested_en,
                "targets_used": [],
                "unexpected_tokens": [],
                "word_glosses": {},
            }
        item = self._scripted[self._i]
        self._i += 1
        if isinstance(item, dict):
            return with_placeholder_glosses(item)
        return item


@dataclass
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

    smoke = sub.add_parser("openai-smoke", help="Minimal OpenAI connectivity test")
    smoke.add_argument("--api-key-file", default="gpt-api.txt")
    smoke.add_argument("--model", default="gpt-4o-mini")

    run = sub.add_parser("run", help="Run a text-only conversation session")
    run.add_argument("--collection", required=True, help="Path to .anki2 file")
    run.add_argument(
        "--deck", action="append", required=True, help="Deck name (repeatable)"
    )
    run.add_argument("--script", required=True, help="Path to JSON script")
    run.add_argument("--topic", help="Optional topic id (eg room_objects)")
    run.add_argument(
        "--provider",
        choices=["fake", "local", "openai"],
        help="LLM provider (default: from saved settings)",
    )
    run.add_argument("--api-key-file", default="gpt-api.txt")
    run.add_argument("--model")
    run.add_argument("--redaction", choices=[e.value for e in RedactionLevel])
    run.add_argument("--safe-mode", action=argparse.BooleanOptionalAction, default=None)
    run.add_argument("--max-rewrites", type=int)
    run.add_argument("--lexeme-field-index", type=int)
    run.add_argument("--gloss-field-index", type=int)
    run.add_argument("--no-gloss-field", action="store_true")
    run.add_argument("--snapshot-max-items", type=int)
    run.add_argument(
        "--provider-script",
        help="JSON file with scripted assistant responses (fake provider only)",
    )

    snap = sub.add_parser(
        "snapshot", help="Print a deterministic deck snapshot as JSON"
    )
    snap.add_argument("--collection", required=True)
    snap.add_argument("--deck", action="append", required=True)
    snap.add_argument("--lexeme-field-index", type=int)
    snap.add_argument("--gloss-field-index", type=int)
    snap.add_argument("--no-gloss-field", action="store_true")
    snap.add_argument("--snapshot-max-items", type=int)

    export = sub.add_parser(
        "export-telemetry", help="Export stored conversation telemetry as JSON"
    )
    export.add_argument("--collection", required=True)
    export.add_argument("--limit-sessions", type=int, default=100)
    export.add_argument("--redaction", choices=[e.value for e in RedactionLevel])

    plan = sub.add_parser(
        "plan-reply", help="Rewrite a Korean draft into reply options"
    )
    plan.add_argument("--collection", required=True)
    plan.add_argument("--deck", action="append", required=True)
    plan.add_argument("--draft-ko", required=True)
    plan.add_argument("--provider", choices=["fake", "openai"])
    plan.add_argument(
        "--provider-script",
        help="JSON file with scripted plan outputs (fake provider only)",
    )
    plan.add_argument("--api-key-file", default="gpt-api.txt")
    plan.add_argument("--model")
    plan.add_argument(
        "--safe-mode", action=argparse.BooleanOptionalAction, default=None
    )

    apply_sug = sub.add_parser(
        "apply-reinforced",
        help="Apply reinforced-word cards from the most recent session wrap",
    )
    apply_sug.add_argument("--collection", required=True)
    apply_sug.add_argument(
        "--deck", required=True, help="Target deck name for added notes"
    )
    apply_sug.add_argument("--limit-sessions", type=int, default=1)

    gloss = sub.add_parser(
        "gloss", help="Lookup a lexeme gloss from the offline glossary cache"
    )
    gloss.add_argument("--collection", required=True)
    gloss.add_argument("--lexeme", required=True)

    rebuild = sub.add_parser(
        "rebuild-glossary", help="Rebuild glossary cache from selected deck(s)"
    )
    rebuild.add_argument("--collection", required=True)
    rebuild.add_argument("--deck", action="append", required=True)
    rebuild.add_argument("--lexeme-field-index", type=int, default=0)
    rebuild.add_argument(
        "--lexeme-field-name",
        action="append",
        default=[],
        help="Preferred lexeme field name (repeatable; overrides persisted names)",
    )
    rebuild.add_argument("--gloss-field-index", type=int, default=1)
    rebuild.add_argument(
        "--gloss-field-name",
        action="append",
        default=[],
        help="Preferred gloss field name (repeatable; overrides persisted names)",
    )
    rebuild.add_argument("--no-gloss-field", action="store_true")
    rebuild.add_argument("--snapshot-max-items", type=int, default=5000)

    imp_gloss = sub.add_parser(
        "import-glossary", help="Import a user-supplied glossary file into cache"
    )
    imp_gloss.add_argument("--collection", required=True)
    imp_gloss.add_argument("--file", required=True, help="Path to .tsv/.csv/.json")
    imp_gloss.add_argument(
        "--format", choices=["tsv", "csv", "json"], help="Override format detection"
    )

    getset = sub.add_parser("settings", help="Get/set persisted conversation settings")
    getset.add_argument("--collection", required=True)
    getset.add_argument("--set-provider")
    getset.add_argument("--set-model")
    getset.add_argument("--set-safe-mode", choices=["true", "false"])
    getset.add_argument("--set-redaction", choices=[e.value for e in RedactionLevel])
    getset.add_argument("--set-max-rewrites", type=int)
    getset.add_argument("--set-lexeme-field-index", type=int)
    getset.add_argument("--set-gloss-field-index", type=int)
    getset.add_argument("--set-no-gloss-field", action="store_true")
    getset.add_argument("--set-snapshot-max-items", type=int)

    args = parser.parse_args(argv)

    if args.cmd == "run":
        _cmd_run(args)
    elif args.cmd == "snapshot":
        _cmd_snapshot(args)
    elif args.cmd == "export-telemetry":
        _cmd_export(args)
    elif args.cmd == "openai-smoke":
        _cmd_openai_smoke(args)
    elif args.cmd == "plan-reply":
        _cmd_plan_reply(args)
    elif args.cmd == "apply-reinforced":
        _cmd_apply_reinforced(args)
    elif args.cmd == "gloss":
        _cmd_gloss(args)
    elif args.cmd == "rebuild-glossary":
        _cmd_rebuild_glossary(args)
    elif args.cmd == "import-glossary":
        _cmd_import_glossary(args)
    elif args.cmd == "settings":
        _cmd_settings(args)


def _merged_settings(
    col: Collection, args: argparse.Namespace, *, provider_default: str = "fake"
) -> ConversationSettings:
    base = load_conversation_settings(col)
    provider = getattr(args, "provider", None) or base.provider or provider_default
    model = getattr(args, "model", None) or base.model

    safe_mode = getattr(args, "safe_mode", None)
    if safe_mode is None:
        safe_mode = base.safe_mode

    redaction_raw = getattr(args, "redaction", None)
    if redaction_raw is None:
        redaction_level = base.redaction_level
    else:
        redaction_level = RedactionLevel(redaction_raw)

    max_rewrites = getattr(args, "max_rewrites", None)
    if max_rewrites is None:
        max_rewrites = base.max_rewrites
    if not isinstance(max_rewrites, int) or max_rewrites < 0 or max_rewrites > 10:
        max_rewrites = base.max_rewrites

    lexeme_field_index = getattr(args, "lexeme_field_index", None)
    if lexeme_field_index is None:
        lexeme_field_index = base.lexeme_field_index

    gloss_field_index = getattr(args, "gloss_field_index", None)
    if gloss_field_index is None:
        gloss_field_index = base.gloss_field_index
    if getattr(args, "no_gloss_field", False):
        gloss_field_index = None

    snapshot_max_items = getattr(args, "snapshot_max_items", None)
    if snapshot_max_items is None:
        snapshot_max_items = base.snapshot_max_items

    return ConversationSettings(
        provider=provider,
        model=model,
        safe_mode=bool(safe_mode),
        redaction_level=redaction_level,
        max_rewrites=max_rewrites,
        lexeme_field_index=int(lexeme_field_index),
        lexeme_field_names=base.lexeme_field_names,
        gloss_field_index=None if gloss_field_index is None else int(gloss_field_index),
        gloss_field_names=base.gloss_field_names,
        snapshot_max_items=int(snapshot_max_items),
    )


def _cmd_run(args: argparse.Namespace) -> None:
    col = Collection(args.collection)
    try:
        settings = _merged_settings(col, args)

        deck_ids: list[DeckId] = []
        for deck_name in args.deck:
            did = col.decks.id_for_name(deck_name)
            if not did:
                raise SystemExit(f"deck not found: {deck_name}")
            deck_ids.append(DeckId(did))

        provider: ConversationProvider
        if settings.provider == "fake":
            scripted = []
            if args.provider_script:
                scripted = json.loads(
                    Path(args.provider_script).read_text(encoding="utf-8")
                )
                if not isinstance(scripted, list):
                    raise SystemExit("--provider-script must be a JSON list")
            provider = FakeConversationProvider(scripted=scripted)
        elif settings.provider == "local":
            provider = LocalConversationProvider()
        else:
            api_key = resolve_openai_api_key(api_key_file=args.api_key_file)
            if not api_key:
                raise SystemExit(
                    "OpenAI API key missing; set OPENAI_API_KEY or provide --api-key-file"
                )
            provider = OpenAIConversationProvider(api_key=api_key, model=settings.model)

        session = ConversationSession.start(
            col=col,
            deck_ids=deck_ids,
            settings=settings,
            provider=provider,
            topic_id=args.topic,
        )

        turns = _load_script(Path(args.script))
        transcript: list[dict[str, Any]] = []
        for turn in turns:
            result = session.run_turn(
                text_ko=turn.user_text_ko, confidence=turn.confidence
            )

            if turn.events:
                for event in turn.events:
                    session.log_event(event)
            transcript.append(
                {
                    "turn_index": session.state.turn_index,
                    "user_input": result.user_input.text_ko,
                    "assistant": result.response.to_json_dict(),
                }
            )
        wrap = session.end(summary={"turns": len(turns)})
        print(
            orjson.dumps(
                {
                    "session_id": session.session_id,
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
        settings = _merged_settings(col, args)

        deck_ids: list[DeckId] = []
        for deck_name in args.deck:
            did = col.decks.id_for_name(deck_name)
            if not did:
                raise SystemExit(f"deck not found: {deck_name}")
            deck_ids.append(DeckId(did))
        snapshot = build_deck_snapshot(
            col,
            deck_ids,
            lexeme_field_index=settings.lexeme_field_index,
            lexeme_field_names=settings.lexeme_field_names,
            gloss_field_index=settings.gloss_field_index,
            gloss_field_names=settings.gloss_field_names,
            max_items=settings.snapshot_max_items,
        )
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
        settings = load_conversation_settings(col)
        redaction_level = settings.redaction_level
        if args.redaction is not None:
            redaction_level = RedactionLevel(args.redaction)
        exported = export_conversation_telemetry(
            col,
            limit_sessions=args.limit_sessions,
            redaction_level=redaction_level,
        )
        print(exported.to_json())
    finally:
        col.close()


def _cmd_openai_smoke(args: argparse.Namespace) -> None:
    api_key = resolve_openai_api_key(api_key_file=Path(args.api_key_file))
    if not api_key:
        raise SystemExit(
            "OpenAI API key missing (set OPENAI_API_KEY or create gpt-api.txt)"
        )
    model = args.model
    if not isinstance(model, str) or not model:
        model = "gpt-4o-mini"

    # Keep this intentionally tiny: it tests TLS/network/auth + JSON-mode parsing,
    # without depending on the Conversation schema/prompting.
    client = OpenAIResponsesJsonClient(api_key=api_key, model=model)
    raw = client.request_json(
        system_role=(
            "Return ONLY a JSON object with keys: ok (boolean), reply (string). "
            "Set ok=true and reply='pong'."
        ),
        user_json={"ping": "ping"},
    )
    if raw.get("ok") is not True or raw.get("reply") != "pong":
        raise SystemExit(f"unexpected response: {raw!r}")
    print(orjson.dumps(raw, option=orjson.OPT_INDENT_2).decode("utf-8"))


def _cmd_plan_reply(args: argparse.Namespace) -> None:
    col = Collection(args.collection)
    try:
        settings = _merged_settings(col, args)

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
            provide_micro_feedback=instructions.provide_micro_feedback,
            provide_suggested_english_intent=instructions.provide_suggested_english_intent,
            max_corrections=instructions.max_corrections,
            safe_mode=settings.safe_mode,
            lexical_similarity_max=settings.lexical_similarity_max,
            semantic_similarity_max=settings.semantic_similarity_max,
        )

        provider: object
        if settings.provider == "fake":
            scripted = []
            if args.provider_script:
                scripted = json.loads(
                    Path(args.provider_script).read_text(encoding="utf-8")
                )
                if not isinstance(scripted, list):
                    raise SystemExit("--provider-script must be a JSON list")
            provider = FakePlanReplyProvider(scripted=scripted)
        else:
            api_key = resolve_openai_api_key(api_key_file=args.api_key_file)
            if not api_key:
                raise SystemExit(
                    "OpenAI API key missing; set OPENAI_API_KEY or provide --api-key-file"
                )
            provider = OpenAIPlanReplyProvider(api_key=api_key, model=settings.model)

        gateway = PlanReplyGateway(provider=provider)  # type: ignore[arg-type]
        req = PlanReplyRequest(
            system_role=PLAN_REPLY_SYSTEM_ROLE,
            conversation_state=conv_state,
            draft_ko=args.draft_ko,
            language_constraints=constraints,
            generation_instructions=instructions,
        )
        resp = gateway.run(request=req)
        print(orjson.dumps(resp.to_json_dict()).decode("utf-8"))
    finally:
        col.close()


def _cmd_apply_reinforced(args: argparse.Namespace) -> None:
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
        suggestions = reinforced_cards_from_wrap(wrap, deck_id=int(did))
        created = apply_reinforced_cards(col, suggestions)
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
            print(
                orjson.dumps(
                    {"found": True, "lexeme": entry.lexeme, "gloss": entry.gloss}
                ).decode("utf-8")
            )
    finally:
        col.close()


def _cmd_rebuild_glossary(args: argparse.Namespace) -> None:
    col = Collection(args.collection)
    try:
        base = load_conversation_settings(col)
        deck_ids: list[DeckId] = []
        for deck_name in args.deck:
            did = col.decks.id_for_name(deck_name)
            if not did:
                raise SystemExit(f"deck not found: {deck_name}")
            deck_ids.append(DeckId(did))
        lexeme_field_names = (
            tuple(args.lexeme_field_name)
            if args.lexeme_field_name
            else base.lexeme_field_names
        )
        gloss_field_names = (
            tuple(args.gloss_field_name)
            if args.gloss_field_name
            else base.gloss_field_names
        )
        snapshot = build_deck_snapshot(
            col,
            deck_ids,
            lexeme_field_index=int(args.lexeme_field_index),
            lexeme_field_names=lexeme_field_names,
            gloss_field_index=None
            if args.no_gloss_field
            else int(args.gloss_field_index),
            gloss_field_names=gloss_field_names,
            max_items=int(args.snapshot_max_items),
        )
        count = rebuild_glossary_from_snapshot(col, snapshot)
        print(orjson.dumps({"updated": count}).decode("utf-8"))
    finally:
        col.close()


def _cmd_import_glossary(args: argparse.Namespace) -> None:
    col = Collection(args.collection)
    try:
        updated = import_glossary_file(col, args.file, format=args.format)
        print(orjson.dumps({"updated": updated}).decode("utf-8"))
    finally:
        col.close()


def _cmd_settings(args: argparse.Namespace) -> None:
    col = Collection(args.collection)
    try:
        settings = load_conversation_settings(col)
        changed = False
        if args.set_provider is not None:
            settings = replace(settings, provider=args.set_provider)
            changed = True
        if args.set_model is not None:
            settings = replace(settings, model=args.set_model)
            changed = True
        if args.set_safe_mode is not None:
            settings = replace(settings, safe_mode=args.set_safe_mode == "true")
            changed = True
        if args.set_redaction is not None:
            settings = replace(
                settings, redaction_level=RedactionLevel(args.set_redaction)
            )
            changed = True
        if args.set_max_rewrites is not None:
            settings = replace(settings, max_rewrites=args.set_max_rewrites)
            changed = True
        if args.set_lexeme_field_index is not None:
            settings = replace(settings, lexeme_field_index=args.set_lexeme_field_index)
            changed = True
        if args.set_no_gloss_field:
            settings = replace(settings, gloss_field_index=None)
            changed = True
        if args.set_gloss_field_index is not None:
            settings = replace(settings, gloss_field_index=args.set_gloss_field_index)
            changed = True
        if args.set_snapshot_max_items is not None:
            settings = replace(settings, snapshot_max_items=args.set_snapshot_max_items)
            changed = True

        if changed:
            save_conversation_settings(col, settings)

        print(
            orjson.dumps(
                {
                    "provider": settings.provider,
                    "model": settings.model,
                    "safe_mode": settings.safe_mode,
                    "redaction_level": settings.redaction_level.value,
                    "max_rewrites": settings.max_rewrites,
                    "lexeme_field_index": settings.lexeme_field_index,
                    "gloss_field_index": settings.gloss_field_index,
                    "snapshot_max_items": settings.snapshot_max_items,
                }
            ).decode("utf-8")
        )
    finally:
        col.close()


if __name__ == "__main__":
    main()
