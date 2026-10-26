"""S07 리스크 가드의 사람 명령(단계 보기·고점 초기화·정지 해제). 기록은 메모리 대역이다."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    RiskCommandStatus,
    RiskGuardService,
)
from strategy_workbench.domain.risk.facade.risk import ActionKind, RiskLevel
from tests.application.test_weekly_rebalance import KST, Log, Risk, close_record

SUNDAY = datetime(2026, 11, 1, 10, 0, tzinfo=KST)


def guard(*equities: int, risk: Risk | None = None) -> tuple[RiskGuardService, Risk]:
    log = Log()
    for index, equity in enumerate(equities):
        log.save_day(close_record(date(2026, 10, 26) + timedelta(days=index), equity))
    risk = risk or Risk()
    return RiskGuardService(risk, log, now=lambda: SUNDAY), risk


def test_status_folds_the_close_records_into_the_level() -> None:
    service, _ = guard(95_325_570, 86_000_000)
    result = service.status()
    assert result.status is RiskCommandStatus.OK and result.state is not None
    assert (result.state.level, result.state.loss_bp, result.state.last_equity_krw) == (
        RiskLevel.WARN,
        1_400,
        86_000_000,
    )
    assert result.limits is not None and result.limits.version == 1


def test_status_reports_unreadable_limits() -> None:
    service, _ = guard(95_000_000, risk=Risk(limits_=None))
    result = service.status()
    assert result.status is RiskCommandStatus.UNAVAILABLE and "limits.json" in result.detail


def test_resume_lifts_a_halt_into_reduce_and_is_recorded_with_the_operator() -> None:
    service, risk = guard(79_000_000)
    result = service.resume("동민", "전략 다시 봄")
    assert result.status is RiskCommandStatus.OK and result.state is not None
    assert result.state.level is RiskLevel.REDUCE
    [action] = risk.actions_
    assert (action.kind, action.operator, action.note, action.at) == (
        ActionKind.RESUME,
        "동민",
        "전략 다시 봄",
        SUNDAY,
    )


def test_resume_refuses_when_buying_is_not_halted() -> None:
    service, risk = guard(84_000_000)
    result = service.resume("동민", "")
    assert result.status is RiskCommandStatus.NOT_HALTED and risk.actions_ == []


def test_reset_moves_the_peak_to_the_last_close() -> None:
    service, risk = guard(100_000_000, 79_000_000)
    result = service.reset("동민", "입금 반영")
    assert result.status is RiskCommandStatus.OK and result.state is not None
    assert (result.state.level, result.state.peak_krw, result.state.loss_bp) == (
        RiskLevel.NORMAL,
        79_000_000,
        0,
    )
    assert risk.actions_[0].peak_krw == 79_000_000


def test_reset_needs_a_close_record() -> None:
    service, risk = guard()
    assert service.reset("동민", "").status is RiskCommandStatus.NO_CLOSE
    assert risk.actions_ == []
