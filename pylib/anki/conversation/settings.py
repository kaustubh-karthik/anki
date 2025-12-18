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
    model: str = "gpt-5-nano"
    safe_mode: bool = True
    redaction_level: RedactionLevel = RedactionLevel.minimal
    max_rewrites: int = 2
    lexeme_field_index: int = 0
    lexeme_field_names: tuple[str, ...] = ()
    gloss_field_index: int | None = 1
    gloss_field_names: tuple[str, ...] = ()
    snapshot_max_items: int = 5000


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

    if not isinstance(provider, str):
        provider = "openai"
    if provider not in ("openai", "local", "fake"):
        provider = "openai"
    if not isinstance(model, str):
        model = "gpt-5-nano"
    if not isinstance(safe_mode, bool):
        safe_mode = True
    if not isinstance(redaction_level_raw, str) or redaction_level_raw not in (
        e.value for e in RedactionLevel
    ):
        redaction_level_raw = RedactionLevel.minimal.value
    if not isinstance(max_rewrites, int) or max_rewrites < 0 or max_rewrites > 10:
        max_rewrites = 2
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
        },
        undoable=False,
    )
