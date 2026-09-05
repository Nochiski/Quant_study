"""전략 호출 일정 판정.

Capability 협상이 pre-run에 미구현 schedule을 거절하므로,
여기 도달하는 schedule은 구현된 것뿐이어야 한다.
"""

from __future__ import annotations

from datetime import datetime

from backtest_engine.errors import CapabilityNotImplemented
from backtest_engine.types.requirements import EverySession, MonthEndSession, Schedule


def matches(schedule: Schedule, ts: datetime) -> bool:
    """이 세션에 전략을 호출해야 하는지."""
    match schedule:
        case EverySession():
            return True
        case MonthEndSession():
            # 방어적 가드: prepare_strategy가 거절했어야 한다.
            raise CapabilityNotImplemented(
                f"MonthEndSession schedule reached the calendar despite capability gate — "
                f"ts={ts}. prepare_strategy must reject it before the data loop."
            )
