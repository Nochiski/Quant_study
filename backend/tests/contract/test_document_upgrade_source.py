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
LF = chr(10)
CR = chr(13)


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _hash_of(document: object) -> str:
    assert isinstance(document, dict)
    hydrated = hydrate_strategy_document(document, identity=DRAFT)
    assert hydrated.ok and hydrated.spec is not None, hydrated.issues
    return strategy_spec_hash(hydrated.spec)


def test_commented_yaml_upgrade_matches_the_golden_byte_for_byte() -> None:
    """주석 배치 전부를 덮는 golden: 안쪽 키 줄끝, 바깥 독립 주석 뒤 시퀀스, 삭제 키 위·아래·줄끝,
    섹션 줄끝, 최상위 다음 키를 설명하는 주석."""
    upgraded = RuamelDocumentCodec().upgrade_source(
        _read("quality_momentum.v1_0.commented.yaml"), format=SourceFormat.YAML
    )

    assert upgraded == _read("quality_momentum.v1_1.commented.yaml")
    assert "# 팩터 목록 (바깥 독립 주석)" in upgraded  # 독립 주석 보존
    assert "# 수수료 bp" in upgraded and "# 종가" in upgraded  # 남은 줄의 줄끝 주석 보존
    assert "# 삭제 키" not in upgraded  # 삭제된 줄의 줄끝 주석은 함께 사라진다
    assert "method 위 독립 주석" not in upgraded  # 삭제 키 위의 독립 주석도 사라진다
    assert "entry 위 독립 주석" not in upgraded and "주문 방식 설명" not in upgraded
    # 삭제 키 아래, 남는 키를 설명하는 주석은 자리를 지킨다
    assert "# score 위 독립 주석" in upgraded
    assert "# 수수료 설명" in upgraded
    assert "# 신호 섹션 줄끝 주석" in upgraded  # 섹션 키의 줄끝 주석은 섹션이 남는 한 유지된다
    assert "# parameters 위 독립 주석" in upgraded  # 최상위 다음 키를 설명하는 주석
    assert 'schema_version: "1.1"' in upgraded  # 따옴표 보존
    assert CR not in upgraded  # 줄바꿈은 LF로 통일된다


def test_emptied_section_keeps_its_eol_comment_and_untouched_sections_keep_theirs() -> None:
    """P1-04 리뷰 P2-002: 이번 변환으로 비어 버린 섹션만 손보고, 섹션 줄끝 주석은 남긴다."""
    source = _read("quality_momentum.v1_0.yaml").replace(
        f"signal:{LF}  method: weighted_sum{LF}",
        f"signal:   # 신호 섹션{LF}  # method 위{LF}  method: weighted_sum{LF}",
    )
    source = source.replace(f"parameters: []{LF}", f"parameters: []   # 비어 있음{LF}")
    assert "signal:   # 신호 섹션" in source and "parameters: []   # 비어 있음" in source

    upgraded = RuamelDocumentCodec().upgrade_source(source, format=SourceFormat.YAML)

    assert f"signal: {{}}  # 신호 섹션{LF}" in upgraded
    assert "# method 위" not in upgraded
    assert "parameters: []   # 비어 있음" in upgraded  # 원래부터 빈 값은 건드리지 않는다
    reparsed = RuamelDocumentCodec().parse(upgraded, format=SourceFormat.YAML)
    assert reparsed.ok and reparsed.tree is not None and reparsed.tree["signal"] == {}


def test_crlf_source_is_normalised_to_lf() -> None:
    source = _read("quality_momentum.v1_0.yaml").replace(LF, CR + LF)

    upgraded = RuamelDocumentCodec().upgrade_source(source, format=SourceFormat.YAML)

    assert CR not in upgraded
    assert _hash_of(RuamelDocumentCodec().parse(upgraded, format=SourceFormat.YAML).tree) == (
        _hash_of(upgrade_document_1_0(yaml.safe_load(source)))
    )


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
    head, rest = block.split(f"factors:{LF}", 1)
    tail = rest[rest.index(f"signal:{LF}") :]
    # JSON은 YAML flow style이므로 같은 팩터 목록을 한 줄 flow mapping으로 바꿔 넣는다.
    flow = json.dumps(yaml.safe_load(block)["factors"], ensure_ascii=False)
    source = f"{head}factors: {flow}{LF}{tail}"
    assert 'factors: {"factors": [' in source
    codec = RuamelDocumentCodec()

    upgraded = codec.upgrade_source(source, format=SourceFormat.YAML)

    reparsed = codec.parse(upgraded, format=SourceFormat.YAML)
    assert reparsed.ok and reparsed.tree is not None
    assert isinstance(reparsed.tree["factors"], list)
    assert _hash_of(reparsed.tree) == _hash_of(upgrade_document_1_0(yaml.safe_load(source)))


@pytest.mark.parametrize(("indent", "trailing"), [(2, LF), (4, ""), (4, LF)])
def test_json_upgrade_keeps_indent_width_and_trailing_newline(indent: int, trailing: str) -> None:
    document = json.loads(_read("quality_momentum.legacy.json"))
    document.pop("identity")
    document["schema_version"] = "1.0"
    document["factors"] = {"factors": document["factors"]}
    document["signal"] = {**document.get("signal", {}), "method": "weighted_sum"}
    source = json.dumps(document, ensure_ascii=False, indent=indent) + trailing
    codec = RuamelDocumentCodec()

    upgraded = codec.upgrade_source(source, format=SourceFormat.JSON)

    assert upgraded.endswith(LF) is bool(trailing)
    assert upgraded.splitlines()[1].startswith(" " * indent + '"')
    assert json.loads(upgraded) == upgrade_document_1_0(document)
    assert json.loads(upgraded)["schema_version"] == "1.1"
