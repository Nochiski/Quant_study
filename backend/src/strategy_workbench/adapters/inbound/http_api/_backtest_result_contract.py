"""완료 run 결과의 HTTP 응답 모양(`GET /api/v1/backtests/{run_id}/result`).

저장 결과(`BacktestRunResult`)에서 세션마다 종목 수만큼 쌓이는 보유 기록(`positions`)만 빼고 그 수를
싣는다. 보유는 세션 하나씩 `holdings/sessions/{session}` 이 정본으로 답하고, 결과 화면은 보유 기록의
수만 보인다 — 긴 run 에서는 보유 기록이 결과 JSON 의 대부분(6.7MB 중 5.4MB)이라 결과를 열 때마다
직렬화·전송·브라우저 해석을 그만큼 했다. 나머지 칸은 저장 결과 그대로다.
"""

from __future__ import annotations

from dataclasses import dataclass

from strategy_workbench.domain.analytics.facade.metrics import MetricDefinition, MetricValue
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    BacktestSeries,
    RawArtifactBundle,
    RawCorporateAction,
    RawCost,
    RawFill,
    RawOrder,
    RawOrderUpdate,
    RawRounding,
    RawSnapshot,
    RawTrade,
    RunManifest,
)


@dataclass(frozen=True)
class BacktestResultArtifacts:
    """`RawArtifactBundle` 에서 `positions` 대신 그 수(`position_count`)를 실은 원장 묶음.

    값은 늘 저장 결과에서 채운다. 기본값은 원장 묶음과 같은 칸을 응답 스키마에서도 같은 선택 칸으로
    두려는 것이다 — 계약 변경을 `positions` → `position_count` 하나로 한정한다. 원장 묶음의 칸·
    타입·기본값을 바꾸면 여기도 함께 바꾼다(어긋나면 `test_backtest_result_contract` 가 실패한다).
    """

    snapshots: tuple[RawSnapshot, ...]
    position_count: int
    orders: tuple[RawOrder, ...]
    fills: tuple[RawFill, ...]
    costs: tuple[RawCost, ...]
    trades: tuple[RawTrade, ...]
    roundings: tuple[RawRounding, ...] = ()
    schema_version: str = RawArtifactBundle.schema_version
    order_updates: tuple[RawOrderUpdate, ...] | None = None
    corporate_actions: tuple[RawCorporateAction, ...] | None = None


@dataclass(frozen=True)
class BacktestResult:
    manifest: RunManifest
    metric_definitions: tuple[MetricDefinition, ...]
    metrics: tuple[MetricValue, ...]
    series: BacktestSeries
    artifacts: BacktestResultArtifacts


def backtest_result(result: BacktestRunResult) -> BacktestResult:
    """저장 결과를 응답 모양으로 옮긴다. `positions` 를 그 수로 바꾸고 나머지 칸은 그대로 싣는다."""
    bundle = result.artifacts
    return BacktestResult(
        manifest=result.manifest,
        metric_definitions=result.metric_definitions,
        metrics=result.metrics,
        series=result.series,
        artifacts=BacktestResultArtifacts(
            snapshots=bundle.snapshots,
            position_count=len(bundle.positions),
            orders=bundle.orders,
            fills=bundle.fills,
            costs=bundle.costs,
            trades=bundle.trades,
            roundings=bundle.roundings,
            schema_version=bundle.schema_version,
            order_updates=bundle.order_updates,
            corporate_actions=bundle.corporate_actions,
        ),
    )
