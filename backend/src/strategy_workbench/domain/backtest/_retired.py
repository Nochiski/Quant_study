"""은퇴 문서의 실행 설정 원문을 `RunEnvironment` 로 바꾼다(spec D6·D7, P2-09).

1.1 까지는 시장·기간·유니버스·체결·비용·결측이 전략 문서 안(`data`·`execution`·팩터마다의
`graph.missing_policy`)에 있었다. 업그레이더(`domain/strategy/_upgrade.py`)가 1.1 → 1.2 단계에서
그 값을 문서에서 떼어 원문 그대로(`RetiredExecutionSettings`) 돌려주고, 실행 설정으로 바꾸는 규칙은
`RunEnvironment` 의 owner 인 이 노드가 갖는다 — `domain.strategy` 가 이 노드를 import 하면 기존
반대 방향과 순환이 된다(P2-01 브리지와 같은 배치).

읽을 수 없는 값은 예외가 아니라 결과 값(`RetiredEnvironment.problems`)이다. 업그레이드 자체는
성공하고 사용자가 실행 설정을 직접 채우면 되는 예상된 실패라서다. 기본값을 지어내지 않는다:
필수 값(기간·유니버스)이 없거나 하나라도 읽히지 않으면 실행 설정 전체를 비운다 — 일부만 채운
실행 설정은 사용자가 지정한 적 없는 값을 사실처럼 보인다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import TypedDict, TypeVar

from strategy_workbench.domain.factor.facade.expression import MissingPolicy
from strategy_workbench.domain.strategy.facade.document import RetiredExecutionSettings

from ._models import DataFrequency, ExecutionTiming, Market, RunEnvironment

# 1.1 문서의 두 섹션에 있던 키. 1.1 모델(`DataStep`·`ExecutionStep`)은 지워졌고 이 모양은 더
# 바뀌지 않는 과거 사실이다. 키 이름이 `RunEnvironment` 필드 이름과 같다.
_RETIRED_SECTION_FIELDS: Mapping[str, tuple[str, ...]] = {
    "data": ("market", "frequency", "start", "end", "universe_id"),
    "execution": ("timing", "participation_rate", "fee_bps", "slippage_bps"),
}

_Parsed = TypeVar("_Parsed")
_Choice = TypeVar("_Choice", bound=StrEnum)


class _OptionalArguments(TypedDict, total=False):
    """기본값이 있는 `RunEnvironment` 인자. pyright 가 모델 시그니처와 대조한다."""

    market: Market
    frequency: DataFrequency
    timing: ExecutionTiming
    participation_rate: float
    fee_bps: float
    slippage_bps: float
    missing: MissingPolicy


@dataclass(frozen=True)
class RetiredEnvironmentProblem:
    """옮기지 못한 값 하나. `pointer` 는 은퇴 문서 기준(`/data/start`), `message` 는 한글 문장."""

    pointer: str
    message: str


@dataclass(frozen=True)
class RetiredEnvironment:
    """변환 결과. 실행 설정과 문제 목록 중 정확히 하나만 있다."""

    environment: RunEnvironment | None
    problems: tuple[RetiredEnvironmentProblem, ...]

    def __post_init__(self) -> None:
        if (self.environment is None) == (not self.problems):
            raise ValueError(
                "retired environment result must carry exactly one of environment or problems — "
                f"environment={self.environment!r} problems={len(self.problems)}"
            )


def _unreadable(
    pointer: str, field: str, value: object, expected: str
) -> RetiredEnvironmentProblem:
    return RetiredEnvironmentProblem(
        pointer,
        "예전 문서의 이 값은 실행 설정으로 읽을 수 없어 옮기지 못했습니다. 실행 설정에서 직접 "
        f"채우세요 — field={field} got={value!r} expected={expected}",
    )


def _choices(kind: type[StrEnum]) -> str:
    return f"one of {[member.value for member in kind]}"


def _choice(kind: type[_Choice]) -> Callable[[object], _Choice | None]:
    def parse(value: object) -> _Choice | None:
        if isinstance(value, str) and value in {member.value for member in kind}:
            return kind(value)
        return None

    return parse


def _date(value: object) -> date | None:
    # datetime 은 date 의 하위 타입이지만 날짜 필드에 시각이 붙은 값은 다른 값이다.
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


class _Reader:
    """섹션별 원문 값을 읽고, 있는데 읽히지 않은 값과 모르는 키를 문제로 모은다."""

    def __init__(self, settings: RetiredExecutionSettings) -> None:
        self._sections: Mapping[str, Mapping[str, object]] = {
            "data": settings.data_section,
            "execution": settings.execution_section,
        }
        self.problems: list[RetiredEnvironmentProblem] = []
        for section, content in self._sections.items():
            allowed = _RETIRED_SECTION_FIELDS[section]
            for field in content:
                if field not in allowed:
                    self.problems.append(
                        RetiredEnvironmentProblem(
                            f"/{section}/{field}",
                            "예전 실행 설정에 없던 키라 실행 설정으로 옮기지 못했습니다. "
                            "실행 설정에서 확인하세요 — "
                            f"got={field!r} section={section} allowed={list(allowed)}",
                        )
                    )

    def read(
        self, section: str, field: str, parse: Callable[[object], _Parsed | None], expected: str
    ) -> _Parsed | None:
        content = self._sections[section]
        if field not in content:
            return None
        parsed = parse(content[field])
        if parsed is None:
            self.problems.append(
                _unreadable(f"/{section}/{field}", field, content[field], expected)
            )
        return parsed

    def require(self, field: str, value: object | None) -> None:
        """기본값이 없는 필드가 빠졌으면 문제로 남긴다(읽히지 않아 이미 남긴 자리는 한 번만)."""
        pointer = f"/data/{field}"
        if value is None and all(problem.pointer != pointer for problem in self.problems):
            self.problems.append(
                RetiredEnvironmentProblem(
                    pointer,
                    "예전 문서에 필수 실행 설정이 없어 옮기지 못했습니다. 실행 설정에서 직접 "
                    f"채우세요 — missing={field!r} required=['start', 'end', 'universe_id']",
                )
            )


def environment_from_retired_settings(settings: RetiredExecutionSettings) -> RetiredEnvironment:
    """은퇴 문서가 갖고 있던 실행 설정 원문으로 `RunEnvironment` 를 만든다.

    생략한 키는 `RunEnvironment` 기본값이 된다(1.1 모델의 기본값과 같다). 범위·순서 규칙은
    `RunEnvironment.__post_init__` 하나가 판정하고 여기서 다시 적지 않는다.
    """
    reader = _Reader(settings)
    optional = _OptionalArguments()
    market = reader.read("data", "market", _choice(Market), _choices(Market))
    frequency = reader.read("data", "frequency", _choice(DataFrequency), _choices(DataFrequency))
    start = reader.read("data", "start", _date, "YYYY-MM-DD")
    end = reader.read("data", "end", _date, "YYYY-MM-DD")
    universe_id = reader.read("data", "universe_id", _text, "text")
    timing = reader.read("execution", "timing", _choice(ExecutionTiming), _choices(ExecutionTiming))
    participation_rate = reader.read("execution", "participation_rate", _number, "number")
    fee_bps = reader.read("execution", "fee_bps", _number, "number")
    slippage_bps = reader.read("execution", "slippage_bps", _number, "number")
    if market is not None:
        optional["market"] = market
    if frequency is not None:
        optional["frequency"] = frequency
    if timing is not None:
        optional["timing"] = timing
    if participation_rate is not None:
        optional["participation_rate"] = participation_rate
    if fee_bps is not None:
        optional["fee_bps"] = fee_bps
    if slippage_bps is not None:
        optional["slippage_bps"] = slippage_bps
    if settings.missing_policy is not None:
        missing = _choice(MissingPolicy)(settings.missing_policy)
        if missing is None:
            reader.problems.append(
                _unreadable(
                    settings.missing_policy_pointer or "",
                    "missing",
                    settings.missing_policy,
                    _choices(MissingPolicy),
                )
            )
        else:
            optional["missing"] = missing
    reader.require("start", start)
    reader.require("end", end)
    reader.require("universe_id", universe_id)
    if reader.problems or start is None or end is None or universe_id is None:
        return RetiredEnvironment(None, tuple(reader.problems))
    try:
        environment = RunEnvironment(start=start, end=end, universe_id=universe_id, **optional)
    except ValueError as error:
        return RetiredEnvironment(
            None,
            (
                RetiredEnvironmentProblem(
                    "",
                    "예전 문서의 실행 설정 값이 실행 설정 규칙에 맞지 않아 옮기지 못했습니다. "
                    f"실행 설정에서 직접 채우세요 — {error}",
                ),
            ),
        )
    return RetiredEnvironment(environment, ())
