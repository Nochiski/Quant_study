"""Inbound-owned wire metadata for the scoped trace endpoint.

Application/domain dataclasses remain transport-agnostic. This adapter describes their HTTP
error envelopes and projects application-owned request caps into OpenAPI so generated clients do
not duplicate server constants.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import Field

from strategy_workbench.application.portfolio_design.facade.design import EngineCompatibility
from strategy_workbench.application.portfolio_design.facade.trace import StrategyTraceRequest
from strategy_workbench.domain.equity.facade.research_data import DataLoadStatus
from strategy_workbench.domain.strategy.facade.validation import StrategyValidation


@dataclass(frozen=True)
class TraceRequestInvalidDetail:
    code: Literal["trace.request.invalid"]
    message: str


@dataclass(frozen=True)
class TraceEngineIncompatibleDetail:
    code: Literal["trace.engine.incompatible"]
    compatibility: EngineCompatibility


@dataclass(frozen=True)
class TracePortfolioStrategyInvalidDetail:
    code: Literal["portfolio.strategy.invalid"]
    validation: StrategyValidation


@dataclass(frozen=True)
class TracePortfolioDataUnavailableDetail:
    code: Literal["portfolio.data.unavailable"]
    status: DataLoadStatus
    detail: str | None


TraceUnprocessableDetail: TypeAlias = Annotated[
    TraceRequestInvalidDetail
    | TraceEngineIncompatibleDetail
    | TracePortfolioStrategyInvalidDetail
    | TracePortfolioDataUnavailableDetail,
    Field(discriminator="code"),
]


@dataclass(frozen=True)
class TraceUnprocessableResponse:
    detail: TraceUnprocessableDetail


@dataclass(frozen=True)
class TraceRequestValidationIssue:
    loc: tuple[str | int, ...]
    msg: str
    type: str


@dataclass(frozen=True)
class TraceRequestValidationResponse:
    """FastAPI's malformed-envelope 422 shape, alongside coded application diagnostics."""

    detail: tuple[TraceRequestValidationIssue, ...]


Trace422Response: TypeAlias = TraceUnprocessableResponse | TraceRequestValidationResponse


@dataclass(frozen=True)
class TraceStrategyNotFoundDetail:
    code: Literal["trace.strategy.not_found"]
    message: str


@dataclass(frozen=True)
class TraceStrategyNotFoundResponse:
    detail: TraceStrategyNotFoundDetail


@dataclass(frozen=True)
class TraceStrategyStaleDetail:
    code: Literal["trace.strategy.stale"]
    message: str


@dataclass(frozen=True)
class TraceStrategyStaleResponse:
    detail: TraceStrategyStaleDetail


@dataclass(frozen=True)
class TraceCancelledDetail:
    code: Literal["trace.cancelled"]
    message: str


@dataclass(frozen=True)
class TraceCancelledResponse:
    detail: TraceCancelledDetail


def apply_trace_openapi_contract(schema: dict[str, Any]) -> None:
    """Overlay caps/discriminator from their application owner onto FastAPI's dataclass schema."""
    components = _mapping(_mapping(schema, "components"), "schemas")
    request = _mapping(components, "StrategyTraceRequest")
    properties = _mapping(request, "properties")

    _mapping(properties, "security_ids").update(
        minItems=1,
        maxItems=StrategyTraceRequest.MAX_SECURITY_IDS,
        uniqueItems=True,
    )
    _mapping(properties, "node_ids").update(
        maxItems=StrategyTraceRequest.MAX_NODE_IDS,
        uniqueItems=True,
    )
    _mapping(properties, "limit").update(
        minimum=1,
        maximum=StrategyTraceRequest.MAX_LIMIT,
    )
    _mapping(properties, "offset").update(
        minimum=0,
        maximum=StrategyTraceRequest.MAX_SCAN_ROWS - 1,
        description=(
            "Zero-based flat trace offset. offset + limit must be <= "
            f"{StrategyTraceRequest.MAX_SCAN_ROWS}."
        ),
    )
    _mapping(properties, "factor_id")["minLength"] = 1

    starting_holdings = _mapping(properties, "starting_holdings")
    holding_array = next(
        (item for item in starting_holdings.get("anyOf", []) if item.get("type") == "array"),
        None,
    )
    if not isinstance(holding_array, dict):
        raise RuntimeError("StrategyTraceRequest.starting_holdings array schema is missing")
    holding_array["maxItems"] = StrategyTraceRequest.MAX_SECURITY_IDS
    starting_holdings["description"] = (
        "Optional full opening-book override. Security ids must be unique; omitted securities "
        "start at zero. null preserves the observation source's opening book."
    )

    source = _mapping(properties, "strategy_source")
    source["discriminator"] = {
        "propertyName": "kind",
        "mapping": {
            "saved_revision": "#/components/schemas/SavedRevisionReference",
            "inline_draft": "#/components/schemas/InlineDraft",
        },
    }

    item_schema = _mapping(_mapping(properties, "security_ids"), "items")
    item_schema["minLength"] = 1
    node_item_schema = _mapping(_mapping(properties, "node_ids"), "items")
    node_item_schema["minLength"] = 1
    _mapping(_mapping(components, "PortfolioStartingHolding"), "properties")[
        "security_id"
    ]["minLength"] = 1


def _mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise RuntimeError(f"trace OpenAPI contract expected an object at {key!r}")
    return value
