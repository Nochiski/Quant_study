from __future__ import annotations

from datetime import UTC, datetime


def datetime_text(value: datetime, *, field: str) -> str:
    """인지형(aware) datetime을 UTC 저장 문자열로 바꾼다(세 SQLite 어댑터의 시각 칸 공통 표기).

    거부하는 것은 naive datetime뿐이다. 그걸 그대로 적으면 재기동 뒤 읽을 때 로컬 시간대가 섞여
    기록의 순서가 말이 되지 않는 조합이 생긴다. 반대로 `Asia/Seoul`처럼 offset이 0이 아닌 aware
    값은 손실 없이 UTC로 옮길 수 있으므로 받는다 — offset 0만 받으면 호출자가 저장 형식에 맞춰 미리
    변환해야 하고, 그 변환을 잊은 곳이 조용히 터진다. UTC만 허용하는 규칙이 있으면 그 값 타입(예:
    `RevisionProvenance`)이 소유한다.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware datetime — got={value!r}")
    return value.astimezone(UTC).isoformat(timespec="microseconds")
