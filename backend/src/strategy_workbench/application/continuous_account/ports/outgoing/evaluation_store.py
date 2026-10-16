"""별도 평가 이력과 프로세스 전체 단일 실행 claim의 저장 계약."""

from datetime import datetime
from typing import Protocol

from ..._models import ContinuousAccountState


class ContinuousEvaluationStorePort(Protocol):
    def claim(self, state: ContinuousAccountState) -> bool:
        """새 claim이면 True, 같은 실험의 기존 ID면 False다.

        트랜잭션으로 running 전역 1개를 보장한다. 다른 실험의 ID 재사용이나 전역 실행
        충돌은 ContinuousAccountRejectedError다. 중복 ID 확인을 running 충돌보다 먼저 한다.
        """
        ...

    def save(self, state: ContinuousAccountState) -> None: ...

    def get(self, evaluation_id: str) -> ContinuousAccountState:
        """미존재는 ContinuousAccountNotFoundError다."""
        ...

    def latest(self, experiment_id: str) -> ContinuousAccountState | None: ...

    def interrupt_running(self, completed_at: datetime) -> None:
        """단일 서버 프로세스 시작 시 이전 running을 interrupted로 닫는다."""
        ...
