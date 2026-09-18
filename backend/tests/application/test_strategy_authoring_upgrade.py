"""StrategyAuthoringService.upgrade (spec D3, P1-04).

safe parse → 1.0 확인 → 텍스트 변환 → drift 검사 → compile. 두 변환 경로(dict·CST)가 어긋나면
결과를 돌려주지 않는다."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompileRequest,
    DocumentNotUpgradeableError,
    DocumentUpgradeDriftError,
    DocumentUpgradeSyntaxError,
    StrategyAuthoringService,
)
from strategy_workbench.application.strategy_authoring.facade.ports import (
    ParsedDocument,
    SourceFormat,
)
from strategy_workbench.domain.strategy.facade.document import (
    hydrate_strategy_document,
    upgrade_document_1_0,
)
from strategy_workbench.domain.strategy.facade.specification import (
    StrategyIdentity,
    strategy_spec_hash,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"


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


def test_upgrade_returns_1_1_text_whose_compile_matches_the_dict_path() -> None:
    source = _read("quality_momentum.v1_0.commented.yaml")

    upgraded = _service().upgrade(CompileRequest(source, SourceFormat.YAML))

    assert upgraded.format is SourceFormat.YAML
    assert upgraded.source == _read("quality_momentum.v1_1.commented.yaml")
    assert upgraded.compiled.spec_hash is not None and not upgraded.compiled.diagnostics
    expected = hydrate_strategy_document(
        upgrade_document_1_0(yaml.safe_load(source)), identity=StrategyIdentity("draft", 0)
    )
    assert expected.spec is not None
    assert upgraded.compiled.spec_hash == strategy_spec_hash(expected.spec)
    assert upgraded.source_hash == upgraded.compiled.source_hash


def test_upgrade_reports_semantic_diagnostics_of_the_upgraded_text_without_hiding_them() -> None:
    """변환 자체는 성공하지만 1.1에서 semantic error가 나는 문서: 결과는 돌려주고 진단을 담는다."""
    source = _read("quality_momentum.v1_0.yaml").replace(
        "max_name_weight: 0.05", "max_name_weight: 1.5"
    )

    upgraded = _service().upgrade(CompileRequest(source, SourceFormat.YAML))

    assert upgraded.compiled.spec_hash is None
    assert [d.code for d in upgraded.compiled.diagnostics] == ["strategy.risk.max_name_weight"]


@pytest.mark.parametrize("version", ['"1.1"', '"2.0"', "1.0"])
def test_non_1_0_documents_are_refused(version: str) -> None:
    source = _read("quality_momentum.v1_0.yaml").replace(
        'schema_version: "1.0"', f"schema_version: {version}"
    )

    with pytest.raises(DocumentNotUpgradeableError, match="only schema 1.0") as info:
        _service().upgrade(CompileRequest(source, SourceFormat.YAML))
    assert str(info.value.schema_version) in {"1.1", "2.0", "1.0"}


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
    drifted = _read("quality_momentum.yaml").replace("selection_count: 20", "selection_count: 21")

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
