"""S07 리스크 가드의 조회와 사람의 명령(단계 보기·고점 초기화·정지 해제).

낙폭 단계는 저장하지 않고 장 마감 기록(대사가 맞은 날)과 사람의 명령으로 매번 다시 낸다.
명령은 주문을 보내지 않고 리스크 기록에만 남는다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, tzinfo
from enum import StrEnum

from strategy_workbench.application.weekly_rebalance.ports.outgoing.records import (
    DayRecord,
    ExecutionLog,
)
from strategy_workbench.application.weekly_rebalance.ports.outgoing.risk import (
    RiskStore,
    RiskUnavailableError,
)
from strategy_workbench.domain.risk.facade.risk import (
    ActionKind,
    DayClose,
    DrawdownState,
    RiskAction,
    RiskLevel,
    RiskLimits,
    drawdown_state,
)

# 정규장 마감. 장 마감 기록은 기록한 시각이 아니라 그날 이 시각으로 사람 명령과 순서를 맞춘다 —
# 같은 날 close 를 다시 돌려도 그 사이 남긴 해제·초기화가 장 마감보다 앞으로 밀리지 않는다
# (리뷰 P2-1).
CLOSE_TIME = time(15, 30)


def close_at(day: date, tz: tzinfo | None) -> datetime:
    return datetime.combine(day, CLOSE_TIME, tz)


def day_closes(days: Sequence[DayRecord]) -> list[DayClose]:
    """대사가 맞은 날의 장 마감 총자산. 어긋난 날은 고점·낙폭에 넣지 않는다."""
    return [
        DayClose(day.day, close_at(day.day, day.recorded_at.tzinfo), day.equity_krw)
        for day in days
        if day.matched
    ]


@dataclass(frozen=True)
class JudgedState:
    """`from_plan`이 참이면 그날 장 마감 기록이 없어 주문안 총자산을 그날 장 마감으로 접었다."""

    state: DrawdownState
    from_plan: bool = False


def current_state(
    risk: RiskStore,
    log: ExecutionLog,
    limits: RiskLimits,
    *,
    before: datetime | None = None,
    close_on: tuple[date, int, tzinfo | None] | None = None,
) -> JudgedState:
    """지금 낙폭 단계. 하루 기록을 읽지 못하면 `RiskUnavailableError`.

    - `before`: 이 시각 앞의 장 마감·명령만 접는다(그날 장 마감으로 단계가 바뀌었나 볼 때).
    - `close_on` (날짜, 총자산, 시간대): 그날 대사가 맞은 장 마감 기록이 없으면 이 총자산을 그날 장
      마감으로 접는다. 신호일 close 가 실패해도 낡은 단계로 판정하지 않는다(리뷰 P1-1).
    """
    try:
        closes = day_closes(log.days())
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RiskUnavailableError(f"day records unreadable — error={error!r}") from error
    actions = risk.actions()
    if before is not None:
        closes = [close for close in closes if close.at < before]
        actions = tuple(action for action in actions if action.at < before)
    from_plan = False
    if close_on is not None and all(close.day != close_on[0] for close in closes):
        day, equity_krw, tz = close_on
        closes = [*closes, DayClose(day, close_at(day, tz), equity_krw)]
        from_plan = True
    return JudgedState(drawdown_state(closes, actions, limits.drawdown), from_plan)


class RiskCommandStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    NO_CLOSE = "no_close"  # 초기화할 장 마감 기록이 없다
    NOT_HALTED = "not_halted"  # 정지 단계가 아니라 풀 것이 없다
    INVALID = "invalid"  # 명령 입력이 맞지 않는다(빈 이름 등)


@dataclass(frozen=True)
class RiskStatusResult:
    status: RiskCommandStatus
    limits: RiskLimits | None = None
    state: DrawdownState | None = None
    detail: str = ""


class RiskGuardService:
    def __init__(self, risk: RiskStore, log: ExecutionLog, *, now: Callable[[], datetime]) -> None:
        self._risk = risk
        self._log = log
        self._now = now

    def status(self) -> RiskStatusResult:
        try:
            limits = self._risk.limits()
            return RiskStatusResult(
                RiskCommandStatus.OK, limits, current_state(self._risk, self._log, limits).state
            )
        except RiskUnavailableError as error:
            return RiskStatusResult(RiskCommandStatus.UNAVAILABLE, detail=str(error))

    def reset(self, operator: str, note: str) -> RiskStatusResult:
        """고점을 마지막 장 마감 총자산으로 바꾸고 정상 단계로 돌린다(입출금 반영 등)."""
        before = self.status()
        if before.state is None:
            return before
        if before.state.last_equity_krw is None:
            return RiskStatusResult(
                RiskCommandStatus.NO_CLOSE, before.limits, before.state, "장 마감 기록이 없다"
            )
        peak_krw = before.state.last_equity_krw
        return self._apply(
            lambda: RiskAction(self._now(), ActionKind.RESET, operator, note, peak_krw)
        )

    def resume(self, operator: str, note: str) -> RiskStatusResult:
        """신규 매수 정지를 풀어 축소 단계로 돌린다(2026-10-26 사용자 결정 D2: 사람만 푼다)."""
        before = self.status()
        if before.state is None:
            return before
        if before.state.level is not RiskLevel.HALT:
            return RiskStatusResult(
                RiskCommandStatus.NOT_HALTED,
                before.limits,
                before.state,
                f"정지 단계가 아니다 — level={before.state.level.value}",
            )
        return self._apply(lambda: RiskAction(self._now(), ActionKind.RESUME, operator, note))

    def _apply(self, make: Callable[[], RiskAction]) -> RiskStatusResult:
        try:
            action = make()
        except ValueError as error:
            return RiskStatusResult(RiskCommandStatus.INVALID, detail=str(error))
        try:
            self._risk.add_action(action)
        except RiskUnavailableError as error:
            return RiskStatusResult(RiskCommandStatus.UNAVAILABLE, detail=str(error))
        return self.status()
