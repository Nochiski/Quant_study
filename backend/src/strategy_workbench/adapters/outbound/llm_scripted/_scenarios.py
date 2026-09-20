"""대본 시나리오 네 개와 질문 → 시나리오 선택 (WORKFLOW B-05, A-07 시나리오 fixture와 같은 내용).

## 왜 backend 안에 두는가

e2e는 브라우저부터 SQLite까지 실제 경로를 그대로 지나야 의미가 있다. 공급자 응답만 MSW로 가로채면
SSE 프레이밍·sequence·턴 러너·제안 재검증이 전부 검사 밖으로 나간다. 그래서 가짜를 두는 자리는
frontend가 아니라 **공급자 port 뒤**이고, 그러면 `tests/`의 fixture를 읽을 수 없다(설치된 패키지는
테스트 트리를 갖고 오지 않는다). 그래서 대본이 여기 있다.

## 전략 문서를 여기 적지 않는다

제안 시나리오는 문서 원문을 상수로 갖지 않고 `read_current_strategy` 도구가 돌려준 **지금 편집기
텍스트**에서 제목만 바꿔 만든다. 이유는 두 가지다.

1. `schema_version` 같은 리터럴을 여기 적으면 authoring schema가 올라갈 때 조용히 stale이 된다
   (SoT 규칙: 버전 리터럴의 owner는 `domain/strategy`의 `CURRENT_SCHEMA_VERSION` 하나다).
2. 제안은 언제나 "지금 문서를 고친 것"이라 compile을 통과하는 것이 보장된다. 대본이 문서를
   직접 들면 골든 fixture가 바뀔 때마다 여기도 같이 고쳐야 한다.

검증 실패 시나리오의 원문만 여기 적는데, 그 원문은 **필수 키가 없는 조각**이라 버전 리터럴이
없다 — 어느 schema에서도 실패한다는 것이 이 시나리오가 원하는 전부다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator, Mapping

from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    Done,
    SearchActivity,
    Source,
    TextDelta,
    ToolCall,
    ToolResult,
    Usage,
)
from strategy_workbench.domain.assistant.facade.tools import (
    PROPOSE_STRATEGY,
    READ_CURRENT_STRATEGY,
)

__all__ = [
    "SCENARIO_KEYWORDS",
    "ExecuteTool",
    "Scenario",
    "scenario_for",
    "search_then_failure",
    "simple_answer",
    "slow_answer",
    "tool_then_proposal",
]

ExecuteTool = Callable[[ToolCall], ToolResult]
Scenario = Callable[[ExecuteTool], Iterator[ChatEvent]]

_KRX_MOMENTUM_SOURCE = Source(title="KRX 모멘텀 리뷰", url="https://example.com/krx-momentum")
_FACTOR_REVIEW_SOURCE = Source(title="팩터 성과 보고", url="https://example.com/factor-review")

_PROPOSAL_TITLE = "KRX 12-1 모멘텀"
_PROPOSAL_SUMMARY = "12개월 모멘텀에서 최근 1개월을 뺀 순위로 상위 종목을 담는다."
_PROPOSAL_RATIONALE = "모멘텀 프리미엄은 KRX에서도 관측된다 (https://example.com/krx-momentum)."

# 필수 키가 없는 조각. 어느 authoring schema에서도 compile이 실패한다.
_BROKEN_SOURCE = "title: 깨진 제안\n"

# 제목 줄 하나만 바꾼다. 들여쓰기 없는 최상위 `title:`만 보므로 factor의 `label:`이나 중첩된
# 키는 건드리지 않는다.
_TITLE_LINE = re.compile(r"^title:.*$", re.MULTILINE)

# 조각 수 × adapter의 조각당 지연이 이 턴의 길이다. 브라우저가 새로고침하고 다시 붙는 데 드는
# 시간보다 넉넉히 길어야, 재연결 경로가 "이미 끝난 턴의 이력 읽기"로 조용히 대체되지 않는다.
_SLOW_ANSWER_PARTS = (
    "천천히 설명하겠습니다. ",
    "모멘텀 전략은 ",
    "최근 수익률이 높았던 종목이 ",
    "당분간 더 오르는 경향에 ",
    "기대는 전략입니다. ",
    "KRX에서도 ",
    "이 경향은 관측됩니다. ",
    "다만 그대로 쓰지는 않습니다. ",
    "12개월 수익률에서 ",
    "최근 1개월을 빼는 ",
    "형태를 많이 씁니다. ",
    "직전 한 달은 ",
    "되돌림이 잦기 때문입니다. ",
    "순위를 매긴 뒤에는 ",
    "상위 몇 종목을 담을지 정합니다. ",
    "종목 수가 적으면 ",
    "변동이 커지고 ",
    "많으면 지수에 가까워집니다. ",
    "리밸런싱 주기도 같이 봅니다. ",
    "자주 갈아탈수록 ",
    "수수료와 슬리피지가 쌓입니다. ",
    "마지막으로 ",
    "종목당 비중 상한을 두어 ",
    "한 종목이 성과를 좌우하지 않게 합니다.",
)


def simple_answer(_execute_tool: ExecuteTool) -> Iterator[ChatEvent]:
    """도구 없이 문장 두 조각만 흘린다. 스트리밍 표시가 붙었는지 보는 시나리오."""
    yield TextDelta(text="모멘텀 전략은 ")
    yield TextDelta(text="최근 많이 오른 종목을 사는 전략입니다.")
    yield Usage(input_tokens=1840, output_tokens=120)
    yield Done(stop_reason="end_turn")


def slow_answer(_execute_tool: ExecuteTool) -> Iterator[ChatEvent]:
    """조각을 많이 흘려 턴이 한동안 RUNNING으로 남게 한다.

    새로고침 중 재연결(spec D7)을 사람 속도로 재현하려면, 브라우저가 다시 뜨는 동안에도 턴이 살아
    있어야 한다. 조각 수가 그 시간을 만든다 — 한 조각당 지연은 adapter가 건다.
    """
    for part in _SLOW_ANSWER_PARTS:
        yield TextDelta(text=part)
    yield Usage(input_tokens=2200, output_tokens=260)
    yield Done(stop_reason="end_turn")


def tool_then_proposal(execute_tool: ExecuteTool) -> Iterator[ChatEvent]:
    """현재 문서를 읽고 제목만 바꾼 제안을 제출한다.

    `Proposal`·`ToolResultSummary`는 여기서 내보내지 않는다. application이 도구 실행 중에 큐에
    넣고, 이 제너레이터가 **다음 이벤트를 흘릴 때** 그 큐를 비워 내보낸다. 그래서 도구 호출
    뒤에는 언제나 이벤트가 하나 더 있어야 한다.
    """
    read_call = ToolCall(call_id="call-read", name=READ_CURRENT_STRATEGY, arguments={})
    yield read_call
    current = execute_tool(read_call)
    yield Usage(input_tokens=2100, output_tokens=180)

    source_text = _proposal_source(current)
    propose_call = ToolCall(
        call_id="call-propose",
        name=PROPOSE_STRATEGY,
        arguments={
            "title": _PROPOSAL_TITLE,
            "summary": _PROPOSAL_SUMMARY,
            "rationale": _PROPOSAL_RATIONALE,
            "sources": [{"title": _KRX_MOMENTUM_SOURCE.title, "url": _KRX_MOMENTUM_SOURCE.url}],
            "source_text": source_text,
        },
    )
    yield propose_call
    execute_tool(propose_call)
    yield TextDelta(text="제목을 바꾼 모멘텀 안을 올렸습니다.")
    yield Usage(input_tokens=3400, output_tokens=920)
    yield Done(stop_reason="end_turn")


def search_then_failure(execute_tool: ExecuteTool) -> Iterator[ChatEvent]:
    """검색 활동을 보인 뒤 같은 원문으로 세 번 거절당해 턴이 끝난다.

    세 번째 거절에서 application이 `PROPOSAL_INVALID`를 확정하고 다음 이벤트 앞에서 루프를 끊는다.
    그래서 마지막 `Done`은 화면에 닿지 않는다 — 큐를 비울 이벤트가 하나 더 필요할 뿐이다.
    """
    yield SearchActivity(
        query="KRX 모멘텀 팩터 2026",
        sources=(_KRX_MOMENTUM_SOURCE, _FACTOR_REVIEW_SOURCE),
    )
    yield Usage(input_tokens=2600, output_tokens=240)
    for attempt in (1, 2, 3):
        call = ToolCall(
            call_id=f"call-propose-{attempt}",
            name=PROPOSE_STRATEGY,
            arguments={
                "title": _PROPOSAL_TITLE,
                "summary": _PROPOSAL_SUMMARY,
                "rationale": _PROPOSAL_RATIONALE,
                "sources": [{"title": _KRX_MOMENTUM_SOURCE.title, "url": _KRX_MOMENTUM_SOURCE.url}],
                "source_text": _BROKEN_SOURCE,
            },
        )
        yield call
        execute_tool(call)
    yield Done(stop_reason="end_turn")


# 질문에 들어 있는 낱말로 시나리오를 고른다. 먼저 맞는 항목이 이긴다 — "검색해서 제안해 줘"처럼
# 둘 다 들어 있으면 제안 쪽이다.
SCENARIO_KEYWORDS: tuple[tuple[str, Scenario], ...] = (
    ("제안", tool_then_proposal),
    ("검색", search_then_failure),
    ("천천히", slow_answer),
)


def scenario_for(text: str) -> Scenario:
    """질문 한 줄로 시나리오를 고른다. 아무 낱말도 없으면 단순 답변이다."""
    for keyword, scenario in SCENARIO_KEYWORDS:
        if keyword in text:
            return scenario
    return simple_answer


def _proposal_source(current: ToolResult) -> str:
    """`read_current_strategy` 결과에서 지금 원문을 꺼내 제목만 바꾼다.

    도구 결과를 읽지 못하면 깨진 조각을 제안한다. 조용히 그럴듯한 원문으로 되돌아가면 e2e가
    "제안이 왔다"까지만 보고 통과해, 도구 경로가 끊긴 사실이 검사 밖으로 나간다.
    """
    if not current.ok:
        return _BROKEN_SOURCE
    try:
        payload = json.loads(current.content)
    except json.JSONDecodeError:
        return _BROKEN_SOURCE
    if not isinstance(payload, Mapping):
        return _BROKEN_SOURCE
    source_text = payload.get("source_text")
    if not isinstance(source_text, str) or source_text.strip() == "":
        return _BROKEN_SOURCE
    return _TITLE_LINE.sub(f'title: "{_PROPOSAL_TITLE}"', source_text, count=1)
