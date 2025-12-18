# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import aqt
from anki.decks import DeckId
from anki.conversation import (
    ConversationGateway,
    ConversationPlanner,
    ConversationTelemetryStore,
    OpenAIConversationProvider,
    build_deck_snapshot,
    lookup_gloss,
)
from anki.conversation.cli import SYSTEM_ROLE
from anki.conversation.types import ConversationRequest, GenerationInstructions, UserInput
from aqt.qt import *
from aqt.utils import disable_help_button, restoreGeom, saveGeom
from aqt.webview import AnkiWebView, AnkiWebViewKind

if TYPE_CHECKING:
    import aqt.main


@dataclass(slots=True)
class _Session:
    deck_ids: list[int]
    planner: ConversationPlanner
    telemetry: ConversationTelemetryStore
    gateway: ConversationGateway | None
    mastery_cache: dict[str, dict[str, int]]
    state: Any
    session_id: int
    lexeme_set: set[str]


class ConversationDialog(QDialog):
    TITLE = "conversationPractice"

    def __init__(self, mw: aqt.main.AnkiQt) -> None:
        super().__init__(mw, Qt.WindowType.Window)
        self.mw = mw
        self._session: _Session | None = None
        disable_help_button(self)
        restoreGeom(self, self.TITLE, default_size=(900, 800))
        self.setWindowTitle("Conversation Practice")
        self.web = AnkiWebView(kind=AnkiWebViewKind.CONVERSATION)
        self.web.set_bridge_command(self._on_bridge_cmd, self)
        self.web.load_sveltekit_page("conversation")
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.web)
        self.setLayout(layout)
        self.show()

    def reject(self) -> None:
        saveGeom(self, self.TITLE)
        self.web.cleanup()
        super().reject()

    def _on_bridge_cmd(self, cmd: str) -> Any:
        # Commands are string-based; return JSON-serializable values.
        if cmd == "conversation:init":
            return {"ok": True}
        if cmd.startswith("conversation:gloss:"):
            lexeme = cmd.split(":", 2)[2]
            entry = lookup_gloss(self.mw.col, lexeme)
            if entry is None:
                return {"found": False}
            return {"found": True, "lexeme": entry.lexeme, "gloss": entry.gloss}
        if cmd.startswith("conversation:start:"):
            payload = json.loads(cmd.split(":", 2)[2])
            return self._start_session(payload)
        if cmd.startswith("conversation:turn:"):
            payload = json.loads(cmd.split(":", 2)[2])
            return self._run_turn(payload)
        return None

    def _start_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        deck_names = payload.get("decks") or []
        if not isinstance(deck_names, list) or not all(isinstance(x, str) for x in deck_names):
            return {"ok": False, "error": "invalid decks"}
        deck_ids: list[int] = []
        for name in deck_names:
            did = self.mw.col.decks.id_for_name(name)
            if not did:
                return {"ok": False, "error": f"deck not found: {name}"}
            deck_ids.append(int(did))
        snapshot = build_deck_snapshot(self.mw.col, [DeckId(d) for d in deck_ids])
        planner = ConversationPlanner(snapshot)
        telemetry = ConversationTelemetryStore(self.mw.col)
        session_id = telemetry.start_session(list(snapshot.deck_ids))
        mastery_cache = telemetry.load_mastery_cache([str(i.item_id) for i in snapshot.items])
        # dev convenience: read key from gpt-api.txt if present
        try:
            api_key = open("gpt-api.txt", encoding="utf-8").read().strip()
        except Exception:
            api_key = ""
        gateway: ConversationGateway | None = None
        if api_key:
            provider = OpenAIConversationProvider(api_key=api_key)
            gateway = ConversationGateway(provider=provider)
        state = planner.initial_state(summary="Conversation practice")
        self._session = _Session(
            deck_ids=deck_ids,
            planner=planner,
            telemetry=telemetry,
            gateway=gateway,
            mastery_cache=mastery_cache,
            state=state,
            session_id=session_id,
            lexeme_set={i.lexeme for i in snapshot.items},
        )
        return {"ok": True, "session_id": session_id, "llm_enabled": gateway is not None}

    def _run_turn(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            return {"ok": False, "error": "session not started"}
        if self._session.gateway is None:
            return {"ok": False, "error": "LLM provider not configured"}
        text = payload.get("text_ko")
        if not isinstance(text, str):
            return {"ok": False, "error": "invalid text"}
        confidence = payload.get("confidence")
        if confidence not in (None, "confident", "unsure", "guessing"):
            confidence = None
        user_input = UserInput(text_ko=text, confidence=confidence)
        conv_state, constraints, instructions = self._session.planner.plan_turn(
            self._session.state, user_input, mastery=self._session.mastery_cache
        )
        instructions = GenerationInstructions(
            conversation_goal=instructions.conversation_goal,
            tone=instructions.tone,
            register=instructions.register,
            provide_follow_up_question=True,
            provide_micro_feedback=True,
            provide_suggested_english_intent=True,
            max_corrections=1,
            safe_mode=True,
        )
        request = ConversationRequest(
            system_role=SYSTEM_ROLE,
            conversation_state=conv_state,
            user_input=user_input,
            language_constraints=constraints,
            generation_instructions=instructions,
        )
        response = self._session.gateway.run_turn(request=request)
        self._session.state.last_assistant_turn_ko = response.assistant_reply_ko
        return {"ok": True, "response": response.to_json_dict()}


def open_conversation_practice() -> None:
    ConversationDialog(aqt.mw)
