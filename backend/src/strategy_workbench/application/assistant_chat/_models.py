"""assistant_chat 유스케이스가 소유하는 값 타입 (설계 spec D3).

세션은 "어느 문서에 대한 대화인가"로 묶인다. 서버가 문서 본문을 들고 있지 않으므로, 한 턴에
필요한 문서 상태는 요청마다 `TurnContext`로 실려 온다(spec D7: 컨텍스트는 보낼 때마다 현재
편집기 텍스트·진단·실행 설정을 싣는다).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from strategy_workbench.domain.assistant.facade.models import ProposalCompileResult
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment

__all__ = [
    "BacktestResultUnavailableError",
    "ChatSession",
    "DocumentRef",
    "ResultContext",
    "TurnContext",
    "TurnContextMismatchError",
    "compile_payload",
]


@dataclass(frozen=True)
class DocumentRef:
    """세션이 붙은 대상. 저장된 전략(`strategy_id`)·초안(`draft_id`)·실행(`run_id`) 중 **정확히
    하나**다.

    둘 이상 설정된 값을 받으면 같은 대화가 두 대상에 붙은 것이 되어, 저장소의
    `list_for_document`가 같은 세션을 서로 다른 키로 보게 된다. 사이드바에서 세션이 사라지거나 한
    문서의 세션 목록이 둘로 갈린다. `revision`은 저장된 전략에만 있는 개념이므로 초안·실행에는 붙지
    않는다.

    실행에 붙은 세션은 결과 설명 전용이다(결과 설명 spec R3·R5). 모드는 요청이 아니라 이 값이
    정한다 — 한 세션의 턴이 서로 다른 모드로 돌지 않게 하려는 것이다.
    """

    strategy_id: str | None
    revision: int | None
    draft_id: str | None
    run_id: str | None = None

    def __post_init__(self) -> None:
        named = sum(value is not None for value in (self.strategy_id, self.draft_id, self.run_id))
        if named != 1:
            raise ValueError(
                "document_ref needs exactly one of a saved strategy, a draft or a run — "
                f"{self._describe()}"
            )
        if self.draft_id is not None and self.revision is not None:
            raise ValueError(f"a draft document_ref has no revision — {self._describe()}")
        if self.run_id is not None and self.revision is not None:
            raise ValueError(f"a run document_ref has no revision — {self._describe()}")

    @property
    def is_result(self) -> bool:
        """백테스트 실행 하나에 붙은 결과 설명 세션인가."""
        return self.run_id is not None

    def _describe(self) -> str:
        return (
            f"strategy_id={self.strategy_id!r} revision={self.revision!r} "
            f"draft_id={self.draft_id!r} run_id={self.run_id!r}"
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
    environment: RunEnvironment | None
    diagnostics: tuple[str, ...]


@dataclass(frozen=True)
class ResultContext:
    """결과 설명 턴이 보는 사실. 턴을 시작할 때 서버가 실행 레지스트리에서 읽어 한 번 요약한다.

    요약을 턴 시작 시점에 고정하는 이유는 거절을 HTTP 응답으로 바로 돌려주기 위해서다. 도구가 불릴
    때 읽으면 결과가 없다는 사실이 턴 스레드 안의 도구 오류로만 드러난다.
    """

    run_id: str
    summary: str


class BacktestResultUnavailableError(LookupError):
    """결과 세션이 가리키는 실행의 결과가 없다. 모르는 실행이거나 끝나지 않았거나 실패했다."""

    def __init__(self, run_id: str) -> None:
        super().__init__(
            "backtest result is not available for explanation — "
            f"run_id={run_id!r}; only a completed run of this backend process can be explained, "
            "so rerun the backtest and ask again"
        )
        self.run_id = run_id


class TurnContextMismatchError(ValueError):
    """턴 요청의 문서 컨텍스트가 세션 모드와 맞지 않는다(결과 설명 spec R5).

    전략 세션은 편집기 상태를 턴마다 받아야 하고, 결과 세션은 문서가 없어 받지 않는다. 조용히
    무시하면 frontend 배선 실수가 "모델이 문서를 못 본다"로만 드러난다.
    """

    def __init__(self, session_id: str, *, result_session: bool) -> None:
        if result_session:
            detail = (
                "a result session explains a finished run and takes no document context — "
                "send the turn without context"
            )
        else:
            detail = (
                "a strategy session needs the editor document context on every turn — "
                "send context with source_text"
            )
        super().__init__(f"{detail}; session_id={session_id!r}")
        self.session_id = session_id


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
