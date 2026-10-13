"""정규 JSON 바이트와 SHA-256 알고리즘의 단일 owner."""

import hashlib
import json
from collections.abc import Callable
from datetime import date
from enum import Enum


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported canonical value — type={type(value).__name__}")


def canonical_payload_json(
    payload: object,
    *,
    indent: int | None = None,
    ensure_ascii: bool = False,
    default: Callable[[object], object] = _json_default,
) -> str:
    """키 정렬·유한 숫자·UTF-8 JSON. ASCII 옵션은 기존 팩터 캐시 바이트를 보존한다.

    payload의 의미 투영과 특수 값 변환은 각 도메인이 소유하고 이 leaf는 직렬화만 한다.
    """
    return json.dumps(
        payload,
        ensure_ascii=ensure_ascii,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":") if indent is None else (",", ": "),
        indent=indent,
        default=default,
    )


def canonical_json_spec_hash(canonical_json: str) -> str:
    """이미 정규화된 JSON의 SHA-256. 은퇴 저장본은 원래 바이트를 그대로 해시한다."""
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def canonical_json_hash(payload: object, *, ensure_ascii: bool = False) -> str:
    """현재 payload를 정규 직렬화하고 해시한다."""
    return canonical_json_spec_hash(canonical_payload_json(payload, ensure_ascii=ensure_ascii))
