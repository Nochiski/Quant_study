"""domain.assistant 열거·union 구성원 계약 테스트.

도구 이름은 `test_assistant_tools.py`가 집합으로 고정하는데, 같은 급 계약인 `FailureCode`와
`ChatEvent` union에는 대응 테스트가 없었다. `FailureCode`는 화면 문구와 이력 필터의 키이고
`ChatEvent`는 SSE 직렬화의 대상이라, 값을 더하거나 빼면서 spec 갱신을 빠뜨려도 게이트가 잡지
못하면 A-04(HTTP)·B-03(화면)에서야 드러난다.

기대 집합은 여기 직접 적는다. spec 문서를 파싱하면 문서 서식이 바뀔 때 테스트가 깨지고, 무엇보다
"두 곳이 같다"를 사람이 한 번 확인하는 지점이 사라진다. 아래 주석의 spec 절 이름이 대조 대상이다.
"""

from __future__ import annotations

import dataclasses
from typing import get_args

from strategy_workbench.domain.assistant.facade.models import ChatEvent, FailureCode, Usage

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


def test_usage_input_columns_do_not_overlap_and_total_is_derived() -> None:
    """`Usage`는 분리형이다. 총입력은 저장하지 않고 세 칸을 더해서 얻는다.

    공급자마다 같은 이름이 반대 뜻을 갖기 때문에(Anthropic의 `input_tokens`는 캐시를 뺀 값,
    OpenAI의 `prompt_tokens`는 포함한 값) domain이 하나로 못 박는다. 이 불변식이 흔들리면
    세션 집계가 한쪽을 두 번 센다.
    """
    usage = Usage(
        input_tokens=120,
        output_tokens=34,
        cache_read_tokens=8_000,
        cache_write_tokens=450,
    )

    assert usage.total_input_tokens == 120 + 8_000 + 450


def test_usage_total_is_not_a_stored_field() -> None:
    """파생값을 저장하면 성분과 합이 어긋난 이력이 생기고, 어느 쪽이 맞는지 알 수 없다.

    저장소 codec과 wire view가 필드 목록을 보고 움직이므로 여기서 고정한다.
    """
    names = {field.name for field in dataclasses.fields(Usage)}

    assert names == {"input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"}
    assert "total_input_tokens" not in names


def test_usage_defaults_keep_older_providers_and_history_working() -> None:
    usage = Usage(input_tokens=10, output_tokens=5)

    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (0, 0)
    assert usage.total_input_tokens == 10
