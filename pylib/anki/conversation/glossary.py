from __future__ import annotations

from dataclasses import dataclass

from anki.collection import Collection

from .snapshot import DeckSnapshot
from .telemetry import _now_ms, ensure_conversation_schema


@dataclass(frozen=True, slots=True)
class GlossaryEntry:
    lexeme: str
    gloss: str | None


def rebuild_glossary_from_snapshot(col: Collection, snapshot: DeckSnapshot) -> int:
    ensure_conversation_schema(col)
    now = _now_ms()
    rows = []
    for item in snapshot.items:
        if not item.lexeme:
            continue
        if item.gloss is None:
            continue
        rows.append((item.lexeme, item.gloss, item.source_note_id, now))
    if not rows:
        return 0
    col.db.executemany(
        """
insert into elites_conversation_glossary(lexeme, gloss, source_note_id, updated_ms)
values(?, ?, ?, ?)
on conflict(lexeme) do update set
  gloss=excluded.gloss,
  source_note_id=excluded.source_note_id,
  updated_ms=excluded.updated_ms
""",
        rows,
    )
    return len(rows)


def lookup_gloss(col: Collection, lexeme: str) -> GlossaryEntry | None:
    ensure_conversation_schema(col)
    row = col.db.first(
        "select lexeme, gloss from elites_conversation_glossary where lexeme=?",
        lexeme,
    )
    if not row:
        return None
    lex, gloss = row
    if not isinstance(lex, str):
        return None
    if gloss is not None and not isinstance(gloss, str):
        gloss = None
    return GlossaryEntry(lexeme=lex, gloss=gloss)
