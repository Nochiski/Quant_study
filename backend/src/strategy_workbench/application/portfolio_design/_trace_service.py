"""Saved/draft strategy resolution and bounded projection over the truthful portfolio pipeline."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from strategy_workbench.application.strategy_design.facade.ports import (
    StrategyNotFoundError,
    StrategyRepositoryPort,
)
from strategy_workbench.domain.factor.facade.trace import TraceSelection
from strategy_workbench.domain.portfolio.facade.construction import (
    PortfolioObservation,
    TargetFrame,
)
from strategy_workbench.domain.strategy.facade.provenance import (
    InlineDraft,
    SavedRevisionReference,
    StrategyProvenance,
    StrategySourceKind,
)
from strategy_workbench.domain.strategy.facade.specification import (
    StrategySpec,
    strategy_spec_hash,
)

from ._models import PortfolioPipelineOptions, PortfolioPreviewRequest
from ._service import (
    InvalidPortfolioTraceSelectionError,
    PortfolioDesignService,
    PortfolioPipelineCancelledError,
)
from ._trace_models import (
    RawStrategyTraceRow,
    StrategyTargetTrace,
    StrategyTraceInput,
    StrategyTracePage,
    StrategyTraceRequest,
    StrategyTraceResponse,
    StrategyTraceRow,
)

MAX_RAW_ROWS = 2_000


class InvalidStrategyTraceRequestError(ValueError):
    """A valid envelope names a factor/date/node scope that cannot be traced."""


class StrategyTraceSourceNotFoundError(LookupError):
    """The requested immutable strategy revision does not exist."""


class StaleStrategyTraceSourceError(RuntimeError):
    """The saved revision's server hash differs from the caller's expected hash."""


class StrategyTraceCancelledError(RuntimeError):
    """The caller disconnected or otherwise cancelled a trace calculation."""


class StrategyTraceService:
    """Resolve one StrategySpec, then query the existing portfolio calculation owner."""

    def __init__(
        self,
        portfolio_design: PortfolioDesignService,
        strategy_repository: StrategyRepositoryPort,
    ) -> None:
        self._portfolio_design = portfolio_design
        self._strategy_repository = strategy_repository

    def trace(
        self,
        request: StrategyTraceRequest,
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> StrategyTraceResponse:
        spec, provenance = self._resolve(request)
        _raise_if_cancelled(cancelled)
        if not spec.data.start <= request.as_of <= spec.data.end:
            raise InvalidStrategyTraceRequestError(
                "trace as_of is outside the strategy data range — "
                f"as_of={request.as_of} range={spec.data.start}..{spec.data.end}"
            )
        factors = {factor.factor_id: factor for factor in spec.factors.factors}
        if request.factor_id not in factors:
            raise InvalidStrategyTraceRequestError(
                "trace factor_id is not present in the strategy — "
                f"factor_id={request.factor_id!r} available={sorted(factors)!r}"
            )

        selection = TraceSelection(
            node_ids=request.node_ids or None,
            security_ids=tuple(sorted(request.security_ids)),
            as_of=(request.as_of,),
            # One look-ahead row is enough to answer `has_more` without an unbounded response.
            max_rows=request.offset + request.limit + 1,
        )
        try:
            pipeline = self._portfolio_design.run_pipeline(
                PortfolioPreviewRequest(spec),
                options=PortfolioPipelineOptions(
                    trace_factor_id=request.factor_id,
                    trace_selection=selection,
                    starting_holdings=request.starting_holdings,
                    require_engine_compatible=True,
                ),
                cancelled=cancelled,
            )
        except InvalidPortfolioTraceSelectionError as error:
            raise InvalidStrategyTraceRequestError(str(error)) from error
        except PortfolioPipelineCancelledError as error:
            raise StrategyTraceCancelledError(str(error)) from error
        record = next(
            item for item in pipeline.factor_evaluations if item.factor_id == request.factor_id
        )
        if record.trace is None:  # pragma: no cover - options above require this invariant
            raise RuntimeError("truthful pipeline omitted its requested factor trace")
        rows = tuple(
            StrategyTraceRow(
                node_id=node.node_id,
                operation=node.operation,
                as_of=value.as_of,
                security_id=value.security_id,
                value=value.value,
                status=value.status,
                inputs=tuple(
                    StrategyTraceInput(node_id=input_id, value=input_value)
                    for input_id, input_value in zip(
                        node.input_node_ids, value.inputs, strict=True
                    )
                ),
            )
            for node in record.trace.nodes
            for value in node.values
        )
        page_rows = rows[request.offset : request.offset + request.limit]
        raw, raw_truncated = _raw_projection(pipeline.observations, request)
        target = _target_projection(
            pipeline.preview.tape.frames, request.as_of, set(request.security_ids)
        )
        return StrategyTraceResponse(
            spec_hash=provenance.spec_hash,
            snapshot_id=pipeline.data_snapshot_id,
            registry_version=record.plan.registry_version,
            plan_hash=record.plan.plan_hash,
            provenance=provenance,
            factor_id=request.factor_id,
            as_of=request.as_of,
            trace=StrategyTracePage(
                rows=page_rows,
                offset=request.offset,
                limit=request.limit,
                returned=len(page_rows),
                has_more=len(rows) > request.offset + request.limit or record.trace.truncated,
            ),
            raw=raw,
            raw_truncated=raw_truncated,
            target=target,
            warnings=pipeline.preview.warnings,
        )

    def _resolve(
        self, request: StrategyTraceRequest
    ) -> tuple[StrategySpec, StrategyProvenance]:
        source = request.strategy_source
        if isinstance(source, SavedRevisionReference):
            try:
                record = self._strategy_repository.get(source.strategy_id, source.revision)
            except StrategyNotFoundError as error:
                raise StrategyTraceSourceNotFoundError(
                    "saved strategy revision not found for trace — "
                    f"strategy_id={source.strategy_id} revision={source.revision}"
                ) from error
            if record.spec_hash != source.expected_spec_hash:
                raise StaleStrategyTraceSourceError(
                    "saved strategy revision hash mismatch for trace — "
                    f"strategy_id={source.strategy_id} revision={source.revision} "
                    f"expected={source.expected_spec_hash} actual={record.spec_hash}"
                )
            return record.spec, StrategyProvenance(
                kind=StrategySourceKind.SAVED_REVISION,
                spec_hash=record.spec_hash,
                schema_version=record.spec.identity.schema_version,
                strategy_id=record.strategy_id,
                revision=record.revision,
                source_hash=record.source.source_hash if record.source else None,
            )
        if not isinstance(source, InlineDraft):  # pragma: no cover - union invariant
            raise TypeError(f"unsupported trace source — type={type(source).__name__}")
        return source.spec, StrategyProvenance(
            kind=StrategySourceKind.INLINE_DRAFT,
            spec_hash=strategy_spec_hash(source.spec),
            schema_version=source.spec.identity.schema_version,
            source_hash=source.source_hash,
        )


def _raw_projection(
    observations: tuple[PortfolioObservation, ...], request: StrategyTraceRequest
) -> tuple[tuple[RawStrategyTraceRow, ...], bool]:
    if not request.include_raw:
        return (), False
    security_ids = set(request.security_ids)
    all_rows = tuple(
        RawStrategyTraceRow(
            as_of=observation.as_of,
            security_id=observation.security_id,
            field_id=field.field_id,
            value=field.value,
            available_date=field.available_date,
        )
        for observation in observations
        if observation.as_of == request.as_of and observation.security_id in security_ids
        for field in sorted(observation.fields, key=lambda item: item.field_id)
    )
    return all_rows[:MAX_RAW_ROWS], len(all_rows) > MAX_RAW_ROWS


def _target_projection(
    frames: tuple[TargetFrame, ...], as_of: date, security_ids: set[str]
) -> StrategyTargetTrace | None:
    frame = next((item for item in frames if item.signal_as_of == as_of), None)
    if frame is None:
        return None
    return StrategyTargetTrace(
        signal_as_of=frame.signal_as_of,
        execution_on=frame.execution_on,
        targets=tuple(item for item in frame.targets if item.security_id in security_ids),
        candidates=tuple(item for item in frame.candidates if item.security_id in security_ids),
    )


def _raise_if_cancelled(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise StrategyTraceCancelledError("strategy trace was cancelled")
