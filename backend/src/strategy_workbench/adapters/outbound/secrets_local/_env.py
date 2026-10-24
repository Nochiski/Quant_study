"""`.env` 문법 하나(코드 리뷰 7).

서버 `.env`에서 이름 몇 개만 읽기(S07)와 내 문서 `auto_trading/.env`
저장(`_env_file.py`)이 같은 파서를 쓴다.

- `export ` 앞머리를 뗀다.
- 큰따옴표 값은 `\\\\`·`\\"`·`\\n`·`\\r` 이스케이프를 푼다.
  작은따옴표 값은 그대로다(감싼 한 쌍만 벗긴다).
- 따옴표 없는 값의 줄 끝 주석(` #`)을 뗀다(2026-10-22 리뷰 #605).
- 파일 맨 앞 UTF-8 BOM을 뗀다. 윈도우 편집기가 붙이면 첫 줄 이름을 못 알아보게 된다.

`read_env_values`는 필요한 이름의 값만 돌려주고 다른 줄의 값은 보관하지 않는다. 파일을 읽지 못하면
`OSError`(UTF-8이 아니면 `UnicodeDecodeError`)를 그대로 올린다.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from pathlib import Path

__all__ = [
    "UTF8_BOM",
    "decode_env_bytes",
    "encode_env_value",
    "env_assignment",
    "parse_env_text",
    "read_env_values",
]

UTF8_BOM = b"\xef\xbb\xbf"
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# 따옴표 없이 써도 다시 같은 값으로 읽히는 글자들. 그 밖의 값은 큰따옴표로 감싸고 이스케이프한다.
_BARE_VALUE = re.compile(r"[A-Za-z0-9_\-.:/+@,=]*")
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r"}
_UNESCAPES = {"\\": "\\", '"': '"', "n": "\n", "r": "\r"}


def read_env_values(path: Path, names: Collection[str]) -> dict[str, str]:
    wanted = frozenset(names)
    text, _ = decode_env_bytes(path.read_bytes())
    return {name: value for name, value in parse_env_text(text).items() if name in wanted}


def decode_env_bytes(raw: bytes) -> tuple[str, bool]:
    """(BOM을 뗀 본문, BOM이 있었나). UTF-8이 아니면 `UnicodeDecodeError`."""
    if raw.startswith(UTF8_BOM):
        return raw[len(UTF8_BOM) :].decode("utf-8"), True
    return raw.decode("utf-8"), False


def parse_env_text(text: str) -> dict[str, str]:
    """`NAME=value` 줄을 모두 읽는다. 같은 이름이 여러 번 나오면 마지막 값이다."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        parsed = env_assignment(raw)
        if parsed is not None:
            values[parsed[0]] = parsed[1]
    return values


def env_assignment(raw: str) -> tuple[str, str] | None:
    """한 줄이 `NAME=value`면 (이름, 값), 아니면(빈 줄·주석·다른 글) None."""
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    name, separator, value = line.removeprefix("export ").partition("=")
    name = name.strip()
    if not separator or not ENV_NAME.fullmatch(name):
        return None
    return name, _decode_value(value.strip())


def encode_env_value(value: str) -> str:
    if _BARE_VALUE.fullmatch(value):
        return value
    return '"' + "".join(_ESCAPES.get(char, char) for char in value) + '"'


def _decode_value(text: str) -> str:
    if text.startswith('"'):
        decoded: list[str] = []
        index = 1
        while index < len(text):
            char = text[index]
            if char == '"':
                return "".join(decoded)
            if char == "\\" and index + 1 < len(text):
                following = text[index + 1]
                decoded.append(_UNESCAPES.get(following, "\\" + following))
                index += 2
                continue
            decoded.append(char)
            index += 1
        # 닫는 따옴표가 없으면 그대로 둔다(손으로 고친 파일의 실수를 값으로 보이게).
        return text
    if text.startswith("'"):
        end = text.find("'", 1)
        if end > 0:
            return text[1:end]
        return text
    return text.split(" #", 1)[0].rstrip()
