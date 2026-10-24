r"""`.env` 문법 하나(코드 리뷰 7).

서버 `.env`에서 이름 몇 개만 읽기(S07)와 내 문서 `auto_trading/.env` 저장(`_env_file.py`)이
같은 줄 나누기·이름 규칙을 쓴다. 값 해석만 두 가지다.

- 공통: 줄은 `\n`(또는 `\r\n`)으로만 나눈다. `str.splitlines`는 `\x0b`·`\x0c`·`\x1c`~`\x1e`·
  `\x85`·` `·` `에서도 쪼개 값 하나를 두 줄로 만든다(재리뷰 3). `export ` 앞머리와
  파일 맨 앞 UTF-8 BOM을 떼고, 따옴표 없는 값의 줄 끝 주석(` #`)을 뗀다(2026-10-22 리뷰 #605).
- 저장 파일(`parse_env_text`): 큰따옴표 값의 `\\`·`\"`·`\n`·`\r`·`\uXXXX` 이스케이프를 푼다.
  쓰는 쪽(`encode_env_value`)과 짝이다.
- 서버 `.env` 이름 읽기(`read_env_values`): 예전 의미 그대로 감싼 따옴표 한 쌍만 벗기고
  이스케이프는 풀지 않는다(재리뷰 6). 다른 도구가 쓴 파일을 조용히 다르게 읽지 않게 한다.
  원하는 이름의 줄만 값을 해석하고 다른 줄의 값은 만들지 않는다. 파일을 읽지 못하면
  `OSError`(UTF-8이 아니면 `UnicodeDecodeError`)를 그대로 올린다.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from pathlib import Path

__all__ = [
    "ENV_NAME",
    "UTF8_BOM",
    "env_lines",
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
# `str.splitlines`가 줄 끝으로 보는 나머지 글자. 다른 도구가
# 줄을 잘못 나누지 않게 `\uXXXX`로 적는다.
_LINE_BREAKS = "\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
_HEX = frozenset("0123456789abcdefABCDEF")
_UNESCAPES = {"\\": "\\", '"': '"', "n": "\n", "r": "\r"}


def read_env_values(path: Path, names: Collection[str]) -> dict[str, str]:
    wanted = frozenset(names)
    text, _ = decode_env_bytes(path.read_bytes())
    values: dict[str, str] = {}
    for raw in env_lines(text):
        split = _split_assignment(raw)
        if split is not None and split[0] in wanted:
            values[split[0]] = _legacy_value(split[1])
    return values


def decode_env_bytes(raw: bytes) -> tuple[str, bool]:
    """(BOM을 뗀 본문, BOM이 있었나). UTF-8이 아니면 `UnicodeDecodeError`."""
    if raw.startswith(UTF8_BOM):
        return raw[len(UTF8_BOM) :].decode("utf-8"), True
    return raw.decode("utf-8"), False


def env_lines(text: str) -> list[str]:
    """`\n`·`\r\n`으로만 나눈 줄. 마지막 줄바꿈 뒤의 빈 조각은 줄로 치지 않는다."""
    if not text:
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line.removesuffix("\r") for line in lines]


def parse_env_text(text: str) -> dict[str, str]:
    """`NAME=value` 줄을 모두 읽는다. 같은 이름이 여러 번 나오면 마지막 값이다."""
    values: dict[str, str] = {}
    for raw in env_lines(text):
        parsed = env_assignment(raw)
        if parsed is not None:
            values[parsed[0]] = parsed[1]
    return values


def env_assignment(raw: str) -> tuple[str, str] | None:
    """한 줄이 `NAME=value`면 (이름, 값), 아니면(빈 줄·주석·다른 글) None."""
    split = _split_assignment(raw)
    if split is None:
        return None
    return split[0], _decode_value(split[1])


def _split_assignment(raw: str) -> tuple[str, str] | None:
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    name, separator, value = line.removeprefix("export ").partition("=")
    name = name.strip()
    if not separator or not ENV_NAME.fullmatch(name):
        return None
    return name, value.strip()


def _legacy_value(text: str) -> str:
    """서버 `.env` 읽기의 예전 의미: 감싼 따옴표 한 쌍만 벗기고 이스케이프는 풀지 않는다."""
    if text[:1] in ('"', "'"):
        end = text.find(text[0], 1)
        if end > 0:
            return text[1:end]
    return text.split(" #", 1)[0].rstrip()


def encode_env_value(value: str) -> str:
    if _BARE_VALUE.fullmatch(value):
        return value
    return '"' + "".join(_escape(char) for char in value) + '"'


def _escape(char: str) -> str:
    if char in _ESCAPES:
        return _ESCAPES[char]
    if char in _LINE_BREAKS:
        return f"\\u{ord(char):04x}"
    return char


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
                code = text[index + 2 : index + 6]
                if following == "u" and len(code) == 4 and all(c in _HEX for c in code):
                    decoded.append(chr(int(code, 16)))
                    index += 6
                    continue
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
