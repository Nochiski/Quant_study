from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult


@dataclass(frozen=True)
class ArtifactCommit:
    uri: str
    sha256: str
    size_bytes: int


class BacktestArtifactStorePort(Protocol):
    def commit(self, result: BacktestRunResult) -> ArtifactCommit: ...

    def discard(self, run_id: str) -> None:
        """Remove a committed bundle that must not become visible to run consumers."""
        ...
