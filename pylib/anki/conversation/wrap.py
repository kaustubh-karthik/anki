# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from dataclasses import dataclass

from .snapshot import DeckSnapshot


@dataclass(frozen=True)
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

    def weakness_score(lexeme: str) -> float:
        m = mastery.get(f"lexeme:{lexeme}", {})
        dont_know = float(m.get("dont_know", 0))
        practice_again = float(m.get("practice_again", 0))
        confusing = float(m.get("mark_confusing", 0))
        used_guessing = float(m.get("used_guessing", 0))
        lookup_ms_total = float(m.get("lookup_ms_total", 0))
        lookup_count = float(m.get("lookup_count", 0))
        avg_lookup = lookup_ms_total / lookup_count if lookup_count > 0 else 0.0

        item = next((i for i in snapshot.items if i.lexeme == lexeme), None)
        stability = item.stability if item else None
        rustiness = (
            1.0 / (1.0 + max(stability or 0.0, 0.0)) if stability is not None else 0.0
        )
        return (
            practice_again * 2.0
            + dont_know * 1.5
            + confusing * 1.0
            + used_guessing * 1.0
            + min(2.0, avg_lookup / 1000.0) * 0.5
            + rustiness * 0.5
        )

    def score_strength(lexeme: str) -> tuple[int, int, str]:
        m = mastery.get(f"lexeme:{lexeme}", {})
        return (
            int(m.get("user_used", 0)),
            -int(m.get("dont_know", 0)),
            lexeme,
        )

    lexemes = sorted({item.lexeme for item in snapshot.items})
    strengths = sorted(lexemes, key=score_strength, reverse=True)[:strengths_n]
    reinforce = sorted(lexemes, key=weakness_score, reverse=True)[:reinforce_n]

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
