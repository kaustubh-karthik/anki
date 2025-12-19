# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

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

# Basic Korean vocabulary that the AI is allowed to use freely
# (even though we don't explicitly pass them in allowed_support)
_BASE_ALLOWED_SUPPORT: tuple[str, ...] = (
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
    "그리고",
    "그래서",
    "근데",
    "그런데",
    "네",
    "응",
    "아니요",
    "맞아요",
    "아니에요",
    "있어요",
    "없어요",
    "있어",
    "없어",
    "뭐",
    "뭐가",
    "뭐예요",
    "어디",
    "어디예요",
    "여기",
    "거기",
    "저기",
    "지금",
    "오늘",
    "내일",
    "좋아요",
    "싫어요",
    "안",
    "못",
    "좀",
    "더",
    "해주세요",
    "주세요",
    "해요",
    "해",
    "했어요",
    "할까요",
    "싶어요",
    "돼",
    "되요",
    "돼요",
    "맞아",
)


@dataclass(frozen=True)
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
    # Also allow basic Korean vocabulary (particles, common words)
    allowed.update(_BASE_ALLOWED_SUPPORT)

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
