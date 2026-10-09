"""업그레이드한 1.1 문서의 미리보기가 1.1 의 결과와 같다(spec D7, P2-09).

1.1 의 합성은 원시값 가중 합이고, 1.2 의 새 문서 기본값은 순위 합성(`rank`)이다. 업그레이더가
`signal.normalization: none` 을 명시하지 않으면 단위가 다른 두 팩터(가격 모멘텀·시가총액)를 쓰는
1.1 전략이 업그레이드만으로 다른 종목을 고른다. 여기서는 1.1 원문 → 업그레이드 응답(원문·실행
설정) → 미리보기의 전 경로를 태워, 모든 후보의 합성 점수가 1.1 공식
`Σ(sign × weight × x) / Σ|weight|` 그대로인지 본다.

비중 방식은 `equal` 이다. `factor_score` 는 1.2 에서 비중 규칙이 바뀌어(P2-04 결정 5) 목표 비중이
1.1 과 달라도 회귀가 아니고, 업그레이드 응답이 그 사실을 warning 으로 알린다
(`tests/application/test_strategy_authoring_upgrade.py`).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioDesignService,
    PortfolioPipelineResult,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompileRequest,
    StrategyAuthoringService,
    UpgradedDocument,
)
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.factor.facade.expression import MissingPolicy
from strategy_workbench.domain.strategy.facade.document import SourceFormat
from strategy_workbench.domain.strategy.facade.specification import (
    FactorDirection,
    SignalNormalization,
    StrategySpec,
)

# 모멘텀(가격 비율)과 시가총액(원)은 단위가 달라 원시값 합과 순위 합이 서로 다른 순서를 낸다.
RETIRED_1_1 = """\
schema_version: "1.1"
title: "두 팩터 1.1 전략"
data:
  market: KRX
  start: "2024-01-08"
  end: "2024-01-12"
  universe_id: krx.common-stock
  frequency: daily
factors:
  - factor_id: momentum_3
    direction: high
    weight: 0.6
    graph:
      nodes:
        - kind: field
          node_id: close
          field_id: price.close
        - kind: time_series
          node_id: mom
          operator: momentum
          input_node_id: close
          window: 3
      output_node_id: mom
      missing_policy: zero
  - factor_id: size
    direction: low
    weight: 0.4
    graph:
      nodes:
        - kind: field
          node_id: cap
          field_id: price.market_cap
      output_node_id: cap
      missing_policy: zero
portfolio:
  selection_count: 3
  rebalance: every_n_sessions
  rebalance_every_n_sessions: 1
execution:
  timing: next_open
  fee_bps: 5.0
  slippage_bps: 2.0
"""


def _upgraded() -> UpgradedDocument:
    authoring = StrategyAuthoringService(
        RuamelDocumentCodec(), factor_registry_version="r", dataset_snapshot_id=lambda: "s"
    )
    return authoring.upgrade(CompileRequest(RETIRED_1_1, SourceFormat.YAML))


def _pipeline(spec: StrategySpec, environment: RunEnvironment) -> PortfolioPipelineResult:
    adapter = MockEquityDataAdapter.demo()
    service = PortfolioDesignService(
        adapter,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=adapter,
        factor_registry_version="test-registry",
    )
    return service.run_pipeline(PortfolioPreviewRequest(spec, environment=environment))


def _raw_weighted_sums(
    spec: StrategySpec, environment: RunEnvironment
) -> dict[tuple[date, str], float]:
    """1.1 공식으로 계산한 후보별 합성 점수(결측이 있는 후보는 뺀다)."""
    result = _pipeline(spec, environment)
    weights = {f.factor_id: (f.weight, f.direction) for f in spec.factors}
    denominator = sum(abs(weight) for weight, _ in weights.values())
    expected: dict[tuple[date, str], float] = {}
    for observation in result.observations:
        values = {v.factor_id: v.value for v in observation.factor_values}
        present = {fid: value for fid, value in values.items() if value is not None}
        if len(present) != len(weights):
            continue
        expected[(observation.as_of, observation.security_id)] = (
            sum(
                (1.0 if direction is FactorDirection.HIGH else -1.0) * weight * present[fid]
                for fid, (weight, direction) in weights.items()
            )
            / denominator
        )
    return expected


def _composites(spec: StrategySpec, environment: RunEnvironment) -> dict[tuple[date, str], float]:
    result = _pipeline(spec, environment)
    return {
        (frame.signal_as_of, decision.security_id): decision.composite_score
        for frame in result.preview.tape.frames
        for decision in frame.candidates
        if decision.composite_score is not None
    }


def test_the_upgrade_response_carries_the_1_1_execution_settings() -> None:
    upgraded = _upgraded()

    assert upgraded.compiled.spec is not None, upgraded.compiled.diagnostics
    assert upgraded.environment == RunEnvironment(
        start=date(2024, 1, 8),
        end=date(2024, 1, 12),
        universe_id="krx.common-stock",
        fee_bps=5.0,
        slippage_bps=2.0,
        missing=MissingPolicy.ZERO,
    )
    assert upgraded.compiled.spec.signal.normalization is SignalNormalization.NONE


def test_an_upgraded_1_1_document_scores_every_candidate_with_the_1_1_formula() -> None:
    upgraded = _upgraded()
    spec, environment = upgraded.compiled.spec, upgraded.environment
    assert spec is not None and environment is not None

    composites = _composites(spec, environment)
    expected = _raw_weighted_sums(spec, environment)

    assert composites, "no candidate was scored — the parity check ran on nothing"
    for key, composite in composites.items():
        assert composite == pytest.approx(expected[key], abs=1e-12), key


def test_without_the_explicit_normalization_the_same_document_scores_differently() -> None:
    """위 대조가 공허하지 않다: 명시를 빼면(1.2 기본값 `rank`) 같은 문서의 합성 점수가 달라진다."""
    upgraded = _upgraded()
    spec, environment = upgraded.compiled.spec, upgraded.environment
    assert spec is not None and environment is not None
    ranked = replace(spec, signal=replace(spec.signal, normalization=SignalNormalization.RANK))

    assert _composites(ranked, environment) != _composites(spec, environment)
