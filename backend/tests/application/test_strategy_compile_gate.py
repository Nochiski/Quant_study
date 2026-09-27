"""compile 이 연결된 equity 어댑터의 필드 계약과 capability 를 읽는다 (P2-07, spec D5).

"검증 통과 = 실행 가능"을 위해 실행 직전에야 나던 판정을 compile 로 앞당긴다.

- 없는 `field_id` 는 `strategy.expression.field_missing` 으로 저장 전에 난다. 이전에는 compile 이
  계약 없이 검증해 통과하고, 추적·백테스트에서야 멈췄다(US-CS-02 비고).
- 어댑터가 그룹 필드(`group_series`)를 주지 않으면 그룹 연산 노드는 `strategy.operator.unsupported`
  다. 연산자 카탈로그의 `availability` 도 같은 capability 로 판정한다.
- 어댑터가 없는 컨텍스트(CLI·테스트)는 지금과 같다.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompiledDocument,
    CompileRequest,
    StrategyAuthoringService,
)
from strategy_workbench.application.strategy_authoring.facade.ports import (
    DiagnosticKind,
    DiagnosticSeverity,
    FieldCatalogPort,
    SourceFormat,
)
from strategy_workbench.application.strategy_design.facade.ports import (
    RevisionOrigin,
    RevisionProvenance,
    RevisionSource,
    StrategyRevisionRecord,
)
from strategy_workbench.bootstrap.facade.container import build_container
from strategy_workbench.domain.factor.facade.expression import FieldMetadata, NodeValueType
from strategy_workbench.domain.factor.facade.operators import OperatorAvailability
from strategy_workbench.domain.strategy.facade.specification import StrategyIdentity

GOLDEN = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "strategy_documents"
    / "quality_momentum.yaml"
).read_text(encoding="utf-8")

_CLOSE = FieldMetadata(field_id="price.close", unit="KRW")
_SECTOR_AS_GROUP = FieldMetadata(
    field_id="classification.sector", unit="category", value_type=NodeValueType.GROUP_SERIES
)
_SECTOR_AS_NUMBER = FieldMetadata(field_id="classification.sector", unit="category")

# US-CS-02 e2e 가 넣는 파생 팩터(`frontend/e2e/stories/cs.derived-factor.spec.ts`).
_DERIVED_FACTOR = """  - factor_id: book_to_market
    label: "장부가/시가총액"
    direction: high
    weight: 0.4
    graph:
      nodes:
        - kind: field
          node_id: book
          field_id: financial.book_equity
        - kind: field
          node_id: cap
          field_id: {denominator}
        - kind: binary
          node_id: btm
          operator: divide
          left_node_id: book
          right_node_id: cap
        - kind: cross_sectional
          node_id: btm_z
          operator: zscore
          input_node_id: btm
      output_node_id: btm_z
"""

_GROUP_FACTOR = """  - factor_id: sector_neutral
    label: "섹터 중립 모멘텀"
    direction: high
    weight: 0.4
    graph:
      nodes:
        - kind: field
          node_id: close
          field_id: price.close
        - kind: group
          node_id: neutral
          operator: neutralize
          input_node_id: close
          group_field_id: classification.sector
      output_node_id: neutral
"""


class _Catalog:
    """`FieldCatalogPort` 가짜 — 주어진 계약을 그대로 답하고 호출 수를 센다."""

    def __init__(self, *fields: FieldMetadata) -> None:
        self._fields = fields
        self.calls = 0

    def factor_field_catalog(self) -> tuple[FieldMetadata, ...]:
        self.calls += 1
        return self._fields


def _service(catalog: FieldCatalogPort | None) -> StrategyAuthoringService:
    return StrategyAuthoringService(
        RuamelDocumentCodec(),
        factor_registry_version="test",
        dataset_snapshot_id=lambda: "snapshot",
        field_catalog=catalog,
    )


def _with_factor(factor_yaml: str) -> str:
    return GOLDEN.replace("portfolio:\n", f"{factor_yaml}portfolio:\n", 1)


def _compile(source: str, catalog: FieldCatalogPort | None) -> CompiledDocument:
    return _service(catalog).compile(CompileRequest(source, SourceFormat.YAML))


def _errors(compiled: CompiledDocument) -> list[tuple[str, str]]:
    return [
        (diagnostic.code, diagnostic.pointer)
        for diagnostic in compiled.diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    ]


# -- field_missing -------------------------------------------------------------------------------

# 없는 필드 진단은 그래프 안(`strategy.expression.field_missing`)과
# 밖(`strategy.field.missing`) 두 코드다.
# 둘 다 기계 디테일(`field_id=…`) 앞에 한글 문장이 있어야 한다(P3-01 리드 결정).
_HANGUL = re.compile(r"[가-힣]")


def test_an_unknown_field_is_a_compile_error_once_an_adapter_is_connected() -> None:
    source = GOLDEN.replace("field_id: price.close", "field_id: price.closee")

    compiled = _compile(source, _Catalog(_CLOSE))

    assert not compiled.ok
    assert _errors(compiled) == [
        ("strategy.expression.field_missing", "/factors/0/graph/nodes/0"),
    ]
    diagnostic = compiled.diagnostics[0]
    assert "price.closee" in diagnostic.message
    # 문제 목록은 backend 문장을 그대로 보인다.
    # frontend 는 진단 코드를 번역하지 않는다(SoT 진단 코드 행).
    assert _HANGUL.search(diagnostic.message.split("field_id=")[0]), diagnostic.message
    assert diagnostic.range is not None, "편집기가 그 노드 줄을 짚을 수 있다"


def test_without_an_adapter_an_unknown_field_still_compiles() -> None:
    source = GOLDEN.replace("field_id: price.close", "field_id: price.closee")

    assert _compile(source, None).ok


def test_the_cs02_denominator_typo_is_caught_by_compile_on_the_mock_adapter() -> None:
    """US-CS-02 e2e 문서에서 분모 필드만 틀리면 이제 compile 이 막는다(전에는 추적에서 멈췄다)."""
    adapter = MockEquityDataAdapter.demo()

    valid = _compile(_with_factor(_DERIVED_FACTOR.format(denominator="price.market_cap")), adapter)
    typo = _compile(_with_factor(_DERIVED_FACTOR.format(denominator="price.market_capx")), adapter)

    assert valid.ok, _errors(valid)
    assert _errors(typo) == [("strategy.expression.field_missing", "/factors/1/graph/nodes/1")]


def test_the_field_catalog_is_read_per_compile() -> None:
    """어댑터가 계약의 owner 다 — bootstrap 에서 복사해 두지 않는다(스냅샷 id 와 같은 규칙)."""
    catalog = _Catalog(_CLOSE)
    service = _service(catalog)

    service.compile(CompileRequest(GOLDEN, SourceFormat.YAML))
    service.compile(CompileRequest(GOLDEN, SourceFormat.YAML))

    assert catalog.calls == 2


# -- 그룹 연산 capability ------------------------------------------------------------------------


def test_a_group_node_is_unsupported_when_the_adapter_has_no_group_field() -> None:
    compiled = _compile(_with_factor(_GROUP_FACTOR), _Catalog(_CLOSE, _SECTOR_AS_NUMBER))

    assert not compiled.ok
    [diagnostic] = [
        item for item in compiled.diagnostics if item.severity is DiagnosticSeverity.ERROR
    ]
    assert diagnostic.code == "strategy.operator.unsupported"
    assert diagnostic.kind is DiagnosticKind.CAPABILITY
    assert diagnostic.pointer == "/factors/1/graph/nodes/1"
    assert diagnostic.node_id == "neutral"
    assert "required='group_series'" in diagnostic.message
    # 같은 원인(그룹 필드가 없다)을 그룹 필드 타입 오류로 한 번 더 말하지 않는다.
    assert "strategy.expression.group_field_type" not in {
        item.code for item in compiled.diagnostics
    }


def test_a_group_node_compiles_when_the_adapter_provides_a_group_field() -> None:
    compiled = _compile(_with_factor(_GROUP_FACTOR), _Catalog(_CLOSE, _SECTOR_AS_GROUP))

    assert compiled.ok, _errors(compiled)


def test_without_an_adapter_a_group_node_compiles_as_before() -> None:
    assert _compile(_with_factor(_GROUP_FACTOR), None).ok


@pytest.mark.parametrize(
    ("catalog", "expected"),
    [
        (None, OperatorAvailability.UNSUPPORTED),
        (_Catalog(_CLOSE, _SECTOR_AS_NUMBER), OperatorAvailability.UNSUPPORTED),
        (_Catalog(_CLOSE, _SECTOR_AS_GROUP), OperatorAvailability.AVAILABLE),
    ],
    ids=["no-adapter", "numeric-only", "group-field"],
)
def test_operator_catalog_availability_follows_the_same_capability(
    catalog: _Catalog | None, expected: OperatorAvailability
) -> None:
    operators = _service(catalog).operators().operators

    group = {item.availability for item in operators if item.kind == "group"}
    others = {item.availability for item in operators if item.kind != "group"}
    assert group == {expected}
    assert others == {OperatorAvailability.AVAILABLE}


def test_operator_catalog_hash_changes_with_availability() -> None:
    """ETag 가 가용성을 덮어야 어댑터를 바꾼 뒤 화면이 옛 팔레트를 304 로 재사용하지 않는다."""
    without = _service(_Catalog(_CLOSE)).operators().catalog_hash
    with_group = _service(_Catalog(_CLOSE, _SECTOR_AS_GROUP)).operators().catalog_hash

    assert without != with_group


def test_the_mock_adapter_answers_every_field_contract_and_provides_group_series() -> None:
    adapter = MockEquityDataAdapter.demo()

    catalog = adapter.factor_field_catalog()

    field_ids = tuple(profile.field_id for profile in adapter.list_fields())
    assert catalog == adapter.resolve_factor_fields(field_ids).fields
    assert NodeValueType.GROUP_SERIES in {field.value_type for field in catalog}


def test_storage_integrity_does_not_depend_on_the_connected_adapter() -> None:
    """저장본 읽기는 어댑터 계약으로 판정하지 않는다.

    무결성 검사(원문 → spec_hash)가 어댑터 필드 계약을 쓰면, 어댑터를 바꾸거나(mock ↔ duckdb)
    필드가 빠진 순간 이미 저장된 revision 을 읽지 못해 목록·이력이 500 이 된다. 같은 원문을
    compile 하면 연결된 어댑터 기준으로는 막힌다 — 실행 가능성은 compile 이 따로 말한다.
    """
    container = build_container(equity_adapter="mock")
    source = GOLDEN.replace("field_id: price.close", "field_id: price.closee")
    compiled = _compile(source, None)
    assert compiled.spec is not None and compiled.spec_hash is not None
    spec = replace(compiled.spec, identity=StrategyIdentity("stored", 1))
    record = StrategyRevisionRecord(
        spec=spec,
        spec_hash=compiled.spec_hash,
        source=RevisionSource(SourceFormat.YAML, source, compiled.source_hash),
        provenance=RevisionProvenance(RevisionOrigin.DOCUMENT, datetime(2026, 9, 27, tzinfo=UTC)),
    )

    container.strategy_repository.add(record)

    assert container.strategy_repository.get("stored").spec_hash == compiled.spec_hash
    live = container.strategy_authoring.compile(CompileRequest(source, SourceFormat.YAML))
    assert "strategy.expression.field_missing" in {item.code for item in live.diagnostics}


# -- 그래프 밖 필드 참조(`x-catalog: equity-field`) -----------------------------------------------

# 문서에서 팩터 그래프 밖에 있는 필드 참조 네 자리. 원문 치환으로 하나씩 켠다(`{field}` 자리에 필드
# id). 목록은 테스트가 적지만 구현은 모델 metadata 에서 파생한다 — 아래
# `test_the_checked_paths_are_exactly_the_runtime_schema_equity_field_catalog` 가 둘을 대조한다.
_OUTSIDE_GRAPH_REFERENCES: dict[str, tuple[tuple[tuple[str, str], ...], str]] = {
    "eligibility": (
        (
            (
                "  rules: []\n",
                "  rules:\n    - field_id: {field}\n      operator: gt\n      value: 0\n",
            ),
        ),
        "/eligibility/rules/0/field_id",
    ),
    "liquidity": (
        (
            (
                "  rebalance: monthly\n",
                "  rebalance: monthly\n  liquidity_field_id: {field}\n  minimum_liquidity: 1.0\n",
            ),
        ),
        "/portfolio/liquidity_field_id",
    ),
    "regime": (
        (
            (
                "portfolio:\n",
                "signal:\n  regime_field_id: {field}\n  regime_minimum: 0.0\nportfolio:\n",
            ),
        ),
        "/signal/regime_field_id",
    ),
    "risk": (
        (
            ("  rebalance: monthly\n", "  rebalance: monthly\n  weighting: risk\n"),
            ("  max_name_weight: 0.05\n", "  max_name_weight: 0.05\n  risk_field_id: {field}\n"),
        ),
        "/risk/risk_field_id",
    ),
}


def _outside_graph_source(slot: str, field_id: str) -> str:
    source = GOLDEN
    for old, new in _OUTSIDE_GRAPH_REFERENCES[slot][0]:
        assert old in source, (slot, old)
        source = source.replace(old, new.format(field=field_id), 1)
    return source


@pytest.mark.parametrize("slot", sorted(_OUTSIDE_GRAPH_REFERENCES))
def test_an_unknown_field_outside_the_graph_is_a_compile_error(slot: str) -> None:
    """그래프 밖 참조도 연결된 어댑터의 계약에서 찾는다(P2-07 리뷰 P1, spec D5).

    막지 않으면 compile 은 진단 0건으로 통과하고 미리보기가 422 `portfolio.data.unavailable`,
    백테스트는 202 로 받은 뒤 tape 단계에서 실패한다.
    """
    adapter = MockEquityDataAdapter.demo()
    pointer = _OUTSIDE_GRAPH_REFERENCES[slot][1]

    valid = _compile(_outside_graph_source(slot, "price.close"), adapter)
    typo = _compile(_outside_graph_source(slot, "price.closex"), adapter)

    assert valid.ok, _errors(valid)
    assert _errors(typo) == [("strategy.field.missing", pointer)]
    [diagnostic] = [item for item in typo.diagnostics if item.code == "strategy.field.missing"]
    assert "price.closex" in diagnostic.message
    assert _HANGUL.search(diagnostic.message.split("field_id=")[0]), diagnostic.message
    assert diagnostic.range is not None, "편집기가 그 필드 줄을 짚을 수 있다"


@pytest.mark.parametrize("slot", sorted(_OUTSIDE_GRAPH_REFERENCES))
def test_a_group_field_where_a_number_is_read_is_a_compile_error(slot: str) -> None:
    """네 자리 모두 숫자로 읽는다(비교·유동성 하한·레짐 하한·역가중). 그룹 필드는 쓸 수 없다."""
    compiled = _compile(
        _outside_graph_source(slot, "classification.sector"), MockEquityDataAdapter.demo()
    )

    assert _errors(compiled) == [("strategy.field.value_type", _OUTSIDE_GRAPH_REFERENCES[slot][1])]
    [diagnostic] = [
        item for item in compiled.diagnostics if item.code == "strategy.field.value_type"
    ]
    assert "actual='group_series'" in diagnostic.message


@pytest.mark.parametrize("slot", sorted(_OUTSIDE_GRAPH_REFERENCES))
def test_without_an_adapter_outside_graph_references_are_not_judged(slot: str) -> None:
    assert _compile(_outside_graph_source(slot, "price.closex"), None).ok


def _equity_field_pointers(schema: dict[str, Any]) -> set[str]:
    """runtime schema 에서 `x-catalog: equity-field` 인 property 의 pointer(배열 항목은 `*`)."""
    found: set[str] = set()

    def walk(node: object, pointer: str, seen: frozenset[str]) -> None:
        if not isinstance(node, dict):
            return
        if node.get("x-catalog") == "equity-field":
            found.add(pointer)
        reference = node.get("$ref")
        if isinstance(reference, str):
            name = reference.rsplit("/", 1)[-1]
            if name not in seen:
                walk(schema["$defs"][name], pointer, seen | {name})
        for name, child in (node.get("properties") or {}).items():
            walk(child, f"{pointer}/{name}", seen)
        walk(node.get("items"), f"{pointer}/*", seen)
        for key in ("anyOf", "oneOf", "allOf"):
            for child in node.get(key) or ():
                walk(child, pointer, seen)

    walk(schema, "", frozenset())
    return found


def test_the_checked_paths_are_exactly_the_runtime_schema_equity_field_catalog() -> None:
    """검사 대상은 runtime schema 의 `x-catalog: equity-field` 와 같은 집합이다(그래프 노드 제외).

    검사기가 목록을 따로 들면 새 필드 참조가 생길 때 한쪽만 늘어난다. 모델 metadata 가 단일
    owner 이고 스키마와 검사기가 둘 다 그것을 읽는다. 여기서는 스키마 쪽 집합이 이 테스트의 네
    자리와 같은지 본다 — 새 자리가 생기면 이 테스트가 실패해 위 표에 사례를 더하게 한다.
    """
    pointers = _equity_field_pointers(_service(None).schema().schema)

    outside_graph = {pointer for pointer in pointers if "/graph/" not in pointer}
    checked = {
        pointer.replace("/0/", "/*/") for _edits, pointer in _OUTSIDE_GRAPH_REFERENCES.values()
    }
    assert outside_graph == checked
    assert any("/graph/" in pointer for pointer in pointers), (
        "그래프 노드 필드는 그래프 검증이 본다"
    )
