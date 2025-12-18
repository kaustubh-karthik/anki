from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import orjson

from anki.collection import Collection


def _now_ms() -> int:
    return int(time.time() * 1000)


def ensure_conversation_schema(col: Collection) -> None:
    # Use executemany() so the Rust backend uses stmt.execute(), which supports DDL.
    ddl = [
        """
create table if not exists elites_conversation_sessions (
  id integer primary key,
  deck_ids text not null,
  started_ms integer not null,
  ended_ms integer,
  summary_json blob
)
""",
        """
create table if not exists elites_conversation_events (
  id integer primary key,
  session_id integer not null,
  turn_index integer not null,
  event_type text not null,
  ts_ms integer not null,
  payload_json blob not null,
  foreign key(session_id) references elites_conversation_sessions(id)
)
""",
        """
create table if not exists elites_conversation_items (
  item_id text primary key,
  kind text not null,
  value text not null,
  mastery_json blob not null,
  updated_ms integer not null
)
""",
        "create index if not exists idx_elites_conversation_events_session on elites_conversation_events(session_id)",
    ]

    for sql in ddl:
        col.db.executemany(sql, [()])


@dataclass(slots=True)
class ConversationTelemetryStore:
    col: Collection

    def __post_init__(self) -> None:
        ensure_conversation_schema(self.col)

    def start_session(self, deck_ids: list[int]) -> int:
        started = _now_ms()
        deck_ids_str = ",".join(str(x) for x in deck_ids)
        self.col.db.executemany(
            "insert into elites_conversation_sessions(deck_ids, started_ms) values(?, ?)",
            [(deck_ids_str, started)],
        )
        session_id = self.col.db.scalar("select last_insert_rowid()")
        assert isinstance(session_id, int)
        return session_id

    def end_session(self, session_id: int, summary: dict[str, Any]) -> None:
        ended = _now_ms()
        payload = orjson.dumps(summary)
        self.col.db.executemany(
            "update elites_conversation_sessions set ended_ms=?, summary_json=? where id=?",
            [(ended, payload, session_id)],
        )

    def log_event(
        self,
        *,
        session_id: int,
        turn_index: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        ts = _now_ms()
        self.col.db.executemany(
            """
insert into elites_conversation_events(session_id, turn_index, event_type, ts_ms, payload_json)
values(?, ?, ?, ?, ?)
""",
            [(session_id, turn_index, event_type, ts, orjson.dumps(payload))],
        )

