from __future__ import annotations

import html
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
_ENV_LOADED = False


def load_env_file(path: Path | None = None) -> None:
    global _ENV_LOADED

    env_path = path or ROOT / ".env"
    if path is None and _ENV_LOADED:
        return
    if not env_path.exists():
        if path is None:
            _ENV_LOADED = True
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value

    if path is None:
        _ENV_LOADED = True


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def normalize_token(value: str) -> str:
    return re.sub(r"[^0-9a-zа-яё+#.]+", "", value.casefold())


def contains_any(text: str, words: list[str]) -> list[str]:
    normalized_text = text.casefold()
    return [word for word in words if word and word.casefold() in normalized_text]


def clamp(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
