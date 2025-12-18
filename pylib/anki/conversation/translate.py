# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from .openai import OpenAIResponsesJsonClient


@dataclass(frozen=True)
class TranslateRequest:
    system_role: str
    text_ko: str

    def to_json_dict(self) -> dict[str, Any]:
        return {"text_ko": self.text_ko}


@dataclass(frozen=True)
class TranslateResponse:
    translation_en: str

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "TranslateResponse":
        if not isinstance(data, dict):
            raise ValueError("response must be a JSON object")
        val = data.get("translation_en")
        if not isinstance(val, str) or not val.strip():
            raise ValueError("translation_en must be a non-empty string")
        return cls(translation_en=val.strip())

    def to_json_dict(self) -> dict[str, Any]:
        return {"translation_en": self.translation_en}


class TranslateProvider(ABC):
    @abstractmethod
    def translate(self, *, request: TranslateRequest) -> dict[str, Any]:
        """Return a parsed JSON object matching TranslateResponse."""


@dataclass
class OpenAITranslateProvider(TranslateProvider):
    api_key: str
    model: str = "gpt-5-nano"

    def translate(self, *, request: TranslateRequest) -> dict[str, Any]:
        client = OpenAIResponsesJsonClient(api_key=self.api_key, model=self.model)
        system_role = (
            request.system_role
            + "\n\n"
            + 'Task: Translate Korean to natural English. Return ONLY JSON like {"translation_en":"..."}.'
        )
        return client.request_json(
            system_role=system_role, user_json=request.to_json_dict()
        )


@dataclass
class LocalTranslateProvider(TranslateProvider):
    """Deterministic, offline translate provider (placeholder)."""

    placeholder: str = "(translation unavailable offline)"

    def translate(self, *, request: TranslateRequest) -> dict[str, Any]:
        return {"translation_en": self.placeholder}


@dataclass
class TranslateGateway:
    provider: TranslateProvider

    def run(self, *, request: TranslateRequest) -> TranslateResponse:
        raw = self.provider.translate(request=request)
        return TranslateResponse.from_json_dict(raw)
