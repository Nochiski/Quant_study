"""Authoring source identity: the exact text a revision was compiled from (authoring ADR D3).

`SourceFormat` names the text syntax; `source_hash_of` is the sha256 of the exact UTF-8 text,
comments and whitespace included. Both are pure facts about the document and are shared by the
codec port (parsing) and the revision envelope (storage) so neither restates the other.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum


class SourceFormat(StrEnum):
    YAML = "yaml"
    JSON = "json"


def source_hash_of(source: str) -> str:
    """sha256 of the exact UTF-8 source text (comments and whitespace included)."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def json_pointer_token(value: str) -> str:
    """RFC 6901 포인터 토큰. 원문의 키와 진단·diff 경로가 같은 표기를 쓴다."""
    return value.replace("~", "~0").replace("/", "~1")
