from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from anki.collection import Collection
from anki.decks import DeckId

from .gateway import ConversationGateway, ConversationProvider, OpenAIConversationProvider
from .planner import ConversationPlanner
from .snapshot import build_deck_snapshot
from .telemetry import ConversationTelemetryStore
from .types import ConversationRequest, UserInput

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
        turns.append(ScriptTurn(user_text_ko=text, confidence=conf))
    return turns


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="anki-conversation")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run a text-only conversation session")
    run.add_argument("--collection", required=True, help="Path to .anki2 file")
    run.add_argument("--deck", required=True, help="Deck name")
    run.add_argument("--script", required=True, help="Path to JSON script")
    run.add_argument(
        "--provider",
        choices=["fake", "openai"],
        default="fake",
        help="LLM provider (default: fake)",
    )
    run.add_argument("--api-key-file", default="gpt-api.txt")
    run.add_argument("--model", default="gpt-5-nano")
    run.add_argument(
        "--provider-script",
        help="JSON file with scripted assistant responses (fake provider only)",
    )

    args = parser.parse_args(argv)

    if args.cmd == "run":
        _cmd_run(args)


def _cmd_run(args: argparse.Namespace) -> None:
    col = Collection(args.collection)
    try:
        did = col.decks.id_for_name(args.deck)
        if not did:
            raise SystemExit(f"deck not found: {args.deck}")

        snapshot = build_deck_snapshot(col, [DeckId(did)])
        planner = ConversationPlanner(snapshot)
        telemetry = ConversationTelemetryStore(col)
        session_id = telemetry.start_session(list(snapshot.deck_ids))

        turns = _load_script(Path(args.script))

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

        gateway = ConversationGateway(provider=provider)

        state = planner.initial_state(summary="Conversation practice")
        transcript: list[dict[str, Any]] = []
        for turn in turns:
            user_input = UserInput(
                text_ko=turn.user_text_ko, confidence=turn.confidence  # type: ignore[arg-type]
            )
            conv_state, constraints, instructions = planner.plan_turn(state, user_input)
            request = ConversationRequest(
                system_role=SYSTEM_ROLE,
                conversation_state=conv_state,
                user_input=user_input,
                language_constraints=constraints,
                generation_instructions=instructions,
            )
            response = gateway.run_turn(request=request)
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

        telemetry.end_session(session_id, summary={"turns": len(turns)})
        print(
            orjson.dumps({"session_id": session_id, "transcript": transcript}).decode(
                "utf-8"
            )
        )
    finally:
        col.close()


if __name__ == "__main__":
    main()

