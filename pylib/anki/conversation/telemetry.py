from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import orjson

from anki.collection import Collection

MasteryCounters = dict[str, int]
MasteryCache = dict[str, MasteryCounters]


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
        """
create table if not exists elites_conversation_glossary (
  lexeme text primary key,
  gloss text,
  source_note_id integer,
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

    def load_mastery_cache(self, item_ids: list[str]) -> MasteryCache:
        return self.get_mastery_bulk(item_ids)

    def bump_item(
        self,
        *,
        item_id: str,
        kind: str,
        value: str,
        deltas: dict[str, int],
    ) -> None:
        cache = self.load_mastery_cache([item_id])
        self.bump_item_cached(
            cache,
            item_id=item_id,
            kind=kind,
            value=value,
            deltas=deltas,
        )

    def bump_item_cached(
        self,
        cache: MasteryCache,
        *,
        item_id: str,
        kind: str,
        value: str,
        deltas: dict[str, int],
    ) -> None:
        """Upsert deterministic mastery counters for a lexeme/grammar item.

        Stored as a JSON object mapping counter names -> integers.
        The provided cache is updated in-place and written back to the DB
        without a read-before-write.
        """

        mastery = cache.get(item_id)
        if mastery is None:
            mastery = {}
            cache[item_id] = mastery

        for key, delta in deltas.items():
            mastery[key] = mastery.get(key, 0) + int(delta)

        self._upsert_item(item_id=item_id, kind=kind, value=value, mastery=mastery)

    def _upsert_item(
        self, *, item_id: str, kind: str, value: str, mastery: MasteryCounters
    ) -> None:
        now = _now_ms()
        payload = orjson.dumps(mastery).decode("utf-8")
        self.col.db.executemany(
            """
insert into elites_conversation_items(item_id, kind, value, mastery_json, updated_ms)
values(?, ?, ?, ?, ?)
on conflict(item_id) do update set
  kind=excluded.kind,
  value=excluded.value,
  mastery_json=excluded.mastery_json,
  updated_ms=excluded.updated_ms
""",
            [(item_id, kind, value, payload, now)],
        )

    def get_mastery_bulk(self, item_ids: list[str]) -> dict[str, dict[str, int]]:
        if not item_ids:
            return {}
        placeholders = ",".join("?" for _ in item_ids)
        rows = self.col.db.all(
            f"select item_id, mastery_json from elites_conversation_items where item_id in ({placeholders})",
            *item_ids,
        )
        out: MasteryCache = {}
        for item_id, mastery_json in rows:
            if not isinstance(item_id, str) or not isinstance(mastery_json, str):
                continue
            try:
                parsed = orjson.loads(mastery_json)
            except Exception:
                continue
            if not isinstance(parsed, dict):
                continue
            cleaned: MasteryCounters = {}
            for k, v in parsed.items():
                if isinstance(k, str) and isinstance(v, int):
                    cleaned[k] = v
            out[item_id] = cleaned
        return out

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
        payload = orjson.dumps(summary).decode("utf-8")
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
            [
                (
                    session_id,
                    turn_index,
                    event_type,
                    ts,
                    orjson.dumps(payload).decode("utf-8"),
                )
            ],
        )
