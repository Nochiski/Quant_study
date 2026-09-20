"""세션·턴 사용량 집계 (WORKFLOW A-07, 설계 spec D3).

## 왜 application이 소유하는가

사용량은 이벤트 이력에서 **파생되는 값**이다(SoT 규칙: 파생 가능한 값을 원본으로 저장하지
않는다). 저장소에 누적 칼럼을 두면 이벤트와 칼럼이 어긋날 수 있고, 어긋난 쪽이 무엇인지 아무도
모른다. 그래서 집계는 저장하지 않고 이벤트에서 그때그때 접는다.

HTTP 어댑터가 직접 세지 않는 이유도 같다. "한 턴의 사용량이 무엇인가"는 도메인 이벤트를 읽는
규칙이고, 화면이 둘이 되면 두 곳에서 다르게 세게 된다.

## 왜 함수이고 서비스 메서드가 아닌가

세션 조회 라우트는 이벤트 이력을 이미 한 번 읽는다(`SessionHistoryView.events`). 집계를 서비스
메서드로 두면 같은 요청이 이벤트를 두 번 읽고, 그 사이에 턴이 이벤트를 붙이면 한 응답 안에서
`events`와 `usage`가 서로 다른 이력을 말하게 된다. 읽은 이력 하나를 접는 순수 함수면 그 틈이
없다.

## 확장 지점

토큰 종류를 추가할 때 건드리는 곳은 세 군데뿐이다 — `TokenTotals`의 필드, `TokenTotals.__add__`의
항, `_tokens_of`의 한 줄. 합산 루프는 `__add__`에 기대므로 고칠 일이 없다. A-05가 도메인 `Usage`에
캐시 토큰을 더하면 그 세 곳에 같은 이름으로 붙인다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from strategy_workbench.domain.assistant.facade.models import (
    SearchActivity,
    SequencedEvent,
    Usage,
)

__all__ = ["SessionUsage", "TokenTotals", "TurnUsage", "aggregate_usage"]


@dataclass(frozen=True)
class TokenTotals:
    """토큰 종류별 합. 종류가 늘어도 더하는 쪽 코드는 그대로다.

    **입력은 분리형이다.** `input_tokens`는 캐시 읽기·쓰기를 **뺀** 성분이고, 캐시 성분은 각자
    자기 칸을 갖는다. 세 칸은 서로 겹치지 않으므로 총 입력은 그 합(`total_input_tokens`)이다.
    공급자 원본의 의미는 서로 다르지만(A-06의 OpenAI adapter가 원시 내역을 빼서 정규화한다)
    도메인 `Usage`가 나올 때는 이미 분리형이라, 집계는 공급자를 구분하지 않고 성분끼리 더한다.

    성분을 따로 두는 이유는 캐시가 비용이 다르기 때문이다. 한 칸으로 뭉치면 화면이 "캐시가
    얼마나 걸렸는가"를 영영 못 보여 주고, 그 질문이 프롬프트 캐싱을 넣은 이유다(spec D4).
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_input_tokens(self) -> int:
        """세 입력 성분의 합. 도메인 `Usage.total_input_tokens`와 같은 정의다.

        도메인은 이 값을 "저장·전송 필드가 아니다"라고 못 박는다. 저장하면 성분과 합이 어긋난
        이력이 생기기 때문이다. 여기서도 저장하지 않는다 — 응답을 만드는 순간에만 파생하므로
        어긋난 이력이 남을 자리가 없다. 화면이 한 숫자를 원해서 응답에는 싣는다.

        `__add__`는 성분만 더하고 이 값은 건드리지 않는다. 성분끼리 더한 뒤 파생시키는 것과
        파생값끼리 더하는 것이 같은 수이기 때문이며, 그 등식을 테스트가 고정한다.
        """
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    def __add__(self, other: TokenTotals) -> TokenTotals:
        return TokenTotals(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass(frozen=True)
class TurnUsage:
    """턴 하나가 쓴 양.

    `provider_calls`는 `Usage` 이벤트 수다. adapter가 공급자를 한 번 부를 때마다 하나씩 내므로,
    도구 라운드 수와 달리 "실제로 몇 번 호출했는가"를 말한다. 두 수가 벌어지면 재개(`pause_turn`)나
    재시도가 있었다는 뜻이다.
    """

    turn_id: str
    tokens: TokenTotals
    search_uses: int
    provider_calls: int


@dataclass(frozen=True)
class SessionUsage:
    """세션 누적과 턴별 내역. 누적은 턴별의 합이며 따로 세지 않는다."""

    tokens: TokenTotals
    search_uses: int
    provider_calls: int
    turns: tuple[TurnUsage, ...]


def _tokens_of(event: Usage) -> TokenTotals:
    """사용량 이벤트 하나를 토큰 합으로. 도메인 이벤트와 집계가 만나는 유일한 지점이다.

    도메인 `Usage`의 세 입력 칸은 겹치지 않는다(adapter가 그 불변식에 맞춘다). 그래서 성분을
    그대로 옮기고 총합은 읽는 쪽에서 파생한다 — 여기서 `total_input_tokens`를 읽어 한 칸에 담으면
    캐시 내역이 사라지고, 성분과 합을 둘 다 담으면 같은 토큰을 두 번 세게 된다.
    """
    return TokenTotals(
        input_tokens=event.input_tokens,
        output_tokens=event.output_tokens,
        cache_read_tokens=event.cache_read_tokens,
        cache_write_tokens=event.cache_write_tokens,
    )


def aggregate_usage(events: Sequence[SequencedEvent]) -> SessionUsage:
    """세션 이벤트 이력을 턴별·세션 누적 사용량으로 접는다.

    사용량 이벤트가 하나도 없는 턴도 목록에 남긴다. 0으로 남기지 않고 빼 버리면 화면이 "아직
    아무것도 안 썼다"와 "그런 턴이 없다"를 구분하지 못한다. 순서는 이벤트가 처음 나타난 순서이며
    sequence가 단조 증가하므로 턴이 시작된 순서와 같다.
    """
    per_turn: dict[str, TurnUsage] = {}
    for stored in events:
        current = per_turn.get(stored.turn_id) or TurnUsage(
            turn_id=stored.turn_id, tokens=TokenTotals(), search_uses=0, provider_calls=0
        )
        if isinstance(stored.event, Usage):
            current = replace(
                current,
                tokens=current.tokens + _tokens_of(stored.event),
                provider_calls=current.provider_calls + 1,
            )
        elif isinstance(stored.event, SearchActivity):
            current = replace(current, search_uses=current.search_uses + 1)
        per_turn[stored.turn_id] = current
    turns = tuple(per_turn.values())
    total = TokenTotals()
    for turn in turns:
        total = total + turn.tokens
    return SessionUsage(
        tokens=total,
        search_uses=sum(turn.search_uses for turn in turns),
        provider_calls=sum(turn.provider_calls for turn in turns),
        turns=turns,
    )
