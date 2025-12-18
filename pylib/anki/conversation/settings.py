from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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

