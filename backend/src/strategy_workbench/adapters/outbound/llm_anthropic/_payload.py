"""`TurnRequest` → Anthropic 요청 인자 (설계 spec D4).

## 프롬프트 캐싱

Anthropic은 요청을 `tools` → `system` → `messages` 순으로 렌더링하고 캐시는 **접두 일치**다.
접두 어딘가가 1바이트라도 바뀌면 그 뒤가 전부 무효가 된다. 그래서 안정된 것(도구 정의, 시스템
프롬프트)을 앞에 두고 breakpoint를 거기까지만 찍는다. 휘발하는 것(오늘 날짜·현재 문서·질문)은
`messages`에 실려 마지막 breakpoint **뒤**에 오므로 캐시를 깨지 않는다.

breakpoint는 요청당 최대 4개다. 여기서는 2개(도구 목록 끝, 시스템 블록)만 쓴다. 도구 목록만
같고 시스템이 바뀐 요청도 앞쪽 절반은 재사용하라고 둘로 나눴다.

**검색이 실제로 일어나면 캐시 접두가 깨진다.** `web_search`의 `max_uses`가 남은 횟수로 줄어
도구 목록이 바뀌기 때문이다. 검색 전에는 남은 횟수가 곧 전체 예산이라 목록이 바이트 단위로
같고, 캐시는 그대로 맞는다. 즉 손해는 "검색한 턴"에만 생기며, 그 대가로 검색 예산이 턴 전체에
걸쳐 지켜진다. 과금되는 쪽은 검색이다.

## 도구

`ToolSpec`의 이름·스키마를 그대로 옮기고 `strict: true`를 붙인다. `strict`가 요구하는
`additionalProperties: false`와 `required`는 domain 상수(`ASSISTANT_TOOLS`)가 이미 갖고 있으므로
adapter가 스키마를 손보지 않는다. 손보기 시작하면 도구 계약의 owner가 둘이 된다.
"""

from __future__ import annotations

from anthropic.types import (
    CacheControlEphemeralParam,
    MessageParam,
    OutputConfigParam,
    TextBlockParam,
    ThinkingConfigParam,
    ToolParam,
    ToolUnionParam,
    WebSearchTool20260209Param,
)

from strategy_workbench.domain.assistant.facade.models import (
    ChatRole,
    ResearchCapability,
    TurnRequest,
)

__all__ = [
    "DEFAULT_MODEL",
    "MAX_CACHE_BREAKPOINTS",
    "OUTPUT_CONFIG",
    "THINKING",
    "WEB_SEARCH_TOOL_NAME",
    "build_messages",
    "build_system",
    "build_tools",
]

# spec D4 Anthropic 행. 사용자가 프로파일에서 바꿀 수 있는 제안값이다.
DEFAULT_MODEL = "claude-opus-5"

# adaptive thinking + summarized. 기본값은 `display: "omitted"`라 `thinking` 블록이 빈 문자열로
# 와서 `ThinkingSummary` 이벤트가 영원히 비어 있게 된다. 사용자에게 사고 요약을 보여 주려면
# 명시해야 한다.
THINKING: ThinkingConfigParam = {"type": "adaptive", "display": "summarized"}
OUTPUT_CONFIG: OutputConfigParam = {"effort": "high"}

WEB_SEARCH_TOOL_NAME = "web_search"
_WEB_SEARCH_TOOL_TYPE = "web_search_20260209"

# 요청당 캐시 breakpoint 상한(Anthropic 계약). 넘기면 400이다.
MAX_CACHE_BREAKPOINTS = 4

_CACHE_CONTROL: CacheControlEphemeralParam = {"type": "ephemeral"}


def build_tools(request: TurnRequest, *, remaining_search_uses: int) -> list[ToolUnionParam]:
    """application이 선언한 도구 + 아직 예산이 남은 리서치 능력.

    Args:
        request: 이번 턴의 요청. 도구 정의와 리서치 요청이 들어 있다.
            `remaining_search_uses`: 이 턴에서 아직 쓸 수 있는 검색 횟수. SDK의 `max_uses`는
            **호출당** 한도(`"Maximum number of times the tool can be used in the API
            request."`)라 턴 상한으로 쓰려면 호출마다 남은 값으로 다시 계산해야 한다. 0이면
            도구를 아예 빼서 그 호출에서는 검색이 불가능하게 만든다(spec D9: 검색 횟수 집행은
            adapter).

    Returns:
        공급자에 보낼 도구 목록. 마지막 항목에 캐시 breakpoint를 찍는다.
    """
    tools: list[ToolUnionParam] = [
        ToolParam(
            name=spec.name,
            description=spec.description,
            input_schema=dict(spec.input_schema),
            strict=True,
        )
        for spec in request.tools
    ]
    if ResearchCapability.WEB_SEARCH in request.research and remaining_search_uses > 0:
        tools.append(
            WebSearchTool20260209Param(
                type=_WEB_SEARCH_TOOL_TYPE,
                name=WEB_SEARCH_TOOL_NAME,
                max_uses=remaining_search_uses,
            )
        )
    if tools:
        # 도구 목록은 턴 안에서 한 글자도 바뀌지 않는다. 여기가 첫 breakpoint다.
        tools[-1]["cache_control"] = _CACHE_CONTROL
    return tools


def build_system(request: TurnRequest) -> list[TextBlockParam]:
    """시스템 프롬프트 한 블록. 두 번째이자 마지막 breakpoint다."""
    return [
        TextBlockParam(
            type="text",
            text=request.system,
            cache_control=_CACHE_CONTROL,
        )
    ]


def build_messages(request: TurnRequest) -> list[MessageParam]:
    """세션 이력을 Anthropic 메시지로 옮긴다. 캐시 breakpoint를 찍지 않는다(휘발 구간)."""
    return [
        MessageParam(
            role="user" if message.role is ChatRole.USER else "assistant",
            content=message.text,
        )
        for message in request.messages
    ]
