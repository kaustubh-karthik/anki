# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from dataclasses import dataclass

from .settings import RedactionLevel


@dataclass(frozen=True)
class RedactionResult:
    text: str
    redacted: bool


def redact_text(text: str, level: RedactionLevel) -> RedactionResult:
    """Conservative redaction for LLM-bound text.

    This is intentionally deterministic and dependency-free.
    """

    if level == RedactionLevel.none:
        return RedactionResult(text=text, redacted=False)

    redacted = False
    out = text

    # minimal: remove obvious emails/urls
    if level in (RedactionLevel.minimal, RedactionLevel.strict):
        out2 = _redact_email(out)
        redacted |= out2 != out
        out = out2
        out2 = _redact_url(out)
        redacted |= out2 != out
        out = out2

    # strict: also remove long digit sequences (phone-like)
    if level == RedactionLevel.strict:
        out2 = _redact_long_digits(out)
        redacted |= out2 != out
        out = out2

    return RedactionResult(text=out, redacted=redacted)


def _redact_email(text: str) -> str:
    import re

    # avoid \\b (word boundary) because email local part often contains symbols that break it
    return re.sub(
        r"(?i)(?<!\S)[\w.+-]+@[\w-]+\.[\w.-]+(?!\S)",
        "[REDACTED_EMAIL]",
        text,
    )


def _redact_url(text: str) -> str:
    import re

    return re.sub(r"\bhttps?://\S+\b", "[REDACTED_URL]", text)


def _redact_long_digits(text: str) -> str:
    import re

    return re.sub(r"\b\d{7,}\b", "[REDACTED_NUMBER]", text)
