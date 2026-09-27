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
from strategy_workbench.domain.strategy.facade.promotion import SynthesizedNode


@dataclass(frozen=True)
class FactorGraphRequest:
    graph: FactorGraph
    parameter_ids: tuple[str, ...] = ()
    # 결측 정책은 전략 문서가 아니라 실행이 소유한다(P2-02). 팩터 연구는 전략 실행 설정 밖에서
    # 도는 sandbox 라 요청이 직접 들고 온다. 1.2 문서에는 떨어질 legacy 값이 없으므로 생략하면
    # (`None`) 실행 설정과 같은 기본값(`DEFAULT_MISSING_POLICY`)을 쓴다 — 편집 화면의 실행 플랜
    # 패널이 실제 실행과 같은 `plan_hash` 를 보이려면 두 기본값이 한 상수여야 한다(P2-03).
    missing: MissingPolicy | None = None


@dataclass(frozen=True)
class FactorExplanation:
    registry_version: str
    data_snapshot_id: str
    validation: FactorGraphValidation
    plan: FactorExecutionPlan | None
    narrative: tuple[str, ...]
    # compile 이 붙인 노드 표식(P3-01, Phase 2 감사 #13). 실행 계획 화면은 전략 compile 이 낸
    # 그래프를 이 경로로 설명받는데, 그 그래프에는 문서에 줄이 없는 승격 노드가 있다. 화면이 승격
    # 노드 이름 규칙을 복제하지 않고 이 표식으로 가른다.
    # 팩터 연구에서 직접 쓴 그래프면 빈 tuple 이다.
    synthesized_nodes: tuple[SynthesizedNode, ...]


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
    missing: MissingPolicy | None = None


@dataclass(frozen=True)
class FactorPreview:
    data_snapshot_id: str
    plan: FactorExecutionPlan
    cache_key: FactorMatrixCacheKey
    evaluation: FactorEvaluation
    analytics: FactorAnalytics
