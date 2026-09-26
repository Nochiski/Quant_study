"""compile 을 통과한 문서는 미리보기 사전 검사에서 거부되지 않는다 (P2-07 property, spec D5).

"검증 통과 = 실행 가능"의 backend 쪽 증명이다. 실행 경계의 방어 검사
(`_reject_non_numeric_factor_outputs`)는 남아 있지만, compile 을 통과한 문서에서 발화하면 결함이다.
여기서는 같은 mock 어댑터를 compile(필드 계약·capability)과 미리보기 사전 검사(`preflight`: 문서
검증 → 필드 계약 → 실행 플랜 → 출력 타입 방어 검사)에 똑같이 연결하고 그래프 공간을 훑는다.

그래프 공간은 네 가지 값 타입의 잎(숫자 필드·그룹 필드·상수·비교 결과) 위에 연산자 카탈로그의
모든 `(kind, operator)` 와 조건 노드를 한 층 올린 조합 전부, 그리고 깊이 3 까지의 무작위 조합
(시드 고정)이다. 무작위 표본은 새 라이브러리 없이 결정적으로 돌게 `random.Random(seed)` 을 쓴다.
"""

from __future__ import annotations

import itertools
import json
import random
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    InvalidPortfolioRequestError,
    PortfolioDesignService,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompileRequest,
    StrategyAuthoringService,
)
from strategy_workbench.application.strategy_authoring.facade.ports import SourceFormat
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.factor.facade.expression import EXPRESSION_NODE_KINDS
from strategy_workbench.domain.factor.facade.operators import (
    OperatorDefinition,
    operator_definitions,
)
from strategy_workbench.domain.strategy.facade.document import CURRENT_SCHEMA_VERSION

LEAF_KINDS = ("numeric", "group", "scalar", "boolean")
_PARAMETERS: dict[str, object] = {
    "window": 2,
    "periods": 1,
    "group_field_id": "classification.sector",
}
_RANDOM_SAMPLES = 300
_MAX_DEPTH = 3


@dataclass(frozen=True)
class _Expression:
    nodes: tuple[dict[str, Any], ...]
    output: str
    label: str


def _leaf(kind: str, prefix: str) -> _Expression:
    if kind == "numeric":
        node = {"kind": "field", "node_id": prefix, "field_id": "price.close"}
        return _Expression((node,), prefix, "close")
    if kind == "group":
        node = {"kind": "field", "node_id": prefix, "field_id": "classification.sector"}
        return _Expression((node,), prefix, "sector")
    if kind == "scalar":
        return _Expression(({"kind": "constant", "node_id": prefix, "value": 1.0},), prefix, "1")
    return _Expression(
        (
            {"kind": "field", "node_id": f"{prefix}_l", "field_id": "price.close"},
            {"kind": "constant", "node_id": f"{prefix}_r", "value": 100.0},
            {
                "kind": "comparison",
                "node_id": prefix,
                "operator": "gt",
                "left_node_id": f"{prefix}_l",
                "right_node_id": f"{prefix}_r",
            },
        ),
        prefix,
        "close>100",
    )


def _input_names(kind: str) -> tuple[str, ...]:
    return tuple(
        item.name
        for item in EXPRESSION_NODE_KINDS[kind].__dataclass_fields__.values()
        if item.metadata.get("reference") == "node"
    )


# 연산자 카탈로그 전부 + 연산자가 없는 조건 노드. 카탈로그가 늘면 공간도 따라 는다.
_OPERATORS: tuple[OperatorDefinition | None, ...] = (*operator_definitions(), None)


def _apply(
    definition: OperatorDefinition | None, inputs: tuple[_Expression, ...], node_id: str
) -> _Expression:
    kind = "conditional" if definition is None else definition.kind
    node: dict[str, Any] = {"kind": kind, "node_id": node_id}
    if definition is not None:
        node["operator"] = definition.operator
        for parameter in definition.params:
            if parameter.property_name in _PARAMETERS:
                node[parameter.property_name] = _PARAMETERS[parameter.property_name]
    for name, expression in zip(_input_names(kind), inputs, strict=True):
        node[name] = expression.output
    name = kind if definition is None else f"{kind}.{definition.operator}"
    return _Expression(
        (*itertools.chain.from_iterable(item.nodes for item in inputs), node),
        node_id,
        f"{name}({', '.join(item.label for item in inputs)})",
    )


def _arity(definition: OperatorDefinition | None) -> int:
    return 3 if definition is None else definition.arity


def _one_layer() -> list[_Expression]:
    graphs = [_leaf(kind, "leaf") for kind in LEAF_KINDS]
    for definition in _OPERATORS:
        for kinds in itertools.product(LEAF_KINDS, repeat=_arity(definition)):
            inputs = tuple(_leaf(kind, f"in{index}") for index, kind in enumerate(kinds))
            graphs.append(_apply(definition, inputs, "out"))
    return graphs


def _random(rng: random.Random, depth: int, prefix: str) -> _Expression:
    if depth == 0 or rng.random() < 0.3:
        return _leaf(rng.choice(LEAF_KINDS), prefix)
    definition = rng.choice(_OPERATORS)
    inputs = tuple(
        _random(rng, depth - 1, f"{prefix}_{index}") for index in range(_arity(definition))
    )
    return _apply(definition, inputs, prefix)


def _sampled() -> list[_Expression]:
    rng = random.Random(20260927)
    return [_random(rng, _MAX_DEPTH, "n") for _ in range(_RANDOM_SAMPLES)]


def _source(expression: _Expression) -> str:
    document = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "title": "compile 게이트 property",
        "factors": [
            {
                "factor_id": "subject",
                "label": expression.label,
                "direction": "high",
                "weight": 1.0,
                "graph": {"nodes": list(expression.nodes), "output_node_id": expression.output},
            }
        ],
        "portfolio": {"selection_count": 5, "rebalance": "monthly"},
        "risk": {"max_name_weight": 0.2},
    }
    return json.dumps(document, ensure_ascii=False)


@pytest.fixture(scope="module")
def adapter() -> MockEquityDataAdapter:
    return MockEquityDataAdapter.demo()


@pytest.fixture(scope="module")
def authoring(adapter: MockEquityDataAdapter) -> StrategyAuthoringService:
    return StrategyAuthoringService(
        RuamelDocumentCodec(),
        factor_registry_version="property",
        dataset_snapshot_id=lambda: adapter.snapshot().snapshot_id,
        field_catalog=adapter,
    )


@pytest.fixture(scope="module")
def portfolio(adapter: MockEquityDataAdapter) -> PortfolioDesignService:
    return PortfolioDesignService(
        adapter,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=adapter,
        factor_registry_version="property",
    )


_ENVIRONMENT = RunEnvironment(
    start=date(2024, 1, 2), end=date(2024, 3, 29), universe_id="krx.common-stock"
)


def _check(
    expressions: list[_Expression],
    authoring: StrategyAuthoringService,
    portfolio: PortfolioDesignService,
) -> Counter[str]:
    outcomes: Counter[str] = Counter()
    leaks: list[str] = []
    for expression in expressions:
        compiled = authoring.compile(CompileRequest(_source(expression), SourceFormat.JSON))
        if compiled.spec is None:
            codes = {item.code for item in compiled.diagnostics}
            outcomes["rejected"] += 1
            if "strategy.factor.output_type" in codes:
                outcomes["rejected:output_type"] += 1
            if "strategy.operator.unsupported" in codes:
                outcomes["rejected:unsupported"] += 1
            continue
        outcomes["compiled"] += 1
        if compiled.spec.factors[0].graph.output_node_id == "__promote_subject":
            outcomes["compiled:promoted"] += 1
        try:
            portfolio.preflight(PortfolioPreviewRequest(compiled.spec, _ENVIRONMENT))
        except InvalidPortfolioRequestError as error:
            codes = sorted({issue.code for issue in error.validation.issues})
            leaks.append(f"{expression.label} → {codes}")
    assert not leaks, f"compile 통과 문서가 미리보기에서 거부됐다({len(leaks)}건): {leaks[:5]}"
    return outcomes


def test_every_one_layer_graph_that_compiles_passes_the_preview_preflight(
    authoring: StrategyAuthoringService, portfolio: PortfolioDesignService
) -> None:
    outcomes = _check(_one_layer(), authoring, portfolio)

    # 공간이 양쪽을 실제로 밟는지: 통과·승격·출력 타입 거부가 모두 있어야 property 가 공허하지 않다.
    assert outcomes["compiled"] > 50, outcomes
    assert outcomes["compiled:promoted"] > 0, outcomes
    assert outcomes["rejected:output_type"] > 0, outcomes


def test_every_sampled_deeper_graph_that_compiles_passes_the_preview_preflight(
    authoring: StrategyAuthoringService, portfolio: PortfolioDesignService
) -> None:
    outcomes = _check(_sampled(), authoring, portfolio)

    assert outcomes["compiled"] > 30, outcomes
    assert outcomes["rejected"] > 0, outcomes
