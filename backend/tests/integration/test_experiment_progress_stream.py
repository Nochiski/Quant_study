"""#348 리뷰 P3-4: 실험 진행 스트림 — 바뀔 때만 보내고, 조용하면 keepalive, 끝나면 닫는다."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import Event
from typing import cast

import pytest

from strategy_workbench.adapters.inbound.http_api._experiment_routes import (
    _ExperimentStreamHub,
    _progress_stream,
)
from strategy_workbench.application.experiment_run.facade.experiments import ExperimentRunService
from strategy_workbench.domain.experiment.facade.trial import ExperimentStatus, TrialStatus


@dataclass
class _Snapshot:
    status: ExperimentStatus
    trial_counts: dict[TrialStatus, int]
    settled: bool = False


class _Experiments:
    """부를 때마다 다음 상태를 돌려주는 가짜 서비스."""

    def __init__(self, *snapshots: _Snapshot) -> None:
        self._snapshots = list(snapshots)

    def get(self, experiment_id: str) -> _Snapshot:
        return self._snapshots.pop(0)


def test_the_stream_sends_changes_keeps_alive_and_closes_after_the_final_counts() -> None:
    running = _Snapshot(ExperimentStatus.RUNNING, {TrialStatus.RUNNING: 1, TrialStatus.QUEUED: 1})
    cancelled_running = _Snapshot(ExperimentStatus.CANCELLED, {TrialStatus.RUNNING: 1})
    cancelled = _Snapshot(ExperimentStatus.CANCELLED, {TrialStatus.CANCELLED: 2}, settled=True)
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


def test_cancelled_stream_waits_for_validation_runs_even_if_trial_counts_are_final() -> None:
    trials = {TrialStatus.COMPLETED: 2}
    experiments = cast(
        ExperimentRunService,
        _Experiments(
            _Snapshot(ExperimentStatus.CANCELLED, trials, settled=False),
            _Snapshot(ExperimentStatus.CANCELLED, trials, settled=True),
        ),
    )

    async def collect() -> list[str]:
        return [
            frame
            async for frame in _progress_stream(
                experiments, "e", poll_seconds=0, keepalive_seconds=0
            )
        ]

    frames = asyncio.run(collect())
    assert len(frames) == 2
    assert frames[-1].startswith(":")


def test_simultaneous_subscribers_share_one_computation_and_release_the_producer() -> None:
    class CountingExperiments(_Experiments):
        calls = 0

        def get(self, experiment_id: str) -> _Snapshot:
            self.calls += 1
            return super().get(experiment_id)

    source = CountingExperiments(
        _Snapshot(ExperimentStatus.RUNNING, {TrialStatus.RUNNING: 1}),
        _Snapshot(ExperimentStatus.COMPLETED, {TrialStatus.COMPLETED: 1}, settled=True),
    )
    hub = _ExperimentStreamHub(cast(ExperimentRunService, source), poll_seconds=0)

    async def collect() -> list[list[str]]:
        async def subscriber() -> list[str]:
            return [frame async for frame in hub.stream("same-experiment")]

        return list(await asyncio.gather(subscriber(), subscriber()))

    first, second = asyncio.run(collect())
    assert first == second
    assert '"completed"' in first[-1]
    assert source.calls == 2
    assert hub._streams == {}


def test_disconnect_keeps_other_subscribers_and_last_disconnect_stops_computing() -> None:
    class RunningExperiments:
        def get(self, experiment_id: str) -> _Snapshot:
            return _Snapshot(ExperimentStatus.RUNNING, {TrialStatus.RUNNING: 1})

    hub = _ExperimentStreamHub(cast(ExperimentRunService, RunningExperiments()))

    async def exercise() -> None:
        first, second = hub.stream("same"), hub.stream("same")
        assert await anext(first) == await anext(second)
        shared = hub._streams["same"]
        await first.aclose()
        assert shared.subscribers == 1 and not shared.done
        await second.aclose()
        assert shared.producer is not None and shared.producer.done()
        assert not hub._streams

    asyncio.run(exercise())


def test_producer_failure_reaches_every_subscriber_and_does_not_leave_cached_state() -> None:
    class BrokenExperiments:
        calls = 0

        def get(self, experiment_id: str) -> _Snapshot:
            self.calls += 1
            raise RuntimeError("research read failed")

    source = BrokenExperiments()
    hub = _ExperimentStreamHub(cast(ExperimentRunService, source))

    async def collect() -> list[object]:
        async def subscriber() -> list[str]:
            return [frame async for frame in hub.stream("same")]

        return list(await asyncio.gather(subscriber(), subscriber(), return_exceptions=True))

    failures = asyncio.run(collect())
    assert len(failures) == 2 and all(isinstance(error, RuntimeError) for error in failures)
    assert source.calls == 1
    assert not hub._streams


def test_shutdown_releases_tasks_and_the_same_hub_can_serve_a_new_event_loop() -> None:
    class RunningExperiments:
        def get(self, experiment_id: str) -> _Snapshot:
            return _Snapshot(ExperimentStatus.RUNNING, {TrialStatus.RUNNING: 1})

    hub = _ExperimentStreamHub(cast(ExperimentRunService, RunningExperiments()))

    async def lifecycle() -> None:
        stream = hub.stream("same")
        assert '"running"' in await anext(stream)
        producer = hub._streams["same"].producer
        await hub.close()
        assert not hub._streams and producer is not None and producer.done()
        assert [frame async for frame in stream] == []

    asyncio.run(lifecycle())
    asyncio.run(lifecycle())


def test_a_new_subscriber_after_completion_does_not_reuse_a_slow_clients_old_result() -> None:
    source = _Experiments(
        _Snapshot(ExperimentStatus.COMPLETED, {TrialStatus.COMPLETED: 1}, settled=True),
        _Snapshot(ExperimentStatus.RUNNING, {TrialStatus.RUNNING: 1}),
    )
    hub = _ExperimentStreamHub(cast(ExperimentRunService, source))

    async def exercise() -> None:
        old = hub.stream("retried")
        assert '"completed"' in await anext(old)
        fresh = hub.stream("retried")
        assert '"running"' in await anext(fresh)
        new_shared = hub._streams["retried"]
        await old.aclose()
        assert hub._streams["retried"] is new_shared
        await fresh.aclose()
        assert not hub._streams

    asyncio.run(exercise())


@pytest.mark.parametrize("during_read", [False, True])
def test_shutdown_wakes_subscribers_even_before_the_producer_starts(during_read: bool) -> None:
    entered, release = Event(), Event()

    class BlockingExperiments:
        def get(self, experiment_id: str) -> _Snapshot:
            entered.set()
            release.wait()
            return _Snapshot(ExperimentStatus.RUNNING, {TrialStatus.RUNNING: 1})

    hub = _ExperimentStreamHub(cast(ExperimentRunService, BlockingExperiments()))

    async def exercise() -> None:
        stream = hub.stream("shutting-down")
        first = asyncio.create_task(anext(stream))
        # 구독이 생산자를 등록했지만 생산자가 아직 실행되지 않은 정확한 경계다.
        await asyncio.sleep(0)
        shared = hub._streams["shutting-down"]
        try:
            if during_read:
                assert await asyncio.to_thread(entered.wait, 1)
            else:
                assert not entered.is_set()
            await hub.close()
            assert shared.producer is not None and shared.producer.done()
            assert shared.done
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(first, timeout=1)
            assert shared.subscribers == 0
            assert not hub._streams
        finally:
            release.set()
            if not first.done():
                first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            await stream.aclose()
            await hub.close()

    asyncio.run(exercise())
