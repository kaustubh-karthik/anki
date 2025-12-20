# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from anki.collection import Collection


class RedactionLevel(str, Enum):
    none = "none"
    minimal = "minimal"
    strict = "strict"


@dataclass(frozen=True)
class ConversationSettings:
    provider: str = "openai"
    model: str = "gpt-4o-mini"  # Fast, cheap, widely available
    safe_mode: bool = True
    redaction_level: RedactionLevel = RedactionLevel.minimal
    max_rewrites: int = 2
    lexeme_field_index: int = 0
    lexeme_field_names: tuple[str, ...] = ()
    gloss_field_index: int | None = 1
    gloss_field_names: tuple[str, ...] = ()
    snapshot_max_items: int = 5000
    band_cold_threshold: float = 0.4
    band_fragile_threshold: float = 0.6
    band_stretch_threshold: float = 0.85
    allow_new_words: bool = False
    max_new_words_per_session: int = 5
    force_new_word_every_n_turns: int = 3
    treat_unseen_deck_words_as_support: bool = False
    lexical_similarity_max: float = 0.7
    semantic_similarity_max: float = 0.6


CONFIG_KEY = "elites.conversation.settings"


def load_conversation_settings(col: Collection) -> ConversationSettings:
    raw = col.get_config(CONFIG_KEY, default=None)
    if not isinstance(raw, dict):
        return ConversationSettings()
    defaults = ConversationSettings()
    provider = raw.get("provider", defaults.provider)
    model = raw.get("model", defaults.model)
    safe_mode = raw.get("safe_mode", defaults.safe_mode)
    redaction_level_raw = raw.get("redaction_level", defaults.redaction_level.value)
    max_rewrites = raw.get("max_rewrites", defaults.max_rewrites)
    lexeme_field_index = raw.get("lexeme_field_index", defaults.lexeme_field_index)
    lexeme_field_names = raw.get(
        "lexeme_field_names", list(defaults.lexeme_field_names)
    )
    gloss_field_index = raw.get("gloss_field_index", defaults.gloss_field_index)
    gloss_field_names = raw.get("gloss_field_names", list(defaults.gloss_field_names))
    snapshot_max_items = raw.get("snapshot_max_items", defaults.snapshot_max_items)
    band_cold_threshold = raw.get("band_cold_threshold", defaults.band_cold_threshold)
    band_fragile_threshold = raw.get(
        "band_fragile_threshold", defaults.band_fragile_threshold
    )
    band_stretch_threshold = raw.get(
        "band_stretch_threshold", defaults.band_stretch_threshold
    )
    allow_new_words = raw.get("allow_new_words", defaults.allow_new_words)
    max_new_words_per_session = raw.get(
        "max_new_words_per_session", defaults.max_new_words_per_session
    )
    force_new_word_every_n_turns = raw.get(
        "force_new_word_every_n_turns", defaults.force_new_word_every_n_turns
    )
    treat_unseen_deck_words_as_support = raw.get(
        "treat_unseen_deck_words_as_support",
        defaults.treat_unseen_deck_words_as_support,
    )
    lexical_similarity_max = raw.get(
        "lexical_similarity_max", defaults.lexical_similarity_max
    )
    semantic_similarity_max = raw.get(
        "semantic_similarity_max", defaults.semantic_similarity_max
    )

    if not isinstance(provider, str):
        provider = "openai"
    if provider not in ("openai", "local", "fake"):
        provider = "openai"
    if not isinstance(model, str):
        model = "gpt-4o-mini"
    if not isinstance(safe_mode, bool):
        safe_mode = True
    if not isinstance(redaction_level_raw, str) or redaction_level_raw not in (
        e.value for e in RedactionLevel
    ):
        redaction_level_raw = RedactionLevel.minimal.value
    if not isinstance(max_rewrites, int) or max_rewrites < 0 or max_rewrites > 10:
        max_rewrites = 0
    if (
        not isinstance(lexeme_field_index, int)
        or lexeme_field_index < 0
        or lexeme_field_index > 50
    ):
        lexeme_field_index = 0
    if not isinstance(lexeme_field_names, list) or not all(
        isinstance(x, str) for x in lexeme_field_names
    ):
        lexeme_field_names = []
    lexeme_field_names = [x.strip() for x in lexeme_field_names if x.strip()][:10]
    if gloss_field_index is not None and (
        not isinstance(gloss_field_index, int)
        or gloss_field_index < 0
        or gloss_field_index > 50
    ):
        gloss_field_index = 1
    if not isinstance(gloss_field_names, list) or not all(
        isinstance(x, str) for x in gloss_field_names
    ):
        gloss_field_names = []
    gloss_field_names = [x.strip() for x in gloss_field_names if x.strip()][:10]
    if (
        not isinstance(snapshot_max_items, int)
        or snapshot_max_items <= 0
        or snapshot_max_items > 50000
    ):
        snapshot_max_items = 5000
    if not isinstance(band_cold_threshold, (int, float)):
        band_cold_threshold = defaults.band_cold_threshold
    if not isinstance(band_fragile_threshold, (int, float)):
        band_fragile_threshold = defaults.band_fragile_threshold
    if not isinstance(band_stretch_threshold, (int, float)):
        band_stretch_threshold = defaults.band_stretch_threshold
    band_cold_threshold = float(band_cold_threshold)
    band_fragile_threshold = float(band_fragile_threshold)
    band_stretch_threshold = float(band_stretch_threshold)
    if not (0.0 < band_cold_threshold < 1.0):
        band_cold_threshold = defaults.band_cold_threshold
    if not (0.0 < band_fragile_threshold < 1.0):
        band_fragile_threshold = defaults.band_fragile_threshold
    if not (0.0 < band_stretch_threshold < 1.0):
        band_stretch_threshold = defaults.band_stretch_threshold
    if not (band_cold_threshold < band_fragile_threshold < band_stretch_threshold):
        band_cold_threshold = defaults.band_cold_threshold
        band_fragile_threshold = defaults.band_fragile_threshold
        band_stretch_threshold = defaults.band_stretch_threshold
    if not isinstance(allow_new_words, bool):
        allow_new_words = defaults.allow_new_words
    if (
        not isinstance(max_new_words_per_session, int)
        or max_new_words_per_session < 0
        or max_new_words_per_session > 50
    ):
        max_new_words_per_session = defaults.max_new_words_per_session
    if (
        not isinstance(force_new_word_every_n_turns, int)
        or force_new_word_every_n_turns < 1
        or force_new_word_every_n_turns > 10
    ):
        force_new_word_every_n_turns = defaults.force_new_word_every_n_turns
    if not isinstance(treat_unseen_deck_words_as_support, bool):
        treat_unseen_deck_words_as_support = (
            defaults.treat_unseen_deck_words_as_support
        )
    if not isinstance(lexical_similarity_max, (int, float)):
        lexical_similarity_max = defaults.lexical_similarity_max
    if not isinstance(semantic_similarity_max, (int, float)):
        semantic_similarity_max = defaults.semantic_similarity_max
    lexical_similarity_max = float(lexical_similarity_max)
    semantic_similarity_max = float(semantic_similarity_max)
    if not (0.0 < lexical_similarity_max < 1.0):
        lexical_similarity_max = defaults.lexical_similarity_max
    if not (0.0 < semantic_similarity_max < 1.0):
        semantic_similarity_max = defaults.semantic_similarity_max

    return ConversationSettings(
        provider=provider,
        model=model,
        safe_mode=safe_mode,
        redaction_level=RedactionLevel(redaction_level_raw),
        max_rewrites=max_rewrites,
        lexeme_field_index=lexeme_field_index,
        lexeme_field_names=tuple(lexeme_field_names),
        gloss_field_index=gloss_field_index,
        gloss_field_names=tuple(gloss_field_names),
        snapshot_max_items=snapshot_max_items,
        band_cold_threshold=band_cold_threshold,
        band_fragile_threshold=band_fragile_threshold,
        band_stretch_threshold=band_stretch_threshold,
        allow_new_words=allow_new_words,
        max_new_words_per_session=max_new_words_per_session,
        force_new_word_every_n_turns=force_new_word_every_n_turns,
        treat_unseen_deck_words_as_support=treat_unseen_deck_words_as_support,
        lexical_similarity_max=lexical_similarity_max,
        semantic_similarity_max=semantic_similarity_max,
    )


def save_conversation_settings(col: Collection, settings: ConversationSettings) -> None:
    col.set_config(
        CONFIG_KEY,
        {
            "provider": settings.provider,
            "model": settings.model,
            "safe_mode": settings.safe_mode,
            "redaction_level": settings.redaction_level.value,
            "max_rewrites": settings.max_rewrites,
            "lexeme_field_index": settings.lexeme_field_index,
            "lexeme_field_names": list(settings.lexeme_field_names),
            "gloss_field_index": settings.gloss_field_index,
            "gloss_field_names": list(settings.gloss_field_names),
            "snapshot_max_items": settings.snapshot_max_items,
            "band_cold_threshold": settings.band_cold_threshold,
            "band_fragile_threshold": settings.band_fragile_threshold,
            "band_stretch_threshold": settings.band_stretch_threshold,
            "allow_new_words": settings.allow_new_words,
            "max_new_words_per_session": settings.max_new_words_per_session,
            "force_new_word_every_n_turns": settings.force_new_word_every_n_turns,
            "treat_unseen_deck_words_as_support": (
                settings.treat_unseen_deck_words_as_support
            ),
            "lexical_similarity_max": settings.lexical_similarity_max,
            "semantic_similarity_max": settings.semantic_similarity_max,
        },
        undoable=False,
    )
