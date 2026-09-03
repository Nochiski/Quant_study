from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from strategy_workbench.domain.factor.facade.analysis import FactorAnalytics
from strategy_workbench.domain.factor.facade.evaluation import FactorEvaluation
from strategy_workbench.domain.factor.facade.expression import FactorGraph, FieldMetadata
from strategy_workbench.domain.factor.facade.planning import (
    FactorExecutionPlan,
    FactorMatrixCacheKey,
    ResolvedFactorParameter,
)
from strategy_workbench.domain.factor.facade.validation import FactorGraphValidation


@dataclass(frozen=True)
class FactorGraphRequest:
    graph: FactorGraph
    fields: tuple[FieldMetadata, ...] = ()
    parameter_ids: tuple[str, ...] = ()
    factor_ids: tuple[str, ...] = ()
    subgraph_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class FactorExplanation:
    validation: FactorGraphValidation
    plan: FactorExecutionPlan | None
    narrative: tuple[str, ...]


@dataclass(frozen=True)
class FactorPreviewRequest:
    graph: FactorGraph
    data_snapshot_id: str
    as_of_start: date
    as_of_end: date
    fields: tuple[FieldMetadata, ...] = ()
    parameters: tuple[ResolvedFactorParameter, ...] = ()
    factor_ids: tuple[str, ...] = ()
    subgraph_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class FactorPreview:
    plan: FactorExecutionPlan
    cache_key: FactorMatrixCacheKey
    evaluation: FactorEvaluation
    analytics: FactorAnalytics
