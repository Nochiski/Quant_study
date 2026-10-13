from __future__ import annotations

from strategy_workbench.domain.backtest.facade.environment import (
    DEFAULT_MISSING_POLICY,
)
from strategy_workbench.domain.factor.facade.expression import MissingPolicy
from strategy_workbench.domain.factor.facade.planning import (
    compile_factor_plan,
)
from strategy_workbench.domain.factor.facade.registry import FactorRegistry
from strategy_workbench.domain.factor.facade.validation import (
    FactorGraphValidation,
    required_field_ids,
    validate_factor_graph,
)
from strategy_workbench.domain.strategy.facade.promotion import synthesized_factor_nodes

from ._catalog import FactorCatalog, FactorCatalogQuery, build_factor_catalog
from ._models import (
    FactorExplanation,
    FactorGraphRequest,
)
from .ports.outgoing.factor_metadata import FactorMetadataPort, FactorMetadataSnapshot


def _resolved_missing(request: FactorGraphRequest) -> MissingPolicy:
    """팩터 sandbox 요청의 결측 정책을 확정한다.

    sandbox(`/factors/explain`)는 전략 실행 설정 밖에서 도는 요청이라 실행
    설정을 갖지 않는다. schema 1.2 문서에는 `graph.missing_policy` 자리가 없어 떨어질 문서 값도
    없으므로(P2-03), 생략하면 실행 설정과 **같은 기본값**을 쓴다 — 두 기본값이 갈리면 편집 화면의
    실행 플랜 패널이 실제 실행과 다른 `plan_hash` 를 보인다.

    P2-02 는 이 자리에서 1.1 문서 값으로 떨어뜨렸다(`resolve_graph_missing_policy`). 그 입력이
    사라져 규칙이 기본값 하나로 줄었다. P3-01 이 실행 설정의 `missing` 을 sandbox 요청에 실어
    보내면 `None` 경로 자체가 사라진다.
    """
    return request.missing if request.missing is not None else DEFAULT_MISSING_POLICY


class FactorResearchService:
    def __init__(
        self,
        registry: FactorRegistry,
        metadata_source: FactorMetadataPort,
    ) -> None:
        self._registry = registry
        self._metadata_source = metadata_source

    def catalog(self, query: FactorCatalogQuery | None = None) -> FactorCatalog:
        # 가용성은 validate·explain 와 같은 필드 계약 port 로 판정한다(#370). 두 어댑터의
        # `factor_field_catalog`(compile 목록)도 같은 `resolve_factor_fields` 를 부른다.
        declared = {
            field_id for item in self._registry.all() for field_id in item.required_field_ids
        }
        resolved = self._metadata_source.resolve_factor_fields(tuple(sorted(declared)))
        provided = {field.field_id for field in resolved.fields}
        return build_factor_catalog(self._registry, query or FactorCatalogQuery(), provided)

    def validate(self, request: FactorGraphRequest) -> FactorGraphValidation:
        validation, _fields = self._validate_request(request)
        return validation

    def _validate_request(
        self, request: FactorGraphRequest
    ) -> tuple[FactorGraphValidation, FactorMetadataSnapshot]:
        metadata = self._metadata_source.resolve_factor_fields(required_field_ids(request.graph))
        validation = validate_factor_graph(
            request.graph,
            fields=metadata.fields,
            parameter_ids=request.parameter_ids,
            require_field_metadata=True,
        )
        return validation, metadata

    def explain(self, request: FactorGraphRequest) -> FactorExplanation:
        validation, metadata = self._validate_request(request)
        if not validation.valid:
            return FactorExplanation(
                registry_version=self._registry.version,
                data_snapshot_id=metadata.data_snapshot_id,
                validation=validation,
                plan=None,
                narrative=("Resolve validation errors before compiling the PIT plan.",),
                synthesized_nodes=synthesized_factor_nodes(request.graph),
            )
        plan = compile_factor_plan(
            request.graph,
            registry_version=self._registry.version,
            missing=_resolved_missing(request),
            fields=metadata.fields,
            parameter_ids=request.parameter_ids,
            require_field_metadata=True,
        )
        return FactorExplanation(
            registry_version=self._registry.version,
            data_snapshot_id=metadata.data_snapshot_id,
            validation=validation,
            plan=plan,
            narrative=(
                f"Read {len(plan.required_field_ids)} PIT field(s).",
                f"Execute {len(plan.steps)} typed DAG step(s) in topological order.",
                f"Require {plan.minimum_history_sessions} session(s) of warm-up history.",
                f"Cache by snapshot, plan, parameters, and as-of range: {plan.plan_hash[:12]}.",
            ),
            synthesized_nodes=synthesized_factor_nodes(request.graph),
        )
