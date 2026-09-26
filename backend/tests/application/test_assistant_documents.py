"""assistant_chat 값 객체 테스트 (A-01).

`DocumentRef`는 "이 대화가 어느 문서에 붙었는가"를 나르는 키다. 모순된 조합을 받아 주면 저장소의
`list_for_document`가 같은 세션을 서로 다른 키로 보게 되고, 사이드바에서 세션이 사라지거나 한
문서의 세션 목록이 둘로 갈린다. 계약 층에서 값으로 막는 편이 싸다.
"""

from __future__ import annotations

import pytest

# A-01에는 이 값들을 내보내는 facade가 아직 없다(`facade/chat.py`는 서비스와 함께 A-02에서 온다).
# 노드 자기 모듈의 단위 테스트이므로 내부 모듈을 직접 읽는다.
from strategy_workbench.application.assistant_chat._models import (
    DocumentRef,
    compile_payload,
)
from strategy_workbench.domain.assistant.facade.models import (
    ProposalCompileResult,
    ProposalDiagnostic,
)


def test_a_saved_strategy_reference_keeps_its_revision() -> None:
    reference = DocumentRef(strategy_id="strategy-1", revision=3, draft_id=None)

    assert reference.strategy_id == "strategy-1"
    assert reference.revision == 3


def test_a_draft_reference_needs_no_revision() -> None:
    reference = DocumentRef(strategy_id=None, revision=None, draft_id="draft-9")

    assert reference.draft_id == "draft-9"


def test_a_reference_without_any_document_is_rejected() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        DocumentRef(strategy_id=None, revision=None, draft_id=None)


def test_a_reference_to_both_a_strategy_and_a_draft_is_rejected() -> None:
    with pytest.raises(ValueError, match="exactly one") as raised:
        DocumentRef(strategy_id="strategy-1", revision=None, draft_id="draft-9")

    # 진단 디테일 규칙: 세 필드 값이 모두 메시지에 있어야 재현할 수 있다.
    rendered = str(raised.value)
    assert "strategy_id='strategy-1'" in rendered
    assert "draft_id='draft-9'" in rendered
    assert "revision=None" in rendered


def test_a_draft_reference_with_a_revision_is_rejected() -> None:
    with pytest.raises(ValueError, match="draft document_ref has no revision"):
        DocumentRef(strategy_id=None, revision=99, draft_id="draft-9")


def test_compile_payload_reports_a_passing_document() -> None:
    result = ProposalCompileResult(ok=True, spec_hash="spec-hash-1", diagnostics=())

    assert compile_payload(result) == {
        "ok": True,
        "spec_hash": "spec-hash-1",
        "diagnostics": [],
    }


def test_compile_payload_flattens_every_diagnostic_for_the_model() -> None:
    result = ProposalCompileResult(
        ok=False,
        spec_hash=None,
        diagnostics=(
            ProposalDiagnostic(
                code="strategy.factor.unknown",
                pointer="/factors/0/factor_id",
                message="알 수 없는 팩터 식별자",
                severity="error",
            ),
            ProposalDiagnostic(
                code="strategy.field.inapplicable",
                pointer="/portfolio/selection_method",
                message="이 모드에서는 읽지 않는 필드",
                severity="warning",
            ),
        ),
    )

    payload = compile_payload(result)

    assert payload["ok"] is False
    assert payload["spec_hash"] is None
    assert payload["diagnostics"] == [
        {
            "code": "strategy.factor.unknown",
            "pointer": "/factors/0/factor_id",
            "message": "알 수 없는 팩터 식별자",
            "severity": "error",
        },
        {
            "code": "strategy.field.inapplicable",
            "pointer": "/portfolio/selection_method",
            "message": "이 모드에서는 읽지 않는 필드",
            "severity": "warning",
        },
    ]
