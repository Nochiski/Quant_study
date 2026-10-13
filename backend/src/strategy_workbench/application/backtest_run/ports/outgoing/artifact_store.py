from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult


@dataclass(frozen=True)
class ArtifactCommit:
    sha256: str


class BacktestArtifactUnreadableError(RuntimeError):
    """완료된 run 의 결과 파일이 없거나, 기록한 sha256 과 다르거나, 되살릴 수 없다(V1-04).

    다시 요청해도 나아지지 않는다. 메시지에는 run_id 와 사유만 싣고 서버 경로는 싣지 않는다(#277).
    """


class BacktestArtifactStorePort(Protocol):
    def commit(self, result: BacktestRunResult) -> ArtifactCommit: ...

    def load(self, run_id: str, *, sha256: str) -> BacktestRunResult:
        """커밋한 결과를 되살린다. 못 읽으면 `BacktestArtifactUnreadableError`."""
        ...

    def discard(self, run_id: str) -> None:
        """Remove a committed bundle that must not become visible to run consumers."""
        ...
