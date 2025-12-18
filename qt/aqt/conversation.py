# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

import json
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import requests

import aqt
from anki.conversation import (
    ConversationGateway,
    ConversationPlanner,
    ConversationTelemetryStore,
    LocalConversationProvider,
    LocalTranslateProvider,
    OpenAIConversationProvider,
    OpenAIPlanReplyProvider,
    OpenAITranslateProvider,
    PlanReplyGateway,
    PlanReplyRequest,
    TranslateGateway,
    TranslateRequest,
    apply_suggested_cards,
    build_deck_snapshot,
    compute_session_wrap,
    export_conversation_telemetry,
    lookup_gloss,
    suggestions_from_wrap,
)
from anki.conversation.events import apply_missed_targets, record_event_from_payload
from anki.conversation.keys import resolve_openai_api_key
from anki.conversation.prompts import SYSTEM_ROLE
from anki.conversation.redaction import redact_text
from anki.conversation.settings import (
    ConversationSettings,
    RedactionLevel,
    load_conversation_settings,
    save_conversation_settings,
)
from anki.conversation.types import (
    ConversationRequest,
    GenerationInstructions,
    UserInput,
)
from anki.decks import DeckId
from aqt.qt import *
from aqt.utils import disable_help_button, restoreGeom, saveGeom
from aqt.webview import AnkiWebView, AnkiWebViewKind

if TYPE_CHECKING:
    import aqt.main


@dataclass
class _Session:
    deck_ids: list[int]
    snapshot: Any
    planner: ConversationPlanner
    telemetry: ConversationTelemetryStore
    gateway: ConversationGateway | None
    mastery_cache: dict[str, dict[str, int]]
    state: Any
    session_id: int
    lexeme_set: set[str]
    settings: ConversationSettings


class ConversationDialog(QDialog):
    TITLE = "conversationPractice"

    def __init__(self, mw: aqt.main.AnkiQt) -> None:
        super().__init__(mw, Qt.WindowType.Window)
        self.mw = mw
        self._session: _Session | None = None
        self._job_lock = Lock()
        self._busy = False
        self._jobs: dict[str, dict[str, Any]] = {}
        self._queued_events: list[dict[str, Any]] = []
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
        if self._session is not None:
            try:
                self._end_session()
            except Exception:
                pass
        self.web.cleanup()
        super().reject()

    def _on_bridge_cmd(self, cmd: str) -> Any:
        # Commands are string-based; return JSON-serializable values.
        # Never raise, or the webview will receive invalid JSON and lock up.
        try:
            result: Any = None
            if cmd == "conversation:init":
                result = {"ok": True}
            elif cmd == "conversation:decks":
                names = [
                    d.name for d in self.mw.col.decks.all_names_and_ids() if d.name
                ]
                result = {"ok": True, "decks": names}
            elif cmd == "conversation:get_settings":
                result = self._get_settings()
            elif cmd.startswith("conversation:set_settings:"):
                payload = json.loads(cmd.split(":", 2)[2])
                result = self._set_settings(payload)
            elif cmd == "conversation:end":
                result = self._end_session()
            elif cmd.startswith("conversation:export_telemetry:"):
                payload = json.loads(cmd.split(":", 2)[2])
                result = self._export_telemetry(payload)
            elif cmd == "conversation:wrap":
                result = self._get_wrap()
            elif cmd.startswith("conversation:gloss:"):
                lexeme = cmd.split(":", 2)[2]
                entry = lookup_gloss(self.mw.col, lexeme)
                if entry is None:
                    result = {"found": False}
                else:
                    result = {
                        "found": True,
                        "lexeme": entry.lexeme,
                        "gloss": entry.gloss,
                    }
            elif cmd.startswith("conversation:start:"):
                payload = json.loads(cmd.split(":", 2)[2])
                result = self._start_session(payload)
            elif cmd.startswith("conversation:turn_async:"):
                payload = json.loads(cmd.split(":", 2)[2])
                result = self._start_job(kind="turn", payload=payload)
            elif cmd.startswith("conversation:translate_async:"):
                payload = json.loads(cmd.split(":", 2)[2])
                result = self._start_job(kind="translate", payload=payload)
            elif cmd.startswith("conversation:plan_reply_async:"):
                payload = json.loads(cmd.split(":", 2)[2])
                result = self._start_job(kind="plan_reply", payload=payload)
            elif cmd.startswith("conversation:poll:"):
                payload = json.loads(cmd.split(":", 2)[2])
                result = self._poll_job(payload)
            elif cmd.startswith("conversation:turn:"):
                payload = json.loads(cmd.split(":", 2)[2])
                result = self._run_turn(payload)
            elif cmd.startswith("conversation:event:"):
                payload = json.loads(cmd.split(":", 2)[2])
                result = self._log_event(payload)
            elif cmd.startswith("conversation:apply_suggestions:"):
                payload = json.loads(cmd.split(":", 2)[2])
                result = self._apply_suggestions(payload)
            elif cmd.startswith("conversation:plan_reply:"):
                payload = json.loads(cmd.split(":", 2)[2])
                result = self._plan_reply(payload)
            elif cmd.startswith("conversation:translate:"):
                payload = json.loads(cmd.split(":", 2)[2])
                result = self._translate(payload)
            return result
        except Exception as e:
            return {"ok": False, "error": str(e) or type(e).__name__}

    def _start_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        deck_names = payload.get("decks") or []
        if not isinstance(deck_names, list) or not all(
            isinstance(x, str) for x in deck_names
        ):
            return {"ok": False, "error": "invalid decks"}
        deck_ids: list[int] = []
        for name in deck_names:
            did = self.mw.col.decks.id_for_name(name)
            if not did:
                return {"ok": False, "error": f"deck not found: {name}"}
            deck_ids.append(int(did))
        settings = load_conversation_settings(self.mw.col)
        snapshot = build_deck_snapshot(
            self.mw.col,
            [DeckId(d) for d in deck_ids],
            lexeme_field_index=settings.lexeme_field_index,
            lexeme_field_names=settings.lexeme_field_names,
            gloss_field_index=settings.gloss_field_index,
            gloss_field_names=settings.gloss_field_names,
            max_items=settings.snapshot_max_items,
        )
        planner = ConversationPlanner(snapshot)
        telemetry = ConversationTelemetryStore(self.mw.col)
        session_id = telemetry.start_session(list(snapshot.deck_ids))
        mastery_cache = telemetry.load_mastery_cache(
            [str(i.item_id) for i in snapshot.items]
        )
        gateway: ConversationGateway | None = None
        if settings.provider == "local":
            gateway = ConversationGateway(
                provider=LocalConversationProvider(),
                max_rewrites=settings.max_rewrites,
            )
        elif settings.provider == "openai":
            api_key = resolve_openai_api_key()
            if api_key:
                provider = OpenAIConversationProvider(
                    api_key=api_key, model=settings.model
                )
                gateway = ConversationGateway(
                    provider=provider, max_rewrites=settings.max_rewrites
                )
        topic_id = payload.get("topic_id")
        if not isinstance(topic_id, str):
            topic_id = None
        state = planner.initial_state(
            summary="Conversation practice", topic_id=topic_id
        )
        self._session = _Session(
            deck_ids=deck_ids,
            snapshot=snapshot,
            planner=planner,
            telemetry=telemetry,
            gateway=gateway,
            mastery_cache=mastery_cache,
            state=state,
            session_id=session_id,
            lexeme_set={i.lexeme for i in snapshot.items},
            settings=settings,
        )
        return {
            "ok": True,
            "session_id": session_id,
            "llm_enabled": gateway is not None,
        }

    def _start_job(self, *, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        if kind not in ("turn", "translate", "plan_reply"):
            return {"ok": False, "error": "invalid job kind"}
        if self._session is None:
            return {"ok": False, "error": "session not started"}

        with self._job_lock:
            if self._busy:
                return {"ok": False, "error": "busy"}
            self._busy = True
            job_id = str(uuid4())
            self._jobs[job_id] = {"status": "pending", "kind": kind}

        def run() -> dict[str, Any]:
            try:
                self._flush_queued_events()
                if kind == "turn":
                    return self._run_turn(payload)
                if kind == "translate":
                    return self._translate(payload)
                if kind == "plan_reply":
                    return self._plan_reply(payload)
                return {"ok": False, "error": "invalid job kind"}
            except requests.exceptions.Timeout:
                return {"ok": False, "error": "request timed out"}
            except requests.exceptions.RequestException as e:
                return {"ok": False, "error": f"network error: {e}"}
            except Exception as e:
                return {"ok": False, "error": str(e) or type(e).__name__}

        def on_done(fut: Any) -> None:
            try:
                result = fut.result()
            except Exception as e:
                result = {"ok": False, "error": str(e) or type(e).__name__}
            with self._job_lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job["status"] = "done"
                    job["result"] = result
                self._busy = False

        self.mw.taskman.run_in_background(run, on_done)
        return {"ok": True, "job_id": job_id}

    def _poll_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = payload.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            return {"ok": False, "error": "job_id required"}
        with self._job_lock:
            job = self._jobs.get(job_id)
            if job is None:
                return {"ok": False, "error": "unknown job"}
            if job.get("status") != "done":
                return {"ok": True, "status": "pending"}
            result = job.get("result", {"ok": False, "error": "missing result"})
            del self._jobs[job_id]
        return {"ok": True, "status": "done", "result": result}

    def _flush_queued_events(self) -> None:
        if self._session is None:
            return
        while True:
            with self._job_lock:
                if not self._queued_events:
                    return
                payload = self._queued_events.pop(0)
            try:
                record_event_from_payload(
                    telemetry=self._session.telemetry,
                    mastery_cache=self._session.mastery_cache,
                    session_id=self._session.session_id,
                    turn_index=self._session.state.turn_index,
                    payload=payload,
                )
            except Exception:
                # ignore bad queued events
                pass

    def _get_settings(self) -> dict[str, Any]:
        settings = load_conversation_settings(self.mw.col)
        return {
            "ok": True,
            "settings": {
                "provider": settings.provider,
                "model": settings.model,
                "safe_mode": settings.safe_mode,
                "redaction": settings.redaction_level.value,
                "max_rewrites": settings.max_rewrites,
                "lexeme_field_index": settings.lexeme_field_index,
                "lexeme_field_names": list(settings.lexeme_field_names),
                "gloss_field_index": settings.gloss_field_index,
                "gloss_field_names": list(settings.gloss_field_names),
                "snapshot_max_items": settings.snapshot_max_items,
            },
        }

    def _set_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {"ok": False, "error": "invalid payload"}
        cur = load_conversation_settings(self.mw.col)

        provider = payload.get("provider", cur.provider)
        model = payload.get("model", cur.model)
        safe_mode = payload.get("safe_mode", cur.safe_mode)
        redaction = payload.get("redaction", cur.redaction_level.value)
        max_rewrites = payload.get("max_rewrites", cur.max_rewrites)
        lexeme_field_index = payload.get("lexeme_field_index", cur.lexeme_field_index)
        lexeme_field_names = payload.get(
            "lexeme_field_names", list(cur.lexeme_field_names)
        )
        gloss_field_index = payload.get("gloss_field_index", cur.gloss_field_index)
        gloss_field_names = payload.get(
            "gloss_field_names", list(cur.gloss_field_names)
        )
        snapshot_max_items = payload.get("snapshot_max_items", cur.snapshot_max_items)

        if not isinstance(provider, str):
            provider = cur.provider
        if provider not in ("fake", "local", "openai"):
            return {"ok": False, "error": "invalid provider"}
        if not isinstance(model, str) or not model:
            model = cur.model
        if not isinstance(safe_mode, bool):
            safe_mode = cur.safe_mode
        if not isinstance(redaction, str) or redaction not in (
            e.value for e in RedactionLevel
        ):
            redaction = cur.redaction_level.value
        if not isinstance(max_rewrites, int) or max_rewrites < 0 or max_rewrites > 10:
            max_rewrites = cur.max_rewrites
        if (
            not isinstance(lexeme_field_index, int)
            or lexeme_field_index < 0
            or lexeme_field_index > 50
        ):
            lexeme_field_index = cur.lexeme_field_index
        if not isinstance(lexeme_field_names, list) or not all(
            isinstance(x, str) for x in lexeme_field_names
        ):
            lexeme_field_names = list(cur.lexeme_field_names)
        lexeme_field_names = [x.strip() for x in lexeme_field_names if x.strip()][:10]
        if gloss_field_index is not None and (
            not isinstance(gloss_field_index, int)
            or gloss_field_index < 0
            or gloss_field_index > 50
        ):
            gloss_field_index = cur.gloss_field_index
        if not isinstance(gloss_field_names, list) or not all(
            isinstance(x, str) for x in gloss_field_names
        ):
            gloss_field_names = list(cur.gloss_field_names)
        gloss_field_names = [x.strip() for x in gloss_field_names if x.strip()][:10]
        if (
            not isinstance(snapshot_max_items, int)
            or snapshot_max_items <= 0
            or snapshot_max_items > 50000
        ):
            snapshot_max_items = cur.snapshot_max_items

        new_settings = ConversationSettings(
            provider=provider,
            model=model,
            safe_mode=safe_mode,
            redaction_level=RedactionLevel(redaction),
            max_rewrites=max_rewrites,
            lexeme_field_index=lexeme_field_index,
            lexeme_field_names=tuple(lexeme_field_names),
            gloss_field_index=gloss_field_index,
            gloss_field_names=tuple(gloss_field_names),
            snapshot_max_items=snapshot_max_items,
        )
        save_conversation_settings(self.mw.col, new_settings)
        return {"ok": True}

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
        redacted = redact_text(text, self._session.settings.redaction_level)
        user_input = UserInput(text_ko=redacted.text, confidence=confidence)
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
            safe_mode=self._session.settings.safe_mode,
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
        missed = self._session.planner.observe_turn(
            self._session.state,
            constraints=constraints,
            user_input=user_input,
            assistant_reply_ko=response.assistant_reply_ko,
            follow_up_question_ko=response.follow_up_question_ko,
        )
        apply_missed_targets(
            telemetry=self._session.telemetry,
            mastery_cache=self._session.mastery_cache,
            missed_item_ids=missed,
        )
        return {"ok": True, "response": response.to_json_dict()}

    def _log_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            return {"ok": False, "error": "session not started"}
        with self._job_lock:
            if self._busy:
                if isinstance(payload, dict):
                    self._queued_events.append(payload)
                return {"ok": True, "queued": True}
        self._flush_queued_events()
        try:
            record_event_from_payload(
                telemetry=self._session.telemetry,
                mastery_cache=self._session.mastery_cache,
                session_id=self._session.session_id,
                turn_index=self._session.state.turn_index,
                payload=payload,
            )
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    def _get_wrap(self) -> dict[str, Any]:
        if self._session is None:
            return {"ok": False, "error": "session not started"}
        wrap = compute_session_wrap(
            snapshot=self._session.snapshot, mastery=self._session.mastery_cache
        )
        return {"ok": True, "wrap": wrap}

    def _end_session(self) -> dict[str, Any]:
        if self._session is None:
            return {"ok": False, "error": "session not started"}
        wrap = compute_session_wrap(
            snapshot=self._session.snapshot, mastery=self._session.mastery_cache
        )
        summary = {"turns": self._session.state.turn_index, "wrap": wrap}
        self._session.telemetry.end_session(self._session.session_id, summary=summary)
        sid = self._session.session_id
        self._session = None
        return {"ok": True, "session_id": sid, "wrap": wrap}

    def _export_telemetry(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            payload = {}
        limit_sessions = payload.get("limit_sessions", 50)
        if not isinstance(limit_sessions, int) or limit_sessions <= 0:
            limit_sessions = 50
        limit_sessions = min(limit_sessions, 500)
        settings = load_conversation_settings(self.mw.col)
        exported = export_conversation_telemetry(
            self.mw.col,
            limit_sessions=limit_sessions,
            redaction_level=settings.redaction_level,
        )
        return {"ok": True, "json": exported.to_json()}

    def _apply_suggestions(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            return {"ok": False, "error": "session not started"}
        deck_name = payload.get("deck")
        if not isinstance(deck_name, str) or not deck_name:
            return {"ok": False, "error": "deck required"}
        did = self.mw.col.decks.id_for_name(deck_name)
        if not did:
            return {"ok": False, "error": "deck not found"}
        wrap = compute_session_wrap(
            snapshot=self._session.snapshot, mastery=self._session.mastery_cache
        )
        suggestions = suggestions_from_wrap(wrap, deck_id=int(did))
        created = apply_suggested_cards(self.mw.col, suggestions)
        return {"ok": True, "created_note_ids": created}

    def _plan_reply(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            return {"ok": False, "error": "session not started"}
        intent = payload.get("intent_en")
        if not isinstance(intent, str) or not intent:
            return {"ok": False, "error": "intent_en required"}
        api_key = resolve_openai_api_key()
        if not api_key or self._session.settings.provider != "openai":
            return {"ok": False, "error": "LLM provider not configured"}
        provider = OpenAIPlanReplyProvider(
            api_key=api_key, model=self._session.settings.model
        )
        gateway = PlanReplyGateway(
            provider=provider, max_rewrites=self._session.settings.max_rewrites
        )
        # reuse planner constraints for current state
        conv_state, constraints, instructions = self._session.planner.plan_turn(
            self._session.state,
            UserInput(text_ko=""),
            mastery=self._session.mastery_cache,
        )
        instructions = GenerationInstructions(
            register=instructions.register,
            tone=instructions.tone,
            safe_mode=self._session.settings.safe_mode,
        )
        req = PlanReplyRequest(
            system_role=SYSTEM_ROLE,
            conversation_state=conv_state,
            intent_en=intent,
            language_constraints=constraints,
            generation_instructions=instructions,
        )
        resp = gateway.run(request=req)
        return {"ok": True, "plan": resp.to_json_dict()}

    def _translate(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            return {"ok": False, "error": "session not started"}
        text = payload.get("text_ko")
        if not isinstance(text, str) or not text.strip():
            return {"ok": False, "error": "text_ko required"}

        provider: object
        if self._session.settings.provider == "local":
            provider = LocalTranslateProvider()
        elif self._session.settings.provider == "openai":
            api_key = resolve_openai_api_key()
            if not api_key:
                return {"ok": False, "error": "OpenAI API key missing"}
            provider = OpenAITranslateProvider(
                api_key=api_key, model=self._session.settings.model
            )
        else:
            return {"ok": False, "error": "translate provider not configured"}

        gateway = TranslateGateway(provider=provider)  # type: ignore[arg-type]
        req = TranslateRequest(system_role=SYSTEM_ROLE, text_ko=text)
        resp = gateway.run(request=req)
        return {"ok": True, "translation_en": resp.translation_en}


def open_conversation_practice() -> None:
    ConversationDialog(aqt.mw)
