from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import orjson

from anki.httpclient import HttpClient

from .types import ConversationRequest, ConversationResponse
from .validation import validate_tokens
from .contract import check_response_against_request


class ConversationProvider(ABC):
    @abstractmethod
    def generate(self, *, request: ConversationRequest) -> dict[str, Any]:
        """Return a parsed JSON object matching ConversationResponse."""


@dataclass(slots=True)
class OpenAIConversationProvider(ConversationProvider):
    api_key: str
    model: str = "gpt-5-nano"
    api_url: str = "https://api.openai.com/v1/responses"

    def generate(self, *, request: ConversationRequest) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "input": [
                {"role": "system", "content": request.system_role},
                {
                    "role": "user",
                    "content": json.dumps(request.to_json_dict(), ensure_ascii=False),
                },
            ],
            "text": {"format": {"type": "json_object"}},
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        with HttpClient() as client:
            resp = client.post(self.api_url, data=orjson.dumps(payload), headers=headers)
            data = resp.json()

        text = data.get("output_text")
        if not isinstance(text, str):
            text = _extract_text_fallback(data)
        return json.loads(text)


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
    raise ValueError("unable to extract text from provider response")


@dataclass(slots=True)
class ConversationGateway:
    provider: ConversationProvider
    max_rewrites: int = 2

    def run_turn(self, *, request: ConversationRequest) -> ConversationResponse:
        last_error: Exception | None = None
        for attempt in range(self.max_rewrites + 1):
            raw = self.provider.generate(request=request)
            try:
                response = ConversationResponse.from_json_dict(raw)
            except Exception as e:
                last_error = e
                if attempt >= self.max_rewrites:
                    raise
                request = _rewrite_request(request, reason=f"invalid_json:{e}")
                continue

            if request.generation_instructions.safe_mode:
                validation = validate_tokens(
                    response.assistant_reply_ko,
                    response.follow_up_question_ko,
                    request.language_constraints,
                )
                if not validation.ok:
                    if attempt >= self.max_rewrites:
                        return ConversationResponse(
                            assistant_reply_ko=response.assistant_reply_ko,
                            follow_up_question_ko=response.follow_up_question_ko,
                            micro_feedback=response.micro_feedback,
                            suggested_user_intent_en=response.suggested_user_intent_en,
                            targets_used=response.targets_used,
                            unexpected_tokens=validation.unexpected_tokens,
                        )
                    request = _rewrite_request(
                        request,
                        reason=f"unexpected_tokens:{','.join(validation.unexpected_tokens)}",
                    )
                    continue

            violation = check_response_against_request(request=request, response=response)
            if violation is not None:
                if attempt >= self.max_rewrites:
                    raise ValueError(f"contract violation: {violation.reason}")
                request = _rewrite_request(request, reason=f"contract:{violation.reason}")
                continue

            return response

        assert last_error is not None
        raise last_error


def _rewrite_request(request: ConversationRequest, *, reason: str) -> ConversationRequest:
    system_role = (
        request.system_role
        + "\n\n"
        + "Rewrite required: your previous output violated the contract ("
        + reason
        + "). Return ONLY a valid JSON object matching the schema, and do not introduce unexpected tokens."
    )
    return ConversationRequest(
        system_role=system_role,
        conversation_state=request.conversation_state,
        user_input=request.user_input,
        language_constraints=request.language_constraints,
        generation_instructions=request.generation_instructions,
    )
