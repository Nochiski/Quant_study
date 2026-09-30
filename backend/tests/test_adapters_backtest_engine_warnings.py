from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from strategy_workbench.adapters.outbound.backtest_engine._adapter import (
    _base_rate_warnings,
    _participation_warnings,
)
from strategy_workbench.domain.analytics.facade.metrics import BASE_RATE_CONFIRMED_ON
from strategy_workbench.domain.backtest.facade.environment import (
    ParticipationBasis,
    RunEnvironment,
)


def test_base_rate_warning_lists_sessions_after_the_confirmed_date() -> None:
    # domain 이 준 확인일 뒤 세션을 경고 문장으로 옮길 뿐 판단하지 않는다(#274).
    carried = (BASE_RATE_CONFIRMED_ON + timedelta(days=1),)

    (warning,) = _base_rate_warnings(carried)

    assert warning.code == "analytics.base_rate_carried_forward"
    assert (
        f"confirmed_on={BASE_RATE_CONFIRMED_ON.isoformat()} carried_sessions=1 "
        f"sessions={carried[0].isoformat()}"
    ) in warning.message
    assert _base_rate_warnings(()) == ()


def test_session_volume_participation_is_flagged_as_optimistic() -> None:
    # #342 DOMAIN-V2-02: 체결일 거래량 기준은 체결 수량 한도에 look-ahead 가 있어 결과에
    # 경고를 싣는다.
    default = RunEnvironment(start=date(2021, 1, 4), end=date(2021, 6, 30), universe_id="u")
    session = replace(default, participation_basis=ParticipationBasis.SESSION_VOLUME)

    assert _participation_warnings(default) == ()
    (warning,) = _participation_warnings(session)
    assert warning.code == "participation.session_volume"
    assert "낙관 쪽" in warning.message
    assert "participation_basis=session_volume participation_rate=0.1" in warning.message
