from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, fields, replace
from datetime import date
from enum import Enum
from typing import Any

from strategy_workbench.domain.factor.facade.expression import ExpressionNode, FactorGraph
from strategy_workbench.domain.factor.facade.validation import node_dependencies

from ._models import StrategySpec


def canonical_strategy_payload(spec: StrategySpec) -> dict[str, Any]:
    """Return semantic content; storage identity never changes the strategy hash.

    Numeric normalisation keeps "same meaning, same hash" (authoring ADR D1): `-0.0` folds to
    `0.0`, and `ParameterValue` union members that are integral floats fold to int so a YAML
    `1.0` and a JSON `1` in a choice parameter canonicalise identically.
    """
    payload = asdict(spec)
    identity = payload.pop("identity")
    payload["schema_version"] = identity["schema_version"]
    payload["parameters"] = [_normalize_parameter(item) for item in payload["parameters"]]
    return _normalize_numbers(payload)


def _normalize_parameter(parameter: dict[str, Any]) -> dict[str, Any]:
    if parameter.get("kind") != "choice":
        return parameter
    return parameter | {
        "default": _normalize_parameter_value(parameter["default"]),
        "choices": [_normalize_parameter_value(choice) for choice in parameter["choices"]],
    }


def _normalize_parameter_value(value: object) -> object:
    if isinstance(value, float) and not isinstance(value, bool) and value.is_integer():
        return int(value)
    return value


def _normalize_numbers(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_numbers(item) for key, item in value.items()}
    # asdict keeps dataclass tuple fields (rules, factors, nodes) as tuples: recurse into both.
    if isinstance(value, (list, tuple)):
        return [_normalize_numbers(item) for item in value]
    if isinstance(value, float) and value == 0.0:
        return 0.0
    return value


def canonical_payload_json(payload: Mapping[str, Any], *, indent: int | None = None) -> str:
    """The one canonical JSON encoding of an already-canonical payload (ADR D3 hash algorithm).

    Compact for hashing, `indent` for a human-readable document; both carry the same payload and
    key order, so `indent` never changes meaning. `strategy_spec_hash` is the sha256 of the
    compact form. Tests pin this encoder against a literal payload so a model change and an
    algorithm change stay distinguishable.
    """
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":") if indent is None else (",", ": "),
        indent=indent,
        default=_json_default,
    )


def canonical_strategy_json(spec: StrategySpec, *, indent: int | None = None) -> str:
    """Canonical JSON text of a spec: `canonical_strategy_payload` through the payload encoder."""
    return canonical_payload_json(canonical_strategy_payload(spec), indent=indent)


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported canonical value — type={type(value).__name__}")


def canonical_json_spec_hash(canonical_json: str) -> str:
    """ADR D3 hash of already-canonical JSON text: sha256 of its UTF-8 bytes.

    The only place the algorithm lives. `strategy_spec_hash` feeds it the current model's
    canonical text; a stored row from a retired schema version feeds it the exact bytes it
    stored, since that version's model no longer exists to re-canonicalise.
    """
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def strategy_spec_hash(spec: StrategySpec) -> str:
    return canonical_json_spec_hash(canonical_strategy_json(spec))


# 전략 의미 해시가 보지 않는 칸 — 이름·설명·스키마 판본과 파라미터 범위 정의(검증 랩 spec D2).
# 파라미터 값은 시도 키가 해소된 값으로 따로 싣는다. 팩터 표시 이름(`label`)도 뺀다.
_NON_SEMANTIC_FIELDS = ("title", "description", "schema_version", "parameters")
# 의미 해시 판본. 해시가 보는 칸이나 정규화를 바꾸는 PR 은 올린다. 올리면 모든 전략의 시도 키가
# 바뀌어 계열마다 다음 실행이 한 번 새 시도로 셀 수 있다(보수 쪽, 원장은 다시 쓰지 않는다).
# v2(#335 DOMAIN-V1-03): 노드·팩터 id 를 구조 번호로 바꾸고 출력에 닿지 않는 노드를 뺀다.
STRATEGY_SEMANTIC_HASH_VERSION = "strategy-semantic-v2"


def strategy_semantic_hash(spec: StrategySpec) -> str:
    """시도 키의 전략 축.

    `strategy_spec_hash` 의 payload 에서 이름·설명·판본·파라미터 정의를 뺀 해시다. 제목·팩터 표시
    이름만 바꾼 저장과 업그레이드만 한 리비전은 새로 고를 거리를 만들지 않으므로 같은 값이다.
    `_structural` 이 노드 이름·선언 순서·팩터 id 와 출력에 닿지 않는 노드를 지워, 그것만 다르면 같은
    값이다. 공유 노드와 같은 내용의 복제 노드처럼 구조가 다르면 계산이 같아도 다를 수 있다(보수 쪽).
    """
    payload = canonical_strategy_payload(_structural(spec))
    for name in _NON_SEMANTIC_FIELDS:
        del payload[name]
    for factor in payload["factors"]:
        del factor["label"]
    payload["hash_version"] = STRATEGY_SEMANTIC_HASH_VERSION
    return canonical_json_spec_hash(canonical_payload_json(payload))


def _structural(spec: StrategySpec) -> StrategySpec:
    """계산에 쓰이지 않는 이름을 구조 번호로 바꾼 spec(이슈 #335 DOMAIN-V1-03).

    팩터 id 는 선언 위치 번호다. 팩터 순서 자체는 정렬하지 않는다 — 합성 점수가 선언 순서대로
    부동소수를 더해(`domain/portfolio/_compiler.py`) 순서가 결과 끝자리에 닿을 수 있다.
    """
    numbers = {factor.factor_id: str(index) for index, factor in enumerate(spec.factors)}
    risk_factor_id = spec.risk.risk_factor_id
    return replace(
        spec,
        factors=tuple(
            replace(factor, factor_id=str(index), graph=_structural_graph(factor.graph))
            for index, factor in enumerate(spec.factors)
        ),
        risk=replace(
            spec.risk,
            risk_factor_id=None
            if risk_factor_id is None
            else numbers.get(risk_factor_id, risk_factor_id),
        ),
    )


def _structural_graph(graph: FactorGraph) -> FactorGraph:
    """노드를 출력에서 거슬러 올라가며 처음 만난 순서로 번호 매긴 그래프.

    입력은 `node_dependencies` 순서(왼쪽·오른쪽, 조건·참·거짓)로 따라가고 뒤바꾸지 않는다 — 빼기·
    나누기·비교·조건은 인자 순서가 결과를 바꾼다. 선언 순서는 평가에 쓰이지 않아(평가는 출력에서
    참조를 따라간다, `domain/factor/_planning.py`) 번호에 남지 않는다. 출력에 닿지 않는 노드는
    계산되지 않아 뺀다. 없는 참조(검증 전 문서)는 이름 그대로 두어 구조 번호와 겹칠 수 있지만, 그런
    문서는 검증 error 라 결과가 없어 N 에 들지 않는다(`_structural` 의 `risk_factor_id` 도 같다).
    """
    nodes = {node.node_id: node for node in graph.nodes}
    numbers: dict[str, str] = {}

    def visit(node_id: str) -> None:
        if node_id in numbers or node_id not in nodes:
            return
        numbers[node_id] = str(len(numbers))
        for dependency in node_dependencies(nodes[node_id]):
            visit(dependency)

    def renamed(node: ExpressionNode) -> ExpressionNode:
        references = {
            item.name: numbers.get(getattr(node, item.name), getattr(node, item.name))
            for item in fields(node)
            if item.metadata.get("reference") == "node"
        }
        return replace(node, node_id=numbers[node.node_id], **references)

    visit(graph.output_node_id)
    return FactorGraph(
        nodes=tuple(renamed(nodes[node_id]) for node_id in numbers),
        output_node_id=numbers.get(graph.output_node_id, graph.output_node_id),
    )
