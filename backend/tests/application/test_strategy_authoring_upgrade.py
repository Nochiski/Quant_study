"""StrategyAuthoringService.upgrade (spec D3·D7, P1-04 → P2-09).

safe parse → 업그레이드 가능 판정 → 텍스트 변환 → drift 검사 → compile. 두 변환 경로(dict·CST)가
어긋나면 결과를 돌려주지 않는다. 결과는 현재 버전 원문과, 옛 문서에서 떼어 낸 실행 설정·알릴
사실(`environment`·`warnings`)이다."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompileRequest,
    DocumentNotUpgradeableError,
    DocumentUpgradeDriftError,
    DocumentUpgradeSyntaxError,
    DocumentUpgradeUnsupportedNodeError,
    StrategyAuthoringService,
)
from strategy_workbench.application.strategy_authoring.facade.ports import (
    ParsedDocument,
    SourceFormat,
)
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.strategy.facade.document import (
    CURRENT_SCHEMA_VERSION,
    ENVIRONMENT_UNAVAILABLE_CODE,
    upgrade_document,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"
LF = chr(10)
# 1.0·1.1 golden 이 `data`·`execution` 에 적어 둔 실행 설정.
GOLDEN_ENVIRONMENT = RunEnvironment(
    start=date(2021, 1, 1), end=date(2026, 8, 31), universe_id="krx.common-stock"
)


class _DriftingCodec:
    """Wraps the real codec but returns rewritten text the domain transform would not produce."""

    def __init__(self, rewrite: str) -> None:
        self._inner = RuamelDocumentCodec()
        self._rewrite = rewrite

    def parse(self, source: str, *, format: SourceFormat) -> ParsedDocument:
        return self._inner.parse(source, format=format)

    def upgrade_source(self, source: str, *, format: SourceFormat) -> str:
        return self._rewrite


def _service(codec: object | None = None) -> StrategyAuthoringService:
    return StrategyAuthoringService(
        codec or RuamelDocumentCodec(),  # pyright: ignore[reportArgumentType]  # reason: 테스트 이중체
        factor_registry_version="r",
        dataset_snapshot_id=lambda: "s",
    )


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_upgrade_returns_current_text_its_environment_and_a_clean_compile() -> None:
    """1.0 문서가 현재 버전까지 올라 곧바로 저장할 수 있는 원문이 된다(spec D7)."""
    source = _read("quality_momentum.v1_0.commented.yaml")

    upgraded = _service().upgrade(CompileRequest(source, SourceFormat.YAML))

    assert upgraded.format is SourceFormat.YAML
    assert upgraded.source == _read("quality_momentum.v1_2.commented.yaml")
    assert upgraded.compiled.schema_version == CURRENT_SCHEMA_VERSION
    assert upgraded.compiled.spec_hash is not None and upgraded.compiled.diagnostics == ()
    assert yaml.safe_load(upgraded.source) == upgrade_document(yaml.safe_load(source)).tree
    assert upgraded.source_hash == upgraded.compiled.source_hash
    # 1.0 원문의 `data`·`execution`·`missing_policy` 가 실행 설정으로 돌아온다.
    assert upgraded.environment == GOLDEN_ENVIRONMENT
    assert upgraded.warnings == ()


def test_a_1_1_document_upgrades_to_the_same_meaning_as_the_1_0_golden() -> None:
    """1.1 원문(P2-03 보존분)은 1.2 단계만 탄다. 결과는 같은 전략·같은 실행 설정이다."""
    from_1_1 = _service().upgrade(
        CompileRequest(_read("quality_momentum.v1_1.yaml"), SourceFormat.YAML)
    )
    from_1_0 = _service().upgrade(
        CompileRequest(_read("quality_momentum.v1_0.yaml"), SourceFormat.YAML)
    )

    assert from_1_1.compiled.spec_hash is not None
    assert from_1_1.compiled.spec_hash == from_1_0.compiled.spec_hash
    assert from_1_1.environment == from_1_0.environment == GOLDEN_ENVIRONMENT
    assert f"signal:{LF}  normalization: none{LF}portfolio:" in from_1_1.source


def test_upgrade_reports_diagnostics_of_the_upgraded_text_without_hiding_them() -> None:
    """변환 자체는 성공해도 결과 문서의 semantic error 는 감추지 않고 그대로 담아 돌려준다.

    P2-03 ~ P2-08 동안 결과가 1.1 에서 멈춰 이 단언을 `structure.unsupported_schema_version` 으로
    바꿔 두었다(P2-03 리뷰 P3-07). 결과가 현재 버전이 되어 원래 계약으로 되돌린다.
    """
    source = _read("quality_momentum.v1_0.yaml").replace(
        "max_name_weight: 0.05", "max_name_weight: 1.5"
    )

    upgraded = _service().upgrade(CompileRequest(source, SourceFormat.YAML))

    assert upgraded.compiled.spec_hash is None
    codes = [diagnostic.code for diagnostic in upgraded.compiled.diagnostics]
    assert codes == ["strategy.risk.max_name_weight"]


def test_an_unquoted_1_0_version_line_upgrades() -> None:
    """따옴표 없는 `schema_version: 1.0` 도 선언된 1.0 이다."""
    source = _read("quality_momentum.v1_0.yaml").replace(
        'schema_version: "1.0"', "schema_version: 1.0"
    )

    upgraded = _service().upgrade(CompileRequest(source, SourceFormat.YAML))

    assert upgraded.compiled.schema_version == CURRENT_SCHEMA_VERSION
    assert upgraded.compiled.spec is not None and upgraded.compiled.diagnostics == ()


@pytest.mark.parametrize("version", ['"1.1"', f'"{CURRENT_SCHEMA_VERSION}"'])
def test_a_1_0_body_under_a_later_version_line_is_refused(version: str) -> None:
    """Phase 2 감사 NB-1: 문서가 선언한 버전보다 앞선 단계는 타지 않는다."""
    source = _read("quality_momentum.v1_0.yaml").replace(
        'schema_version: "1.0"', f"schema_version: {version}"
    )

    with pytest.raises(DocumentNotUpgradeableError) as info:
        _service().upgrade(CompileRequest(source, SourceFormat.YAML))
    assert str(info.value.schema_version) == version.strip('"')


# 감사 탐침(`ap2_probe_upgrade.py`) 문서: 1.2 작성자가 옛 예제를 베껴 팩터 하나에 `unary rank` 를
# 적었고 `signal` 은 생략했다(= 1.2 기본값 `rank`).
CURRENT_WITH_ONE_1_0_NODE = """\
schema_version: "1.2"
title: "1.2 문서에 옛 unary rank 한 줄"
factors:
  - factor_id: pbr
    direction: low
    graph:
      nodes:
        - kind: field
          node_id: pbr
          field_id: valuation.pbr
        - kind: unary
          node_id: ranked
          operator: rank
          input_node_id: pbr
      output_node_id: ranked
  - factor_id: mom
    direction: high
    graph:
      nodes:
        - kind: field
          node_id: close
          field_id: price.close
        - kind: time_series
          node_id: m
          operator: momentum
          input_node_id: close
          window: 20
      output_node_id: m
portfolio:
  selection_count: 20
"""


def test_a_current_document_with_a_1_0_node_is_fixed_in_place_not_upgraded() -> None:
    """NB-1 회귀: 업그레이드가 `normalization: none` 을 조용히 넣고 문서에 없던 `/data/*` warning 을
    내던 경로를 막는다. compile 은 구조 오류와 제자리 수정 방법을 말하고, 업그레이드는 거절한다."""
    assert f'schema_version: "{CURRENT_SCHEMA_VERSION}"' in CURRENT_WITH_ONE_1_0_NODE
    request = CompileRequest(CURRENT_WITH_ONE_1_0_NODE, SourceFormat.YAML)

    compiled = _service().compile(request)

    assert [(d.code, d.pointer) for d in compiled.diagnostics] == [
        ("structure.legacy_shape", "/factors/0/graph/nodes/1/operator")
    ]
    assert "kind와 operator를 함께 바꾸세요" in compiled.diagnostics[0].message
    assert "업그레이드" not in compiled.diagnostics[0].message
    with pytest.raises(DocumentNotUpgradeableError, match="fix them in place"):
        _service().upgrade(request)


def test_a_future_version_line_is_refused_even_with_a_1_0_body() -> None:
    """버전 상한(BACKLOG-010): 모르는 버전은 본문이 옛 모양이어도 강등하지 않는다."""
    source = _read("quality_momentum.v1_0.yaml").replace(
        'schema_version: "1.0"', 'schema_version: "2.0"'
    )

    with pytest.raises(DocumentNotUpgradeableError, match="neither current nor a known") as info:
        _service().upgrade(CompileRequest(source, SourceFormat.YAML))
    assert str(info.value.schema_version) == "2.0"


def test_a_current_document_is_refused() -> None:
    with pytest.raises(DocumentNotUpgradeableError, match="already current") as info:
        _service().upgrade(CompileRequest(_read("quality_momentum.yaml"), SourceFormat.YAML))
    assert info.value.schema_version == CURRENT_SCHEMA_VERSION


def test_a_saved_reference_node_refuses_the_upgrade_with_its_pointer() -> None:
    """spec D7: `saved_*` 노드는 1.2 에 없다. 조용히 지우지 않고 자리와 함께 거절한다."""
    source = _read("quality_momentum.v1_1.yaml").replace(
        f"          window: 252{LF}",
        f"          window: 252{LF}        - kind: saved_factor{LF}          node_id: ref{LF}",
    )

    with pytest.raises(DocumentUpgradeUnsupportedNodeError) as info:
        _service().upgrade(CompileRequest(source, SourceFormat.YAML))

    assert info.value.pointer == "/factors/0/graph/nodes/2/kind"
    assert "saved_factor" in str(info.value)


def test_settings_that_cannot_become_a_run_environment_are_warned_not_invented() -> None:
    """옮기지 못한 실행 설정은 기본값으로 지어내지 않는다.

    `environment` 는 비고 warning 이 자리를 짚는다.
    """
    source = _read("quality_momentum.v1_1.yaml").replace(
        'start: "2021-01-01"', 'start: "2021/01/01"'
    )

    upgraded = _service().upgrade(CompileRequest(source, SourceFormat.YAML))

    assert upgraded.compiled.spec_hash is not None  # 문서 업그레이드 자체는 성공한다
    assert upgraded.environment is None
    assert [(w.code, w.pointer) for w in upgraded.warnings] == [
        (ENVIRONMENT_UNAVAILABLE_CODE, "/data/start")
    ]
    assert "2021/01/01" in upgraded.warnings[0].message


def test_document_warnings_reach_the_result() -> None:
    source = _read("quality_momentum.v1_1.yaml").replace(
        "  rebalance: monthly", "  rebalance: monthly\n  weighting: factor_score"
    )

    upgraded = _service().upgrade(CompileRequest(source, SourceFormat.YAML))

    assert [w.code for w in upgraded.warnings] == [
        "strategy_document.upgrade_weighting_rule_changed"
    ]
    assert upgraded.environment == GOLDEN_ENVIRONMENT


def test_syntax_errors_carry_the_codec_diagnostics() -> None:
    with pytest.raises(DocumentUpgradeSyntaxError) as info:
        _service().upgrade(
            CompileRequest(_read("quality_momentum.invalid.yaml"), SourceFormat.YAML)
        )

    compiled = info.value.compiled
    assert compiled.spec is None and compiled.diagnostics
    assert all(d.kind.value == "syntax" for d in compiled.diagnostics)
    # DEFECT-P1X-004: 로그만 보고도 어떤 요청이 실패했는지 특정할 수 있어야 한다.
    message = str(info.value)
    assert f"format={compiled.format.value}" in message
    assert f"source_hash={compiled.source_hash}" in message
    assert compiled.diagnostics[0].code in message


def test_drift_between_the_two_transform_paths_is_refused_with_a_pointer() -> None:
    source = _read("quality_momentum.v1_0.yaml")
    drifted = (
        _service()
        .upgrade(CompileRequest(source, SourceFormat.YAML))
        .source.replace("selection_count: 20", "selection_count: 21")
    )

    with pytest.raises(DocumentUpgradeDriftError, match="pointer='/portfolio/selection_count'"):
        _service(_DriftingCodec(drifted)).upgrade(CompileRequest(source, SourceFormat.YAML))


def test_rewritten_text_that_does_not_parse_is_drift_too() -> None:
    source = _read("quality_momentum.v1_0.yaml")

    with pytest.raises(DocumentUpgradeDriftError, match="does not parse"):
        _service(_DriftingCodec("schema_version: [unclosed")).upgrade(
            CompileRequest(source, SourceFormat.YAML)
        )


class _CrashingCodec(_DriftingCodec):
    def upgrade_source(self, source: str, *, format: SourceFormat) -> str:
        raise IndexError("string index out of range")  # ruamel 내부 오류를 흉내 낸다


def test_any_adapter_failure_is_a_drift_not_a_server_error() -> None:
    """P1-04 재검토 P1-003: untrusted input이 어댑터 안에서 무엇을 던지든 500이 아니라 422
    drift다."""
    source = _read("quality_momentum.v1_0.yaml")

    with pytest.raises(DocumentUpgradeDriftError, match="rewrite failed: IndexError"):
        _service(_CrashingCodec("")).upgrade(CompileRequest(source, SourceFormat.YAML))
