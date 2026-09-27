"""Outgoing port: 끝난 백테스트 실행의 결과를 읽는다 (결과 설명 spec R2).

구현은 bootstrap이 `BacktestRunService.result`를 감싸 주입한다. `StrategyCompilerPort`와 같은
방식이라 `assistant_chat`이 `backtest_run` 유스케이스에 의존하지 않는다. 포트가 돌려주는 값은
domain 타입(`BacktestRunResult`)이고, 모델에게 무엇을 얼마나 보일지는 application이 정한다.
"""

from __future__ import annotations

from typing import Protocol

from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult

__all__ = ["BacktestResultPort"]


class BacktestResultPort(Protocol):
    def completed_result(self, run_id: str) -> BacktestRunResult | None:
        """완료된 실행의 결과. 모르는 실행이거나 아직 끝나지 않았거나 실패했으면 None.

        None 하나로 묶는 이유는 어시스턴트 쪽 대응이 같기 때문이다 — 셋 다 설명할 결과가 없다.
        """
        ...
