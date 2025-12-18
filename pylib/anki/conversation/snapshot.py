from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from anki.collection import Collection
from anki.decks import DeckId
from anki.utils import strip_html

from .types import ItemId

_LEXEME_RE = re.compile(r"[A-Za-z0-9가-힣]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class SnapshotItem:
    item_id: ItemId
    lexeme: str
    source_note_id: int
    source_card_id: int
    card_type: int | None = None
    card_queue: int | None = None
    due: int | None = None
    ivl: int | None = None
    reps: int | None = None
    lapses: int | None = None
    stability: float | None = None
    difficulty: float | None = None
    gloss: str | None = None


@dataclass(frozen=True, slots=True)
class DeckSnapshot:
    deck_ids: tuple[int, ...]
    items: tuple[SnapshotItem, ...]
    today: int | None = None


def build_deck_snapshot(
    col: Collection,
    deck_ids: Iterable[DeckId],
    *,
    lexeme_field_index: int = 0,
    gloss_field_index: int | None = 1,
    max_items: int = 5000,
    include_fsrs_metrics: bool = True,
) -> DeckSnapshot:
    try:
        today = int(col.sched.today)
    except Exception:
        today = None
    dids: list[int] = []
    for did in deck_ids:
        dids.extend(int(x) for x in col.decks.deck_and_child_ids(did))
    unique_dids = tuple(sorted(set(dids)))
    if not unique_dids:
        raise ValueError("no decks provided")

    placeholders = ",".join("?" for _ in unique_dids)
    sql = (
        "select c.id, c.nid, n.flds, c.type, c.queue, c.due, c.ivl, c.reps, c.lapses "
        "from cards c "
        "join notes n on n.id = c.nid "
        f"where c.did in ({placeholders}) "
        "limit ?"
    )
    rows = col.db.all(sql, *unique_dids, max_items)

    items: list[SnapshotItem] = []
    for card_id, note_id, flds, ctype, cqueue, due, ivl, reps, lapses in rows:
        if not isinstance(flds, str):
            continue
        fields = flds.split("\x1f")
        if lexeme_field_index >= len(fields):
            continue
        raw = strip_html(fields[lexeme_field_index]).strip()
        if not raw:
            continue
        gloss: str | None = None
        if gloss_field_index is not None and gloss_field_index < len(fields):
            raw_gloss = strip_html(fields[gloss_field_index]).strip()
            gloss = raw_gloss if raw_gloss else None

        lexeme = _extract_lexeme(raw)
        if not lexeme:
            continue

        stability: float | None = None
        difficulty: float | None = None
        if include_fsrs_metrics:
            state = col.compute_memory_state(card_id)
            stability = state.stability
            difficulty = state.difficulty
        items.append(
            SnapshotItem(
                item_id=ItemId(f"lexeme:{lexeme}"),
                lexeme=lexeme,
                source_note_id=int(note_id),
                source_card_id=int(card_id),
                card_type=int(ctype) if isinstance(ctype, int) else None,
                card_queue=int(cqueue) if isinstance(cqueue, int) else None,
                due=int(due) if isinstance(due, int) else None,
                ivl=int(ivl) if isinstance(ivl, int) else None,
                reps=int(reps) if isinstance(reps, int) else None,
                lapses=int(lapses) if isinstance(lapses, int) else None,
                stability=stability,
                difficulty=difficulty,
                gloss=gloss,
            )
        )

    return DeckSnapshot(deck_ids=unique_dids, items=tuple(items), today=today)


def _extract_lexeme(text: str) -> str:
    m = _LEXEME_RE.search(text)
    return m.group(0) if m else ""
