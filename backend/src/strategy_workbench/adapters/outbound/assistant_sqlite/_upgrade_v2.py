"""assistant DB v1 → v2의 **행 변환** (C-03).

스키마(DDL)를 바꾸는 순서는 `_schema.py`가 쥐고, 여기는 옛 행을 새 모양으로 옮기는 두 가지만 한다.

1. `chat_messages`에 새로 생긴 `turn_id`를 채운다.
2. v1이 검색 상한 **통지**를 검색 활동(`search_activity`)으로 저장한 행을 전용 이벤트로 다시 쓴다.

둘 다 v1 파일을 한 번 올릴 때만 돈다. 올린 뒤의 codec·집계·화면은 추정이나 문구 비교를 하지 않는다.
이 어댑터는 로그를 남기지 않는다(`test_neither_storage_adapter_logs_anything`) — 옮긴 행 수 같은
진단도 찍지 않는다.

## `turn_id`를 무엇으로 채우는가

같은 세션에서 **메시지 작성 시각 이전(같은 시각 포함)에 시작한 마지막 턴**이다. v1 러너가 남긴
시각 순서가 이 규칙을 보장한다.

- 러너는 턴 시작 시각을 먼저 찍고 그다음 사용자 메시지를 쓴다(`AssistantTurnRunner.start` →
  `AssistantChatService.send`). 그래서 질문 시각 ≥ 그 턴의 시작 시각이다.
- 어시스턴트 메시지는 스트림이 끝날 때 쓰고, 세션 슬롯은 그 뒤에야 풀린다. 다음 턴은 슬롯이 풀린
  뒤에만 시작하므로 답 시각 < 다음 턴의 시작 시각이다.

순서(n번째 사용자 메시지 = n번째 턴)로 짝짓지 않는 이유는 그 가정이 깨지는 경로가 있기 때문이다.
v1에서 턴 행 저장이 실패하면 사용자 메시지만 남고, 순서로 짝지으면 그 뒤 모든 질문이 한 칸씩
밀린다. 시각 규칙에서는 그런 메시지가 앞 턴에 붙을 수는 있어도 뒤 질문을 밀지 않는다. 어느 턴보다
먼저 쓰인 메시지는 붙일 턴이 없으므로 NULL로 둔다 — 지어내지 않는다.

## 통지 행을 무엇으로 알아보는가

v1 OpenAI adapter가 쓴 모양 그대로다: 검색어가 v1 통지 문장과 글자 단위로 같고 출처가 빈 배열.
문장은 v1 코드가 저장한 **과거 사실**이라 여기 고정해 둔다. 모델에게 보내는 현재 문장
(`SEARCH_BUDGET_EXHAUSTED_NOTICE`)을 읽어 오면 그 문장이 바뀌는 날 v1 행을 더는 알아보지 못한다.
"""

from __future__ import annotations

import json
import sqlite3
from bisect import bisect_right
from datetime import UTC, datetime

from strategy_workbench.domain.assistant.facade.models import SearchBudgetExhausted

from ._errors import AssistantStorageError
from ._event_codec import encode_event

__all__ = ["copy_messages_with_turn_ids", "rewrite_v1_search_budget_notices"]

# v1 코드(A-06 ~ C-02)가 검색 상한 통지로 저장한 문장. 바꾸지 않는다(모듈 docstring).
V1_SEARCH_BUDGET_NOTICE = (
    "이 턴에 허용된 웹 검색 횟수를 모두 썼습니다. 더 이상 검색하지 않고 지금까지 확인한 자료로만 "
    "답합니다. 확인하지 못한 사실은 모른다고 말합니다."
)


def copy_messages_with_turn_ids(connection: sqlite3.Connection, *, source_table: str) -> None:
    """`source_table`(v1 모양)의 메시지를 새 `chat_messages`로 옮기며 `turn_id`를 채운다."""
    starts: dict[str, list[datetime]] = {}
    turn_ids: dict[str, list[str]] = {}
    turn_rows = connection.execute(
        "SELECT session_id, turn_id, started_at FROM chat_turns "
        "ORDER BY session_id, started_at, accepted_sequence"
    ).fetchall()
    for session_id, turn_id, started_at in turn_rows:
        starts.setdefault(session_id, []).append(
            _stored_datetime(started_at, field=f"chat_turns.started_at turn_id={turn_id!r}")
        )
        turn_ids.setdefault(session_id, []).append(turn_id)

    message_rows = connection.execute(
        f"SELECT session_id, ordinal, role, text, created_at FROM {source_table} "
        "ORDER BY session_id, ordinal"
    ).fetchall()
    for session_id, ordinal, role, text, created_at in message_rows:
        created = _stored_datetime(
            created_at,
            field=f"chat_messages.created_at session_id={session_id!r} ordinal={ordinal}",
        )
        # 작성 시각 이전(같은 시각 포함)에 시작한 마지막 턴.
        index = bisect_right(starts.get(session_id, []), created) - 1
        turn_id = turn_ids[session_id][index] if index >= 0 else None
        connection.execute(
            """
            INSERT INTO chat_messages (session_id, ordinal, role, text, created_at, turn_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, ordinal, role, text, created_at, turn_id),
        )


def rewrite_v1_search_budget_notices(connection: sqlite3.Connection) -> None:
    """v1이 검색 활동으로 저장한 상한 통지 행을 전용 이벤트 행으로 바꾼다."""
    tag, payload = encode_event(SearchBudgetExhausted())
    rows = connection.execute(
        "SELECT session_id, sequence, event_json FROM chat_events "
        "WHERE event_type = 'search_activity'"
    ).fetchall()
    for session_id, sequence, event_json in rows:
        if not _is_v1_notice(event_json):
            continue
        connection.execute(
            "UPDATE chat_events SET event_type = ?, event_json = ? "
            "WHERE session_id = ? AND sequence = ?",
            (tag, payload, session_id, sequence),
        )


def _is_v1_notice(event_json: object) -> bool:
    """v1 OpenAI adapter가 쓴 통지 모양인가. 읽을 수 없는 행은 건드리지 않는다.

    손상된 행을 여기서 고치거나 지우지 않는다 — 읽을 때 codec이 저장 오류로 멈추는 것이 이
    어댑터의 규칙이다(`_event_codec.py`).
    """
    if not isinstance(event_json, str):
        return False
    try:
        body = json.loads(event_json)
    except json.JSONDecodeError:
        return False
    return (
        isinstance(body, dict)
        and body.get("query") == V1_SEARCH_BUDGET_NOTICE
        and body.get("sources") == []
    )


def _stored_datetime(raw: object, *, field: str) -> datetime:
    """v1이 쓴 UTC ISO 문자열을 읽는다. 읽을 수 없으면 올리기를 멈춘다(트랜잭션이 되돌린다)."""
    if not isinstance(raw, str):
        raise AssistantStorageError(
            f"cannot upgrade the assistant database — {field} is not text "
            f"(type={type(raw).__name__})"
        )
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise AssistantStorageError(
            f"cannot upgrade the assistant database — {field} is not an ISO-8601 datetime "
            f"(value={raw!r})"
        ) from error
    if parsed.tzinfo is None:
        raise AssistantStorageError(
            f"cannot upgrade the assistant database — {field} has no time zone (value={raw!r})"
        )
    return parsed.astimezone(UTC)
