from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from strategy_workbench.domain.factor.facade.analysis import FactorAnalytics
from strategy_workbench.domain.factor.facade.evaluation import FactorEvaluation
from strategy_workbench.domain.factor.facade.expression import FactorGraph, MissingPolicy
from strategy_workbench.domain.factor.facade.planning import (
    FactorExecutionPlan,
    FactorMatrixCacheKey,
    ResolvedFactorParameter,
)
from strategy_workbench.domain.factor.facade.validation import FactorGraphValidation


@dataclass(frozen=True)
class FactorGraphRequest:
    graph: FactorGraph
    parameter_ids: tuple[str, ...] = ()
    factor_ids: tuple[str, ...] = ()
    subgraph_ids: tuple[str, ...] = ()
    # 결측 정책은 전략 문서가 아니라 실행이 소유한다(P2-02). 팩터 연구는 전략 실행 설정 밖에서
    # 도는 sandbox 라 요청이 직접 들고 온다. 생략하면(`None`) 1.1 그래프의 legacy 값으로
    # 떨어져 전략 실행과 같은 plan 을 낸다 — 편집 화면의 실행 플랜 패널이 그 경로다.
    missing: MissingPolicy | None = None


@dataclass(frozen=True)
class FactorExplanation:
    registry_version: str
    data_snapshot_id: str
    validation: FactorGraphValidation
    plan: FactorExecutionPlan | None
    narrative: tuple[str, ...]


@dataclass(frozen=True)
class FactorPreviewRequest:
    """Preview request. The data snapshot is owned by the adapter (P1.5-01).

    `expected_data_snapshot_id` is optional provenance the client saw in the catalog; when it
    differs from the adapter's actual snapshot the preview fails closed instead of silently
    computing against different data.
    """

    graph: FactorGraph
    as_of_start: date
    as_of_end: date
    expected_data_snapshot_id: str | None = None
    parameters: tuple[ResolvedFactorParameter, ...] = ()
    factor_ids: tuple[str, ...] = ()
    subgraph_ids: tuple[str, ...] = ()
    missing: MissingPolicy | None = None


@dataclass(frozen=True)
class FactorPreview:
    data_snapshot_id: str
    plan: FactorExecutionPlan
    cache_key: FactorMatrixCacheKey
    evaluation: FactorEvaluation
    analytics: FactorAnalytics
