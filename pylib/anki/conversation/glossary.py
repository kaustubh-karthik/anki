# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from anki.collection import Collection

from .snapshot import DeckSnapshot
from .telemetry import _now_ms, ensure_conversation_schema


@dataclass(frozen=True)
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


def import_glossary_file(
    col: Collection,
    path: str | Path,
    *,
    format: str | None = None,
) -> int:
    """Import lexeme->gloss mappings into the offline glossary cache.

    Supported formats:
    - TSV/CSV: `lexeme<TAB>gloss` (or comma-separated for csv)
    - JSON: list of objects with `lexeme` and `gloss`, or object mapping lexeme->gloss
    """

    ensure_conversation_schema(col)
    file_path = Path(path)
    if format is None:
        suffix = file_path.suffix.lower()
        if suffix in (".tsv", ".tab"):
            format = "tsv"
        elif suffix == ".csv":
            format = "csv"
        elif suffix == ".json":
            format = "json"
        else:
            raise ValueError("unknown glossary format; use .tsv/.csv/.json")

    now = _now_ms()
    rows: list[tuple[str, str, int | None, int]] = []

    if format in ("tsv", "csv"):
        delimiter = "\t" if format == "tsv" else ","
        with file_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f, delimiter=delimiter)
            for parts in reader:
                if not parts:
                    continue
                if len(parts) == 1:
                    lexeme = parts[0].strip()
                    gloss = ""
                else:
                    lexeme = parts[0].strip()
                    gloss = parts[1].strip()
                if not lexeme or lexeme.startswith("#"):
                    continue
                rows.append((lexeme, gloss, None, now))

    elif format == "json":
        data = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for lexeme, gloss in data.items():
                if not isinstance(lexeme, str) or not lexeme.strip():
                    continue
                if not isinstance(gloss, str):
                    continue
                rows.append((lexeme.strip(), gloss.strip(), None, now))
        elif isinstance(data, list):
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                lexeme = entry.get("lexeme")
                gloss = entry.get("gloss")
                if not isinstance(lexeme, str) or not lexeme.strip():
                    continue
                if gloss is None:
                    gloss = ""
                if not isinstance(gloss, str):
                    continue
                rows.append((lexeme.strip(), gloss.strip(), None, now))
        else:
            raise ValueError("json glossary must be object or list")
    else:
        raise ValueError("format must be tsv|csv|json")

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
