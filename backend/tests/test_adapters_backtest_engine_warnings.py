from __future__ import annotations

from datetime import timedelta

from strategy_workbench.adapters.outbound.backtest_engine._adapter import _base_rate_warnings
from strategy_workbench.domain.analytics.facade.metrics import BASE_RATE_CONFIRMED_ON


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
