"""source 텍스트 업그레이드 어댑터 계약 (spec D3 source 경로, P1-04).

YAML은 ruamel round-trip으로 주석·따옴표·순서를 보존하고, JSON은 원문 들여쓰기로 다시 직렬화한다.
어느 쪽이든 결과 텍스트를 safe codec으로 다시 읽으면 domain dict 변환과 같은 tree여야 한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.domain.strategy.facade.document import (
    SourceFormat,
    hydrate_strategy_document,
    upgrade_document_1_0,
)
from strategy_workbench.domain.strategy.facade.specification import (
    StrategyIdentity,
    strategy_spec_hash,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"
DRAFT = StrategyIdentity("draft", 0)


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _hash_of(document: object) -> str:
    assert isinstance(document, dict)
    hydrated = hydrate_strategy_document(document, identity=DRAFT)
    assert hydrated.ok and hydrated.spec is not None, hydrated.issues
    return strategy_spec_hash(hydrated.spec)


def test_commented_yaml_upgrade_matches_the_golden_byte_for_byte() -> None:
    """리뷰 지적 3종(안쪽 키 줄끝 주석, 바깥 독립 주석 뒤 시퀀스, 삭제 키의 주석)을 덮는 golden."""
    upgraded = RuamelDocumentCodec().upgrade_source(
        _read("quality_momentum.v1_0.commented.yaml"), format=SourceFormat.YAML
    )

    assert upgraded == _read("quality_momentum.v1_1.commented.yaml")
    assert "# 팩터 목록 (바깥 독립 주석)" in upgraded  # 독립 주석 보존
    assert "# 수수료 bp" in upgraded and "# 종가" in upgraded  # 남은 줄의 줄끝 주석 보존
    assert "# 삭제 키" not in upgraded  # 삭제된 줄의 주석은 함께 사라진다
    assert "method 위 독립 주석" not in upgraded  # 비어 버린 섹션의 주석 잔해도 사라진다
    assert 'schema_version: "1.1"' in upgraded  # 따옴표 보존


def test_upgraded_yaml_reparses_to_the_dict_path_tree_and_hash() -> None:
    codec = RuamelDocumentCodec()
    source = _read("quality_momentum.v1_0.commented.yaml")

    upgraded = codec.upgrade_source(source, format=SourceFormat.YAML)

    reparsed = codec.parse(upgraded, format=SourceFormat.YAML)
    assert reparsed.ok and reparsed.tree is not None
    expected = upgrade_document_1_0(yaml.safe_load(source))
    assert json.loads(json.dumps(reparsed.tree)) == json.loads(json.dumps(expected))
    assert _hash_of(reparsed.tree) == _hash_of(expected)


def test_flow_style_factors_are_flattened_too() -> None:
    block = _read("quality_momentum.v1_0.yaml")
    newline = chr(10)
    head, rest = block.split(f"factors:{newline}", 1)
    tail = rest[rest.index(f"signal:{newline}") :]
    # JSON은 YAML flow style이므로 같은 팩터 목록을 한 줄 flow mapping으로 바꿔 넣는다.
    flow = json.dumps(yaml.safe_load(block)["factors"], ensure_ascii=False)
    source = f"{head}factors: {flow}{newline}{tail}"
    assert 'factors: {"factors": [' in source
    codec = RuamelDocumentCodec()

    upgraded = codec.upgrade_source(source, format=SourceFormat.YAML)

    reparsed = codec.parse(upgraded, format=SourceFormat.YAML)
    assert reparsed.ok and reparsed.tree is not None
    assert isinstance(reparsed.tree["factors"], list)
    assert _hash_of(reparsed.tree) == _hash_of(upgrade_document_1_0(yaml.safe_load(source)))


@pytest.mark.parametrize(("indent", "trailing"), [(2, "\n"), (4, ""), (4, "\n")])
def test_json_upgrade_keeps_indent_width_and_trailing_newline(indent: int, trailing: str) -> None:
    document = json.loads(_read("quality_momentum.legacy.json"))
    document.pop("identity")
    document["schema_version"] = "1.0"
    document["factors"] = {"factors": document["factors"]}
    document["signal"] = {**document.get("signal", {}), "method": "weighted_sum"}
    source = json.dumps(document, ensure_ascii=False, indent=indent) + trailing
    codec = RuamelDocumentCodec()

    upgraded = codec.upgrade_source(source, format=SourceFormat.JSON)

    assert upgraded.endswith("\n") is bool(trailing)
    assert upgraded.splitlines()[1].startswith(" " * indent + '"')
    assert json.loads(upgraded) == upgrade_document_1_0(document)
    assert json.loads(upgraded)["schema_version"] == "1.1"
