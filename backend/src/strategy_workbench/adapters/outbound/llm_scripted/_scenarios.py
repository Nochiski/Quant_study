"""대본 시나리오 아홉 개와 질문 → 시나리오 선택 (WORKFLOW B-05, A-07 시나리오 fixture와 같은 내용).

"새 전략"(`idea_to_new_strategy`)과 "샤프"(`concept_answer`)는 유저 스토리 e2e(US-DM-03·US-SM-09)가
쓰려고 뒤에 더한 대본이라 A-07 골든에는 없다. "검색 상한"(`search_budget_answer`, C-03·US-CS-04)과
결과 설명(`result_explanation`, US-DM-08)도 같다.

## 결과 설명 대본은 질문이 아니라 도구 목록으로 고른다

결과 세션에서는 무엇을 물어도 이 대본이 돈다. application이 결과 세션에만 `read_backtest_result`를
주기 때문이다(결과 설명 spec R5). 그래서 이 대본이 골라졌다는 것 자체가 "세션 모드가 도구 목록을
바꿨다"의 증거이고, e2e는 그 대본의 답이 화면에 뜨는 것으로 모드 분기까지 확인한다. 답에 들어가는
숫자는 도구가 돌려준 서버 요약에서 읽는다 — 상수로 적으면 요약이 모델에 닿지 않아도 e2e가 통과한다.

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
from dataclasses import dataclass

from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    Done,
    SearchActivity,
    SearchBudgetExhausted,
    Source,
    TextDelta,
    ToolCall,
    ToolResult,
    Usage,
)
from strategy_workbench.domain.assistant.facade.tools import (
    PROPOSE_STRATEGY,
    READ_BACKTEST_RESULT,
    READ_CURRENT_STRATEGY,
)

__all__ = [
    "SCENARIO_KEYWORDS",
    "SLOW_ANSWER_DELAY_SCALE",
    "ExecuteTool",
    "Scenario",
    "ScenarioPlan",
    "scenario_for",
    "search_budget_answer",
    "search_then_failure",
    "simple_answer",
    "factor_window_proposal",
    "concept_answer",
    "idea_to_new_strategy",
    "result_explanation",
    "slow_answer",
    "tool_then_proposal",
]

ExecuteTool = Callable[[ToolCall], ToolResult]
Scenario = Callable[[ExecuteTool], Iterator[ChatEvent]]


@dataclass(frozen=True)
class ScenarioPlan:
    """돌릴 대본과 그 대본에만 걸 지연 배율.

    배율이 대본마다 다른 이유는 시나리오가 재는 것이 다르기 때문이다. 짧은 답변은 "스트리밍이
    보이는가"만 보면 되지만, 재연결 시나리오는 브라우저가 새로고침하고 다시 붙는 동안 턴이 살아
    있어야 한다. 그 여유를 머신 속도에 맡기면 빠른 장비에서 턴이 먼저 끝나 재연결 경로가 조용히
    "이미 끝난 턴의 이력 읽기"로 바뀐다(B-05 리뷰 R1-003).
    """

    run: Scenario
    delay_scale: float = 1.0


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

# 팩터 그래프를 바꾸는 제안(C-02 리뷰 P1-1). 첫 시계열 노드의 `window:` 값 하나만 바꾼다. 그래프가
# 달라지므로 frontend는 캐시에 없는 팩터 계획(explain)을 새로 조회한다 — "적용 후 백테스트"가 그
# 조회를 기다리는지 e2e가 이 경로로 본다.
_WINDOW_LINE = re.compile(r"^(\s+window:\s*)\d+\s*$", re.MULTILINE)
_SHORT_WINDOW = 126
_WINDOW_PROPOSAL_TITLE = "KRX 6개월 모멘텀"
_WINDOW_PROPOSAL_SUMMARY = "모멘텀 창을 252거래일에서 126거래일로 줄인다."

# 빈 새 전략에서 아이디어를 전략 전체로 바꾸는 제안(유저 스토리 US-DM-03). 새 전략 화면의
# 시작 문서는 `schema_version`·`title` 두 줄뿐이라 제목만 바꾸는 대본으로는 검증을 통과할 수
# 없다. 그래서 본문을 여기 적는다. `schema_version` 줄은 적지 않고 **지금 편집기 원문에서 그대로
# 가져온다** — 버전 리터럴의 owner는 `domain/strategy`이고, 새 전략 화면이 이미 runtime schema의
# `const`와 같은 값을 쓴다.
# 본문 섹션은 authoring schema를 따르므로 schema가 바뀌면 여기도 바뀌어야 한다.
# 지금 본문의 `data`(시장·기간·유니버스)와 `execution`(체결·수수료)은 schema 1.1이 요구해서
# 전략 안에 있다. 제품 방향은 실행 설정을 언어 밖에 두는 것이므로, schema 1.2(lang2 P2-03)를
# 머지하는 PR이 이 두 섹션을 본문에서 빼고 이 대본을 함께 고친다.
# `tests/application/test_scripted_idea_proposal.py`가 현재 schema로 깨끗이 compile되는지 고정해,
# 섹션이 낡으면 backend 게이트에서 먼저 깨진다.
_SCHEMA_VERSION_LINE = re.compile(r"^schema_version:.*$", re.MULTILINE)
_IDEA_TITLE = "KRX 대형 모멘텀"
_IDEA_SUMMARY = "최근 1년 많이 오른 종목 가운데 시가총액이 큰 종목을 매달 20개 담는다."
_IDEA_BODY = """description: "최근 1년 수익률이 높고 시가총액이 큰 종목을 매달 20개 담는다."
data:
  market: KRX
  start: "2021-01-01"
  end: "2026-08-31"
  universe_id: krx.common-stock
  frequency: daily
eligibility:
  rules: []
factors:
  - factor_id: momentum
    label: "모멘텀"
    direction: high
    weight: 0.7
    graph:
      nodes:
        - kind: field
          node_id: close
          field_id: price.close
        - kind: time_series
          node_id: mom_252
          operator: momentum
          input_node_id: close
          window: 252
      output_node_id: mom_252
  - factor_id: size
    label: "대형주"
    direction: high
    weight: 0.3
    graph:
      nodes:
        - kind: field
          node_id: cap
          field_id: price.market_cap
        - kind: cross_sectional
          node_id: cap_rank
          operator: rank
          input_node_id: cap
      output_node_id: cap_rank
portfolio:
  selection_count: 20
  rebalance: monthly
risk:
  max_name_weight: 0.05
execution:
  timing: next_open
  fee_bps: 15.0
parameters: []
"""

# 금융 개념을 쉬운 말로 푸는 답(유저 스토리 US-SM-09). 샤프 비율을 예로 든다.
_CONCEPT_ANSWER_PARTS = (
    "샤프 비율은 ",
    "위험 한 단위당 얼마나 벌었는지를 나타내는 숫자입니다. ",
    "수익률에서 무위험 수익률을 뺀 값을 수익률의 변동성으로 나눕니다. ",
    "같은 수익이라도 덜 흔들리며 벌었다면 샤프 비율이 더 높습니다.",
)

# 조각 수 × adapter의 조각당 지연 × `SLOW_ANSWER_DELAY_SCALE`이 이 턴의 길이다. 기본값으로 20초를
# 넘겨, 브라우저가 새로고침하고 다시 붙는 데 드는 1~2초가 그 안에 넉넉히 들어오게 한다.
SLOW_ANSWER_DELAY_SCALE = 4.0
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


def concept_answer(_execute_tool: ExecuteTool) -> Iterator[ChatEvent]:
    """도구 없이 금융 개념(샤프 비율)을 쉬운 말로 푸는 답을 흘린다."""
    for part in _CONCEPT_ANSWER_PARTS:
        yield TextDelta(text=part)
    yield Usage(input_tokens=1900, output_tokens=150)
    yield Done(stop_reason="end_turn")


def result_explanation(execute_tool: ExecuteTool) -> Iterator[ChatEvent]:
    """결과 요약을 읽고 핵심 지표를 쉬운 말로 풀어 답한다(US-DM-08).

    숫자는 전부 도구 결과에서 읽는다(모듈 docstring). 요약을 읽지 못하면 그렇다고 말한다 — 그럴듯한
    숫자로 되돌아가면 e2e가 "답이 왔다"까지만 보고 통과한다.
    """
    read_call = ToolCall(call_id="call-read-result", name=READ_BACKTEST_RESULT, arguments={})
    yield read_call
    summary = _result_summary(execute_tool(read_call))
    yield Usage(input_tokens=2400, output_tokens=160)
    for part in _result_answer_parts(summary):
        yield TextDelta(text=part)
    yield Usage(input_tokens=3100, output_tokens=420)
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


def factor_window_proposal(execute_tool: ExecuteTool) -> Iterator[ChatEvent]:
    """현재 문서를 읽고 제목과 첫 시계열 창(`window:`)을 바꾼 제안을 제출한다.

    제목만 바꾸는 `tool_then_proposal`과 달리 팩터 그래프가 달라진다. 창을 찾지 못하면 깨진
    조각을 제안한다 — 그래프가 그대로인 제안으로 조용히 되돌아가면 e2e가 캐시된 계획 경로를 밟고도
    통과한다.
    """
    read_call = ToolCall(call_id="call-read", name=READ_CURRENT_STRATEGY, arguments={})
    yield read_call
    current = execute_tool(read_call)
    yield Usage(input_tokens=2100, output_tokens=180)

    source_text = _window_proposal_source(current)
    propose_call = ToolCall(
        call_id="call-propose",
        name=PROPOSE_STRATEGY,
        arguments={
            "title": _WINDOW_PROPOSAL_TITLE,
            "summary": _WINDOW_PROPOSAL_SUMMARY,
            "rationale": _PROPOSAL_RATIONALE,
            "sources": [{"title": _KRX_MOMENTUM_SOURCE.title, "url": _KRX_MOMENTUM_SOURCE.url}],
            "source_text": source_text,
        },
    )
    yield propose_call
    execute_tool(propose_call)
    yield TextDelta(text="모멘텀 창을 줄인 안을 올렸습니다.")
    yield Usage(input_tokens=3400, output_tokens=920)
    yield Done(stop_reason="end_turn")


def idea_to_new_strategy(execute_tool: ExecuteTool) -> Iterator[ChatEvent]:
    """지금 문서의 `schema_version` 줄을 살려 아이디어를 전략 전체로 쓴 제안을 제출한다.

    빈 새 전략에서 시작하는 여정용이다. 지금 원문에 `schema_version` 줄이 없으면 깨진 조각을
    제안한다 — 버전을 여기서 지어내면 authoring schema가 올라갈 때 조용히 낡는다.
    """
    read_call = ToolCall(call_id="call-read", name=READ_CURRENT_STRATEGY, arguments={})
    yield read_call
    current = execute_tool(read_call)
    yield Usage(input_tokens=2100, output_tokens=180)

    propose_call = ToolCall(
        call_id="call-propose",
        name=PROPOSE_STRATEGY,
        arguments={
            "title": _IDEA_TITLE,
            "summary": _IDEA_SUMMARY,
            "rationale": _PROPOSAL_RATIONALE,
            "sources": [{"title": _KRX_MOMENTUM_SOURCE.title, "url": _KRX_MOMENTUM_SOURCE.url}],
            "source_text": _idea_proposal_source(current),
        },
    )
    yield propose_call
    execute_tool(propose_call)
    yield TextDelta(text="말씀하신 아이디어를 전략으로 옮긴 안을 올렸습니다.")
    yield Usage(input_tokens=3600, output_tokens=1100)
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


def search_budget_answer(_execute_tool: ExecuteTool) -> Iterator[ChatEvent]:
    """검색 두 번 뒤 검색 횟수 상한 통지를 받고, 검색 없이 모은 자료로 답한다.

    진짜 adapter가 상한에 닿았을 때 내는 순서와 같다 — 앞 호출의 검색·사용량, 도구를 뺀 호출 앞의
    통지, 그 호출의 답. 화면이 통지를 검색 활동 칩으로 그리지 않고 안내 문구로 보이는지 e2e가 이
    대본으로 본다.
    """
    yield SearchActivity(query="KRX 모멘텀 팩터 2026", sources=(_KRX_MOMENTUM_SOURCE,))
    yield SearchActivity(query="KRX 팩터 성과 보고", sources=(_FACTOR_REVIEW_SOURCE,))
    yield Usage(input_tokens=2600, output_tokens=240)
    yield SearchBudgetExhausted()
    yield TextDelta(text="찾은 자료 두 건으로 정리하면, ")
    yield TextDelta(text="KRX에서도 모멘텀 프리미엄이 관측됩니다.")
    yield Usage(input_tokens=3100, output_tokens=180)
    yield Done(stop_reason="end_turn")


# 질문에 들어 있는 낱말로 시나리오를 고른다. 먼저 맞는 항목이 이긴다 — "검색해서 제안해 줘"처럼
# 둘 다 들어 있으면 제안 쪽이다. "창을 줄"은 "제안"보다 앞이라 "창을 줄인 안을 제안해 줘"도
# 그래프를 바꾸는 대본을 고른다. "새 전략"도 "제안"보다 앞이라 "새 전략으로 제안해 줘"는
# 전체 전략을 쓴다. "검색 상한"은 "검색"보다 앞이라 검색 실패 대본으로 새지 않는다.
SCENARIO_KEYWORDS: tuple[tuple[str, ScenarioPlan], ...] = (
    ("창을 줄", ScenarioPlan(factor_window_proposal)),
    ("새 전략", ScenarioPlan(idea_to_new_strategy)),
    ("제안", ScenarioPlan(tool_then_proposal)),
    ("검색 상한", ScenarioPlan(search_budget_answer)),
    ("검색", ScenarioPlan(search_then_failure)),
    ("천천히", ScenarioPlan(slow_answer, delay_scale=SLOW_ANSWER_DELAY_SCALE)),
    ("샤프", ScenarioPlan(concept_answer)),
)


def scenario_for(text: str, *, offered: frozenset[str] = frozenset()) -> ScenarioPlan:
    """질문 한 줄로 시나리오를 고른다. 아무 낱말도 없으면 단순 답변이다.

    결과 세션(`read_backtest_result`를 받은 턴)은 질문과 무관하게 결과 설명이다(모듈 docstring).
    """
    if READ_BACKTEST_RESULT in offered:
        return ScenarioPlan(result_explanation)
    for keyword, plan in SCENARIO_KEYWORDS:
        if keyword in text:
            return plan
    return ScenarioPlan(simple_answer)


def _current_source(current: ToolResult) -> str | None:
    """`read_current_strategy` 결과에서 지금 원문을 꺼낸다. 읽지 못하면 None이다."""
    if not current.ok:
        return None
    try:
        payload = json.loads(current.content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    source_text = payload.get("source_text")
    if not isinstance(source_text, str) or source_text.strip() == "":
        return None
    return source_text


def _result_summary(result: ToolResult) -> Mapping[str, object] | None:
    """`read_backtest_result` 결과 JSON. 읽지 못하면 None이다."""
    if not result.ok:
        return None
    try:
        payload = json.loads(result.content)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def _full_metric(summary: Mapping[str, object], metric_id: str) -> float | None:
    metrics = summary.get("metrics")
    if not isinstance(metrics, list):
        return None
    for entry in metrics:
        if (
            isinstance(entry, Mapping)
            and entry.get("metric_id") == metric_id
            and entry.get("scope") == "full"
        ):
            value = entry.get("value")
            return float(value) if isinstance(value, int | float) else None
    return None


def _percent(value: float) -> str:
    """결과 화면의 퍼센트 표기와 같은 모양(소수 둘째 자리). e2e가 화면 값과 답을 맞춰 본다."""
    return f"{value * 100:.2f}%"


def _result_answer_parts(summary: Mapping[str, object] | None) -> tuple[str, ...]:
    if summary is None:
        return ("결과 요약을 읽지 못해 이 실행을 설명할 수 없습니다.",)
    parts = ["이 결과를 쉬운 말로 풀어 보겠습니다. "]
    total = _full_metric(summary, "total_return")
    if total is not None:
        direction = "늘었다" if total >= 0 else "줄었다"
        parts.append(
            f"총수익률(Total return)은 {_percent(total)}입니다. "
            f"처음 넣은 돈이 끝날 때 그만큼 {direction}는 뜻입니다. "
        )
    capital = summary.get("capital")
    benchmark = capital.get("benchmark_security_id") if isinstance(capital, Mapping) else None
    benchmark_return = _full_metric(summary, "benchmark_return")
    excess = _full_metric(summary, "excess_return")
    if isinstance(benchmark, str) and benchmark_return is not None and excess is not None:
        verdict = "더 벌었습니다" if excess >= 0 else "덜 벌었습니다"
        parts.append(
            f"같은 기간 벤치마크({benchmark})는 {_percent(benchmark_return)}였고, "
            f"전략은 벤치마크보다 {_percent(abs(excess))} {verdict}. "
        )
    else:
        parts.append(
            "이 실행에는 벤치마크가 없어 시장과 비교하지 못했습니다. "
            "실행 설정에서 벤치마크 종목을 정하고 다시 돌리면 비교할 수 있습니다. "
        )
    sharpe = _full_metric(summary, "sharpe")
    if sharpe is not None:
        parts.append(
            f"샤프 비율(Sharpe ratio) {sharpe:.2f}은 위험 한 단위당 얼마나 벌었는지를 뜻합니다. "
            "보통 1을 넘으면 괜찮은 편으로 봅니다. "
        )
    drawdown = _full_metric(summary, "max_drawdown")
    if drawdown is not None:
        parts.append(
            f"최대 낙폭(Maximum drawdown) {_percent(drawdown)}은 가장 높았던 때에서 가장 많이 "
            "떨어진 폭입니다. 그만큼 떨어지는 시기를 견딜 수 있는지 생각해 보세요. "
        )
    parts.append("과거 결과가 앞으로의 수익을 보장하지는 않습니다.")
    return tuple(parts)


def _window_proposal_source(current: ToolResult) -> str:
    """지금 원문의 제목과 첫 `window:` 값을 바꾼다. 둘 중 하나라도 못 찾으면 깨진 조각이다."""
    source_text = _current_source(current)
    if source_text is None or _WINDOW_LINE.search(source_text) is None:
        return _BROKEN_SOURCE
    retitled = _TITLE_LINE.sub(f'title: "{_WINDOW_PROPOSAL_TITLE}"', source_text, count=1)
    return _WINDOW_LINE.sub(lambda match: f"{match.group(1)}{_SHORT_WINDOW}", retitled, count=1)


def _idea_proposal_source(current: ToolResult) -> str:
    """지금 원문의 `schema_version` 줄 + 제목 + 아이디어 본문. 버전 줄이 없으면 깨진 조각이다."""
    source_text = _current_source(current)
    version = None if source_text is None else _SCHEMA_VERSION_LINE.search(source_text)
    if version is None:
        return _BROKEN_SOURCE
    return f'{version.group(0)}\ntitle: "{_IDEA_TITLE}"\n{_IDEA_BODY}'


def _proposal_source(current: ToolResult) -> str:
    """`read_current_strategy` 결과에서 지금 원문을 꺼내 제목만 바꾼다.

    도구 결과를 읽지 못하면 깨진 조각을 제안한다. 조용히 그럴듯한 원문으로 되돌아가면 e2e가
    "제안이 왔다"까지만 보고 통과해, 도구 경로가 끊긴 사실이 검사 밖으로 나간다.
    """
    source_text = _current_source(current)
    if source_text is None:
        return _BROKEN_SOURCE
    return _TITLE_LINE.sub(f'title: "{_PROPOSAL_TITLE}"', source_text, count=1)
