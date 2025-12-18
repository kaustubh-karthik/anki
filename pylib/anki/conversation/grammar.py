# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from dataclasses import dataclass

from .types import GrammarPattern, ItemId


@dataclass(frozen=True)
class GrammarItem:
    id: ItemId
    pattern: str
    triggers: tuple[str, ...]


DEFAULT_KO_GRAMMAR: tuple[GrammarItem, ...] = (
    GrammarItem(
        id=ItemId("gram:n1_n2_사이에_있다"),
        pattern="N1와 N2 사이에 N3이/가 있어요",
        triggers=("사이",),
    ),
    GrammarItem(
        id=ItemId("gram:~해도_돼요"),
        pattern="~해도 돼요",
        triggers=("돼요",),
    ),
    GrammarItem(
        id=ItemId("gram:n1에_있어요"),
        pattern="N1에 N2이/가 있어요",
        triggers=("있어요",),
    ),
    GrammarItem(
        id=ItemId("gram:n1에_없어요"),
        pattern="N1에 N2이/가 없어요",
        triggers=("없어요",),
    ),
    GrammarItem(
        id=ItemId("gram:n은_어디에_있어요"),
        pattern="N은/는 어디에 있어요?",
        triggers=("어디",),
    ),
    GrammarItem(
        id=ItemId("gram:~하면_안_돼요"),
        pattern="~하면 안 돼요",
        triggers=("안", "돼요"),
    ),
    GrammarItem(
        id=ItemId("gram:~할까요"),
        pattern="~할까요?",
        triggers=("할까요",),
    ),
    GrammarItem(
        id=ItemId("gram:~하고_싶어요"),
        pattern="~하고 싶어요",
        triggers=("싶어요",),
    ),
)


def select_grammar_patterns(
    *,
    must_targets: tuple[str, ...],
    max_patterns: int = 2,
) -> tuple[GrammarPattern, ...]:
    """Deterministic mapping from lexical targets -> allowed grammar patterns."""

    selected: list[GrammarPattern] = []
    for item in DEFAULT_KO_GRAMMAR:
        if any(trigger in must_targets for trigger in item.triggers):
            selected.append(GrammarPattern(id=item.id, pattern=item.pattern))
            if len(selected) >= max_patterns:
                break
    return tuple(selected)
