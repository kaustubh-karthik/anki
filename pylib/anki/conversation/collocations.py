from __future__ import annotations

from dataclasses import dataclass

from .types import ItemId, MustTarget


@dataclass(frozen=True, slots=True)
class Collocation:
    id: ItemId
    # tokens are stored in the surface forms to work with token-based validation
    tokens: tuple[str, ...]
    triggers: tuple[str, ...]


DEFAULT_KO_COLLOCATIONS: tuple[Collocation, ...] = (
    Collocation(
        id=ItemId("colloc:사이에_있어요"),
        tokens=("사이에", "있어요"),
        triggers=("사이",),
    ),
    Collocation(
        id=ItemId("colloc:~해도_돼요"),
        tokens=("해도", "돼요"),
        triggers=("돼요",),
    ),
)


def select_collocation_targets(
    *,
    lexical_targets: tuple[str, ...],
    max_targets: int = 1,
) -> tuple[MustTarget, ...]:
    selected: list[MustTarget] = []
    for colloc in DEFAULT_KO_COLLOCATIONS:
        if any(t in lexical_targets for t in colloc.triggers):
            selected.append(
                MustTarget(
                    id=colloc.id,
                    type="collocation",
                    surface_forms=colloc.tokens,
                    priority=0.9,
                )
            )
            if len(selected) >= max_targets:
                break
    return tuple(selected)

