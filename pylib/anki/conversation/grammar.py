from __future__ import annotations

from dataclasses import dataclass

from .types import GrammarPattern, ItemId


@dataclass(frozen=True, slots=True)
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

