"""Outgoing port: 전략 YAML 원문을 검증한다 (설계 spec D3).

구현은 bootstrap이 기존 `StrategyAuthoringService.compile`(codec → hydrate → validate)을 감싸
주입한다. 어시스턴트가 검증 규칙을 다시 구현하지 않게 하려는 포트이므로, adapter는 진단을
`ProposalCompileResult`로 옮기기만 하고 새로 판단하지 않는다.
"""

from __future__ import annotations

from typing import Protocol

from strategy_workbench.domain.assistant.facade.models import ProposalCompileResult

__all__ = ["StrategyCompilerPort"]


class StrategyCompilerPort(Protocol):
    def compile(self, source_text: str) -> ProposalCompileResult:
        """YAML 원문을 parse·hydrate·validate한 결과. 오류 진단이 하나라도 있으면 `ok`는 거짓."""
        ...
