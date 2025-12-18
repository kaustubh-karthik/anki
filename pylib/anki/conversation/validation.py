from __future__ import annotations

import re
from dataclasses import dataclass

from .types import LanguageConstraints

_WORD_RE = re.compile(r"[\\w가-힣]+", re.UNICODE)
_JOSA_SUFFIXES = (
    "이",
    "가",
    "은",
    "는",
    "을",
    "를",
    "에",
    "에서",
    "로",
    "으로",
    "와",
    "과",
    "랑",
    "하고",
    "도",
    "만",
)


@dataclass(frozen=True, slots=True)
class TokenValidation:
    unexpected_tokens: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.unexpected_tokens


def tokenize_for_validation(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def validate_tokens(
    assistant_reply_ko: str,
    follow_up_question_ko: str,
    constraints: LanguageConstraints,
    *,
    always_allowed: tuple[str, ...] = (
        "아",
        "응",
        "네",
        "그래",
        "그럼",
        "음",
        "아니",
        "그리고",
        "그래서",
    ),
) -> TokenValidation:
    allowed: set[str] = set(constraints.allowed_support)
    for mt in constraints.must_target:
        allowed.update(mt.surface_forms)
    allowed.update(always_allowed)

    tokens = tokenize_for_validation(assistant_reply_ko) + tokenize_for_validation(
        follow_up_question_ko
    )

    unexpected = []
    for token in tokens:
        if token.isdigit():
            continue
        if _token_is_allowed(token, allowed):
            continue
        unexpected.append(token)

    return TokenValidation(unexpected_tokens=tuple(dict.fromkeys(unexpected)))


def _token_is_allowed(token: str, allowed: set[str]) -> bool:
    if token in allowed:
        return True
    # Korean-specific heuristic: allow a token like "의자가" if "의자" and "가" are allowed.
    # This reduces false positives due to common particle attachment.
    for suffix in _JOSA_SUFFIXES:
        if token.endswith(suffix) and len(token) > len(suffix):
            stem = token[: -len(suffix)]
            if stem in allowed and suffix in allowed:
                return True
    return False
