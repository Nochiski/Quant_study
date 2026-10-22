"""서버 `.env`에서 이름 몇 개의 값만 읽는다(S07 서버 키·봇 토큰).

필요한 이름의 값만 돌려주고 다른 줄의 값은 보관하지 않는다. `export ` 앞머리, 따옴표로 감싼 값(감싼
한 쌍만 벗긴다), 따옴표 없는 값의 줄 끝 주석(` #`)을 다룬다(2026-10-22 리뷰 #605: 양끝 따옴표를 모두
벗기고 줄 끝 주석을 값에 남기던 것을 고침). 파일을 읽지 못하면 `OSError`를 그대로 올린다.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

_QUOTES = "\"'"


def read_env_values(path: Path, names: Collection[str]) -> dict[str, str]:
    wanted = frozenset(names)
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.removeprefix("export ").partition("=")
        name = name.strip()
        if separator and name in wanted:
            values[name] = _value(value.strip())
    return values


def _value(text: str) -> str:
    if text[:1] and text[0] in _QUOTES:
        end = text.find(text[0], 1)
        if end > 0:
            return text[1:end]
    return text.split(" #", 1)[0].rstrip()
