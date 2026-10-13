"""#348 리뷰 P3-4: 실험 진행 스트림 — 바뀔 때만 보내고, 조용하면 keepalive, 끝나면 닫는다."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import cast

from strategy_workbench.adapters.inbound.http_api._experiment_routes import _progress_stream
from strategy_workbench.application.experiment_run.facade.experiments import ExperimentRunService
from strategy_workbench.domain.experiment.facade.trial import ExperimentStatus, TrialStatus


@dataclass
class _Snapshot:
    status: ExperimentStatus
    trial_counts: dict[TrialStatus, int]


class _Experiments:
    """부를 때마다 다음 상태를 돌려주는 가짜 서비스."""

    def __init__(self, *snapshots: _Snapshot) -> None:
        self._snapshots = list(snapshots)

    def get(self, experiment_id: str) -> _Snapshot:
        return self._snapshots.pop(0)


def test_the_stream_sends_changes_keeps_alive_and_closes_after_the_final_counts() -> None:
    running = _Snapshot(ExperimentStatus.RUNNING, {TrialStatus.RUNNING: 1, TrialStatus.QUEUED: 1})
    cancelled_running = _Snapshot(ExperimentStatus.CANCELLED, {TrialStatus.RUNNING: 1})
    cancelled = _Snapshot(ExperimentStatus.CANCELLED, {TrialStatus.CANCELLED: 2})
    experiments = cast(
        ExperimentRunService, _Experiments(running, running, cancelled_running, cancelled)
    )

    async def collect() -> list[str]:
        stream = _progress_stream(experiments, "e", poll_seconds=0, keepalive_seconds=0)
        return [frame async for frame in stream]

    frames = asyncio.run(collect())

    kinds = ["keepalive" if frame.startswith(":") else frame.split("\n")[2] for frame in frames]
    # 같은 수는 다시 보내지 않고 조용한 동안 keepalive 만 보낸다. 취소해도 도는 trial 이 끝날
    # 때까지 열려 있다가 최종 수를 보내고 닫힌다.
    assert kinds == [
        'data: {"status":"running","trial_counts":{"running":1,"queued":1}}',
        "keepalive",
        "keepalive",
        'data: {"status":"cancelled","trial_counts":{"running":1}}',
        "keepalive",
        'data: {"status":"cancelled","trial_counts":{"cancelled":2}}',
    ]
