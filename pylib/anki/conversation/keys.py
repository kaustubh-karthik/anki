# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

import os
from pathlib import Path


def read_api_key_file(path: str | Path) -> str | None:
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except Exception:
        return None
    return text or None


def resolve_openai_api_key(*, api_key_file: str | Path = "gpt-api.txt") -> str | None:
    for env_key in ("OPENAI_API_KEY", "ANKI_OPENAI_API_KEY"):
        val = os.environ.get(env_key)
        if val:
            val = val.strip()
            if val:
                return val
    return read_api_key_file(api_key_file)
