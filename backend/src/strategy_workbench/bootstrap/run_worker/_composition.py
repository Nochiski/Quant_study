"""run worker 프로세스의 composition root(run-worker spec D2·D10).

부모가 job 에 실어 보낸 설정(equity 어댑터 종류·루트, 산출물 루트)으로 run 계산 하나를 조립한다.
환경 변수를 다시 읽지 않고, 서버 composition root 의 생성 함수를 그대로 쓴다.
`BacktestRunService` 는 만들지 않는다 — 만들면 부팅 복구가 부모의 도는 run 을 닫는다.
"""

from __future__ import annotations

import logging
import sys

from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.run_worker_process.facade.worker import (
    WorkerBoot,
    WorkerSettings,
    serve_stdio,
)
from strategy_workbench.application.backtest_run.facade.computation import RunComputation
from strategy_workbench.bootstrap.facade.container import (
    build_equity_data,
    build_portfolio_design,
    build_run_versions,
)
from strategy_workbench.domain.analytics.facade.metrics import build_default_metric_registry
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry


def boot(settings: WorkerSettings) -> WorkerBoot:
    equity_data = build_equity_data(settings.equity_adapter, settings.equity_root)
    factor_registry = build_default_factor_registry()
    metric_registry = build_default_metric_registry()
    executor = BacktestEngineExecutorAdapter(metric_registry)
    # 지문을 재기 전에 엔진 확장을 올린다. 그래야 계산이 쓰는 확장이 지문을 잰 파일과 같거나 그보다
    # 오래되고, 사이에 바뀌었으면 판본 불일치로 멈춘다 — 예비가 기다리는 동안 바뀐 확장을 대조 없이
    # 쓰지 않는다(spec D6·D7, #591 리뷰 P2-1).
    executor.load_kernel()
    computation = RunComputation(
        build_portfolio_design(equity_data, BacktestEnginePortfolioAdapter(), factor_registry),
        equity_data,
        executor,
        # staging 정리는 부르지 않는다 — 같은 루트의 다른 run staging 을 지운다(spec D4).
        LocalArtifactStore(settings.artifact_root),
    )
    return WorkerBoot(
        computation, build_run_versions(equity_data, factor_registry, metric_registry)
    )


def main() -> int:
    # stderr 는 로그다. 부모가 줄 머리의 레벨을 읽어 run_id 를 붙여 중계한다(spec D3).
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    return serve_stdio(boot)
