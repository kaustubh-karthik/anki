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
    output = data.get("output")
    if not isinstance(output, list):
        raise ValueError("unable to extract text from OpenAI response")
    for item in output:
        if not isinstance(item, dict):
            continue
        contents = item.get("content", [])
        if not isinstance(contents, list):
            continue
        for content in contents:
            if not isinstance(content, dict):
                continue
            if content.get("type") != "output_text":
                continue
            text = content.get("text")
            if isinstance(text, str) and text:
                return text
            if (
                isinstance(text, dict)
                and isinstance(text.get("value"), str)
                and text["value"]
            ):
                return text["value"]
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

        payload: dict[str, Any] = {
            "model": self.model,
            "input": [
                {"role": "system", "content": system_role},
                {"role": "user", "content": json.dumps(user_json, ensure_ascii=False)},
            ],
            # Request JSON-only output; keep verbosity low to reduce latency.
            "text": {"format": {"type": "json_object"}, "verbosity": "low"},
            # Reduce the risk of spending the entire token budget on reasoning.
            "reasoning": {"effort": "low"},
            "max_output_tokens": self.max_output_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        def do_request(p: dict[str, Any]) -> dict[str, Any]:
            resp = client.post(self.api_url, data=orjson.dumps(p), headers=headers)
            resp.raise_for_status()
            return resp.json()

        data = do_request(payload)
        if data.get("error") is not None:
            raise ValueError(f"OpenAI error: {data['error']}")

        # If the response is incomplete due to max_output_tokens (often because
        # the model used the budget on reasoning), retry once with a larger budget.
        if (
            data.get("status") == "incomplete"
            and isinstance(data.get("incomplete_details"), dict)
            and data["incomplete_details"].get("reason") == "max_output_tokens"
        ):
            payload2 = dict(payload)
            payload2["max_output_tokens"] = max(int(self.max_output_tokens), 256) * 4
            data = do_request(payload2)

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
