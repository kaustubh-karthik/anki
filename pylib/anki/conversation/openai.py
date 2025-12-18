from __future__ import annotations

import json
from dataclasses import dataclass
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


@dataclass(slots=True)
class OpenAIResponsesJsonClient:
    api_key: str
    model: str
    api_url: str = "https://api.openai.com/v1/responses"
    http_client_factory: Callable[[], HttpClient] = HttpClient

    def request_json(
        self, *, system_role: str, user_json: dict[str, Any]
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "input": [
                {"role": "system", "content": system_role},
                {"role": "user", "content": json.dumps(user_json, ensure_ascii=False)},
            ],
            "text": {"format": {"type": "json_object"}},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with self.http_client_factory() as client:
            resp = client.post(
                self.api_url, data=orjson.dumps(payload), headers=headers
            )
            data = resp.json()

        text = data.get("output_text")
        if not isinstance(text, str):
            text = _extract_text_fallback(data)
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("OpenAI returned non-object JSON")
        return parsed
