from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from strategy_workbench.domain.factor.facade.analysis import FactorAnalytics
from strategy_workbench.domain.factor.facade.evaluation import FactorEvaluation
from strategy_workbench.domain.factor.facade.expression import FactorGraph
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


@dataclass(frozen=True)
class FactorPreview:
    data_snapshot_id: str
    plan: FactorExecutionPlan
    cache_key: FactorMatrixCacheKey
    evaluation: FactorEvaluation
    analytics: FactorAnalytics
