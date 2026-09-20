"""`TurnRequest` → OpenAI Responses 요청 인자 (설계 spec D4).

## 요청의 모양

Responses API는 시스템 프롬프트를 `instructions`로 따로 받고, 대화 이력과 도구 결과는 `input`
항목 배열로 받는다. 그래서 Anthropic adapter의 `system`/`messages` 구분이 여기서는
`instructions`/`input`이 된다.

## 프롬프트 캐싱

OpenAI의 프롬프트 캐싱은 명시적 breakpoint가 없고(요청에 `cache_control` 같은 인자가 없다)
**접두 일치로 자동 적용**된다. 그래서 adapter가 할 수 있는 일은 안정된 것을 앞에, 휘발하는 것을
뒤에 두는 것뿐이다. `instructions`와 `tools`는 턴 안에서 바뀌지 않고, 도구 결과·새 질문은
`input` 끝에 쌓이므로 이 배치가 그대로 접두 재사용이 된다.

예외가 하나 있다. 검색 상한에 닿아 `web_search`를 도구 목록에서 빼는 호출은 도구 접두가 바뀌어
그 뒤 캐시가 무효가 된다. 상한에 닿는 것은 턴당 많아야 한 번이고, 대안(도구를 남겨 두기)은 상한
자체를 포기하는 것이라 이 손해를 받는다.

## 도구

`ToolSpec`의 이름·스키마를 그대로 옮기고 `strict: True`를 붙인다. `strict`가 요구하는
`additionalProperties: false`와 `required`는 domain 상수(`ASSISTANT_TOOLS`)가 이미 갖고 있으므로
adapter가 스키마를 손보지 않는다. 손보기 시작하면 도구 계약의 owner가 둘이 된다.

웹 검색은 `{"type": "web_search"}` 서버 도구다. Anthropic의 `web_search_20260209`와 달리
**`max_uses`에 해당하는 인자가 없다**(SDK `WebSearchToolParam`에 필드 자체가 없다). 횟수 집행이
`_turn.py`의 루프로 올라온 이유가 이것이다.
"""

from __future__ import annotations

from openai.types.responses import (
    EasyInputMessageParam,
    FunctionToolParam,
    ResponseIncludable,
    ResponseInputItemParam,
    ToolParam,
    WebSearchToolParam,
)
from openai.types.responses.response_input_item_param import Message
from openai.types.shared_params import Reasoning

from strategy_workbench.domain.assistant.facade.models import (
    ChatRole,
    ResearchCapability,
    TurnRequest,
)

__all__ = [
    "DEFAULT_MODEL",
    "INCLUDE",
    "REASONING",
    "WEB_SEARCH_TOOL_TYPE",
    "build_input",
    "build_tools",
    "notice_item",
]

# spec D4 OpenAI 행은 "Responses API 최신 GPT 모델(A-06 구현 시 SDK 문서로 확정)"만 정했다.
# 확정 근거: 설치된 `openai` 3.16.2의 `ChatModel` 목록 첫 항목이자 `Response.model` docstring의
# 예시가 `gpt-6-astra`이고, 공식 모델 문서가 "가장 유능한 모델, 어디서 시작할지 모르겠으면
# GPT-6 Astra"로 소개한다. 사용자가 프로파일에서 바꿀 수 있는 제안값이다.
DEFAULT_MODEL = "gpt-6-astra"

# 추론 노력은 높게, 사고 요약은 켠다. `summary`를 주지 않으면 reasoning 요약 델타 이벤트가 아예
# 오지 않아 `ThinkingSummary`가 영원히 비어 있게 된다(Anthropic의 `display: "summarized"`와 같은
# 자리). `"auto"`는 모델이 지원하는 상세도를 스스로 고른다.
REASONING: Reasoning = {"effort": "high", "summary": "auto"}

# `web_search_call.action.sources`가 없으면 검색 호출 항목의 `action.sources`가 비어 와서
# `SearchActivity`에 실을 출처가 없다. `reasoning.encrypted_content`는 도구 라운드마다 추론
# 항목을 그대로 되돌려 보내기 위한 것이다 — 우리 루프는 대화 상태를 서버에 맡기지 않고
# (`previous_response_id`를 쓰지 않고) `input`에 누적하므로, 암호화된 추론 내용을 같이 실어야
# 모델이 앞 라운드의 사고를 이어 간다.
INCLUDE: list[ResponseIncludable] = [
    "web_search_call.action.sources",
    "reasoning.encrypted_content",
]

WEB_SEARCH_TOOL_TYPE = "web_search"


def build_tools(request: TurnRequest, *, web_search: bool) -> list[ToolParam]:
    """application이 선언한 도구 + (허용된 동안만) 웹 검색 서버 도구.

    Args:
        request: 이 턴의 도구 선언과 상한.
        web_search: 이번 호출에 웹 검색을 남겨 둘지. 누적 검색 횟수가 `max_search_uses`에 닿으면
            `_turn.py`가 거짓을 준다.
    """
    tools: list[ToolParam] = [
        FunctionToolParam(
            type="function",
            name=spec.name,
            description=spec.description,
            parameters=dict(spec.input_schema),
            strict=True,
        )
        for spec in request.tools
    ]
    if web_search and ResearchCapability.WEB_SEARCH in request.research:
        tools.append(WebSearchToolParam(type="web_search"))
    return tools


def build_input(request: TurnRequest) -> list[ResponseInputItemParam]:
    """세션 이력을 Responses 입력 항목으로 옮긴다. 도구 결과는 루프가 뒤에 이어 붙인다."""
    return [
        EasyInputMessageParam(
            type="message",
            role="user" if message.role is ChatRole.USER else "assistant",
            content=message.text,
        )
        for message in request.messages
    ]


def notice_item(text: str) -> ResponseInputItemParam:
    """application이 준 고정 문구를 모델에게 전달하는 입력 항목 하나.

    `developer` 역할을 쓴다. 사용자가 하지 않은 말을 `user`로 넣으면 이력의 화자가 흐려지고,
    `web_search`는 서버 도구라 답을 붙일 `function_call_output`이 애초에 없다. Responses의 역할
    서열상 `developer`는 `user`보다 우선하는 지시 채널이라 "이제 검색할 수 없다"를 알리기에 맞다.
    """
    return Message(type="message", role="developer", content=[{"type": "input_text", "text": text}])
