# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable

import orjson

from anki.httpclient import HttpClient

logger = logging.getLogger(__name__)


def _extract_text_fallback(data: dict[str, Any]) -> str:
    output = data.get("output")
    if not isinstance(output, list):
        # Log what we actually got
        logger.error(f"Expected 'output' to be a list, got: {type(output)}")
        logger.error(f"Response structure: {json.dumps(data, indent=2)[:2000]}")
        raise ValueError(
            f"unable to extract text from OpenAI response: 'output' is {type(output)}, not list"
        )

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

    # If we get here, we couldn't find the text
    logger.error(f"Could not find output_text in response structure")
    logger.error(f"Full response: {json.dumps(data, indent=2)[:2000]}")
    raise ValueError(
        "unable to extract text from OpenAI response: no output_text found in structure"
    )


@dataclass
class OpenAIResponsesJsonClient:
    api_key: str
    model: str
    api_url: str | None = None  # Auto-detect based on model
    http_client_factory: Callable[[], HttpClient] = HttpClient
    timeout_s: float | tuple[float, float] = (5.0, 60.0)  # Reasonable timeout
    max_output_tokens: int = 256
    _client: HttpClient | None = field(default=None, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def _get_api_url(self) -> str:
        """Determine which endpoint to use based on model name."""
        if self.api_url is not None:
            return self.api_url
        # Reasoning models use /v1/responses, standard models use /v1/chat/completions
        reasoning_models = ["o1", "o3", "gpt-5"]
        if any(self.model.startswith(prefix) for prefix in reasoning_models):
            return "https://api.openai.com/v1/responses"
        return "https://api.openai.com/v1/chat/completions"

    def _is_reasoning_model(self) -> bool:
        """Check if this is a reasoning model."""
        return "v1/responses" in self._get_api_url()

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

        api_url = self._get_api_url()
        is_reasoning = self._is_reasoning_model()

        # Build payload based on endpoint type
        if is_reasoning:
            # /v1/responses format (for o1/o3/gpt-5 models)
            payload: dict[str, Any] = {
                "model": self.model,
                "input": [
                    {"role": "system", "content": system_role},
                    {
                        "role": "user",
                        "content": json.dumps(user_json, ensure_ascii=False),
                    },
                ],
                "text": {"format": {"type": "json_object"}, "verbosity": "low"},
                "reasoning": {"effort": "low"},
                "max_output_tokens": self.max_output_tokens,
            }
        else:
            # /v1/chat/completions format (for gpt-4, gpt-3.5-turbo, etc.)
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_role},
                    {
                        "role": "user",
                        "content": json.dumps(user_json, ensure_ascii=False),
                    },
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": self.max_output_tokens,
                "temperature": 0.7,
                "stream": False,
            }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        def do_request(p: dict[str, Any]) -> dict[str, Any]:
            resp = client.post(
                api_url, data=orjson.dumps(p), headers=headers, stream=False
            )
            try:
                resp.raise_for_status()
                return resp.json()
            finally:
                resp.close()

        import time

        start_time = time.time()

        data = do_request(payload)

        elapsed = time.time() - start_time
        logger.debug(f"OpenAI API request ({self.model}) took {elapsed:.2f}s")

        if data.get("error") is not None:
            raise ValueError(f"OpenAI error: {data['error']}")

        # Handle retry for reasoning models with incomplete responses
        if is_reasoning and (
            data.get("status") == "incomplete"
            and isinstance(data.get("incomplete_details"), dict)
            and data["incomplete_details"].get("reason") == "max_output_tokens"
        ):
            payload2 = dict(payload)
            payload2["max_output_tokens"] = max(int(self.max_output_tokens), 256) * 4
            data = do_request(payload2)

        # Extract text based on endpoint format
        if is_reasoning:
            # /v1/responses format
            text = data.get("output_text")
            if not isinstance(text, str):
                logger.debug(
                    f"No output_text field, trying fallback. Response keys: {list(data.keys())}"
                )
                logger.debug(f"Full response: {json.dumps(data, indent=2)[:1000]}")
                text = _extract_text_fallback(data)
        else:
            # /v1/chat/completions format
            try:
                text = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as e:
                logger.error(
                    f"Failed to extract response. Data: {json.dumps(data, indent=2)[:500]}"
                )
                raise ValueError(
                    f"Unable to extract text from OpenAI response: {e}"
                ) from e

        if not isinstance(text, str):
            raise ValueError(f"Expected string content, got {type(text)}")

        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("OpenAI returned non-object JSON")
        return parsed

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
