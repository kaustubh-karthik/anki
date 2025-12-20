# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from anki.collection import Collection
from anki.decks import DeckId


@dataclass(frozen=True)
class CardSuggestion:
    front: str
    back: str | None
    deck_id: int
    tags: tuple[str, ...] = ("conv_reinforced",)


def apply_reinforced_cards(
    col: Collection, suggestions: list[CardSuggestion]
) -> list[int]:
    """Add reinforced-word cards as Basic notes, returning created note ids.

    This is intentionally deterministic and avoids any UI confirmation.
    The caller is responsible for user approval/guardrails.
    """

    if not suggestions:
        return []

    notetype = col.models.by_name("Basic")
    if not notetype:
        raise ValueError("Basic notetype missing")

    created: list[int] = []
    for s in suggestions:
        note = col.new_note(notetype)
        note["Front"] = s.front
        note["Back"] = s.back or ""
        col.add_note(note, deck_id=DeckId(s.deck_id))
        if s.tags:
            col.tags.bulk_add([note.id], " ".join(s.tags))
        created.append(int(note.id))
    return created


def reinforced_cards_from_wrap(
    wrap: dict[str, Any], *, deck_id: int
) -> list[CardSuggestion]:
    out: list[CardSuggestion] = []
    for entry in wrap.get("reinforced_words", []) or []:
        if not isinstance(entry, dict):
            continue
        front = entry.get("front")
        back = entry.get("back")
        tags = entry.get("tags", ["conv_reinforced"])
        if not isinstance(front, str) or not front:
            continue
        if back is not None and not isinstance(back, str):
            back = None
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            tags = ["conv_reinforced"]
        out.append(
            CardSuggestion(front=front, back=back, deck_id=deck_id, tags=tuple(tags))
        )
    return out
