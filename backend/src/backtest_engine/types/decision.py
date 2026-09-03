"""전략의 반환형 StrategyDecision.

decision은 판단 시각과 이유까지 묶는 바깥 봉투이고,
action은 실제로 무엇을 원하는지 나타내는 내용물이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backtest_engine.types.actions import NoAction, StrategyAction

SCHEMA_VERSION = 1
"""Python/Rust 직렬화 계약 버전. 필드가 바뀌면 올린다."""


@dataclass(frozen=True)
class StrategyDecision:
    schema_version: int
    as_of: datetime
    actions: tuple[StrategyAction, ...]
    reason: str | None = None

    @classmethod
    def of(
        cls,
        as_of: datetime,
        action: StrategyAction,
        reason: str | None = None,
    ) -> StrategyDecision:
        """단일 Action도 항상 tuple envelope로 감싼다."""
        return cls(schema_version=SCHEMA_VERSION, as_of=as_of, actions=(action,), reason=reason)

    @classmethod
    def no_action(cls, as_of: datetime, reason: str | None = None) -> StrategyDecision:
        return cls(
            schema_version=SCHEMA_VERSION,
            as_of=as_of,
            actions=(NoAction(reason),),
            reason=reason,
        )
