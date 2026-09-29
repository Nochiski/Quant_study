"""검증 랩 V3-02: 실행 요청의 파라미터 값이 문서 기본값 대신 팩터·tape·실행 지문까지 간다(spec D4).

전략은 `3세션 모멘텀 × scale` 한 팩터로 한 종목만 고른다. `scale` 기본값은 1 이고, -1 을 주면
팩터 값의 부호가 뒤집혀 모멘텀이 가장 낮은 종목이 뽑힌다 — 정답은 기본값 실행의 팩터 값에서
손으로 셈한다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestParameterValueError,
    BacktestRunService,
    BacktestRunSpec,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioDesignService,
    PortfolioPipelineOptions,
    PortfolioPipelineResult,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.application.strategy_design.facade.ports import PageRequest
from strategy_workbench.domain.analytics.facade.metrics import build_default_metric_registry
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import ExecutionCore
from strategy_workbench.domain.factor.facade.expression import (
    BinaryNode,
    BinaryOperator,
    FactorGraph,
    FieldNode,
    ParameterNode,
    TimeSeriesNode,
    TimeSeriesOperator,
)
from strategy_workbench.domain.strategy.facade.specification import (
    FactorDirection,
    FactorSignal,
    FloatParameter,
    ParameterValue,
    RebalanceFrequency,
    StrategySpec,
    strategy_spec_hash,
)
from tests.backtest_run_wait import wait_for_terminal_run

_ENVIRONMENT = RunEnvironment(
    start=date(2024, 1, 8), end=date(2024, 1, 12), universe_id="krx.common-stock"
)


def _spec() -> StrategySpec:
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).template()
    scaled_momentum = FactorSignal(
        factor_id="scaled_momentum",
        label="3세션 모멘텀 × scale",
        direction=FactorDirection.HIGH,
        weight=1.0,
        graph=FactorGraph(
            nodes=(
                FieldNode("close", "price.close", "field"),
                TimeSeriesNode("mom", TimeSeriesOperator.MOMENTUM, "close", 3, "time_series"),
                ParameterNode("scale", "scale", "parameter"),
                BinaryNode("scaled", BinaryOperator.MULTIPLY, "mom", "scale", "binary"),
            ),
            output_node_id="scaled",
        ),
    )
    return replace(
        template,
        factors=(scaled_momentum,),
        parameters=(FloatParameter("scale", 1.0, -1.0, 1.0, "float"),),
        portfolio=replace(
            template.portfolio,
            selection_count=1,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
        ),
    )


def _portfolio() -> PortfolioDesignService:
    adapter = MockEquityDataAdapter.demo()
    return PortfolioDesignService(
        adapter,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=adapter,
        factor_registry_version="test-registry",
    )


def _pipeline(parameter_values: dict[str, ParameterValue]) -> PortfolioPipelineResult:
    return _portfolio().run_pipeline(
        PortfolioPreviewRequest(_spec(), environment=_ENVIRONMENT),
        options=PortfolioPipelineOptions(parameter_values=parameter_values),
    )


def _runs(tmp_path: Path, *run_ids: str) -> BacktestRunService:
    return BacktestRunService(
        _portfolio(),
        InMemoryStrategyRepository(),
        MockEquityDataAdapter.demo(),
        BacktestEngineExecutorAdapter(build_default_metric_registry()),
        LocalArtifactStore(tmp_path),
        new_id=iter(run_ids).__next__,
    )


def _request(**parameter_values: ParameterValue) -> BacktestRunSpec:
    return BacktestRunSpec(
        strategy=_spec(),
        core=ExecutionCore.PYTHON,
        environment=_ENVIRONMENT,
        parameter_values=parameter_values,
    )


def test_requested_value_replaces_the_document_default_in_factor_values_and_tape() -> None:
    default = _pipeline({})
    flipped = _pipeline({"scale": -1.0})

    # 팩터 값 = 모멘텀 × scale 이므로 scale=-1 의 값은 기본값(scale=1) 값의 부호만 바꾼 것이다.
    base_values = {
        (item.as_of, item.security_id): item.value
        for item in default.factor_evaluations[0].values
        if item.value is not None
    }
    assert base_values
    assert {
        (item.as_of, item.security_id): item.value
        for item in flipped.factor_evaluations[0].values
        if item.value is not None
    } == {key: -value for key, value in base_values.items()}

    # 한 종목만 고르므로 첫 리밸런스는 기본값에서 모멘텀 최대, scale=-1 에서 최소 종목이다.
    frame = default.preview.tape.frames[0]
    eligible = {
        candidate.security_id: base_values[(frame.signal_as_of, candidate.security_id)]
        for candidate in frame.candidates
        if candidate.eligible
    }
    assert len(eligible) > 1
    highest = max(eligible, key=eligible.__getitem__)
    lowest = min(eligible, key=eligible.__getitem__)
    assert [target.security_id for target in frame.targets] == [highest]
    flipped_frame = flipped.preview.tape.frames[0]
    assert flipped_frame.signal_as_of == frame.signal_as_of
    assert [target.security_id for target in flipped_frame.targets] == [lowest]
    assert flipped.preview.tape.tape_hash != default.preview.tape.tape_hash
    # 파라미터 값은 전략 문서를 바꾸지 않는다 — tape 의 전략 hash 는 그대로다.
    assert flipped.preview.tape.strategy_hash == default.preview.tape.strategy_hash


def test_run_records_resolved_values_and_splits_the_fingerprint_not_the_provenance(
    tmp_path: Path,
) -> None:
    runs = _runs(tmp_path, "default-run", "flipped-run")
    runs.start(_request())
    # 실수 칸에 정수 -1 을 보내도 선언 타입(float)으로 맞춰 싣는다.
    runs.start(_request(scale=-1))
    for run_id in ("default-run", "flipped-run"):
        assert wait_for_terminal_run(runs, run_id).status.value == "completed"

    default = runs.result("default-run").manifest
    flipped = runs.result("flipped-run").manifest
    # 생략한 파라미터도 문서 기본값으로 해소돼 매니페스트에 남는다.
    assert default.run_spec.parameter_values == {"scale": 1.0}
    assert flipped.run_spec.parameter_values == {"scale": -1.0}
    assert type(flipped.run_spec.parameter_values["scale"]) is float
    assert flipped.run_fingerprint != default.run_fingerprint
    assert flipped.target_tape_hash != default.target_tape_hash
    # 저장·인라인 어느 쪽이든 실행한 문서는 같다: provenance 의 spec_hash 는 값과 무관하다.
    assert (
        flipped.strategy_provenance.spec_hash
        == default.strategy_provenance.spec_hash
        == strategy_spec_hash(_spec())
    )
    # 재제출용 원본 요청은 해소 전 값을 그대로 돌려준다.
    assert runs.request("default-run").parameter_values == {}


@pytest.mark.parametrize(
    ("parameter_values", "parameter_id", "reason"),
    [
        ({"missing": 1.0}, "missing", "declared=['scale']"),
        ({"scale": 1.5}, "scale", "value=1.5 kind=float minimum=-1.0 maximum=1.0"),
        ({"scale": True}, "scale", "value=True kind=float"),
        ({"scale": "1"}, "scale", "value='1' kind=float"),
    ],
)
def test_unresolvable_values_are_refused_before_the_run_is_queued(
    tmp_path: Path,
    parameter_values: dict[str, ParameterValue],
    parameter_id: str,
    reason: str,
) -> None:
    runs = _runs(tmp_path, "never")

    with pytest.raises(BacktestParameterValueError) as caught:
        runs.start(_request(**parameter_values))

    assert caught.value.parameter_id == parameter_id
    assert f"parameter_id={parameter_id}" in str(caught.value)
    assert reason in str(caught.value)
    assert runs.list_runs(PageRequest(offset=0, limit=50)).total == 0
