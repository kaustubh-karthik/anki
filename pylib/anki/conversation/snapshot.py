# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

import orjson

from anki.collection import Collection
from anki.decks import DeckId
from anki.models import NotetypeId
from anki.utils import strip_html

from .types import ItemId

_LEXEME_RE = re.compile(r"[A-Za-z0-9가-힣]+", re.UNICODE)
_HAS_LATIN_RE = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
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
    last_review_date: int | None = None  # scheduler day number
    decay: float | None = None
    gloss: str | None = None


@dataclass(frozen=True)
class DeckSnapshot:
    deck_ids: tuple[int, ...]
    items: tuple[SnapshotItem, ...]
    today: int | None = None


def build_deck_snapshot(
    col: Collection,
    deck_ids: Iterable[DeckId],
    *,
    lexeme_field_index: int = 0,
    lexeme_field_names: tuple[str, ...] = (),
    gloss_field_index: int | None = 1,
    gloss_field_names: tuple[str, ...] = (),
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
        "select c.id, c.nid, n.mid, n.flds, c.type, c.queue, c.due, c.ivl, c.reps, c.lapses, c.data "
        "from cards c "
        "join notes n on n.id = c.nid "
        f"where c.did in ({placeholders}) "
        "limit ?"
    )
    rows = col.db.all(sql, *unique_dids, max_items)

    items: list[SnapshotItem] = []
    field_name_cache: dict[int, dict[str, int]] = {}
    lexeme_names = tuple(x.strip() for x in lexeme_field_names if x.strip())
    gloss_names = tuple(x.strip() for x in gloss_field_names if x.strip())

    try:
        day_cutoff = int(col.sched.day_cutoff)
    except Exception:
        day_cutoff = None

    for (
        card_id,
        note_id,
        mid,
        flds,
        ctype,
        cqueue,
        due,
        ivl,
        reps,
        lapses,
        cdata,
    ) in rows:
        if not isinstance(flds, str):
            continue
        fields = flds.split("\x1f")

        lexeme_idx = lexeme_field_index
        if isinstance(mid, int) and lexeme_names:
            idx = _field_index_for_notetype(col, mid, lexeme_names, field_name_cache)
            if idx is not None:
                lexeme_idx = idx
        if lexeme_idx >= len(fields):
            continue
        raw_lexeme = strip_html(fields[lexeme_idx]).strip()
        if not raw_lexeme:
            continue
        gloss: str | None = None
        gloss_idx: int | None = gloss_field_index
        if isinstance(mid, int) and gloss_names:
            idx = _field_index_for_notetype(col, mid, gloss_names, field_name_cache)
            if idx is not None:
                gloss_idx = idx
        if gloss_idx is not None and gloss_idx < len(fields):
            raw_gloss = strip_html(fields[gloss_idx]).strip()
            gloss = raw_gloss if raw_gloss else None

        lexeme = _extract_lexeme(raw_lexeme)
        if (
            lexeme
            and gloss
            and _HAS_LATIN_RE.search(lexeme)
            and not _HAS_LATIN_RE.search(gloss)
        ):
            swapped = _extract_lexeme(gloss)
            if swapped:
                gloss = raw_lexeme or gloss
                lexeme = swapped
        if not lexeme:
            continue

        stability: float | None = None
        difficulty: float | None = None
        decay: float | None = None
        if include_fsrs_metrics:
            state = col.compute_memory_state(card_id)
            stability = state.stability
            difficulty = state.difficulty
            decay = state.decay

        last_review_date: int | None = None
        if (
            isinstance(today, int)
            and isinstance(day_cutoff, int)
            and isinstance(cdata, str)
        ):
            try:
                parsed = orjson.loads(cdata)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                lrt = parsed.get("lrt")
                if isinstance(lrt, int):
                    elapsed_days = max(0, int((day_cutoff - lrt) / 86400))
                    last_review_date = today - elapsed_days
                if decay is None:
                    d = parsed.get("decay")
                    if isinstance(d, (int, float)):
                        decay = float(d)

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
                last_review_date=last_review_date,
                decay=decay,
                gloss=gloss,
            )
        )

    return DeckSnapshot(deck_ids=unique_dids, items=tuple(items), today=today)


def _extract_lexeme(text: str) -> str:
    m = _LEXEME_RE.search(text)
    return m.group(0) if m else ""


def _field_index_for_notetype(
    col: Collection,
    mid: int,
    preferred_names: tuple[str, ...],
    cache: dict[int, dict[str, int]],
) -> int | None:
    mapping = cache.get(mid)
    if mapping is None:
        mapping = {}
        notetype = col.models.get(NotetypeId(mid))
        if notetype:
            for idx, fld in enumerate(notetype.get("flds", [])):
                name = fld.get("name")
                if isinstance(name, str) and name:
                    mapping[name] = idx
        cache[mid] = mapping
    for name in preferred_names:
        idx = mapping.get(name)
        if idx is not None:
            return idx
    return None
