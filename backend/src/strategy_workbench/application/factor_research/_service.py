from __future__ import annotations

from strategy_workbench.domain.factor.facade.analysis import analyze_factor_values
from strategy_workbench.domain.factor.facade.evaluation import (
    FactorEvaluation,
    evaluate_factor_graph,
)
from strategy_workbench.domain.factor.facade.planning import (
    InvalidFactorGraphError,
    build_factor_matrix_cache_key,
    compile_factor_plan,
)
from strategy_workbench.domain.factor.facade.registry import FactorRegistry
from strategy_workbench.domain.factor.facade.validation import (
    FactorGraphValidation,
    required_field_ids,
    validate_factor_graph,
)

from ._catalog import FactorCatalog, FactorCatalogQuery, build_factor_catalog
from ._models import (
    FactorExplanation,
    FactorGraphRequest,
    FactorPreview,
    FactorPreviewRequest,
)
from .ports.outgoing.factor_metadata import FactorMetadataPort, FactorMetadataSnapshot
from .ports.outgoing.factor_observations import (
    FactorObservationPort,
    FactorObservationQuery,
)


class FactorSnapshotMismatchError(ValueError):
    """Expected factor metadata or client provenance differs from observation data."""

    def __init__(self, *, expected: str, actual: str) -> None:
        super().__init__(
            f"factor preview snapshot mismatch — expected_data_snapshot_id={expected!r} "
            f"actual_data_snapshot_id={actual!r}"
        )
        self.expected = expected
        self.actual = actual


class InvalidFactorRequestError(ValueError):
    def __init__(self, validation: FactorGraphValidation) -> None:
        super().__init__("factor request contains an invalid graph")
        self.validation = validation


class FactorResearchService:
    def __init__(
        self,
        registry: FactorRegistry,
        metadata_source: FactorMetadataPort,
        observation_source: FactorObservationPort,
    ) -> None:
        self._registry = registry
        self._metadata_source = metadata_source
        self._observation_source = observation_source

    def catalog(self, query: FactorCatalogQuery | None = None) -> FactorCatalog:
        return build_factor_catalog(self._registry, query or FactorCatalogQuery())

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
            factor_ids=self._known_factor_ids(request.factor_ids),
            subgraph_ids=request.subgraph_ids,
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
            )
        plan = compile_factor_plan(
            request.graph,
            registry_version=self._registry.version,
            fields=metadata.fields,
            parameter_ids=request.parameter_ids,
            factor_ids=self._known_factor_ids(request.factor_ids),
            subgraph_ids=request.subgraph_ids,
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
        )

    def preview(self, request: FactorPreviewRequest) -> FactorPreview:
        parameter_ids = tuple(item.parameter_id for item in request.parameters)
        metadata = self._metadata_source.resolve_factor_fields(required_field_ids(request.graph))
        validation = validate_factor_graph(
            request.graph,
            fields=metadata.fields,
            parameter_ids=parameter_ids,
            factor_ids=self._known_factor_ids(request.factor_ids),
            subgraph_ids=request.subgraph_ids,
            require_field_metadata=True,
        )
        if not validation.valid:
            raise InvalidFactorRequestError(validation)
        try:
            plan = compile_factor_plan(
                request.graph,
                registry_version=self._registry.version,
                fields=metadata.fields,
                parameter_ids=parameter_ids,
                factor_ids=self._known_factor_ids(request.factor_ids),
                subgraph_ids=request.subgraph_ids,
                require_field_metadata=True,
            )
        except InvalidFactorGraphError as error:
            raise InvalidFactorRequestError(error.validation) from error
        observation_set = self._observation_source.load_factor_observations(
            FactorObservationQuery(
                required_field_ids=plan.required_field_ids,
                start=request.as_of_start,
                end=request.as_of_end,
                minimum_history_sessions=plan.minimum_history_sessions,
            )
        )
        if observation_set.data_snapshot_id != metadata.data_snapshot_id:
            raise FactorSnapshotMismatchError(
                expected=metadata.data_snapshot_id,
                actual=observation_set.data_snapshot_id,
            )
        if (
            request.expected_data_snapshot_id is not None
            and request.expected_data_snapshot_id != observation_set.data_snapshot_id
        ):
            raise FactorSnapshotMismatchError(
                expected=request.expected_data_snapshot_id,
                actual=observation_set.data_snapshot_id,
            )
        observations = observation_set.observations
        full_evaluation = evaluate_factor_graph(
            request.graph,
            observations=observations,
            parameters=request.parameters,
        )
        evaluation = FactorEvaluation(
            output_node_id=full_evaluation.output_node_id,
            values=tuple(
                value
                for value in full_evaluation.values
                if request.as_of_start <= value.as_of <= request.as_of_end
            ),
        )
        preview_observations = tuple(
            observation
            for observation in observations
            if request.as_of_start <= observation.as_of <= request.as_of_end
        )
        cache_key = build_factor_matrix_cache_key(
            data_snapshot_id=observation_set.data_snapshot_id,
            plan_hash=plan.plan_hash,
            registry_version=self._registry.version,
            parameters=request.parameters,
            as_of_start=request.as_of_start,
            as_of_end=request.as_of_end,
        )
        return FactorPreview(
            data_snapshot_id=observation_set.data_snapshot_id,
            plan=plan,
            cache_key=cache_key,
            evaluation=evaluation,
            analytics=analyze_factor_values(evaluation.values, preview_observations),
        )

    def _known_factor_ids(self, extra: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(definition.factor_id for definition in self._registry.all()) + extra
