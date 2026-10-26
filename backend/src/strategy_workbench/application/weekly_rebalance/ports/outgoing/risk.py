"""Outgoing port: S07 리스크 가드의 한도와 기록.

한도는 전략 코드 밖 한도 파일 하나에서 읽는다. 판정(주문안마다 한 번)·하루 점검·사람의 명령은
작업 폴더 `risk/`에 남긴다. 읽지 못하면 `RiskUnavailableError` — 판정을 못 하면 주문하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from strategy_workbench.domain.risk.facade.risk import (
    DrawdownState,
    GuardedTargets,
    PostTradeReview,
    RiskAction,
    RiskLevel,
    RiskLimits,
)

__all__ = ["RiskDay", "RiskStore", "RiskUnavailableError", "RiskVerdict"]


class RiskUnavailableError(Exception):
    """한도 파일이나 리스크 기록을 읽지 못했다."""


@dataclass(frozen=True)
class RiskVerdict:
    """주문안을 만들 때의 리스크 판정. 주문안마다 한 번만 쓰고 실행기는 이 목표로 낸다.

    - `violations_since_last`: 바로 앞 주문안 뒤 체결 후 점검이 찾은 위반(주문지 한 줄).
    - `plan_created_at`: 판정한 주문안의 만든 시각. 실행기는 주문안과 같을 때만 이 판정으로 낸다.
    - `close_from_plan`: 신호일 장 마감 기록이 없어 주문안 총자산을 그날 장 마감으로 접었다.
    """

    plan_id: str
    created_at: datetime
    limits_version: int
    limits_sha256: str
    state: DrawdownState
    guarded: GuardedTargets
    violations_since_last: tuple[str, ...] = ()
    plan_created_at: datetime | None = None
    close_from_plan: bool = False


@dataclass(frozen=True)
class RiskDay:
    """장 마감 때의 낙폭 단계와 체결 후 점검. 그날 낸 주문이 없으면 `review`는 None이다."""

    day: date
    recorded_at: datetime
    limits_version: int
    limits_sha256: str
    previous_level: RiskLevel
    state: DrawdownState
    review: PostTradeReview | None


class RiskStore(Protocol):
    def limits(self) -> RiskLimits: ...

    def actions(self) -> tuple[RiskAction, ...]:
        """사람의 명령(초기화·정지 해제), 남긴 순서대로."""
        ...

    def add_action(self, action: RiskAction) -> None: ...

    def save_verdict(self, verdict: RiskVerdict) -> None:
        """이미 있으면 `PlanExistsError`, 쓰지 못하면 `RiskUnavailableError`."""
        ...

    def verdict(self, plan_id: str) -> RiskVerdict | None: ...

    def save_day(self, record: RiskDay) -> None:
        """그날 마지막 장 마감 점검으로 갈아 끼운다."""
        ...

    def days(self) -> tuple[RiskDay, ...]: ...
