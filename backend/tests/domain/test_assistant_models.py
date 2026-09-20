"""domain.assistant 열거·union 구성원 계약 테스트.

도구 이름은 `test_assistant_tools.py`가 집합으로 고정하는데, 같은 급 계약인 `FailureCode`와
`ChatEvent` union에는 대응 테스트가 없었다. `FailureCode`는 화면 문구와 이력 필터의 키이고
`ChatEvent`는 SSE 직렬화의 대상이라, 값을 더하거나 빼면서 spec 갱신을 빠뜨려도 게이트가 잡지
못하면 A-04(HTTP)·B-03(화면)에서야 드러난다.

기대 집합은 여기 직접 적는다. spec 문서를 파싱하면 문서 서식이 바뀔 때 테스트가 깨지고, 무엇보다
"두 곳이 같다"를 사람이 한 번 확인하는 지점이 사라진다. 아래 주석의 spec 절 이름이 대조 대상이다.
"""

from __future__ import annotations

from typing import get_args

from strategy_workbench.domain.assistant.facade.models import ChatEvent, FailureCode

# spec `docs/superpowers/specs/2026-09-20-ai-assistant-design.md` D2의 `FailureCode` 목록 (12종).
EXPECTED_FAILURE_CODES = frozenset(
    {
        "auth",
        "rate_limit",
        "network",
        "refusal",
        "provider",
        "internal",
        "tool_rounds_exceeded",
        "timeout",
        "cancelled",
        "proposal_invalid",
        "output_truncated",
        "token_budget_exceeded",
    }
)

# 같은 spec D2의 `ChatEvent` union 구성원 (9종).
EXPECTED_CHAT_EVENTS = frozenset(
    {
        "TextDelta",
        "ThinkingSummary",
        "ToolCall",
        "ToolResultSummary",
        "SearchActivity",
        "Proposal",
        "Usage",
        "Done",
        "Failure",
    }
)


def test_failure_codes_match_the_design_spec() -> None:
    assert {member.value for member in FailureCode} == EXPECTED_FAILURE_CODES


def test_failure_code_values_are_the_wire_contract() -> None:
    """값은 HTTP·저장소로 나가는 문자열이다. 이름만 바꾸고 값을 두는 실수를 막는다."""
    assert {member.name.lower() for member in FailureCode} == EXPECTED_FAILURE_CODES


def test_chat_event_union_members_match_the_design_spec() -> None:
    assert {member.__name__ for member in get_args(ChatEvent)} == EXPECTED_CHAT_EVENTS


def test_every_chat_event_is_a_distinct_type() -> None:
    members = get_args(ChatEvent)

    assert len(members) == len(set(members))
