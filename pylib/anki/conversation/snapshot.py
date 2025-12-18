from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from anki.collection import Collection
from anki.decks import DeckId

from .types import ItemId

_LEXEME_RE = re.compile(r"[A-Za-z0-9가-힣]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class SnapshotItem:
    item_id: ItemId
    lexeme: str
    source_note_id: int
    source_card_id: int
    stability: float | None = None
    difficulty: float | None = None


@dataclass(frozen=True, slots=True)
class DeckSnapshot:
    deck_ids: tuple[int, ...]
    items: tuple[SnapshotItem, ...]


def build_deck_snapshot(
    col: Collection,
    deck_ids: Iterable[DeckId],
    *,
    lexeme_field_index: int = 0,
    max_items: int = 5000,
    include_fsrs_metrics: bool = True,
) -> DeckSnapshot:
    dids: list[int] = []
    for did in deck_ids:
        dids.extend(int(x) for x in col.decks.deck_and_child_ids(did))
    unique_dids = tuple(sorted(set(dids)))
    if not unique_dids:
        raise ValueError("no decks provided")

    placeholders = ",".join("?" for _ in unique_dids)
    sql = (
        "select c.id, c.nid, n.flds "
        "from cards c "
        "join notes n on n.id = c.nid "
        f"where c.did in ({placeholders}) "
        "limit ?"
    )
    rows = col.db.all(sql, *unique_dids, max_items)

    items: list[SnapshotItem] = []
    for card_id, note_id, flds in rows:
        if not isinstance(flds, str):
            continue
        fields = flds.split("\x1f")
        if lexeme_field_index >= len(fields):
            continue
        raw = fields[lexeme_field_index].strip()
        if not raw:
            continue

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
                stability=stability,
                difficulty=difficulty,
            )
        )

    return DeckSnapshot(deck_ids=unique_dids, items=tuple(items))


def _extract_lexeme(text: str) -> str:
    m = _LEXEME_RE.search(text)
    return m.group(0) if m else ""
