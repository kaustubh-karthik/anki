# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

import json
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable

import orjson

from anki.httpclient import HttpClient


def _extract_text_fallback(data: dict[str, Any]) -> str:
    try:
        output = data["output"]
        for item in output:
            for content in item.get("content", []):
                if content.get("type") == "output_text" and isinstance(
                    content.get("text"), str
                ):
                    return content["text"]
    except Exception:
        pass
    raise ValueError("unable to extract text from OpenAI response")


@dataclass
class OpenAIResponsesJsonClient:
    api_key: str
    model: str
    api_url: str = "https://api.openai.com/v1/responses"
    http_client_factory: Callable[[], HttpClient] = HttpClient
    timeout_s: float | tuple[float, float] = (10.0, 180.0)
    max_output_tokens: int = 256
    _client: HttpClient | None = field(default=None, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def request_json(
        self, *, system_role: str, user_json: dict[str, Any]
    ) -> dict[str, Any]:
        # Reuse a single requests.Session() across calls to avoid repeated TLS handshakes.
        with self._lock:
            if self._client is None:
                self._client = self.http_client_factory()
            client = self._client
        client.timeout = self.timeout_s

        payload = {
            "model": self.model,
            "input": [
                {"role": "system", "content": system_role},
                {"role": "user", "content": json.dumps(user_json, ensure_ascii=False)},
            ],
            "text": {"format": {"type": "json_object"}},
            "max_output_tokens": self.max_output_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        resp = client.post(self.api_url, data=orjson.dumps(payload), headers=headers)
        resp.raise_for_status()
        data = resp.json()

        text = data.get("output_text")
        if not isinstance(text, str):
            text = _extract_text_fallback(data)
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("OpenAI returned non-object JSON")
        return parsed

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
