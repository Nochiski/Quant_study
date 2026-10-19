"""run worker 프로세스 전용 조립. 서버 composition root 의 생성 함수를 재사용한다."""

DEPENDS_ON: tuple[str, ...] = (
    "application.backtest_run",
    "adapters.outbound.artifact_local",
    "adapters.outbound.backtest_engine",
    "adapters.outbound.engine_portfolio",
    "adapters.outbound.run_worker_process",
    "bootstrap",
    "domain.analytics",
    "domain.factor",
)
