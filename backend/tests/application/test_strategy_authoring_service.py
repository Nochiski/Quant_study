"""P1-03 compile use case: warning-only validations still yield an executable spec."""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.application.strategy_authoring import _service
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompileRequest,
    StrategyAuthoringService,
)
from strategy_workbench.application.strategy_authoring.facade.ports import (
    DiagnosticKind,
    DiagnosticSeverity,
    SourceFormat,
)
from strategy_workbench.domain.strategy.facade.validation import (
    StrategyValidation,
    ValidationIssue,
    ValidationKind,
    ValidationSeverity,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"
GOLDEN_SPEC_HASH = "9eb6872a3ca250dfb78b0887e5b236a98b24fb2ccdf2d6af0540218d49e998fe"


def test_warning_only_validation_keeps_spec_and_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    warning = ValidationIssue(
        code="strategy.expression.lag_periods",
        path="factors.factors.0.graph.nodes.1",
        message="warm-up",
        kind=ValidationKind.SEMANTIC,
        severity=ValidationSeverity.WARNING,
        node_id="mom_252",
    )
    monkeypatch.setattr(
        _service,
        "validate_strategy",
        lambda spec: StrategyValidation(valid=True, issues=(warning,)),
    )
    service = StrategyAuthoringService(
        RuamelDocumentCodec(), factor_registry_version="r", dataset_snapshot_id=lambda: "s"
    )

    compiled = service.compile(
        CompileRequest(
            (FIXTURES / "quality_momentum.yaml").read_text(encoding="utf-8"), SourceFormat.YAML
        )
    )

    assert compiled.ok and compiled.spec_hash == GOLDEN_SPEC_HASH
    (diagnostic,) = compiled.diagnostics
    assert diagnostic.severity is DiagnosticSeverity.WARNING
    assert diagnostic.kind is DiagnosticKind.SEMANTIC
    assert diagnostic.node_id == "mom_252"
    assert diagnostic.pointer == "/factors/factors/0/graph/nodes/1"
    assert diagnostic.range is not None


def test_contract_reads_the_dataset_snapshot_per_call() -> None:
    snapshots = iter(["snap-1", "snap-2"])
    service = StrategyAuthoringService(
        RuamelDocumentCodec(),
        factor_registry_version="r",
        dataset_snapshot_id=lambda: next(snapshots),
    )
    first, second = service.contract(), service.contract()
    assert (first.dataset_snapshot_id, second.dataset_snapshot_id) == ("snap-1", "snap-2")
    assert first.contract_hash != second.contract_hash
    assert first.schema_hash == second.schema_hash
