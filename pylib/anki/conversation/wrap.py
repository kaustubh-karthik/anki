from __future__ import annotations

from dataclasses import dataclass

from .snapshot import DeckSnapshot


@dataclass(frozen=True, slots=True)
class SuggestedCard:
    front: str
    back: str | None
    tags: tuple[str, ...] = ()


def compute_session_wrap(
    *,
    snapshot: DeckSnapshot,
    mastery: dict[str, dict[str, int]],
    strengths_n: int = 3,
    reinforce_n: int = 2,
    suggest_n: int = 1,
) -> dict[str, object]:
    lexeme_to_gloss = {item.lexeme: item.gloss for item in snapshot.items}

    def score_strength(lexeme: str) -> tuple[int, int, str]:
        m = mastery.get(f"lexeme:{lexeme}", {})
        return (
            int(m.get("user_used", 0)),
            -int(m.get("dont_know", 0)),
            lexeme,
        )

    def score_reinforce(lexeme: str) -> tuple[int, int, int, str]:
        m = mastery.get(f"lexeme:{lexeme}", {})
        return (
            int(m.get("practice_again", 0)),
            int(m.get("dont_know", 0)),
            int(m.get("mark_confusing", 0)),
            -int(m.get("user_used", 0)),
            lexeme,
        )

    lexemes = sorted({item.lexeme for item in snapshot.items})
    strengths = sorted(lexemes, key=score_strength, reverse=True)[:strengths_n]
    reinforce = sorted(lexemes, key=score_reinforce, reverse=True)[:reinforce_n]

    suggested_cards: list[dict[str, object]] = []
    for lexeme in reinforce[:suggest_n]:
        suggested_cards.append(
            {
                "front": lexeme,
                "back": lexeme_to_gloss.get(lexeme),
                "tags": ["conv_suggested"],
            }
        )

    return {
        "strengths": strengths,
        "reinforce": reinforce,
        "suggested_cards": suggested_cards,
    }
