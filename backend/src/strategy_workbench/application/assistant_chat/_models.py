"""assistant_chat 유스케이스가 소유하는 값 타입 (설계 spec D3).

세션은 "어느 문서에 대한 대화인가"로 묶인다. 서버가 문서 본문을 들고 있지 않으므로, 한 턴에
필요한 문서 상태는 요청마다 `TurnContext`로 실려 온다(spec D7: 컨텍스트는 보낼 때마다 현재
편집기 텍스트·진단·실행 설정을 싣는다).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from strategy_workbench.domain.assistant.facade.models import ProposalCompileResult

__all__ = ["ChatSession", "DocumentRef", "TurnContext", "compile_payload"]


@dataclass(frozen=True)
class DocumentRef:
    """세션이 붙은 문서. 저장된 전략(`strategy_id`) 또는 초안(`draft_id`) **정확히 하나**다.

    둘 다 설정된 값을 받으면 같은 대화가 두 문서에 붙은 것이 되어, 저장소의
    `list_for_document`가 같은 세션을 서로 다른 키로 보게 된다. 사이드바에서 세션이 사라지거나 한
    문서의 세션 목록이 둘로 갈린다. `revision`은 저장된 전략에만 있는 개념이므로 초안에는 붙지
    않는다.
    """

    strategy_id: str | None
    revision: int | None
    draft_id: str | None

    def __post_init__(self) -> None:
        if (self.strategy_id is None) == (self.draft_id is None):
            raise ValueError(
                "document_ref needs exactly one of a saved strategy or a draft — "
                f"strategy_id={self.strategy_id!r} revision={self.revision!r} "
                f"draft_id={self.draft_id!r}"
            )
        if self.draft_id is not None and self.revision is not None:
            raise ValueError(
                "a draft document_ref has no revision — "
                f"strategy_id={self.strategy_id!r} revision={self.revision!r} "
                f"draft_id={self.draft_id!r}"
            )


@dataclass(frozen=True)
class ChatSession:
    session_id: str
    document_ref: DocumentRef
    provider_profile_id: str
    created_at: datetime
    title: str


@dataclass(frozen=True)
class TurnContext:
    """한 턴이 보는 문서 상태. 서버가 문서를 따로 들지 않으므로 요청마다 실려 온다."""

    source_text: str
    source_format: str
    environment: Mapping[str, object] | None  # reason: 실행 설정은 1.2에서 타입이 정해진다
    diagnostics: tuple[str, ...]


def compile_payload(result: ProposalCompileResult) -> dict[str, object]:
    """검증 결과를 모델에게 돌려줄 JSON 값으로 편다. 도구 결과와 제안 오류가 같은 모양을 쓴다."""
    return {
        "ok": result.ok,
        "spec_hash": result.spec_hash,
        "diagnostics": [
            {
                "code": diagnostic.code,
                "pointer": diagnostic.pointer,
                "message": diagnostic.message,
                "severity": diagnostic.severity,
            }
            for diagnostic in result.diagnostics
        ],
    }
