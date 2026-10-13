"""결과 설명 테스트와 골든이 함께 쓰는 결정적 백테스트 결과 (결과 설명 spec R2).

실제 엔진을 돌리지 않고 결과 값 타입을 손으로 조립한다. 요약 규칙(무엇을 넣고 무엇을 덜어 내는가)을
보려는 것이라 숫자가 서로 공식으로 맞을 필요는 없다. 대신 날짜·값은 고정이라 골든
(`tests/fixtures/assistant/result_context.json`)이 실행마다 바뀌지 않는다.

`tools/export_assistant_prompts.py`도 이 모듈을 읽는다. 골든과 단위 테스트가 다른 표본을 보면
골든이 잠근 모양과 테스트가 확인한 모양이 갈린다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.analytics.facade.metrics import (
    DrawdownPoint,
    EquityCurvePoint,
    MetricScope,
    MetricUnavailableReason,
    MetricValue,
    MonthlyReturnPoint,
    build_default_metric_registry,
)
from strategy_workbench.domain.backtest.facade.environment import (
    RunEnvironment,
    environment_hash,
)
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    BacktestRunSpec,
    BacktestSeries,
    DataWarning,
    ExecutionCore,
    RawArtifactBundle,
    RawFill,
    RawOrder,
    RawSnapshot,
    RawTrade,
    RunManifest,
    StrategyProvenance,
    StrategySourceKind,
)

__all__ = ["SAMPLE_RUN_ID", "sample_backtest_result"]

SAMPLE_RUN_ID = "run-sample-001"

_TODAY = date(2026, 9, 1)
_FIRST_SESSION = date(2023, 1, 2)
_MONTHS = 40
_BENCHMARK = "sec-005930-1"
# schema 1.2 문서는 실행 설정을 담지 않는다(lang2 P2-03). 결과 설명이 요약할 실행 설정은 run 이
# 소유하므로 표본도 run 쪽에 명시한다 — 값은 1.1 템플릿이 만들던 5년 구간·기본 비용과 같다.
_ENVIRONMENT = RunEnvironment(start=date(2021, 9, 2), end=_TODAY, universe_id="krx.common-stock")

# full 구간 지표 값. registry에 있는 id만 쓴다 — 없는 id를 넣으면 요약이 정의 없이 옮기는 경로만
# 보게 된다.
_FULL_VALUES: dict[str, float | None] = {
    "total_return": 0.3412,
    "cagr": 0.0921,
    "volatility": 0.1874,
    "sharpe": 0.87,
    "sortino": 1.12,
    "max_drawdown": -0.2231,
    "calmar": 0.41,
    "turnover": 0.35,
    "max_drawdown_duration_sessions": 143.0,
    "max_drawdown_recovery_sessions": None,
    "benchmark_return": 0.1805,
    "excess_return": 0.1607,
    "trade_count": 4.0,
    "win_rate": 0.5,
    "profit_factor": 1.8,
}


def _month(index: int) -> tuple[int, int]:
    return _FIRST_SESSION.year + index // 12, index % 12 + 1


def sample_backtest_result() -> BacktestRunResult:
    """40개월짜리 완료 실행. 벤치마크·검증 구간 지표·데이터 경고·거래가 모두 있다."""
    registry = build_default_metric_registry()
    template = StrategyDesignService(
        InMemoryStrategyRepository(),
        new_id=lambda: "unused",
    ).template()
    strategy = replace(
        template,
        title="KRX 모멘텀 표본",
        description="최근 1년 수익률이 높은 종목을 매달 담는다.",
    )
    environment = _ENVIRONMENT
    created = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    metrics = tuple(
        MetricValue(
            metric_id,
            value,
            MetricScope.FULL,
            820,
            unavailable_reason=(
                MetricUnavailableReason.MAXIMUM_DRAWDOWN_NOT_RECOVERED if value is None else None
            ),
        )
        for metric_id, value in _FULL_VALUES.items()
    ) + (
        MetricValue("total_return", 0.0712, MetricScope.OUT_OF_SAMPLE, 120, "OOS 2026-03-02"),
        MetricValue("sharpe", 0.64, MetricScope.OUT_OF_SAMPLE, 120, "OOS 2026-03-02"),
    )
    equity = tuple(
        EquityCurvePoint(
            _FIRST_SESSION + timedelta(days=30 * index),
            100_000_000.0 * (1 + 0.0075 * index),
            100_000_000.0 * (1 + 0.0045 * index),
        )
        for index in range(_MONTHS)
    )
    drawdown = tuple(
        DrawdownPoint(point.session, -0.2231 if index == 17 else -0.01 * (index % 5))
        for index, point in enumerate(equity)
    )
    monthly = tuple(
        MonthlyReturnPoint(*_month(index), round(0.01 * ((index % 7) - 3), 4))
        for index in range(_MONTHS)
    )
    trades = tuple(
        RawTrade(
            security_id=f"sec-00{index}",
            opened_on=_FIRST_SESSION + timedelta(days=60 * index),
            closed_on=_FIRST_SESSION + timedelta(days=60 * index + 30),
            side="long",
            quantity="10",
            entry_price=100.0,
            exit_price=110.0,
            pnl=100.0,
            fees=1.5,
            slippage_cost=0.5,
        )
        for index in range(4)
    )
    orders = tuple(
        RawOrder(
            f"order-{index}",
            f"decision-{index}",
            _FIRST_SESSION,
            f"sec-00{index}",
            "buy",
            "10",
            "market",
            "day",
        )
        for index in range(6)
    )
    fills = tuple(
        RawFill(
            f"fill-{index}",
            f"order-{index}",
            _FIRST_SESSION,
            f"sec-00{index}",
            "buy",
            "10",
            100.0,
            1.5,
            0.05,
        )
        for index in range(5)
    )
    return BacktestRunResult(
        manifest=RunManifest(
            run_id=SAMPLE_RUN_ID,
            created_at=created,
            completed_at=created,
            engine_core=ExecutionCore.RUST,
            engine_version="test",
            run_fingerprint="fingerprint",
            run_spec=BacktestRunSpec(
                strategy,
                environment=environment,
                benchmark_security_id=_BENCHMARK,
            ),
            strategy_hash="spec-hash-sample",
            data_snapshot_id="snapshot",
            target_tape_hash="tape",
            metric_registry_version=registry.version,
            initial_cash=100_000_000.0,
            annualization_days=252,
            fee_bps=environment.fee_bps,
            slippage_bps=environment.slippage_bps,
            participation_rate=environment.participation_rate,
            environment=environment,
            environment_hash=environment_hash(environment),
            strategy_provenance=StrategyProvenance(
                StrategySourceKind.SAVED_REVISION,
                "spec-hash-sample",
                "1.2",
                strategy_id="strategy-sample",
                revision=2,
            ),
            warnings=(
                DataWarning(
                    "data.coverage.gap",
                    "2023-05 한 달 동안 일부 종목의 종가가 비어 있습니다.",
                ),
            ),
        ),
        metric_definitions=registry.definitions(),
        metrics=metrics,
        series=BacktestSeries(
            equity=equity,
            drawdown=drawdown,
            monthly_returns=monthly,
            rolling_sharpe=(),
            rolling_sharpe_window_sessions=126,
        ),
        artifacts=RawArtifactBundle(
            snapshots=(RawSnapshot(_FIRST_SESSION, 100.0, 100.0, 0.0, 0.0),),
            positions=(),
            orders=orders,
            fills=fills,
            costs=(),
            trades=trades,
        ),
    )
