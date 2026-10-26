"""계좌 고점 대비 낙폭 단계(S07 리스크 가드, 2026-10-26 사용자 결정).

단계는 따로 저장하지 않고 장 마감 총자산 기록과 사람의 명령(초기화·정지 해제)을 시각 순으로 접어
매번 다시 낸다. 같은 기록이면 몇 번을 물어도 같은 단계다.

- 고점: 장 마감 총자산의 최댓값. 첫 기록 전에는 한도 파일의 시작 고점(운용 시작 자본)이다.
- 낙폭(`loss_bp`): (고점 − 총자산) ÷ 고점을 bp로 내림한다. 정확히 −10%면 1,000bp다.
- 정상 → 경고(≥ warn) → 축소(≥ reduce). 축소는 `recover_bp`보다 작게 회복해야 풀린다(기준 근처에서
  사고팔기를 되풀이하지 않게).
- 정지는 낙폭이 halt 선을 새로 넘을 때 걸리고 저절로 풀리지 않는다. 사람이 풀면 축소 단계가 되고,
  다시 정지하려면 halt 선을 아래에서 위로 다시 넘어야 한다(2026-10-26 사용자 결정 D2).
- 초기화: 사람이 준 총자산을 새 고점으로 삼고 정상으로 돌아간다(입출금 반영 등).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from strategy_workbench.domain.risk._limits import BP, DrawdownLimits


class RiskLevel(StrEnum):
    NORMAL = "normal"
    WARN = "warn"
    REDUCE = "reduce"
    HALT = "halt"


class ActionKind(StrEnum):
    RESET = "reset"  # 고점을 주어진 총자산으로 바꾸고 정상으로
    RESUME = "resume"  # 정지를 풀어 축소 단계로


@dataclass(frozen=True)
class DayClose:
    """대사가 맞은 날의 장 마감 총자산. `at`은 기록한 시각이다(명령과 순서를 맞춘다)."""

    day: date
    at: datetime
    equity_krw: int


@dataclass(frozen=True)
class RiskAction:
    at: datetime
    kind: ActionKind
    operator: str
    note: str = ""
    peak_krw: int | None = None  # 초기화만: 새 고점

    def __post_init__(self) -> None:
        if not self.operator.strip():
            raise ValueError(f"risk action needs an operator — kind={self.kind.value}")
        if (self.kind is ActionKind.RESET) != (self.peak_krw is not None):
            raise ValueError(
                f"only a reset carries a peak — kind={self.kind.value} peak_krw={self.peak_krw}"
            )
        if self.peak_krw is not None and self.peak_krw <= 0:
            raise ValueError(f"reset peak must be positive — peak_krw={self.peak_krw}")


@dataclass(frozen=True)
class DrawdownState:
    level: RiskLevel
    peak_krw: int
    peak_day: date | None  # None: 아직 시작 고점 그대로
    loss_bp: int
    last_day: date | None  # 마지막으로 접은 장 마감일
    last_equity_krw: int | None


def drawdown_state(
    closes: Sequence[DayClose], actions: Sequence[RiskAction], limits: DrawdownLimits
) -> DrawdownState:
    peak, peak_day = limits.initial_peak_krw, None
    level, loss = RiskLevel.NORMAL, 0
    last: DayClose | None = None
    timeline: list[tuple[datetime, DayClose | RiskAction]] = [(c.at, c) for c in closes]
    timeline += [(a.at, a) for a in actions]
    for _, item in sorted(timeline, key=lambda pair: pair[0]):
        if isinstance(item, DayClose):
            if item.equity_krw > peak:
                peak, peak_day = item.equity_krw, item.day
            previous_loss = loss
            loss = max(peak - item.equity_krw, 0) * BP // peak
            level = _next_level(level, previous_loss, loss, limits)
            last = item
        elif item.kind is ActionKind.RESET and item.peak_krw is not None:
            peak, peak_day, level, loss = item.peak_krw, item.at.date(), RiskLevel.NORMAL, 0
        elif item.kind is ActionKind.RESUME and level is RiskLevel.HALT:
            level = RiskLevel.REDUCE
    return DrawdownState(
        level,
        peak,
        peak_day,
        loss,
        None if last is None else last.day,
        None if last is None else last.equity_krw,
    )


def exposure_bp(level: RiskLevel, limits: DrawdownLimits) -> int:
    """그 단계에서 목표 주식 수에 곱하는 비율."""
    if level in (RiskLevel.REDUCE, RiskLevel.HALT):
        return limits.reduced_exposure_bp
    return BP


def _next_level(
    level: RiskLevel, previous_loss: int, loss: int, limits: DrawdownLimits
) -> RiskLevel:
    if level is RiskLevel.HALT:
        return level
    if previous_loss < limits.halt_bp <= loss:
        return RiskLevel.HALT
    if level is RiskLevel.REDUCE and loss >= limits.recover_bp:
        return level
    if loss >= limits.reduce_bp:
        return RiskLevel.REDUCE
    return RiskLevel.WARN if loss >= limits.warn_bp else RiskLevel.NORMAL
