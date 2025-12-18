# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from anki.collection import Collection

from .redaction import redact_text
from .settings import RedactionLevel


@dataclass(frozen=True)
class TelemetryExport:
    sessions: list[dict[str, Any]]
    events: list[dict[str, Any]]
    items: list[dict[str, Any]]

    def to_json(self) -> str:
        return json.dumps(
            {"sessions": self.sessions, "events": self.events, "items": self.items},
            ensure_ascii=False,
        )


def export_conversation_telemetry(
    col: Collection,
    *,
    limit_sessions: int = 100,
    redaction_level: RedactionLevel = RedactionLevel.none,
) -> TelemetryExport:
    def redact_json(value: Any) -> Any:
        if redaction_level == RedactionLevel.none:
            return value
        if isinstance(value, str):
            return redact_text(value, redaction_level).text
        if isinstance(value, list):
            return [redact_json(v) for v in value]
        if isinstance(value, dict):
            return {k: redact_json(v) for k, v in value.items()}
        return value

    sessions_rows = col.db.all(
        "select id, deck_ids, started_ms, ended_ms, summary_json from elites_conversation_sessions order by id desc limit ?",
        limit_sessions,
    )
    sessions: list[dict[str, Any]] = []
    session_ids: list[int] = []
    for sid, deck_ids, started, ended, summary in sessions_rows:
        if not isinstance(sid, int):
            continue
        session_ids.append(sid)
        if redaction_level != RedactionLevel.none and isinstance(summary, str):
            try:
                summary_obj = json.loads(summary)
            except Exception:
                summary_obj = summary
            summary = json.dumps(redact_json(summary_obj), ensure_ascii=False)
        sessions.append(
            {
                "id": sid,
                "deck_ids": deck_ids,
                "started_ms": started,
                "ended_ms": ended,
                "summary_json": summary,
            }
        )

    events: list[dict[str, Any]] = []
    if session_ids:
        placeholders = ",".join("?" for _ in session_ids)
        rows = col.db.all(
            f"select session_id, turn_index, event_type, ts_ms, payload_json from elites_conversation_events where session_id in ({placeholders}) order by id",
            *session_ids,
        )
        for session_id, turn_index, event_type, ts_ms, payload_json in rows:
            if redaction_level != RedactionLevel.none and isinstance(payload_json, str):
                try:
                    payload_obj = json.loads(payload_json)
                except Exception:
                    payload_obj = payload_json
                payload_json = json.dumps(redact_json(payload_obj), ensure_ascii=False)
            events.append(
                {
                    "session_id": session_id,
                    "turn_index": turn_index,
                    "event_type": event_type,
                    "ts_ms": ts_ms,
                    "payload_json": payload_json,
                }
            )

    items: list[dict[str, Any]] = []
    for item_id, kind, value, mastery_json, updated_ms in col.db.all(
        "select item_id, kind, value, mastery_json, updated_ms from elites_conversation_items order by updated_ms desc"
    ):
        items.append(
            {
                "item_id": item_id,
                "kind": kind,
                "value": value,
                "mastery_json": mastery_json,
                "updated_ms": updated_ms,
            }
        )

    return TelemetryExport(sessions=sessions, events=events, items=items)
