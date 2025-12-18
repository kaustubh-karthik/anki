from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from anki.collection import Collection


class RedactionLevel(str, Enum):
    none = "none"
    minimal = "minimal"
    strict = "strict"


@dataclass(frozen=True, slots=True)
class ConversationSettings:
    provider: str = "fake"
    model: str = "gpt-5-nano"
    safe_mode: bool = True
    redaction_level: RedactionLevel = RedactionLevel.minimal
    max_rewrites: int = 2


CONFIG_KEY = "elites.conversation.settings"


def load_conversation_settings(col: Collection) -> ConversationSettings:
    raw = col.get_config(CONFIG_KEY, default=None)
    if not isinstance(raw, dict):
        return ConversationSettings()
    provider = raw.get("provider", "fake")
    model = raw.get("model", "gpt-5-nano")
    safe_mode = raw.get("safe_mode", True)
    redaction_level = raw.get("redaction_level", RedactionLevel.minimal.value)
    max_rewrites = raw.get("max_rewrites", 2)

    if not isinstance(provider, str):
        provider = "fake"
    if not isinstance(model, str):
        model = "gpt-5-nano"
    if not isinstance(safe_mode, bool):
        safe_mode = True
    if not isinstance(redaction_level, str) or redaction_level not in (
        e.value for e in RedactionLevel
    ):
        redaction_level = RedactionLevel.minimal.value
    if not isinstance(max_rewrites, int) or max_rewrites < 0 or max_rewrites > 10:
        max_rewrites = 2

    return ConversationSettings(
        provider=provider,
        model=model,
        safe_mode=safe_mode,
        redaction_level=RedactionLevel(redaction_level),
        max_rewrites=max_rewrites,
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
        },
        undoable=False,
    )
